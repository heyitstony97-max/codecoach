"""The ONE facade that the tools, the /codecoach command and the REST API all call. Hermes-free.

Every method returns a pydantic response model from codecoach.core.models (or raises ServiceError with
an HTTP-style status and a short machine code). Items leave only through core.public_view(), and
citations / the correct answer only through reveal() inside answer(). Nothing here imports Hermes:
the LLM arrives as an LLMPort, storage as a Store, settings as ServiceConfig.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from codecoach import adapt
from codecoach import pdf
from codecoach import check as _check  # noqa: F401  (registers the checkers)
from codecoach import gen as _gen  # noqa: F401  (registers the generators)
from codecoach.adapt.mastery import iso, parse_iso
from codecoach.adapt.selector import lessons_key
from codecoach.check import code_runner
from codecoach.check import math_sympy
from codecoach.check.fair import Corpus, fair_cloze
from codecoach.core.llm import LLMPort
from codecoach.core.models import (SourceDoc, SourcePassage, SourceView, EVENT_BANK_UPDATED, EVENT_JOB_PROGRESS, GATE_CODES, GATE_LABELS, NOT_COVERED_LABEL, AnswerResponse, ClozePayload, CodePayload,
                                CourseMap, CourseSummary, CoursesResponse, DayActivity, DropsResponse,
                                GateCount, HintResponse, ImportRequest, Item, Job, LessonView, MapResponse,
                                MapSummary, MathPayload, NextResponse, NextReview, ObjectiveView,
                                PassageResponse, PlotResponse, PlotSeries, ProgressResponse, ReportResponse,
                                RunResponse, TimelineEvent, TimelineResponse, UnitNode, LessonNode, utcnow,
                                MaterialPassage, MaterialSection, MaterialsResponse, MaterialTag, ReleaseResponse, MistakesResponse, StudyGuideExport)
from codecoach.audio import AudioCache, Clip, TTSUnavailable
from codecoach.core.public import public_view, reveal, source_label
from codecoach.core.registry import NotRegistered, check as registry_check
from codecoach.core.sources import reconstruct_doc
from codecoach.core.store import Store
from codecoach import study
from codecoach.gen import build_bank, make_lesson
from codecoach.gen.lesson import framing_key
from codecoach.ground.facts import date_sort_key
from codecoach.importer import ImportFailed, import_materials, import_outline, import_with_syllabus
from codecoach.importer.build import OUTLINE_MODE_REASON

MAX_IMPORT_CHARS = 2_000_000
MAX_RESPONSE_JSON = 100_000
MAX_REASON_CHARS = 2_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 200 * 1024 * 1024        # PDFs are mostly images; the text limit still applies after reading


def doc_note_key(doc_id: str) -> str:
    """Meta key for a document's import note (e.g. PDF pages with no text layer)."""
    return f"codecoach:doc_note:{doc_id}"
BANK_COOLDOWN_S = 600
PLOT_N = 120
PLOT_RANGE = (-5.0, 5.0)
WITHHOLD_MINUTES = 15
RELEASE_MESSAGE = "Your open question went back to the queue ungraded."
OUTLINE_LABEL = "Course outline: not source material"
TTS_UNAVAILABLE = "Hermes could not read this aloud."
NEXT_REASONS = {"new", "review", "interleave", "placement", "none"}
READABLE_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".text", ".pdf"}

Emit = Callable[[str, dict], None]


class ServiceError(Exception):
    """status: 404 unknown id, 409 not allowed now, 413 too large, 422 bad input, 5xx server side."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def as_dict(self) -> dict:
        return {"detail": self.message, "code": self.code}


@dataclass
class ServiceConfig:
    default_mode: str = "strict"
    allow_unsandboxed_code: bool = False
    bank_per_objective: int = 3
    llm_timeout_s: int = 600

    @classmethod
    def from_mapping(cls, get: Callable[[str, Any], Any]) -> "ServiceConfig":
        """`get(key, default)` is ctx.get_config or a dict's .get; bad values fall back to the defaults."""
        mode = get("default_mode", "strict")
        per = get("bank_per_objective", 3)
        try:
            per = int(per)
        except (TypeError, ValueError):
            per = 3
        try:
            tmo = int(get("llm_timeout_s", 600))
        except (TypeError, ValueError):
            tmo = 600
        return cls(default_mode=mode if mode in ("strict", "open") else "strict",
                   allow_unsandboxed_code=get("allow_unsandboxed_code", False) is True,
                   bank_per_objective=min(max(per, 1), 10),
                   llm_timeout_s=min(max(tmo, 10), 7200))


def _fmt_number(x: float) -> str:
    return f"{x:g}"


def correct_answer_text(item: Item, store: Store | None = None) -> Any:
    """The correct answer in display form. Called only after the learner has answered."""
    p = item.payload
    f = item.format
    if f == "mc":
        return p.options[p.correct_index]
    if f == "cloze":
        return p.answer
    if f == "order":
        return [p.events[i].label for i in p.correct_order]
    if f == "match":
        return [f"{m.left} = {m.right}" for m in p.pairs]
    if f == "short":
        return ", ".join(p.required_terms)
    if f == "code":
        return p.reference_solution
    if f == "math":
        return p.given_answer or None
    if f == "numeric":
        return f"{_fmt_number(p.answer_value)} {p.unit}".strip()
    return None


_DOUBLE_STOP = re.compile(r"(?<!\.)([.!?])\s*\.(?!\.)|(?<=[:;,])\s*\.(?!\.)")


def _is_verse(orig: str) -> bool:
    """Short lines are verse and keep their breaks. Prose hard-wrapped at ~70 columns (a text file, a PDF)
    has long, even lines; showing those raw would leave a ragged narrow column."""
    lens = sorted(len(l.strip()) for l in orig.split("\n") if l.strip())
    return len(lens) >= 2 and lens[len(lens) // 2] <= 55


def tidy_feedback(text: str | None) -> str:
    """One clean sentence-ender: no "..", ". .", "?." or doubled spaces in what the learner reads."""
    t = _DOUBLE_STOP.sub(lambda m: m.group(1) or "", (text or "").strip())
    return re.sub(r"[ \t]{2,}", " ", t)


def correct_feedback(detail: str | None) -> str:
    """Checkers already say "Correct" (sometimes with a note); never show "Correct. Correct"."""
    d = tidy_feedback(detail)
    if d.lower().startswith("correct"):
        return d if d[:8].rstrip().endswith(".") or len(d) > 7 else d + "."
    return ("Correct. " + d).strip()


class CodeCoachService:
    def __init__(self, store: Store, llm: LLMPort | None, config: ServiceConfig | None = None, *,
                 now_fn: Callable[[], datetime] = utcnow, emit: Emit | None = None,
                 path_reader: Callable[[str], str] | None = None, background: bool = True,
                 auto_bank: bool = True, audio: AudioCache | None = None,
                 exports_dir: Path | None = None):
        self._corpora: dict[str, tuple] = {}     # course id -> (signature, fair.Corpus); see _corpus
        import codecoach.check  # noqa: F401  (registers the checkers; idempotent)
        codecoach.check.register_all()
        self.audio = audio                       # read-aloud; None where Hermes TTS is not wired (the tools child)
        self.exports_dir = Path(exports_dir) if exports_dir else None   # study-guide files land here
        self.store = store
        self.llm = llm
        self.cfg = config or ServiceConfig()
        self.now_fn = now_fn
        self.emit = emit
        self.path_reader = path_reader or read_local_path
        self.background = background
        self.auto_bank = auto_bank
        self._lock = threading.Lock()
        self._busy: set[str] = set()
        self._bank_running: set[str] = set()
        self._bank_cancel: set[str] = set()      # a delete asked this course's question build to stop
        self._bank_last: dict[str, datetime] = {}
        self._threads: list[threading.Thread] = []
        try:
            self.store.reap_orphan_jobs()          # a job whose process was killed must not stay "running" forever
        except Exception:
            pass

    # ------------------------------------------------------------------ plumbing

    def now(self) -> datetime:
        return self.now_fn()

    def _fire(self, event: str, payload: dict) -> None:
        if self.emit is None:
            return
        try:
            self.emit(event, payload)
        except Exception:
            pass

    def _course(self, course_id: str):
        c = self.store.get_course(course_id) if isinstance(course_id, str) and course_id else None
        if c is None:
            raise ServiceError(404, "unknown_course", f"No course with id {course_id!r}.")
        return c

    def _item(self, item_id: str) -> Item:
        it = self.store.get_item(item_id) if isinstance(item_id, str) and item_id else None
        if it is None:
            raise ServiceError(404, "unknown_item", f"No question with id {item_id!r}.")
        return it

    def _active_item(self, item_id: str) -> Item:
        it = self._item(item_id)
        if it.status != "active":
            raise ServiceError(409, "item_not_active", "That question has been retired.")
        return it

    def _exclusive(self, item_id: str):
        svc = self

        class _Guard:
            def __enter__(self_inner):
                with svc._lock:
                    if item_id in svc._busy:
                        raise ServiceError(409, "busy", "That question is already being checked. Wait a moment.")
                    svc._busy.add(item_id)

            def __exit__(self_inner, *exc):
                with svc._lock:
                    svc._busy.discard(item_id)
                return False

        return _Guard()

    def _spawn(self, name: str, fn: Callable[[], None]) -> None:
        if not self.background:
            fn()
            return
        t = threading.Thread(target=fn, name=name, daemon=True)
        self._threads.append(t)
        self._threads = [x for x in self._threads if x.is_alive()] + [t] if len(self._threads) > 50 else self._threads
        t.start()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Join background jobs (tests, and tools that want to wait a little for an import)."""
        end = timeout
        for t in list(self._threads):
            t.join(max(0.0, end))
        return not any(t.is_alive() for t in self._threads)

    # ------------------------------------------------------------------ courses / import / jobs

    def _summary(self, course, mm: dict | None = None) -> CourseSummary:
        objs = self.store.list_objectives(course.id)
        covered = [o for o in objs if o.coverage == "covered"]
        mm = adapt.mastery_map(self.store, course.id) if mm is None else mm
        mean = sum(m.p for m in mm.values()) / len(mm) if mm else 0.0
        return CourseSummary(
            id=course.id, title=course.title, mode=course.mode, mode_reason=course.mode_reason, subject=course.subject,
            created_at=course.created_at, objectives=len(objs), covered=len(covered),
            not_covered=len(objs) - len(covered),
            items_active=self.store.count_items(course_id=course.id, status="active"),
            mastery=round(min(max(mean, 0.0), 1.0), 4),
            due_reviews=len(self.store.list_due(course_id=course.id, now=self.now())))

    def courses(self) -> CoursesResponse:
        return CoursesResponse(courses=[self._summary(c) for c in self.store.list_courses()])

    def set_subject(self, course_id: str, subject: str | None) -> CourseSummary:
        """Change a course's subject. Trimmed; empty clears it (Uncategorized); over 40 characters is a 422."""
        c = self._course(course_id)
        if subject is not None and not isinstance(subject, str):
            raise ServiceError(422, "bad_request", "The subject must be text.")
        if subject is not None and len(subject.strip()) > 40:
            raise ServiceError(422, "subject_too_long", "A subject can be at most 40 characters.")
        self.store.set_course_subject(c.id, subject)
        return self._summary(self._course(course_id))

    def delete_course(self, course_id: str, *, confirm: bool = False) -> dict:
        """Without confirm: what WOULD be deleted. With confirm: delete it all (one transaction)."""
        c = self.store.get_course(course_id) if isinstance(course_id, str) and course_id else None
        if c is None:
            raise ServiceError(404, "unknown_course", f"No course with id {course_id!r}.")
        self.store.reap_orphan_jobs()
        busy = [j for j in self.store.list_jobs() if j.course_id == course_id and j.status in ("queued", "running")]
        if busy:
            if confirm and all(j.kind == "bank" for j in busy):
                # a question build can be stopped between drafts; do not make the learner wait out the whole build
                with self._lock:
                    self._bank_cancel.add(course_id)
                raise ServiceError(409, "course_stopping", "Stopping the question build for that course. Run delete again in a minute.")
            raise ServiceError(409, "course_busy", "That course is still being imported or built. Wait for it to finish.")
        counts = self.store.course_footprint(course_id)
        if not confirm:
            return {"deleted": False, "course_id": course_id, "title": c.title, "counts": counts}
        done = self.store.delete_course(course_id) or counts
        return {"deleted": True, "course_id": course_id, "title": c.title, "counts": done}

    def job(self, job_id: str) -> Job:
        try:
            self.store.reap_orphan_jobs()
        except Exception:
            pass
        j = self.store.get_job(job_id) if isinstance(job_id, str) and job_id else None
        if j is None:
            raise ServiceError(404, "unknown_job", f"No job with id {job_id!r}.")
        return j

    def start_import(self, *, title: str, text: str | None = None, path: str | None = None,
                     mode: str | None = None, outline: bool = False, syllabus_text: str | None = None,
                     syllabus_path: str | None = None, subject: str | None = None) -> str:
        """Validate, read the source, create the job and return its id. The work runs on a thread.
        With a syllabus (text or path) the materials are checked against it line by line."""
        mode = mode or self.cfg.default_mode
        if syllabus_text is not None and not syllabus_text.strip():
            syllabus_text = None
        if syllabus_path is not None and not syllabus_path.strip():
            syllabus_path = None
        try:
            req = ImportRequest(title=(title or "").strip(), text=text, path=path, mode=mode, outline=outline,
                                syllabus_text=syllabus_text, syllabus_path=syllabus_path,
                                subject=(subject or None))
        except Exception as e:
            raise ServiceError(422, "bad_request", _first_line(e)) from None
        syllabus: str | None = req.syllabus_text
        if req.syllabus_path is not None:
            syllabus = self.path_reader(req.syllabus_path)
        if syllabus is not None and (len(syllabus) > MAX_IMPORT_CHARS or not syllabus.strip()):
            raise ServiceError(422, "bad_request", "The syllabus is empty or too long.")
        doc_note: str | None = None
        if req.path is not None:
            body = self.path_reader(req.path)
            doc_name = os.path.basename(req.path.rstrip("/")) or req.title
            if doc_name.lower().endswith(".pdf"):
                empty = pdf.empty_pages_of(body)
                doc_note = pdf.skipped_pages_note(empty, body.count("\f") + 1 if empty else None)
                fnote = pdf.formula_gap_note(pdf.count_formula_gaps(body))
                if fnote:
                    doc_note = f"{doc_note} {fnote}" if doc_note else fnote
        else:
            body, doc_name = req.text or "", req.title
        if len(body) > MAX_IMPORT_CHARS:
            raise ServiceError(413, "too_large", f"That text is too long (limit {MAX_IMPORT_CHARS:,} characters).")
        if not body.strip():
            raise ServiceError(422, "empty", "There is no text to import.")
        job = self.store.create_job("import")
        self._spawn(f"codecoach-import-{job.id}",
                    lambda: self._run_import(job.id, req.title, body, doc_name, req.mode, outline,
                                           syllabus, req.subject, doc_note))
        return job.id

    def _progress_cb(self, job_id: str):
        def cb(stage: str, pct: float) -> None:
            pct = min(max(float(pct), 0.0), 100.0)
            self._fire(EVENT_JOB_PROGRESS, {"job_id": job_id, "pct": pct, "stage": str(stage)[:200]})
        return cb

    def _run_import(self, job_id: str, title: str, text: str, doc_name: str, mode: str, outline: bool,
                    syllabus: str | None = None, subject: str | None = None, doc_note: str | None = None) -> None:
        try:
            self.store.update_job(job_id, status="running", pct=1.0, stage="reading")
            cb = self._progress_cb(job_id)
            if syllabus is not None:
                cid = import_with_syllabus(self.store, self.llm, title=title, materials_text=text,
                                           doc_name=doc_name, syllabus_text=syllabus, mode=mode,
                                           progress=cb, job_id=job_id, subject=subject)
            elif outline:
                cid = import_outline(self.store, self.llm, title=title, text=text, doc_name=doc_name,
                                     progress=cb, job_id=job_id, subject=subject)
            else:
                cid = import_materials(self.store, self.llm, title=title, text=text, doc_name=doc_name,
                                       mode=mode, progress=cb, job_id=job_id, subject=subject)
        except ImportFailed as e:
            self.store.update_job(job_id, status="error", error=str(e)[:500])
            self._fire(EVENT_JOB_PROGRESS, {"job_id": job_id, "pct": 100.0, "stage": "failed"})
            return
        except Exception as e:  # never let a thread die silently
            self.store.update_job(job_id, status="error", error=f"Import failed: {_first_line(e)}"[:500])
            self._fire(EVENT_JOB_PROGRESS, {"job_id": job_id, "pct": 100.0, "stage": "failed"})
            return
        if doc_note:
            for d in self.store.list_docs(cid):
                if d.name == doc_name:
                    self.store.set_meta(doc_note_key(d.id), doc_note)
        self.store.update_job(job_id, status="done", pct=100.0, stage="done", course_id=cid)
        self._fire(EVENT_JOB_PROGRESS, {"job_id": job_id, "pct": 100.0, "stage": "done"})
        self.start_bank(cid)

    # ------------------------------------------------------------------ bank

    def start_bank(self, course_id: str) -> str | None:
        """Build the question bank on a worker. One build per course at a time. Returns the job id."""
        with self._lock:
            if course_id in self._bank_running or self.llm is None:
                return None
            self._bank_running.add(course_id)
        job = self.store.create_job("bank", course_id=course_id)

        def run() -> None:
            try:
                def cb(pct: float, stage: str) -> None:
                    pct = min(max(float(pct), 0.0), 99.0)
                    self.store.update_job(job.id, status="running", pct=pct, stage=str(stage)[:200])
                    self._fire(EVENT_JOB_PROGRESS, {"job_id": job.id, "pct": pct, "stage": str(stage)[:200]})
                rep = build_bank(self.store, self.llm, course_id, self.cfg.bank_per_objective, cb,
                                 now_fn=self.now_fn, should_stop=lambda: course_id in self._bank_cancel)
                msg = "done" if not rep.llm_unavailable else "stopped: the model was unreachable"
                if rep.stopped:
                    msg = "stopped: the course is being deleted"
                if not rep.llm_unavailable and not rep.stopped:
                    try:
                        self.warm_lessons(course_id)
                    except Exception:
                        pass
                self.store.update_job(job.id, status="done", pct=100.0, stage=msg)
                self._fire(EVENT_JOB_PROGRESS, {"job_id": job.id, "pct": 100.0, "stage": msg})
            except Exception as e:
                self.store.update_job(job.id, status="error", error=f"Question generation failed: {_first_line(e)}"[:500])
            finally:
                with self._lock:
                    self._bank_running.discard(course_id)
                    self._bank_cancel.discard(course_id)
                    self._bank_last[course_id] = self.now()
                self._fire(EVENT_BANK_UPDATED, {"course_id": course_id})

        self._spawn(f"codecoach-bank-{course_id}", run)
        return job.id

    def _maybe_bank(self, course_id: str) -> None:
        if not self.auto_bank or self.llm is None:
            return
        with self._lock:
            last = self._bank_last.get(course_id)
            if course_id in self._bank_running or (last and (self.now() - last).total_seconds() < BANK_COOLDOWN_S):
                return
        need = False
        for o in self.store.list_objectives(course_id):
            if o.coverage == "covered" and self.store.count_items(objective_id=o.id, status="active") < self.cfg.bank_per_objective:
                need = True
                break
        if need:
            self.start_bank(course_id)

    # ------------------------------------------------------------------ map / progress / drops

    def map(self, course_id: str) -> MapResponse:
        course = self._course(course_id)
        cmap = self.store.get_map(course_id)
        if cmap is None:
            raise ServiceError(404, "no_map", "That course has no map yet.")
        mm = adapt.mastery_map(self.store, course_id)
        now = self.now()
        due_by_obj: dict[str, int] = {}
        for rs in self.store.list_due(course_id=course_id, now=now):
            it = self.store.get_item(rs.item_id)
            if it:
                due_by_obj[it.objective_id] = due_by_obj.get(it.objective_id, 0) + 1
        active_by_obj: dict[str, int] = {}
        for it in self.store.list_items(course_id=course_id, status="active"):
            active_by_obj[it.objective_id] = active_by_obj.get(it.objective_id, 0) + 1
        units = []
        for u in cmap.units:
            lessons = []
            for l in u.lessons:
                views = []
                for o in l.objectives:
                    nc = o.coverage == "not_covered"
                    m = mm.get(o.id)
                    views.append(ObjectiveView(
                        **o.model_dump(), mastery=0.0 if nc or m is None else round(m.p, 4),
                        items_active=active_by_obj.get(o.id, 0), due_reviews=due_by_obj.get(o.id, 0),
                        status_label=NOT_COVERED_LABEL if nc else None))
                lessons.append(LessonNode(id=l.id, title=l.title, objectives=views))
            units.append(UnitNode(id=u.id, title=u.title, lessons=lessons))
        summ = self._summary(course, mm)
        return MapResponse(
            course=summ, units=units, edges=cmap.edges,
            summary=MapSummary(objectives=summ.objectives, covered=summ.covered, not_covered=summ.not_covered,
                               mastered=sum(1 for m in mm.values() if m.state == "known"),
                               mean_mastery=summ.mastery))

    def progress(self, course_id: str) -> ProgressResponse:
        self._course(course_id)
        s = self.store
        objs = s.list_objectives(course_id)
        mm = adapt.mastery_map(s, course_id)
        attempts = s.list_attempts(course_id=course_id)
        decided = [a for a in attempts if a.correct is not None]
        now = self.now()
        days = [(now - timedelta(days=n)).astimezone(timezone.utc).date() for n in range(6, -1, -1)]
        per_day = {d: [0, 0] for d in days}
        for a in attempts:
            d = a.at.astimezone(timezone.utc).date()
            if d in per_day:
                per_day[d][0] += 1
                per_day[d][1] += 1 if a.correct else 0
        by_gate = {c: 0 for c in GATE_CODES}
        by_gate.update(s.count_drops_by_gate(course_id))
        total = s.count_items(course_id=course_id)
        mean = sum(m.p for m in mm.values()) / len(mm) if mm else 0.0
        covered = sum(1 for o in objs if o.coverage == "covered")
        return ProgressResponse(
            course_id=course_id, objectives=len(objs), covered=covered, not_covered=len(objs) - covered,
            mastered=sum(1 for m in mm.values() if m.state == "known"), mean_mastery=round(mean, 4),
            items_generated=total, items_active=s.count_items(course_id=course_id, status="active"),
            items_dropped=s.count_items(course_id=course_id, status="dropped"),
            items_reported=s.count_items(course_id=course_id, status="reported"),
            attempts=len(attempts),
            accuracy=round(sum(1 for a in decided if a.correct) / len(decided), 4) if decided else None,
            due_reviews=len(s.list_due(course_id=course_id, now=now)), drops_by_gate=by_gate,
            reports=s.count_reports(course_id),
            recent_days=[DayActivity(date=d.isoformat(), attempts=v[0], correct=v[1]) for d, v in per_day.items()])

    def drops(self, course_id: str) -> DropsResponse:
        self._course(course_id)
        counts = self.store.count_drops_by_gate(course_id)
        by = [GateCount(gate_code=c, label=GATE_LABELS[c], count=int(counts.get(c, 0))) for c in GATE_CODES]
        return DropsResponse(course_id=course_id, total=sum(g.count for g in by), by_gate=by,
                             recent=self.store.list_drops(course_id, limit=20))

    # ------------------------------------------------------------------ next

    def _titles(self, cmap: CourseMap | None, objective_id: str) -> tuple[str, str]:
        if cmap:
            for u in cmap.units:
                for l in u.lessons:
                    if any(o.id == objective_id for o in l.objectives):
                        return u.title, l.title
        return "", ""

    def _lesson(self, course, objective_id: str) -> LessonView | None:
        obj = self.store.get_objective(objective_id)
        if obj is None:
            return None
        passages = [p for pid in obj.passage_ids if (p := self.store.get_passage(pid))]
        ut, lt = self._titles(self.store.get_map(course.id), objective_id)
        # Never make the learner wait on the model: serve the passage now with the cached framing line
        # (if any) and write the framing in the background for next time. Inline when background=False.
        cached = self.store.get_meta(framing_key(objective_id))
        if not isinstance(cached, dict) and self.llm is not None:
            self._start_framing(course, obj, passages)
            cached = self.store.get_meta(framing_key(objective_id))
        framing = cached.get("framing") if isinstance(cached, dict) else None
        return make_lesson(obj, passages, None, mode=course.mode, unit_title=ut, lesson_title=lt,
                           cached_framing=framing if isinstance(framing, str) and framing else None)

    def _start_framing(self, course, obj, passages) -> None:
        key = framing_key(obj.id)
        with self._lock:
            if key in self._busy:
                return
            self._busy.add(key)

        def run() -> None:
            try:
                lv = make_lesson(obj, passages, self.llm, mode=course.mode, store=self.store)
                self.store.set_meta(key, {"framing": lv.framing if lv is not None else None})
            except Exception:
                pass
            finally:
                with self._lock:
                    self._busy.discard(key)

        self._spawn(f"codecoach-framing-{obj.id}", run)

    def warm_lessons(self, course_id: str) -> int:
        """Write and cache the framing line for every covered objective that has none yet (blocking).
        Returns how many were written. Used after a bank build and by maintenance scripts."""
        course = self._course(course_id)
        if self.llm is None:
            return 0
        done = 0
        for obj in self.store.list_objectives(course_id):
            if obj.coverage != "covered" or isinstance(self.store.get_meta(framing_key(obj.id)), dict):
                continue
            passages = [p for pid in obj.passage_ids if (p := self.store.get_passage(pid))]
            try:
                lv = make_lesson(obj, passages, self.llm, mode=course.mode, store=self.store)
            except Exception:
                continue
            self.store.set_meta(framing_key(obj.id), {"framing": lv.framing if lv is not None else None})
            done += 1
        return done

    def _maybe_placement(self, course_id: str, now: datetime) -> None:
        from codecoach.adapt import placement
        if self.store.count_attempts(course_id=course_id) or placement.get_state(self.store, course_id) is not None:
            return
        ready = {it.objective_id for it in self.store.list_items(course_id=course_id, status="active")}
        if len(ready) >= 4:
            try:
                adapt.start_placement(self.store, course_id, now)
            except Exception:
                pass

    def next(self, course_id: str, objective_id: str | None = None, mistakes_upto: int | None = None) -> NextResponse:
        course = self._course(course_id)
        now = self.now()
        if mistakes_upto is not None:
            return self._next_mistake(course_id, mistakes_upto, now)
        if objective_id:
            return self._next_for_objective(course, objective_id, now)
        self._maybe_placement(course_id, now)
        self._maybe_bank(course_id)
        held = self._withheld(course_id, now)
        for _ in range(3):
            pick = adapt.next_item(self.store, course_id, now, exclude=held)
            reason = pick.reason if pick.reason in NEXT_REASONS else "new"
            if pick.kind == "item" and pick.item is not None:
                return NextResponse(kind="item", reason=reason, item=public_view(pick.item))
            if pick.kind == "lesson" and pick.objective_id:
                lesson = self._lesson(course, pick.objective_id)
                adapt.mark_lesson_seen(self.store, course_id, pick.objective_id, now)
                if lesson is not None:
                    return NextResponse(kind="lesson", reason=reason, lesson=lesson)
                continue
            break
        msg = "Nothing to practise right now."
        if self.store.count_items(course_id=course_id, status="active") == 0:
            msg = "Questions are still being prepared. Try again in a minute."
        return NextResponse(kind="done", reason="none", message=msg)

    def mistakes(self, course_id: str) -> MistakesResponse:
        """How many items the learner last got wrong (or only partly right), and the session cursor."""
        self._course(course_id)
        rows = study.mistakes(self.store, course_id)
        return MistakesResponse(course_id=course_id, count=len(rows), session=min(len(rows), study.MISTAKES_SESSION),
                                upto=study.newest_attempt_id(self.store, course_id))

    def _next_mistake(self, course_id: str, upto: int, now: datetime) -> NextResponse:
        held = self._withheld(course_id, now)
        for item, _aid in study.mistakes(self.store, course_id, upto):
            if item.id not in held:
                return NextResponse(kind="item", reason="review", item=public_view(item))
        return NextResponse(kind="done", reason="none", message="No mistakes left to review.")

    def _next_for_objective(self, course, objective_id: str, now: datetime) -> NextResponse:
        obj = self.store.get_objective(objective_id)
        if obj is None or self.store.course_of_objective(objective_id) != course.id:
            raise ServiceError(404, "unknown_objective", f"No topic with id {objective_id!r} in this course.")
        if obj.coverage == "not_covered":
            raise ServiceError(409, "not_covered", NOT_COVERED_LABEL)
        seen = self.store.get_meta(lessons_key(course.id), {}) or {}
        if objective_id not in seen and obj.passage_ids:
            lesson = self._lesson(course, objective_id)
            adapt.mark_lesson_seen(self.store, course.id, objective_id, now)
            if lesson is not None:
                return NextResponse(kind="lesson", reason="new", lesson=lesson)
        held = self._withheld(course.id, now)
        items = [i for i in self.store.list_items(objective_id=objective_id, status="active") if i.id not in held]
        if not items:
            return NextResponse(kind="done", reason="none", message="No questions are ready for this topic yet.")
        items.sort(key=lambda i: (self.store.count_attempts(item_id=i.id), i.id))
        return NextResponse(kind="item", reason="new", item=public_view(items[0]))

    # ------------------------------------------------------------------ leaving a question to read

    def _withheld_key(self, course_id: str) -> str:
        return f"codecoach:withheld:{course_id}"

    def _withheld(self, course_id: str, now: datetime) -> frozenset[str]:
        """Questions the learner left open to go and read. They are held back from /next until one other
        answer is recorded or WITHHOLD_MINUTES pass, whichever comes first; they are never graded meanwhile."""
        w = self.store.get_meta(self._withheld_key(course_id), None)
        if not isinstance(w, dict):
            return frozenset()
        try:
            live = (w.get("attempts") == self.store.count_attempts(course_id=course_id)
                    and now < parse_iso(w["until"]))
        except (KeyError, TypeError, ValueError):
            live = False
        return frozenset(w.get("ids") or []) if live else frozenset()

    def release(self, item_id: str) -> ReleaseResponse:
        """The learner opened Read while this question was open and unanswered. Nothing is graded: no attempt,
        no mastery or review change, and any hints they took are forgotten. The question stays in the bank and
        is not served again until another answer is recorded (or WITHHOLD_MINUTES pass), so reading the
        passage cannot hand them this answer."""
        item = self._item(item_id)
        if item.status != "active":
            return ReleaseResponse(item_id=item_id, released=False, message="")
        obj = self.store.get_objective(item.objective_id)
        course_id = self.store.course_of_objective(item.objective_id) if obj else None
        if course_id is None:
            raise ServiceError(404, "unknown_course", "That question's course is gone.")
        now = self.now()
        with self._exclusive(item_id):
            ids = set(self._withheld(course_id, now)) | {item_id}
            self.store.set_meta(self._withheld_key(course_id), {
                "ids": sorted(ids), "attempts": self.store.count_attempts(course_id=course_id),
                "until": iso(now + timedelta(minutes=WITHHOLD_MINUTES))})
            self.store.set_meta(self._hints_key(item_id), 0)
        return ReleaseResponse(item_id=item_id, released=True, message=RELEASE_MESSAGE)

    # ------------------------------------------------------------------ materials / read aloud

    def materials(self, course_id: str) -> MaterialsResponse:
        """The course's source text in document order, grouped under the heading each passage sits in."""
        course = self._course(course_id)
        outline = course.mode_reason == OUTLINE_MODE_REASON
        tags: dict[str, list[MaterialTag]] = {}
        for o in self.store.list_objectives(course_id):
            if o.coverage != "covered":
                continue
            for pid in dict.fromkeys(o.passage_ids):
                tags.setdefault(pid, []).append(MaterialTag(id=o.id, statement=o.statement))
        docs = {d.id: d.name for d in self.store.list_docs(course_id)}
        sections: list[MaterialSection] = []
        doc_texts: dict[str, str | None] = {}
        for p in self.store.list_passages(course_id=course_id):
            if p.doc_id not in doc_texts:
                doc_texts[p.doc_id] = self.store.get_doc_text(p.doc_id)
            key = p.id.rsplit(":", 1)[0]
            if not sections or sections[-1].id != key:
                title = p.location.rsplit(", para", 1)[0] if ", para" in p.location else docs.get(p.doc_id, p.doc_id)
                sections.append(MaterialSection(id=key, title=title, doc_name=docs.get(p.doc_id, p.doc_id), passages=[]))
            src = doc_texts[p.doc_id]
            shown = None
            if src is not None and p.start is not None and p.end is not None and 0 <= p.start < p.end <= len(src):
                orig = src[p.start:p.end].strip("\n")
                # Only worth sending when the original holds line breaks the flat passage text lost.
                if orig and "\n" in orig and orig != p.text and _is_verse(orig):
                    shown = orig
            sections[-1].passages.append(MaterialPassage(id=p.id, doc_id=p.doc_id, location=p.location, text=p.text,
                                                         display=shown, objectives=tags.get(p.id, [])))
        return MaterialsResponse(course_id=course.id, title=course.title, source="outline" if outline else "materials",
                                 label=OUTLINE_LABEL if outline else "", sections=sections)

    # ------------------------------------------------------------------ sources (the original text)

    def _doc_of_course(self, course_id: str, doc_id: str):
        self._course(course_id)
        doc = self.store.get_doc(doc_id) if isinstance(doc_id, str) and doc_id else None
        if doc is None or doc.course_id != course_id:
            raise ServiceError(404, "unknown_doc", f"No document with id {doc_id!r} in this course.")
        return doc

    def sources(self, course_id: str) -> list[SourceDoc]:
        """The documents of a course. `chars` is the length of the text the reader would show."""
        self._course(course_id)
        out: list[SourceDoc] = []
        for d in self.store.list_docs(course_id):
            ps = self.store.list_passages(doc_id=d.id)
            n = self.store.doc_text_length(d.id)
            has = n is not None and all(p.start is not None and p.end is not None for p in ps)
            if not has:
                n = len(reconstruct_doc(ps, d.kind)[0])
            note = self.store.get_meta(doc_note_key(d.id))
            out.append(SourceDoc(doc_id=d.id, name=d.name, kind=d.kind, chars=n or 0, passages=len(ps), has_text=has,
                                 note=note if isinstance(note, str) and note else None))
        return out

    def source(self, course_id: str, doc_id: str) -> SourceView:
        """One document: its text (stored, or rebuilt from the passages for older courses) and where each
        passage sits in it. The reader never has to special-case old courses."""
        doc = self._doc_of_course(course_id, doc_id)
        ps = self.store.list_passages(doc_id=doc.id)
        text = self.store.get_doc_text(doc.id)
        if text is not None and all(p.start is not None and p.end is not None for p in ps):
            spans = [(p, p.start, p.end) for p in ps]
            rebuilt = False
        else:
            text, by_id = reconstruct_doc(ps, doc.kind)
            spans = [(p, *by_id[p.id]) for p in ps]
            rebuilt = True
        return SourceView(doc_id=doc.id, name=doc.name, kind=doc.kind, text=text, reconstructed=rebuilt,
                          passages=[SourcePassage(id=p.id, location=p.location, start=a, end=b) for p, a, b in spans])

    def source_guide(self, course_id: str, doc_id: str) -> study.SourceGuide:
        """Contents, topics and gaps for one file, assembled from its text and the course map. No model."""
        v = self.source(course_id, doc_id)
        spans = {p.id: (p.start, p.end) for p in v.passages}
        locs = {p.id: p.location for p in v.passages}
        return study.source_guide(self.store, course_id, doc_id, v.text, spans, locs,
                                  adapt.mastery_map(self.store, course_id))

    def export_study_guide(self, course_id: str) -> StudyGuideExport:
        """Write <exports dir>/<course>-study-guide.md and return the same Markdown for a browser download."""
        course = self._course(course_id)
        if self.store.get_map(course_id) is None:
            raise ServiceError(404, "no_map", "That course has no map yet.")
        md = study.study_guide_md(self.store, course_id, outline_label=(course.mode == "open" or course.mode_reason == OUTLINE_MODE_REASON),
                                  answer_text=lambda it: correct_answer_text(it, self.store))
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{course_id}-study-guide.md").lstrip(".-") or "study-guide.md"
        path = None
        if self.exports_dir is not None:
            try:
                self.exports_dir.mkdir(parents=True, exist_ok=True)
                (self.exports_dir / name).write_text(md, encoding="utf-8")
                path = str(self.exports_dir / name)
            except OSError:
                raise ServiceError(500, "export_failed", "CodeCoach could not write the study guide file.") from None
        return StudyGuideExport(course_id=course_id, filename=name, path=path, markdown=md)

    def passage_audio(self, course_id: str, passage_id: str) -> Clip:
        """Speech for one passage, synthesized from its stored text verbatim by the learner's Hermes TTS."""
        self._course(course_id)
        p = self.store.get_passage(passage_id) if isinstance(passage_id, str) and passage_id else None
        doc = self.store.get_doc(p.doc_id) if p else None
        if p is None or doc is None or doc.course_id != course_id:
            raise ServiceError(404, "unknown_passage", f"No passage with id {passage_id!r} in this course.")
        if self.audio is None:
            raise ServiceError(503, "tts_unavailable", TTS_UNAVAILABLE)
        try:
            return self.audio.get(p.text)
        except TTSUnavailable as e:
            raise ServiceError(503, "tts_unavailable", f"{TTS_UNAVAILABLE} ({_first_line(e)})") from None
        except OSError:
            raise ServiceError(503, "tts_unavailable", TTS_UNAVAILABLE) from None

    # ------------------------------------------------------------------ ask your materials (codecoach/ask)

    @property
    def _ask(self):
        from codecoach.ask import AskEngine
        with self._lock:
            if getattr(self, "_ask_engine", None) is None:
                self._ask_engine = AskEngine(self)
            return self._ask_engine

    def ask_start(self, course_id: str, question: str, open_item_id: str | None = None) -> dict: return self._ask.start(course_id, question, open_item_id)  # noqa: E501,E704
    def ask_job(self, course_id: str, job_id: str) -> dict: return self._ask.job(course_id, job_id)  # noqa: E704
    def asks(self, course_id: str) -> dict: return self._ask.asks(course_id)  # noqa: E704
    def ask_now(self, course_id: str, question: str) -> dict: return self._ask.answer(course_id, question)  # noqa: E704

    # ------------------------------------------------------------------ Learn mode (codecoach/learn.py)

    @property
    def _learn(self):
        from codecoach.learn import LearnEngine
        with self._lock:
            if getattr(self, "_learn_engine", None) is None:
                self._learn_engine = LearnEngine(self)
            return self._learn_engine

    def learn_overview(self, course_id: str) -> dict: return self._learn.overview(course_id)  # noqa: E704
    def learn_topic(self, course_id: str, objective_id: str) -> dict: return self._learn.topic(course_id, objective_id)  # noqa: E501,E704
    def learn_check(self, course_id: str, objective_id: str, item_id: str, response: Any) -> dict: return self._learn.check(course_id, objective_id, item_id, response)  # noqa: E501,E704
    def learn_step(self, course_id: str, objective_id: str, index: int) -> dict: return self._learn.step(course_id, objective_id, index)  # noqa: E501,E704

    # ------------------------------------------------------------------ Project mode (codecoach/project.py)

    @property
    def _project(self):
        from codecoach.project import ProjectEngine
        with self._lock:
            if getattr(self, "_project_engine", None) is None:
                self._project_engine = ProjectEngine(self)
            return self._project_engine

    def project_list(self) -> dict: return self._project.list()  # noqa: E704
    def project_create(self, idea: Any, language: Any) -> dict: return {"project_id": self._project.create(idea, language), "status": "planning"}  # noqa: E501,E704
    def project_get(self, pid: str) -> dict: return self._project.get(pid)  # noqa: E704
    def project_step(self, pid: str, i: int) -> dict: return self._project.step(pid, i)  # noqa: E704
    def project_run(self, pid: str, i: int, code: Any) -> dict: return self._project.run(pid, i, code)  # noqa: E704
    def project_check(self, pid: str, i: int, code: Any) -> dict: return self._project.check(pid, i, code)  # noqa: E704
    def project_hint(self, pid: str, i: int) -> dict: return self._project.hint(pid, i)  # noqa: E704
    def project_solution(self, pid: str, i: int) -> dict: return self._project.solution(pid, i)  # noqa: E704
    def project_retry(self, pid: str, i: int) -> dict: return {"status": self._project.retry(pid, i)}  # noqa: E704
    def project_delete(self, pid: str) -> dict: self._project.delete(pid); return {"deleted": True}  # noqa: E702,E704

    # ------------------------------------------------------------------ answer / run / hint / report

    def _hints_key(self, item_id: str) -> str:
        return f"codecoach:hints:{item_id}"

    def hints_used(self, item_id: str) -> int:
        try:
            return min(3, max(0, int(self.store.get_meta(self._hints_key(item_id), 0) or 0)))
        except (TypeError, ValueError):
            return 0

    def _corpus(self, item: Item) -> Corpus | None:
        """The course's passages as word streams for fair cloze grading (codecoach/check/fair.py); cached per course and
        rebuilt when its passages change. None when the course has no passages."""
        cid = self.store.course_of_objective(item.objective_id)
        if not cid:
            return None
        ps = self.store.list_passages(course_id=cid)
        if not ps:
            return None
        sig = (len(ps), sum(len(p.text) for p in ps), ps[0].id, ps[-1].id)
        hit = self._corpora.get(cid)
        if hit is None or hit[0] != sig:
            hit = (sig, Corpus.build([p.text for p in ps]))
            self._corpora[cid] = hit
        return hit[1]

    def _grade(self, item: Item, response: Any):
        try:
            if item.format == "code" and self.cfg.allow_unsandboxed_code and isinstance(item.payload, CodePayload):
                return code_runner.grade(item.payload, response if isinstance(response, str) else "",
                                         allow_unsandboxed=True)
            res = registry_check(item, response)
            if item.format == "cloze" and not res.ok and not res.undecided and isinstance(item.payload, ClozePayload) \
                    and "accents" not in (res.detail or ""):
                fair = fair_cloze([item.payload.answer, *item.payload.aliases], response, self._corpus(item))
                if fair is not None:
                    return fair
            return res
        except NotRegistered:
            raise ServiceError(501, "no_checker", "This kind of question cannot be checked here.") from None
        except ServiceError:
            raise
        except Exception:
            raise ServiceError(500, "checker_error", "The checker failed on that answer. It was not recorded.") from None

    def answer(self, item_id: str, response: Any) -> AnswerResponse:
        item = self._active_item(item_id)
        try:
            if len(json.dumps(response, default=str)) > MAX_RESPONSE_JSON:
                raise ServiceError(413, "too_large", "That answer is too long.")
        except (TypeError, ValueError):
            raise ServiceError(422, "bad_request", "That answer is not valid JSON.") from None
        with self._exclusive(item_id):
            res = self._grade(item, response)
            if res.undecided:
                raise ServiceError(503, "undecided", res.detail or "The checker could not decide. Try again; nothing was recorded.")
            hints = self.hints_used(item_id)
            out = adapt.submit_answer(self.store, item_id, response, now=self.now(), hints_used=hints, result=res)
        self.store.set_meta(self._hints_key(item_id), 0)
        nr = None
        if out.review is not None and out.interval_days is not None:
            nr = NextReview(due=out.review.due, interval_days=round(max(out.interval_days, 0.0), 3))
        return self._answer_view(item, res, out.correct, nr, out.mastery)

    def _answer_view(self, item: Item, res, correct: bool | None, nr: NextReview | None,
                     mastery: float | None) -> AnswerResponse:
        """What a learner sees after an answer: the proof (quote, location, key spans) and the plain feedback.
        Shared by answer() (which recorded the attempt) and Learn's check (which records nothing)."""
        locate = lambda pid: (p.location if (p := self.store.get_passage(pid)) else None)
        cites = reveal(item, locate)
        keys = study.answer_keys(item)
        for c in cites:
            p = self.store.get_passage(c.passage_id)
            if p is not None and keys:
                c.key_spans = study.key_spans(p.text, c.quote, keys)
        if item.format == "explain":
            feedback = "Compare your explanation with the source below."
        elif correct:
            feedback = correct_feedback(res.detail)
        else:
            feedback = tidy_feedback(res.detail) or "Not quite."
        return AnswerResponse(
            item_id=item.id, correct=correct, score=min(max(float(res.score), 0.0), 1.0), feedback=feedback,
            correct_answer=correct_answer_text(item, self.store), citations=cites,
            source_label=source_label(item), next_review=nr,
            mastery=None if mastery is None else round(min(max(mastery, 0.0), 1.0), 4))

    def run(self, item_id: str, code: str) -> RunResponse:
        item = self._active_item(item_id)
        if not isinstance(item.payload, CodePayload):
            raise ServiceError(422, "not_code", "Only code questions can be run.")
        if not isinstance(code, str) or len(code) > MAX_RESPONSE_JSON:
            raise ServiceError(413, "too_large", "That code is too long.")
        with self._exclusive(item_id):
            r = code_runner.run_visible(item.payload, code, allow_unsandboxed=self.cfg.allow_unsandboxed_code)
        if r.undecided:
            raise ServiceError(503, "sandbox_unavailable", r.detail or "Install bubblewrap to run code exercises.")
        results = [t for t in (r.per_test or []) if t.visible]
        return RunResponse(item_id=item_id, ok=bool(r.ok), passed=r.passed, total=r.total, results=results,
                           compile_error=r.compile_error, timed_out=r.timed_out)

    def hint(self, item_id: str, level: int | None = None) -> HintResponse:
        item = self._active_item(item_id)
        used = self.hints_used(item_id)
        if level is None:
            level = min(used + 1, 3)
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 3:
            raise ServiceError(422, "bad_request", "Hint level must be 1, 2 or 3.")
        try:
            h = adapt.get_hint(self.store, item_id, level)
        except KeyError:
            raise ServiceError(404, "unknown_item", f"No question with id {item_id!r}.") from None
        if not h.locked:
            used = max(used, level)
            self.store.set_meta(self._hints_key(item_id), used)
        return HintResponse(item_id=item.id, hint=h.hint, location=h.location, passage_text=h.passage_text,
                            hints_used=used)

    def report(self, item_id: str, reason: str = "") -> ReportResponse:
        item = self._item(item_id)
        reason = (reason or "")[:MAX_REASON_CHARS] if isinstance(reason, str) else ""
        self.store.mark_reported(item.id, reason)
        cid = self.store.course_of_objective(item.objective_id)
        if cid:
            self._fire(EVENT_BANK_UPDATED, {"course_id": cid})
        return ReportResponse(item_id=item.id, status="reported", reports_total=self.store.count_reports())

    # ------------------------------------------------------------------ timeline / plot / passage

    def timeline(self, course_id: str) -> TimelineResponse:
        """Dates from gate-passed order items, only for items the learner has already attempted
        (a date list for an unanswered order question would be its answer key)."""
        self._course(course_id)
        seen: set[tuple[str, str]] = set()
        rows: list[tuple[tuple, TimelineEvent]] = []
        for it in self.store.list_items(course_id=course_id, format="order", status="active"):
            if not self.store.count_attempts(item_id=it.id):
                continue
            for ev in it.payload.events:
                key = (ev.label, ev.date_text)
                p = self.store.get_passage(ev.passage_id)
                if key in seen or p is None:
                    continue
                seen.add(key)
                quote = next((c.quote for c in it.citations if c.passage_id == ev.passage_id), "") \
                    or _sentence_with(p.text, ev.date_text)
                sk = date_sort_key(ev.date_text)
                rows.append(((0, sk) if sk else (1, (0, 0, 0)),
                             TimelineEvent(label=ev.label, date_text=ev.date_text,
                                           sort_year=sk[0] if sk else None, passage_id=p.id,
                                           location=p.location, quote=quote)))
        rows.sort(key=lambda r: (r[0][0], r[0][1], r[1].label))
        return TimelineResponse(course_id=course_id, events=[r[1] for r in rows])

    def plot(self, item_id: str) -> PlotResponse:
        item = self._item(item_id)
        p = item.payload
        if not isinstance(p, MathPayload):
            raise ServiceError(422, "not_plottable", "Only math questions have a plot.")
        answered = self.store.count_attempts(item_id=item.id) > 0
        if p.op in ("solve", "evaluate") and not answered:
            raise ServiceError(409, "plot_after_answer", "The plot for this question is shown after you answer.")
        expr = p.expr
        if "=" in expr:
            lhs, _, rhs = expr.partition("=")
            expr = f"({lhs.strip()})-({rhs.strip()})"
        var = p.var or "x"
        series_specs = [(f"f({var})", expr)]
        if answered and p.op in ("diff", "integrate"):
            ans = p.given_answer or math_sympy.truth(p)
            if ans:
                series_specs.append(("f'(x)" if p.op == "diff" else "F(x)", ans))
        series = []
        for label, ex in series_specs:
            try:
                pts = math_sympy.plot_points(ex, var, PLOT_RANGE[0], PLOT_RANGE[1], PLOT_N)
            except ValueError as e:
                if label.startswith("f(") or not series:
                    raise ServiceError(422, "plot_failed", f"Could not plot this one: {e}") from None
                continue
            series.append(PlotSeries(label=label, expr=ex,
                                     points=[(round(x, 5), None if y is None else round(y, 5)) for x, y in pts]))
        return PlotResponse(item_id=item.id, title=f"{series[0].label} = {expr}", x_label=var, y_label="y",
                            x_range=PLOT_RANGE, series=series)

    def passage(self, passage_id: str) -> PassageResponse:
        p = self.store.get_passage(passage_id) if isinstance(passage_id, str) and passage_id else None
        if p is None:
            raise ServiceError(404, "unknown_passage", f"No passage with id {passage_id!r}.")
        doc = self.store.get_doc(p.doc_id)
        prev_id, next_id = self.store.adjacent_passage_ids(p.id)
        return PassageResponse(passage=p, doc_name=doc.name if doc else p.doc_id,
                               doc_kind=doc.kind if doc else "text", prev_id=prev_id, next_id=next_id)


# ---------------------------------------------------------------------- helpers

def _first_line(e: BaseException) -> str:
    s = str(e).strip().splitlines()
    return (s[0] if s else type(e).__name__)[:300]


def _sentence_with(text: str, needle: str) -> str:
    for part in text.replace("\n", " ").split(". "):
        if needle and needle in part:
            return part.strip().rstrip(".") + "."
    return text[:200]


_DEVICE_ROOTS = ("/proc", "/sys", "/dev")
_SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config", ".password-store", ".netrc"}
_SNIFF_BYTES = 8192


def _under_device_root(p: str) -> bool:
    return any(p == r or p.startswith(r + "/") for r in _DEVICE_ROOTS)


def _hidden_or_secret(p: str) -> bool:
    parts = [x for x in p.split("/") if x]
    return bool(parts) and (parts[-1].startswith(".") or any(x in _SECRET_DIRS for x in parts))


def check_local_path(path: str) -> tuple[str, str]:
    """Policy for every file CodeCoach reads on someone's behalf (the import box, codecoach_import and the
    syllabus path): a prompt-injected agent must not be able to point an import at a secret.

    Allowed: a regular file whose name AND resolved target end in .txt .md .markdown .rst .text .pdf,
    that is not hidden, not inside a credentials folder (.ssh, .aws, ...) and not under /proc, /sys or
    /dev. Returns (resolved path, extension). Raises ServiceError with a plain-language message."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ServiceError(422, "bad_request", "Give a file path.")
    raw = path.strip()
    p = os.path.abspath(os.path.expanduser(raw))
    ext = os.path.splitext(p)[1].lower()
    if ext not in READABLE_SUFFIXES:
        raise ServiceError(422, "unsupported_file", "I can import .txt, .md and .pdf files.")
    refuse = ServiceError(422, "path_not_allowed",
                          "I can only import ordinary document files (.txt, .md, .pdf), not hidden files, "
                          "credential folders or system paths. Move or copy the document somewhere normal and try again.")
    if _under_device_root(p) or _hidden_or_secret(p):
        raise refuse
    try:
        rp = os.path.realpath(p)
    except (OSError, ValueError):
        raise ServiceError(404, "no_file", f"No file at {raw!r}.") from None
    if _under_device_root(rp) or _hidden_or_secret(rp) or os.path.splitext(rp)[1].lower() not in READABLE_SUFFIXES:
        raise refuse            # a symlink with a friendly name, pointing at something it should not
    try:
        st = os.stat(rp)
    except OSError:
        raise ServiceError(404, "no_file", f"No file at {raw!r}.") from None
    if not stat.S_ISREG(st.st_mode):
        raise ServiceError(422, "not_a_file", "That is not a regular file (it is a folder, pipe or device). "
                                              "Pick a .txt, .md or .pdf document.")
    return rp, ext


def read_local_path(path: str) -> str:
    """Default path reader for text files (Hermes-free). PDFs need an extractor from the glue."""
    rp, ext = check_local_path(path)
    if ext == ".pdf" and not pdf.available():
        raise ServiceError(422, "pdf_unavailable", "PDF text extraction is not available here. Paste the text instead.")
    if ext == ".pdf":
        if os.stat(rp).st_size > MAX_PDF_BYTES:
            raise ServiceError(413, "too_large", "That PDF is too big (limit 200 MB).")
        try:
            return pdf.read_pdf(rp).text
        except pdf.PdfError as e:
            raise ServiceError(422, e.code, e.message) from None
    too_big = ServiceError(413, "too_large", "That file is too big (limit 20 MB).")
    try:
        fd = os.open(rp, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)    # never block on a pipe swapped in late
    except OSError:
        raise ServiceError(404, "no_file", f"Could not open {path!r}.") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ServiceError(422, "not_a_file", "That is not a regular file.")
        if st.st_size > MAX_FILE_BYTES:
            raise too_big
        data = f.read(MAX_FILE_BYTES + 1)       # bounded even if the file grows after the size check
    if len(data) > MAX_FILE_BYTES:
        raise too_big
    # UTF-16 text is full of NUL bytes by nature: decide on its BOM before the binary sniff
    if not data.startswith((b"\xff\xfe", b"\xfe\xff")) and b"\x00" in data[:_SNIFF_BYTES]:
        raise ServiceError(422, "not_text", "That file does not look like text (it may be a picture or another binary "
                                            "format renamed to .md). Import a real text or PDF file.")
    return decode_text(data)


def decode_text(raw: bytes) -> str:
    """UTF-8 (with or without a BOM), then UTF-16 when a BOM says so, then Windows-1252 (which covers Latin-1
    text with accents, quotes and dashes). Never U+FFFD garbage from a legacy file."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # Mostly UTF-8 with a few stray bytes: real multi-byte characters prove the encoding, so keep them
    # ("café" must not turn into "cafÃ©") and mark only the stray bytes.
    u = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    if any(ord(c) > 127 and c != "\ufffd" for c in u):
        return u
    return raw.decode("cp1252", errors="replace")
