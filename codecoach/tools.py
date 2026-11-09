"""Tool handlers and the /codecoach command. Hermes-free: they call CodeCoachService through a getter.

Every handler returns a JSON string and never raises. Text fields ("text") are ready to show on
Telegram or a terminal. Citations and the correct answer appear only in the reply to an answer.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from codecoach.core.models import Job, MapResponse, NextResponse, PublicItem
from codecoach.service import CodeCoachService, ServiceError

Getter = Callable[[], CodeCoachService]

LAST_ITEM_KEY = "codecoach:last_item"
ACTIVE_COURSE_KEY = "codecoach:active_course"
MAX_TEXT = 3500           # keeps a reply inside one Telegram message
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

TOOL_NAMES = ["codecoach_import", "codecoach_next", "codecoach_answer", "codecoach_hint", "codecoach_progress", "codecoach_report",
              "codecoach_ask"]

HELP = """CodeCoach tutor
/codecoach courses            list your courses
/codecoach import <path>      import a .txt, .md or .pdf (or paste: /codecoach import My title | text...)
/codecoach import <path> --syllabus <syllabus-path>   also check the materials against a syllabus
/codecoach import <path> --subject "<name>"   file it under a subject (default: detected)
/codecoach import outline <path>   import a syllabus or topic outline (open mode)
/codecoach next               the next lesson or question
/codecoach answer <text>      answer the current question (mc: the option number)
/codecoach hint               a hint for the current question
/codecoach progress           how you are doing
/codecoach map                the course outline with mastery
/codecoach subject <course-id> <subject...>   change a course's subject (empty clears it)
/codecoach delete <course-id> confirm   delete a course and all its progress"""


# ------------------------------------------------------------------ small helpers

def _j(obj: dict) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"ok": False, "error": "internal", "text": "Something went wrong."})


def _clip(s: str, n: int = MAX_TEXT) -> str:
    return s if len(s) <= n else s[: n - 20].rstrip() + "\n... (shortened)"


def _err(e: BaseException) -> dict:
    if isinstance(e, ServiceError):
        return {"ok": False, "error": e.code, "text": e.message}
    return {"ok": False, "error": "internal", "text": "CodeCoach hit an unexpected problem. Try again."}


def _safe(fn: Callable[[], dict]) -> str:
    try:
        return _j(fn())
    except Exception as e:  # tools never raise
        return _j(_err(e))


def _args(a: Any) -> dict:
    return a if isinstance(a, dict) else {}


def _str(v: Any, limit: int = 100_000) -> str:
    return v.strip()[:limit] if isinstance(v, str) else (str(v)[:limit] if isinstance(v, (int, float)) and not isinstance(v, bool) else "")


def resolve_course(svc: CodeCoachService, course_id: Any = None) -> str:
    cid = _str(course_id, 200)
    if cid:
        return cid
    remembered = svc.store.get_meta(ACTIVE_COURSE_KEY)
    if isinstance(remembered, str) and svc.store.get_course(remembered):
        return remembered
    courses = svc.store.list_courses()
    if not courses:
        raise ServiceError(404, "no_courses", "No courses yet. Import some materials first: /codecoach import <path>.")
    return max(courses, key=lambda c: c.created_at).id


def _remember(svc: CodeCoachService, course_id: str) -> None:
    svc.store.set_meta(ACTIVE_COURSE_KEY, course_id)


# ------------------------------------------------------------------ rendering

def render_item(item: PublicItem) -> str:
    p = item.payload
    lines = [item.prompt.strip()]
    f = item.format
    if f == "mc":
        lines += [f"{i}. {o}" for i, o in enumerate(p.get("options", []), 1)]
        lines.append("Reply with the option number.")
    elif f == "cloze":
        lines = [item.prompt.strip(), p.get("text_with_blank", ""), "Reply with the missing word or phrase."]
    elif f == "order":
        lines += [f"{i}. {e.get('label', '')}" for i, e in enumerate(p.get("events", []), 1)]
        lines.append("Reply with the numbers in order, earliest first (for example: 2 1 3).")
    elif f == "match":
        lines += [f"{i}. {t}" for i, t in enumerate(p.get("lefts", []), 1)]
        lines.append("Choices:")
        lines += [f"{LETTERS[i]}. {t}" for i, t in enumerate(p.get("rights", [])[:26])]
        lines.append("Reply like: 1A 2C 3B")
    elif f == "short":
        lines.append(f"Answer in a sentence. Aim to cover {p.get('terms_expected', 1)} key point(s).")
    elif f == "explain":
        lines.append("Explain it in your own words, then reply with a self-rating: 0 (missed it), 0.5 (partly) or 1 (got it).")
    elif f == "code":
        lines.append(f"Language: {p.get('language', '')}")
        if p.get("starter"):
            lines += ["Starter:", p["starter"]]
        for t in p.get("visible_tests", [])[:5]:
            lines.append(f"Test: {t.get('name', '')}")
        lines.append("Reply with your full code.")
    elif f == "math":
        lines.append(f"{p.get('op', '')}: {p.get('expr', '')}")
        lines.append("Reply with your answer.")
    elif f == "numeric":
        given = ", ".join(f"{q.get('name', '')} = {q.get('value', '')} {q.get('unit', '')}".strip() for q in p.get("given", []))
        if given:
            lines.append(f"Given: {given}")
        lines.append(f"Find: {p.get('quantity', '')} in {p.get('unit', '')}. Reply with a number and unit.")
    lines.append(f"({item.source_label})")
    return "\n".join(x for x in lines if x is not None)


def render_next(n: NextResponse) -> str:
    if n.kind == "lesson" and n.lesson:
        l = n.lesson
        out = [f"Lesson: {l.lesson_title or l.statement}", l.statement]
        if l.framing:
            out.append(l.framing)
        for ps in l.passages:
            out.append(f"[{ps.location}]\n{ps.text}")
        out.append(l.source_label)
        out.append("Say 'next' when you are ready for a question.")
        return "\n\n".join(out)
    if n.kind == "item" and n.item:
        return render_item(n.item)
    return n.message or "Nothing to practise right now."


def parse_answer(fmt: str, payload: dict, raw: Any) -> Any:
    """Turn text from a chat into the response the checker expects. Garbage in -> a value the checker rejects."""
    if not isinstance(raw, (str, int, float, list, dict)) or isinstance(raw, bool):
        return ""
    if isinstance(raw, (list, dict)):
        return raw
    s = str(raw).strip()
    if fmt == "mc":
        m = re.search(r"\d+", s)
        if m:
            return int(m.group()) - 1
        low = s.lower()
        for i, o in enumerate(payload.get("options", [])):
            if low and low == str(o).strip().lower():
                return i
        return -1
    if fmt == "order":
        nums = re.findall(r"\d+", s)
        return [int(x) - 1 for x in nums]
    if fmt == "match":
        pairs = re.findall(r"(\d+)\s*[-:=.]?\s*([A-Za-z])", s)
        return [[int(a) - 1, LETTERS.index(b.upper())] for a, b in pairs]
    if fmt == "explain":
        low = s.lower()
        words = {"missed": 0.0, "no": 0.0, "partly": 0.5, "partial": 0.5, "got it": 1.0, "yes": 1.0}
        for k, v in words.items():
            if low == k:
                return v
        try:
            return float(s.split()[0])
        except (ValueError, IndexError):
            return None
    return s


# ------------------------------------------------------------------ operations (shared by tools and command)

def op_courses(svc: CodeCoachService) -> dict:
    cs = svc.courses().courses
    if not cs:
        return {"ok": True, "courses": [], "text": "No courses yet. Import some materials: /codecoach import <path>."}
    lines = [f"{i}. {c.title} [{c.id}] - {c.covered}/{c.objectives} topics covered, mastery {round(c.mastery * 100)}%, "
             f"{c.items_active} questions" + (f", {c.subject}" if c.subject else "") for i, c in enumerate(cs, 1)]
    return {"ok": True, "courses": [c.model_dump(mode="json") for c in cs], "text": "\n".join(lines)}


def op_delete(svc: CodeCoachService, course_id: str, confirm: bool = False) -> dict:
    course_id = _str(course_id, 200)
    if not course_id:
        raise ServiceError(422, "bad_request", "Usage: /codecoach delete <course-id> confirm   (see /codecoach courses)")
    r = svc.delete_course(course_id, confirm=confirm is True)
    c = r["counts"]
    what = (f"{c['docs']} document(s), {c['passages']} passages, {c['objectives']} topics, {c['items']} questions, "
            f"{c['attempts']} attempts and {c['review_states']} review schedules")
    if r["deleted"]:
        return {"ok": True, **r, "text": f"Deleted \"{r['title']}\" [{course_id}]: {what}."}
    return {"ok": True, **r, "text": (f"This would delete \"{r['title']}\" [{course_id}]: {what}. "
                                      f"Nothing was deleted. To go ahead: /codecoach delete {course_id} confirm")}


def op_import(svc: CodeCoachService, *, path: str = "", text: str = "", title: str = "", mode: str | None = None,
              outline: bool = False, syllabus_path: str = "", subject: str = "") -> dict:
    path, text, title = _str(path, 4096), _str(text), _str(title, 300)
    syllabus_path = _str(syllabus_path, 4096)
    subject = _str(subject, 200).strip()
    if len(subject) > 40:
        raise ServiceError(422, "subject_too_long", "A subject can be at most 40 characters.")
    if mode not in (None, "", "strict", "open"):
        raise ServiceError(422, "bad_request", "mode must be strict or open.")
    if path and text:
        raise ServiceError(422, "bad_request", "Give a path or text, not both.")
    if not path and not text:
        raise ServiceError(422, "bad_request", "Give a file path or the text to import.")
    if not title:
        base = path.rstrip("/").rsplit("/", 1)[-1] if path else "Pasted notes"
        title = (base.rsplit(".", 1)[0] if "." in base else base) or "Imported course"
    job_id = svc.start_import(title=title, text=text or None, path=path or None, mode=mode or None,
                              outline=outline is True, syllabus_path=syllabus_path or None, subject=subject or None)
    return {"ok": True, "job_id": job_id,
            "text": f"Import started (job {job_id})"
                    + (", checking your materials against the syllabus" if syllabus_path else "")
                    + f". It takes a few minutes. Check progress with /codecoach progress "
                    f"or codecoach_progress(job_id='{job_id}'). Questions are prepared after the map is built."}


def op_subject(svc: CodeCoachService, course_id: str, subject: str = "") -> dict:
    course_id = _str(course_id, 200)
    if not course_id:
        raise ServiceError(422, "bad_request", "Usage: /codecoach subject <course-id> <subject...>   (see /codecoach courses)")
    c = svc.set_subject(course_id, _str(subject, 200))
    if c.subject:
        return {"ok": True, "course_id": c.id, "subject": c.subject, "text": f"\"{c.title}\" [{c.id}] is now filed under {c.subject}."}
    return {"ok": True, "course_id": c.id, "subject": None, "text": f"\"{c.title}\" [{c.id}] now has no subject (Uncategorized)."}


def op_job(svc: CodeCoachService, job_id: str) -> dict:
    j: Job = svc.job(job_id)
    msg = f"Job {j.id} ({j.kind}): {j.status}, {round(j.pct)}%"
    if j.stage:
        msg += f" - {j.stage}"
    if j.error:
        msg += f"\nProblem: {j.error}"
    if j.status == "done" and j.course_id:
        msg += f"\nCourse ready: {j.course_id}"
    return {"ok": True, "job": j.model_dump(mode="json"), "text": msg}


def op_next(svc: CodeCoachService, course_id: Any = None) -> dict:
    cid = resolve_course(svc, course_id)
    n = svc.next(cid)
    _remember(svc, cid)
    if n.kind == "item" and n.item:
        svc.store.set_meta(LAST_ITEM_KEY, n.item.id)
    return {"ok": True, "course_id": cid, "kind": n.kind, "reason": n.reason,
            "item_id": n.item.id if n.item else None, "text": _clip(render_next(n)),
            "next": n.model_dump(mode="json")}


def _last_item(svc: CodeCoachService, item_id: Any) -> str:
    iid = _str(item_id, 200)
    if iid:
        return iid
    last = svc.store.get_meta(LAST_ITEM_KEY)
    if isinstance(last, str) and last:
        return last
    raise ServiceError(404, "no_current_item", "There is no current question. Ask for one with /codecoach next.")


def op_answer(svc: CodeCoachService, item_id: Any, answer: Any) -> dict:
    iid = _last_item(svc, item_id)
    item = svc._item(iid)
    from codecoach.core.public import public_view
    pv = public_view(item)
    resp = parse_answer(item.format, pv.payload, answer)
    a = svc.answer(iid, resp)
    verdict = "Rated." if a.correct is None else ("Correct." if a.correct else "Not quite.")
    lines = [verdict, a.feedback]
    if a.correct is not True and a.correct_answer not in (None, "", []):
        ca = a.correct_answer
        lines.append("Answer: " + (", ".join(map(str, ca)) if isinstance(ca, list) else str(ca)))
    if a.citations:
        lines.append("Source:")
        lines += [f'- {c.location or c.passage_id}: "{c.quote}"' for c in a.citations]
    lines.append(a.source_label)
    if a.next_review:
        lines.append(f"Next review in about {a.next_review.interval_days:g} day(s).")
    svc.store.set_meta(LAST_ITEM_KEY, "")
    return {"ok": True, "item_id": iid, "correct": a.correct, "score": a.score,
            "text": _clip("\n".join(x for x in lines if x)), "answer": a.model_dump(mode="json")}


def op_hint(svc: CodeCoachService, item_id: Any, level: Any = None) -> dict:
    iid = _last_item(svc, item_id)
    lv = level if isinstance(level, int) and not isinstance(level, bool) else None
    h = svc.hint(iid, lv)
    body = getattr(h.hint, "text", None) or str(h.hint)
    lines = [f"Hint {h.hints_used}: {body}" if h.hints_used else f"Hint: {body}"]
    if h.passage_text:
        lines.append(f"[{h.location}] {h.passage_text}")
    return {"ok": True, "item_id": iid, "hints_used": h.hints_used, "text": _clip("\n".join(lines)),
            "hint": h.model_dump(mode="json")}


def op_progress(svc: CodeCoachService, course_id: Any = None, job_id: Any = None) -> dict:
    jid = _str(job_id, 200)
    if jid:
        return op_job(svc, jid)
    cid = resolve_course(svc, course_id)
    p = svc.progress(cid)
    acc = "n/a" if p.accuracy is None else f"{round(p.accuracy * 100)}%"
    text = (f"Course {cid}: {p.covered}/{p.objectives} topics covered, {p.mastered} mastered, "
            f"mean mastery {round(p.mean_mastery * 100)}%.\n"
            f"Questions: {p.items_active} active, {p.items_dropped} dropped by the source check, {p.items_reported} reported.\n"
            f"Attempts: {p.attempts}, accuracy {acc}, {p.due_reviews} review(s) due.")
    return {"ok": True, "course_id": cid, "text": text, "progress": p.model_dump(mode="json")}


def op_report(svc: CodeCoachService, item_id: Any, reason: Any = "") -> dict:
    iid = _last_item(svc, item_id)
    r = svc.report(iid, _str(reason, 2000))
    return {"ok": True, "item_id": iid, "text": "Thanks. That question is retired and will not come back.",
            "report": r.model_dump(mode="json")}


def render_map(m: MapResponse) -> str:
    out = [f"{m.course.title}  ({m.summary.covered}/{m.summary.objectives} covered, mean mastery "
           f"{round(m.summary.mean_mastery * 100)}%)"]
    for u in m.units:
        out.append(f"{u.title}")
        for l in u.lessons:
            out.append(f"  {l.title}")
            for o in l.objectives:
                tag = o.status_label if o.status_label else f"{round(o.mastery * 100)}%"
                out.append(f"    - {o.statement} [{tag}]")
    return "\n".join(out)


def op_map(svc: CodeCoachService, course_id: Any = None) -> dict:
    cid = resolve_course(svc, course_id)
    return {"ok": True, "course_id": cid, "text": _clip(render_map(svc.map(cid)), 20_000)}


# ------------------------------------------------------------------ tool schemas and factories

SCHEMAS: dict[str, dict] = {
    "codecoach_import": {
        "name": "codecoach_import",
        "description": "Import course materials (a .txt, .md or .pdf path, or pasted text) into the CodeCoach tutor. "
                       "Starts a background job and returns its job_id.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to a .txt, .md or .pdf file."},
            "text": {"type": "string", "description": "Pasted text to import instead of a path."},
            "title": {"type": "string", "description": "Course title."},
            "mode": {"type": "string", "enum": ["strict", "open"], "description": "strict = only facts in the materials."},
            "outline": {"type": "boolean", "description": "true = the text is only a topic outline or syllabus, not "
                                                          "teaching material (the course runs in open mode)."},
            "subject": {"type": "string", "description": "Optional subject to file the course under (History, "
                                                         "Programming, Math, ...). Max 40 characters. Left out, "
                                                         "CodeCoach detects it."},
            "syllabus_path": {"type": "string", "description": "Optional path to a syllabus (.txt/.md), one topic per "
                                                               "line. CodeCoach checks the materials against it and marks "
                                                               "the topics they do not cover."}}},
    },
    "codecoach_next": {
        "name": "codecoach_next",
        "description": "Get the next lesson or practice question for a course. Options are numbered; the answer key is never shown.",
        "parameters": {"type": "object", "properties": {"course_id": {"type": "string"}}},
    },
    "codecoach_answer": {
        "name": "codecoach_answer",
        "description": "Answer the current question (or item_id). For multiple choice send the option number. "
                       "The reply shows whether it was right, the correct answer and the cited source.",
        "parameters": {"type": "object", "properties": {
            "answer": {"type": "string", "description": "The learner's answer as text."},
            "item_id": {"type": "string", "description": "Defaults to the last question served."}},
            "required": ["answer"]},
    },
    "codecoach_hint": {
        "name": "codecoach_hint",
        "description": "Give the next hint (levels 1-3) for the current question.",
        "parameters": {"type": "object", "properties": {
            "item_id": {"type": "string"}, "level": {"type": "integer", "minimum": 1, "maximum": 3}}},
    },
    "codecoach_progress": {
        "name": "codecoach_progress",
        "description": "Show learning progress for a course, or the status of an import job (job_id).",
        "parameters": {"type": "object", "properties": {"course_id": {"type": "string"}, "job_id": {"type": "string"}}},
    },
    "codecoach_ask": {
        "name": "codecoach_ask",
        "description": ("Answer a question from a strict course's own materials. Every sentence returned was checked by "
                        "code against a verbatim quote from the materials; anything unchecked is removed. If the "
                        "materials do not answer, the reply is status not_covered with no answer text: say so, "
                        "do not answer from memory. Takes up to a minute."),
        "parameters": {"type": "object", "properties": {
            "course": {"type": "string", "description": "course id"},
            "question": {"type": "string", "description": "1-300 characters"}}, "required": ["course", "question"]},
    },
    "codecoach_report": {
        "name": "codecoach_report",
        "description": "Report the current question as wrong or unfair. It is retired from the bank.",
        "parameters": {"type": "object", "properties": {
            "item_id": {"type": "string"}, "reason": {"type": "string"}}},
    },
}


def op_ask(svc: CodeCoachService, course: str | None, question: str) -> dict:
    """Blocking Ask for the chat agent: only checked sentences with their quotes, or not_covered. No cache, no job."""
    question = (question or "").strip()
    if not course or not 1 <= len(question) <= 300:
        return {"ok": False, "error": "bad_request", "text": "codecoach_ask needs a course id and a question of 1 to 300 characters."}
    res = svc.ask_now(course, question)
    st = res["status"]
    if st == "answered":
        lines = [f"{i}. {s['text']}" for i, s in enumerate(res["sentences"], 1)]
        notes = [f"[{i}] \"{c['quote']}\" ({c['location']})" for i, s in enumerate(res["sentences"], 1) for c in s["cites"]]
        return {"ok": True, "status": st, "sentences": [{"text": s["text"], "cites": [
                {"quote": c["quote"], "location": c["location"], "passage_id": c["passage_id"]} for c in s["cites"]]}
                for s in res["sentences"]], "text": "\n".join(lines + ["Sources:"] + notes)}
    if st == "not_covered":
        return {"ok": True, "status": st, "sentences": [], "closest_locations": [c["location"] for c in res["closest"]],
                "text": "Not covered in the materials, so there is no answer. Do not answer from memory."}
    if st == "unavailable":
        return {"ok": False, "status": st, "error": "unavailable", "text": res["reason"]}
    return {"ok": False, "status": st, "error": "model", "text": res.get("message", "Ask failed.")}


def make_handlers(get_svc: Getter) -> dict[str, Callable[..., str]]:
    def h_import(args=None, **kw):
        a = _args(args)
        return _safe(lambda: op_import(get_svc(), path=a.get("path", ""), text=a.get("text", ""),
                                       title=a.get("title", ""), mode=a.get("mode"),
                                       outline=a.get("outline") is True,
                                       syllabus_path=a.get("syllabus_path", ""),
                                       subject=a.get("subject", "")))

    def h_next(args=None, **kw):
        return _safe(lambda: op_next(get_svc(), _args(args).get("course_id")))

    def h_answer(args=None, **kw):
        a = _args(args)
        return _safe(lambda: op_answer(get_svc(), a.get("item_id"), a.get("answer", a.get("response"))))

    def h_hint(args=None, **kw):
        a = _args(args)
        return _safe(lambda: op_hint(get_svc(), a.get("item_id"), a.get("level")))

    def h_progress(args=None, **kw):
        a = _args(args)
        return _safe(lambda: op_progress(get_svc(), a.get("course_id"), a.get("job_id")))

    def h_report(args=None, **kw):
        a = _args(args)
        return _safe(lambda: op_report(get_svc(), a.get("item_id"), a.get("reason", "")))

    def h_ask(args=None, **kw):
        a = _args(args)
        return _safe(lambda: op_ask(get_svc(), a.get("course", a.get("course_id")), a.get("question", "")))

    return {"codecoach_ask": h_ask, "codecoach_import": h_import, "codecoach_next": h_next, "codecoach_answer": h_answer,
            "codecoach_hint": h_hint, "codecoach_progress": h_progress, "codecoach_report": h_report}


# ------------------------------------------------------------------ the /codecoach command

def make_command(get_svc: Getter) -> Callable[..., str]:
    def handler(raw_args: str = "", **kw) -> str:
        try:
            raw = raw_args.strip() if isinstance(raw_args, str) else ""
            verb, _, rest = raw.partition(" ")
            verb, rest = verb.lower(), rest.strip()
            if verb in ("", "help", "?"):
                return HELP
            svc = get_svc()
            if verb == "courses":
                return op_courses(svc)["text"]
            if verb == "import":
                outline = rest.lower().startswith("outline ")
                if outline:
                    rest = rest[len("outline "):].strip()
                syllabus, subject = "", ""
                m = re.search(r"(?:^|\s)--subject(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|(\S+))", rest)
                if m:
                    subject = next(g for g in m.groups() if g is not None).strip()
                    rest = (rest[:m.start()] + " " + rest[m.end():]).strip()
                m = re.search(r"(?:^|\s)--syllabus(?:=|\s+)(\S.*)$", rest)
                if m:
                    syllabus, rest = m.group(1).strip(), rest[:m.start()].strip()
                if not rest:
                    return ("Usage: /codecoach import <path> [--syllabus <syllabus-path>]   "
                            "or   /codecoach import <title> | <pasted text>")
                if "|" in rest and not rest.lower().endswith((".pdf", ".md", ".txt")):
                    title, _, body = rest.partition("|")
                    return op_import(svc, title=title, text=body, outline=outline, syllabus_path=syllabus,
                                     subject=subject)["text"]
                return op_import(svc, path=rest, outline=outline, syllabus_path=syllabus, subject=subject)["text"]
            if verb == "subject":
                words = rest.split(None, 1)
                if not words:
                    return "Usage: /codecoach subject <course-id> <subject...>   (see /codecoach courses)"
                return op_subject(svc, words[0], words[1] if len(words) > 1 else "")["text"]
            if verb == "delete":
                words = rest.split()
                if not words:
                    return "Usage: /codecoach delete <course-id> confirm   (see /codecoach courses)"
                return op_delete(svc, words[0], confirm=len(words) > 1 and words[1].lower() == "confirm")["text"]
            if verb == "next":
                return op_next(svc, rest or None)["text"]
            if verb == "answer":
                return op_answer(svc, None, rest)["text"] if rest else "Usage: /codecoach answer <your answer>"
            if verb == "hint":
                return op_hint(svc, None)["text"]
            if verb == "progress":
                if rest.startswith("job"):
                    return op_job(svc, rest.split()[-1])["text"]
                return op_progress(svc, rest or None)["text"]
            if verb == "map":
                return op_map(svc, rest or None)["text"]
            if verb == "report":
                return op_report(svc, None, rest)["text"]
            return f"Unknown command {verb!r}.\n\n{HELP}"
        except ServiceError as e:
            return e.message
        except Exception:
            return "CodeCoach hit an unexpected problem. Try again."
    return handler
