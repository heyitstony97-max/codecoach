"""import_materials / import_outline: turn text into a saved course map.

Nothing is written to the store until the whole map is built and validated, so a failed import leaves
no half-built course. The failure is recorded under meta `import:<course_id>` (status "failed") and,
when a job id is given, as job status "error".
"""

import re
from typing import Callable

from codecoach.core.llm import LLMBadOutput, LLMError, LLMPort, LLMUnavailable, structured_call
from codecoach.pdf import GAP_RE, is_fragment as pdf_is_fragment
from codecoach.core.models import Course, CourseMap, Doc, Edge, Lesson, Objective, Unit
from codecoach.core.passages import is_quotable, normalize_newlines, split_document
from codecoach.core.store import Store
from codecoach.core.text import normalize, slugify
from codecoach.importer import draft as D
from codecoach.importer import syllabus as SY
from codecoach.importer.lang import infer_code_language
from codecoach.importer.subject import infer_subject
from codecoach.importer.graph import break_cycles, clean_edges, fallback_edges
from codecoach.importer.sections import Section, has_headings, plan_sections, split_sections
from codecoach.importer.verbs import COMPUTED_VERBS, looks_like_code

Progress = Callable[[str, float], None]

SECTION_ATTEMPTS = 3            # a section that fails this many times becomes an import issue
ABORT_AFTER_UNAVAILABLE = 3     # this many sections in a row with the model unreachable aborts the import
OUTLINE_MODE_REASON = "no source materials attached"
OPEN_MODE_REASON = "open mode chosen by the learner"


class ImportFailed(Exception):
    """The whole import failed. Nothing was saved. `course_id` is the id that was reserved."""

    def __init__(self, message: str, course_id: str = ""):
        super().__init__(message)
        self.course_id = course_id


def report_key(course_id: str) -> str:
    return f"import:{course_id}"


def get_import_report(store: Store, course_id: str) -> dict | None:
    return store.get_meta(report_key(course_id))


def import_materials(store: Store, llm: LLMPort, *, title: str, text: str, doc_name: str,
                     mode: str = "strict", progress: Progress | None = None,
                     doc_kind: str | None = None, job_id: str | None = None,
                     code_language: str | None = None, subject: str | None = None) -> str:
    """Import the learner's materials. Returns the new course id; raises ImportFailed on total failure."""
    if mode not in ("strict", "open"):
        raise ValueError(f"mode must be 'strict' or 'open', got {mode!r}")
    return _run(store, llm, title=title, text=text, doc_name=doc_name, mode=mode,
                mode_reason="" if mode == "strict" else OPEN_MODE_REASON, outline=False,
                progress=progress, doc_kind=doc_kind, job_id=job_id, code_language=code_language, subject=subject)


def import_with_syllabus(store: Store, llm: LLMPort, *, title: str, materials_text: str, doc_name: str,
                         syllabus_text: str, mode: str = "strict", progress: Progress | None = None,
                         job_id: str | None = None, doc_kind: str | None = None,
                         code_language: str | None = None, subject: str | None = None) -> str:
    """Check the learner's syllabus against their materials. One objective per syllabus line; a line
    the materials do not cover is stored not_covered (greyed out, never taught or quizzed) with the reason
    in the import report under `syllabus`. Returns the new course id; raises ImportFailed on total failure."""
    if mode not in ("strict", "open"):
        raise ValueError(f"mode must be 'strict' or 'open', got {mode!r}")
    return _run(store, llm, title=title, text=materials_text, doc_name=doc_name, mode=mode,
                mode_reason="" if mode == "strict" else OPEN_MODE_REASON, outline=False,
                progress=progress, doc_kind=doc_kind, job_id=job_id, code_language=code_language, subject=subject,
                syllabus_text=syllabus_text)


def import_outline(store: Store, llm: LLMPort, *, title: str, text: str, doc_name: str = "outline",
                   progress: Progress | None = None, job_id: str | None = None,
                   code_language: str | None = None, subject: str | None = None) -> str:
    """Import a syllabus/outline that has no source text. The course is mode 'open' with a stated
    reason, so reading items are labelled origin=model_knowledge. Code/math/numeric truth still comes
    from the checkers."""
    return _run(store, llm, title=title, text=text, doc_name=doc_name, mode="open",
                mode_reason=OUTLINE_MODE_REASON, outline=True, progress=progress, doc_kind=None,
                job_id=job_id, code_language=code_language, subject=subject)


# ---------------------------------------------------------------- the pipeline


def _unique(base: str, taken) -> str:
    cand, n = base[:60], 1
    while taken(cand):
        n += 1
        cand = f"{base[:56]}-{n}"
    return cand


def _run(store: Store, llm: LLMPort, *, title: str, text: str, doc_name: str, mode: str,
         mode_reason: str, outline: bool, progress: Progress | None, doc_kind: str | None,
         job_id: str | None, code_language: str | None = None, syllabus_text: str | None = None,
         subject: str | None = None) -> str:
    if code_language not in (None, "python", "c"):
        raise ValueError(f"code_language must be 'python', 'c' or None, got {code_language!r}")
    code_language = code_language or infer_code_language(title, text)
    course_id = _unique(slugify(title, default="course"), lambda c: store.get_course(c) is not None)

    def report(stage: str, pct: float) -> None:
        if job_id:
            store.update_job(job_id, status="running", pct=pct, stage=stage, course_id=course_id)
        if progress:
            try:
                progress(stage, pct)
            except Exception:
                pass    # a broken UI callback must not break the import

    try:
        report("Reading your materials", 2)
        result = _build(store, llm, course_id, title, text, doc_name, mode, mode_reason, outline,
                        doc_kind, report, code_language, syllabus_text, subject)
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        msg = str(e) or type(e).__name__
        try:
            store.set_meta(report_key(course_id), {"status": "failed", "error": msg, "issues": []})
            if job_id:
                store.update_job(job_id, status="error", error=msg, stage="Import failed", course_id=course_id)
        except Exception:
            pass
        if isinstance(e, ImportFailed):
            raise
        raise ImportFailed(msg, course_id) from e
    if job_id:
        store.update_job(job_id, status="done", pct=100, stage="Done", course_id=course_id)
    if progress:
        try:
            progress("Done", 100)
        except Exception:
            pass
    return result


def _build(store, llm, course_id, title, text, doc_name, mode, mode_reason, outline, doc_kind, report,
           code_language=None, syllabus_text=None, subject=None) -> str:
    if not text or not text.strip():
        raise ImportFailed("there is no text to import", course_id)
    doc_id = _unique(slugify(doc_name, default="doc"), lambda d: store.get_doc(d) is not None)
    kind = doc_kind or ("markdown" if has_headings(text) else "text")
    passages = split_document(doc_id, text, kind)
    if doc_name.lower().endswith(".pdf"):
        # Page numbers, map labels and dot-leader contents lines stay in the stored text (Sources shows
        # all of it) but are not passages: nothing is taught or asked from them. Ids keep their numbers.
        passages = [p.model_copy(update={"formula_missing": bool(GAP_RE.search(p.text))})
                    for p in passages if not pdf_is_fragment(p.text)]
    if not passages:
        raise ImportFailed("no readable paragraphs found in the text", course_id)
    # Only passages that say something are quoted, cited or used as the support of a topic. Headings, numerals
    # and 1-3 word lines stay stored (Read and Sources show them, ids are unchanged). An outline's lines are
    # short by nature, so outlines use every passage. If nothing is quotable the whole text is kept as is.
    usable = passages if outline else ([p for p in passages if is_quotable(p.text)] or passages)
    # (`usable is passages` means nothing was quotable: a text of one-word lines is taken as it comes)
    sections = [] if syllabus_text is not None else plan_sections(split_sections(usable, text, kind, title))
    report("Finding sections", 8)

    issues: list[dict] = []
    drafted: list[tuple[Section, D.SectionDraft]] = []
    unavailable_run = 0
    unavailable_total = 0
    for i, sec in enumerate(sections):
        report(f"Drafting objectives ({i + 1}/{len(sections)}): {sec.title}", 8 + 72 * i / len(sections))
        got, err, unavailable = None, "", False
        for _ in range(SECTION_ATTEMPTS):
            try:
                got = structured_call(llm, D.SectionDraft, instructions=D.objective_instructions(outline, sec.cap),
                                      text=D.section_prompt(sec.unit, sec.title, sec.passages),
                                      purpose=D.PURPOSE_OBJECTIVES, retries=SECTION_ATTEMPTS)
                break
            except LLMUnavailable as e:
                err, unavailable = str(e), True
            except LLMError as e:       # includes LLMBadOutput (already retried inside structured_call)
                err, unavailable = str(e), False
                break
        if got is None:
            unavailable_run = unavailable_run + 1 if unavailable else 0
            unavailable_total += 1 if unavailable else 0
            issues.append({"kind": "section_failed", "section": sec.title, "detail": err})
            if unavailable_run >= ABORT_AFTER_UNAVAILABLE:
                raise ImportFailed(f"the model was unreachable for {unavailable_run} sections in a row: {err}",
                                   course_id)
            continue
        unavailable_run = 0
        _retitle(sec, got)
        drafted.append((sec, got))

    checked = None
    if syllabus_text is not None:
        lines = SY.parse_syllabus(syllabus_text)
        if not lines:
            raise ImportFailed("the syllabus has no lines to check", course_id)
        if len(lines) > SY.MAX_LINES:
            issues.append({"kind": "syllabus_truncated", "section": "syllabus",
                           "detail": f"{len(lines)} lines; only the first {SY.MAX_LINES} were checked"})
            lines = lines[:SY.MAX_LINES]
        checked = SY.check_syllabus(llm, course_id, passages, lines, report,
                                    SY.heading_paths(passages, text) if kind == "markdown" else None)
        objectives, lessons_of = checked.objectives, {}
        issues += checked.issues
    else:
        objectives, lessons_of = _assemble(course_id, drafted, mode, issues,
                                           outline or usable is passages)
        if not outline:
            _attach_orphans(objectives, lessons_of, drafted, issues)
    if not objectives and unavailable_total:
        raise ImportFailed(f"the language model could not be reached ({unavailable_total} section(s) failed): "
                           "check that it is running, then import again", course_id)
    if not objectives:
        raise ImportFailed("no objectives could be drafted from this text"
                           + (f" ({len(issues)} section problems)" if issues else ""), course_id)

    report("Linking prerequisites", 82)
    order = [o.id for o in objectives if o.coverage == "covered"]
    edges, prereq_source = _prereqs(llm, objectives, order, issues)
    kept, dropped, cycles = break_cycles(edges, order)
    for a, b in dropped:
        issues.append({"kind": "cycle_edge_dropped", "section": "", "detail": f"{a} -> {b}"})
    prereqs: dict[str, list[str]] = {}
    for a, b in kept:
        prereqs.setdefault(b, []).append(a)
    objectives = [o.model_copy(update={"prereqs": prereqs.get(o.id, [])}) for o in objectives]

    report("Checking coverage", 92)
    if checked is not None:
        cmap = SY.course_map(course_id, title, objectives, kept)
    else:
        cmap = _course_map(course_id, drafted, lessons_of, objectives, kept)
    n_cov = sum(o.coverage == "covered" for o in objectives)

    report("Saving the course", 96)
    store.put_passages(passages)
    store.put_doc(Doc(id=doc_id, course_id=course_id, name=doc_name, kind=kind),     # type: ignore[arg-type]
                  text=normalize_newlines(text))
    store.save_map(cmap)
    meta = {
        "status": "done", "source": "outline" if outline else "materials", "doc_id": doc_id,
        "passages": len(passages), "sections": len(sections), "sections_failed":
            sum(1 for x in issues if x["kind"] == "section_failed"),
        "quotable_passages": len(usable) if not outline else len(passages),
        "passages_without_topic": _without_topic(objectives, usable) if not outline and checked is None else None,
        "objectives": len(objectives), "covered": n_cov, "not_covered": len(objectives) - n_cov,
        "prereq_source": prereq_source, "cycles_repaired": cycles,
        "edges_dropped": [list(e) for e in dropped], "issues": issues}
    if checked is not None:
        meta["source"] = "syllabus"
        meta["syllabus"] = SY.syllabus_report(objectives, checked.verdicts)
    store.set_meta(report_key(course_id), meta)
    subject = infer_subject(llm, title=title, text=text, explicit=subject, code_language=code_language)
    store.put_course(Course(id=course_id, title=title, mode=mode, mode_reason=mode_reason,  # type: ignore[arg-type]
                           code_language=code_language, subject=subject))  # type: ignore[arg-type]
    return course_id


def _assemble(course_id: str, drafted, mode: str, issues: list[dict], outline_mode: bool = False):
    """Apply the coverage rule and number objectives. Returns (objectives, lesson key per objective id)."""
    objectives: list[Objective] = []
    lessons_of: dict[str, int] = {}
    n = 0
    for sec, sd in drafted:
        in_section = set(sec.passage_ids)
        by_id = {p.id: p for p in sec.passages}
        seen_stmt: set[str] = set()
        kept_here = 0
        for d in sd.objectives:
            key = normalize(d.statement)
            if key in seen_stmt:
                issues.append({"kind": "duplicate_objective", "section": sec.title, "detail": d.statement})
                continue
            seen_stmt.add(key)
            if not outline_mode and is_meta_objective(d.statement):
                issues.append({"kind": "meta_objective", "section": sec.title, "detail": d.statement})
                continue
            if kept_here >= sec.cap:
                issues.append({"kind": "over_topic_budget", "section": sec.title, "detail": d.statement})
                continue
            good = list(dict.fromkeys(p for p in d.passage_ids if p in in_section))
            bad = [p for p in d.passage_ids if p not in in_section]
            if good and not outline_mode and sum(len(by_id[p].text.split()) for p in good) < MIN_SUPPORT_WORDS:
                issues.append({"kind": "title_only_objective", "section": sec.title, "detail": d.statement})
                continue
            twin = _near_twin(objectives, d.statement)
            if twin is not None and not outline_mode:
                issues.append({"kind": "duplicate_objective", "section": sec.title, "detail": d.statement})
                continue
            kept_here += 1
            kind = d.kind
            if d.verb == "write-code":
                kind = "code"
            elif d.verb == "trace" and kind == "reading" and looks_like_code(
                    d.statement, " ".join(p.text for p in sec.passages if p.id in good)):
                kind = "code"       # tracing a program is a code objective; tracing a historical process is not
            elif d.verb in COMPUTED_VERBS and kind == "reading":
                kind = "math"
            coverage = "covered"
            if not good and mode != "open" and not outline_mode:
                good = _grounded_in_section(d.statement, sec)     # the ids were wrong but the topic is plainly here
                if good:
                    issues.append({"kind": "ids_repaired", "section": sec.title, "detail": d.statement})
            if not good:
                if mode == "open":
                    # open mode: the outline entry itself is what the objective rests on; say so
                    good = list(sec.passage_ids)
                    issues.append({"kind": "open_fallback_citation", "section": sec.title, "detail": d.statement})
                else:
                    coverage = "not_covered"
                    issues.append({"kind": "not_covered", "section": sec.title, "detail":
                                   d.statement + (f" (model cited ids not in this section: {bad})" if bad else
                                                  " (no supporting passage)")})
            elif bad:
                issues.append({"kind": "invalid_passage_ids_dropped", "section": sec.title,
                               "detail": f"{d.statement}: {bad}"})
            n += 1
            oid = f"{course_id}:o{n:02d}"
            objectives.append(Objective(id=oid, unit="-", lesson="-", verb=d.verb, statement=d.statement,
                                        kind=kind, passage_ids=good if coverage == "covered" else [],
                                        coverage=coverage))   # type: ignore[arg-type]
            lessons_of[oid] = sec.index
    return objectives, lessons_of


def _prereqs(llm, objectives: list[Objective], order: list[str], issues: list[dict]):
    """One model call over the statements; deterministic in-order chain when it fails or says nothing."""
    covered = [o for o in objectives if o.coverage == "covered"]
    if len(covered) < 2:
        return [], "none"
    known = {o.id for o in covered}
    try:
        got = structured_call(llm, D.PrereqDraft, instructions=D.PREREQ_INSTRUCTIONS,
                              text=D.prereq_prompt([(o.id, o.statement) for o in covered]),
                              purpose=D.PURPOSE_PREREQS, retries=SECTION_ATTEMPTS)
        edges = clean_edges([(e.prereq.strip(), e.objective.strip()) for e in got.edges], known)
        if edges:
            return edges, "model"
        why = "the model returned no usable edges"
    except LLMError as e:
        why = str(e)
    issues.append({"kind": "prereq_fallback", "section": "", "detail": why})
    return fallback_edges(order), "fallback"


def _course_map(course_id: str, drafted, lessons_of: dict[str, int], objectives: list[Objective],
                edges: list[tuple[str, str]]) -> CourseMap:
    secs = {sec.index: sec for sec, _ in drafted}
    units: list[Unit] = []
    unit_ids: dict[str, str] = {}
    lesson_nodes: dict[int, Lesson] = {}
    by_lesson: dict[int, list[Objective]] = {}
    for o in objectives:
        by_lesson.setdefault(lessons_of[o.id], []).append(o)
    for idx in sorted(by_lesson):
        sec = secs[idx]
        if sec.unit not in unit_ids:
            uid = f"{course_id}:u{len(units) + 1:02d}"
            unit_ids[sec.unit] = uid
            units.append(Unit(id=uid, title=sec.unit, lessons=[]))
        unit = next(u for u in units if u.id == unit_ids[sec.unit])
        lid = f"{unit.id}:l{len(unit.lessons) + 1:02d}"
        objs = [o.model_copy(update={"unit": unit.id, "lesson": lid}) for o in by_lesson[idx]]
        unit.lessons.append(Lesson(id=lid, title=sec.title, objectives=objs))
        lesson_nodes[idx] = unit.lessons[-1]
    return CourseMap(course_id=course_id, units=units,
                     edges=[Edge(prereq=a, objective=b) for a, b in edges])


# ---------------------------------------------------------------- topic quality

MIN_SUPPORT_WORDS = 8       # a topic needs at least this much quoted text behind it, not just a title line
_META = re.compile(
    r"\b(?:is|are|was|were)\s+(?:titled|entitled|headed|labell?ed)\b|\b(?:titled|entitled)\s+['\"“‘]|"
    r"\bthe\s+(?:section|chapter|poem|part|passage|text)\s+(?:is\s+)?(?:titled|called|named)\b|"
    r"\bpresents?\s+(?:poems?|chapters?|sections?)\s+(?:[IVXLC\d]+\b|from)|"
    r"\b(?:URL|web ?address|contact information|copyright|table of contents|page numbers?)\b", re.IGNORECASE)
_TWIN_WORDS = re.compile(r"[a-z0-9]+")
TWIN_JACCARD = 0.8


def is_meta_objective(statement: str) -> bool:
    """An objective about a title or heading ("Poem VIII is titled TRIUMPHANT") is not something to study."""
    return bool(_META.search(statement))


def _tokens(statement: str) -> frozenset[str]:
    return frozenset(w for w in _TWIN_WORDS.findall(normalize(statement)) if len(w) > 2 or w.isdigit())


def _near_twin(objectives: list[Objective], statement: str) -> Objective | None:
    mine = _tokens(statement)
    if len(mine) < 5:
        return None
    for o in objectives:
        theirs = _tokens(o.statement)
        if len(theirs) >= 5 and len(mine & theirs) / len(mine | theirs) >= TWIN_JACCARD:
            return o
    return None


def _retitle(sec: Section, got: D.SectionDraft) -> None:
    """A section that was a '(part N)' chunk, or several small headings merged, is named by its content."""
    if not sec.retitle:
        return
    name = " ".join((got.title or "").split()).strip(" .:-")
    if len(name) < 3 or re.fullmatch(r"(?i)(?:part|section|chapter)?\s*\d+", name):
        words = " ".join(got.objectives[0].statement.split()[:6]) if got.objectives else ""
        name = words.rstrip(".,;:") or "More"
    base = sec.base
    if base and "; " not in base and not name.lower().startswith(base.lower()[:12]):
        sec.title = f"{base}: {name}"
    else:
        sec.title = name


def _attach_orphans(objectives: list[Objective], lessons_of: dict[str, int], drafted, issues: list[dict]) -> None:
    """Every substantive passage of a drafted section ends up under a topic of that section: one the model
    left out (or cut by the topic budget) joins the nearest earlier topic, or the first one."""
    for sec, _ in drafted:
        mine = [i for i, o in enumerate(objectives) if lessons_of.get(o.id) == sec.index and o.coverage == "covered"]
        if not mine:
            continue
        order = {pid: k for k, pid in enumerate(sec.passage_ids)}
        cited = {pid for i in mine for pid in objectives[i].passage_ids}
        pos = {i: min((order[p] for p in objectives[i].passage_ids if p in order), default=0) for i in mine}
        for pid in sec.passage_ids:
            if pid in cited:
                continue
            k = order[pid]
            before = [i for i in mine if pos[i] <= k]
            target = max(before, key=lambda i: pos[i]) if before else min(mine, key=lambda i: pos[i])
            o = objectives[target]
            objectives[target] = o.model_copy(update={"passage_ids": list(o.passage_ids) + [pid]})
            cited.add(pid)


def _without_topic(objectives: list[Objective], passages) -> int:
    cited = {pid for o in objectives if o.coverage == "covered" for pid in o.passage_ids}
    return sum(1 for p in passages if p.id not in cited)


def _grounded_in_section(statement: str, sec: Section) -> list[str]:
    """The model named passages outside the section it was shown. When most of the statement's content words
    appear in this section's own text, the topic is this section's: cite the passages that carry those words."""
    lead = (statement.split() or [""])[0].lower().strip(".,:;")
    words = {w for w in _tokens(statement) if len(w) > 3 and not w.isdigit() and w != lead}
    if len(words) < 3:
        return []
    have = set()
    for p in sec.passages:
        have |= _tokens(p.text)
    if len(words & have) / len(words) < 0.7:
        return []
    scored = [(len(words & _tokens(p.text)), i, p.id) for i, p in enumerate(sec.passages)]
    scored = [x for x in scored if x[0] >= 2]
    return [pid for _, _, pid in sorted(scored, key=lambda x: (-x[0], x[1]))[:6]]
