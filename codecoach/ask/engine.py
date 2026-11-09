"""Ask: the flow, the prompt, the jobs, the cache and the history. Hermes-free; CodeCoachService delegates here."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from codecoach.ask import assist as A
from codecoach.ask import retrieve as R
from codecoach.ask.check import MAX_SENTENCES, Verdict, check_sentence, passage_sentences
from codecoach.core.llm import LLMBadOutput, LLMError, structured_call
from codecoach.core.models import Drop, Passage
from codecoach.core.text import normalize
from codecoach.importer.syllabus import content_words

PURPOSE = "codecoach.ask"
MAX_QUESTION_CHARS = 300
MAX_HISTORY = 50
ASK_STAGES = ("Finding passages", "Writing the answer", "Checking every sentence")
CLOSEST_LABEL = "closest, not an answer"
PASSAGE_CHARS_IN_PROMPT = 1400
NOT_COVERED_MESSAGE = "Your materials do not answer this. The passages below are the closest ones; they are not an answer."
ERROR_UNREACHABLE = "The model could not be reached, so there is no answer. Try again in a minute."
ERROR_BAD_JSON = "The model replied in a form CodeCoach could not read, twice, so there is no answer. Try again."


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str
    open_item_id: str | None = None          # the Practice question that is open right now, if any


class Sentence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    cites: list["Cite"] = Field(default_factory=list)


class Cite(BaseModel):
    model_config = ConfigDict(extra="ignore")
    passage_id: str = ""
    quote: str = ""


Sentence.model_rebuild()


class Reply(BaseModel):
    """What the model must return. Lengths are enforced by the gate (per sentence), not here, so one long
    sentence cannot reject a whole reply."""
    model_config = ConfigDict(extra="ignore")
    sentences: list[Sentence] = Field(default_factory=list)
    answerable: bool = True


INSTRUCTIONS = (
    "You answer a learner's question using ONLY the passages below, which are from their own study materials. "
    "Reply with JSON: {\"answerable\": true or false, \"sentences\": [{\"text\": \"...\", \"cites\": "
    "[{\"passage_id\": \"...\", \"quote\": \"...\"}]}]}.\n"
    "Rules:\n"
    "- At most 5 sentences, each at most 30 words, each with at least one cite.\n"
    "- passage_id must be exactly one of the ids shown in square brackets. quote must be copied WORD FOR WORD from "
    "that passage: one sentence or part of one sentence, at least 5 words, never the whole passage.\n"
    "- Write each sentence by copying the quote's own words, changing as little as possible (you may drop words or "
    "turn a clause into a sentence). Every content word of your sentence must also be in its quote. Add no number, "
    "date, name or other fact that the quote does not contain. No background, opinions or paraphrase, and no cause or motive of your own.\n"
    "- For a \"why\" question, first find the passage that tells the event the question names (who did what), then "
    "answer only with a reason that passage states in its own words (a because or so-that clause, a command given, a "
    "purpose, or a feeling the text names as what drove it). Quote that clause. A sentence about a different event or a "
    "different person is not an answer. If the passages state "
    "no reason, reply {\"answerable\": false, \"sentences\": []}; never supply a motive yourself.\n"
    "- A quote must contain every name, number and date that its sentence uses (quote the whole clause that has "
    "them, not a fragment). A sentence may have several cites: if a name or number it uses sits in a different "
    "part of the passage, cite that part too.\n"
    "- One sentence per fact: never join facts from different passages in one sentence; write a separate sentence for each, with its own quote.\n"
    "- Every sentence must directly answer what was asked. Passages that only mention the same words or a "
    "neighbouring event are not an answer.\n"
    "- If the question assumes something the passages contradict, say what the passages say instead of agreeing, "
    "and cite it.\n"
    "- If the passages do not answer the question, or only mention a neighbouring subject, reply "
    "{\"answerable\": false, \"sentences\": []}. Never use outside knowledge and never guess."
)


def order_by_relevance(question: str, kept: list[Verdict]) -> list[Verdict]:
    """Sentences that share more content words with the question come first; ties keep the model's order.
    Nothing is dropped here."""
    q = set(content_words(question))
    return sorted(kept, key=lambda v: -len(q & set(content_words(v.text))))


def normalise_question(q: str) -> str:
    q = normalize(q or "")
    return re.sub(r"[\s?!.]+$", "", q).strip()


def fingerprint(passages: list[Passage]) -> str:
    h = hashlib.sha1()
    for p in passages:
        h.update(p.id.encode())
        h.update(b"\0")
        h.update(p.text.encode())
        h.update(b"\1")
    return h.hexdigest()[:16]


def _window(text: str, terms: set[str], limit: int) -> str:
    """`text` if it fits, else the run of whole sentences (at most `limit` chars) that holds the most question terms,
    so a long passage is not shown by its first 1400 characters alone (the reason a question asks for can sit
    later). Always a verbatim slice of the passage; the quote check runs on the full text either way."""
    if len(text) <= limit:
        return text
    spans = passage_sentences(text)
    best, best_at = (-1, 0), 0
    for i in range(len(spans)):
        j = i
        while j + 1 < len(spans) and spans[j + 1][1] - spans[i][0] <= limit:
            j += 1
        hits = len(terms & set(content_words(text[spans[i][0]:spans[j][1]])))
        if (hits, -i) > best:
            best, best_at = (hits, -i), i
    a = spans[best_at][0]
    b = min(len(text), a + limit)
    j = best_at
    while j + 1 < len(spans) and spans[j + 1][1] - a <= limit:
        j += 1
    b = spans[j][1] if spans[j][1] - a <= limit else b
    return ("... " if a > 0 else "") + text[a:b] + (" ..." if b < len(text.rstrip()) else "")


def prompt_text(question: str, passages: list[Passage]) -> str:
    rows = [f"Question: {question}", "Passages:"]
    terms = set(content_words(question))
    for p in passages:
        rows.append(f"[{p.id}] ({p.location}) {_window(p.text, terms, PASSAGE_CHARS_IN_PROMPT)}")
    return "\n".join(rows)


def with_followers(offered: list[Passage], ranked: list[R.Ranked], passages: list[Passage], n: int = 3) -> list[Passage]:
    """For a "why" question: the passage right after each of the `n` best ones is offered too. A reason usually
    comes just after the event it explains and may share no word with the question."""
    pos = {p.id: i for i, p in enumerate(passages)}
    have = {p.id for p in offered}
    out = list(offered)
    for r in ranked[:n]:
        i = pos.get(r.passage.id)
        if i is not None and i + 1 < len(passages) and passages[i + 1].doc_id == r.passage.doc_id \
                and passages[i + 1].id not in have:
            have.add(passages[i + 1].id)
            out.append(passages[i + 1])
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AskEngine:
    """One per CodeCoachService. All shared state is behind `_lock`."""

    def __init__(self, svc):
        self.svc = svc
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._inflight: dict[tuple[str, str], str] = {}
        self._index: dict[str, tuple[str, R.AskIndex]] = {}

    # ------------------------------------------------------------------ storage

    def _key(self, course_id: str) -> str:
        return f"codecoach:asks:{course_id}"          # ends in :<course_id>, so deleting the course deletes it

    def _history(self, course_id: str) -> list[dict]:
        h = self.svc.store.get_meta(self._key(course_id), [])
        return h if isinstance(h, list) else []

    def _remember(self, course_id: str, entry: dict) -> None:
        with self._lock:
            h = [e for e in self._history(course_id) if e.get("nq") != entry["nq"]]
            h.insert(0, entry)
            self.svc.store.set_meta(self._key(course_id), h[:MAX_HISTORY])

    def asks(self, course_id: str) -> dict:
        self.svc._course(course_id)
        rows = [{"job_id": e["job_id"], "question": e["question"], "asked_at": e["at"],
                 "status": e["result"].get("status"), "result": e["result"]} for e in self._history(course_id)]
        return {"course_id": course_id, "asks": rows[:MAX_HISTORY]}

    # ------------------------------------------------------------------ start / poll

    def unavailable(self, course) -> str | None:
        if course.mode != "strict":
            return "Ask only works on a course in strict mode, where every fact comes from your materials."
        if not self.svc.store.list_passages(course_id=course.id):
            return "This course has no source text to answer from."
        return None

    def start(self, course_id: str, question: str, open_item_id: str | None = None) -> dict:
        from codecoach.service import ServiceError
        course = self.svc._course(course_id)
        q = question.strip() if isinstance(question, str) else ""
        if not 1 <= len(q) <= MAX_QUESTION_CHARS:
            raise ServiceError(422, "bad_question", f"A question needs 1 to {MAX_QUESTION_CHARS} characters.")
        why = self.unavailable(course)
        if why:
            return {"job_id": None, "status": "unavailable", "reason": why, "cached": False, "released_item": None}
        released = self._release(course_id, open_item_id)
        nq = normalise_question(q)
        passages = self.svc.store.list_passages(course_id=course_id)
        fp = fingerprint(passages)
        for e in self._history(course_id):
            if e.get("nq") == nq and e.get("fp") == fp and e["result"].get("status") in ("answered", "not_covered"):
                job_id = self._new_job(course_id, q, done=dict(e["result"], cached=True))
                self._remember(course_id, dict(e, job_id=job_id, at=_now(), result=dict(e["result"], cached=True)))
                return {"job_id": job_id, "status": "done", "cached": True, "released_item": released}
        with self._lock:
            live = self._inflight.get((course_id, nq))
            if live and self._jobs.get(live, {}).get("status") != "done":
                return {"job_id": live, "status": self._jobs[live]["status"], "cached": False, "released_item": released}
            job_id = self._new_job_locked(course_id, q)
            self._inflight[(course_id, nq)] = job_id
        self.svc._spawn(f"codecoach-ask-{job_id}", lambda: self._run_job(job_id, course_id, q, nq, passages, fp))
        return {"job_id": job_id, "status": self._jobs[job_id]["status"], "cached": False, "released_item": released}

    def _release(self, course_id: str, item_id: str | None) -> dict | None:
        """Integrity: a Practice question left open to ask something is put back ungraded (the Read-tab rule)."""
        if not item_id:
            return None
        from codecoach.service import ServiceError
        try:
            item = self.svc.store.get_item(item_id)
            obj = self.svc.store.get_objective(item.objective_id) if item else None
            if item is None or obj is None or self.svc.store.course_of_objective(item.objective_id) != course_id:
                return None
            return self.svc.release(item_id).model_dump(mode="json")
        except ServiceError:
            return None

    def _new_job(self, course_id: str, question: str, *, done: dict) -> str:
        with self._lock:
            job_id = self._new_job_locked(course_id, question)
            self._jobs[job_id].update(status="done", stage=ASK_STAGES[-1], result=done)
        return job_id

    def _new_job_locked(self, course_id: str, question: str) -> str:
        job_id = "ask-" + uuid.uuid4().hex[:12]
        self._jobs[job_id] = {"job_id": job_id, "course_id": course_id, "question": question, "status": "queued",
                              "stage": ASK_STAGES[0], "result": None}
        if len(self._jobs) > 200:
            for k in [k for k, j in self._jobs.items() if j["status"] == "done"][:100]:
                del self._jobs[k]
        return job_id

    def job(self, course_id: str, job_id: str) -> dict:
        from codecoach.service import ServiceError
        self.svc._course(course_id)
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None and j["course_id"] == course_id:
                return {"job_id": job_id, "status": j["status"], "stage": j["stage"], "result": j["result"]}
        for e in self._history(course_id):          # a finished ask survives a restart in the history
            if e.get("job_id") == job_id:
                return {"job_id": job_id, "status": "done", "stage": ASK_STAGES[-1], "result": e["result"]}
        raise ServiceError(404, "unknown_job", f"No ask with id {job_id!r}.")

    def _set(self, job_id: str, **kw: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(kw)

    # ------------------------------------------------------------------ the work

    def _run_job(self, job_id: str, course_id: str, question: str, nq: str, passages: list[Passage], fp: str) -> None:
        try:
            self._set(job_id, status="running")
            result = self.answer(course_id, question, passages=passages,
                                 stage=lambda s: self._set(job_id, stage=s))
        except Exception as e:                      # never leave a job running forever
            result = self._error(question, f"Ask hit an unexpected problem ({type(e).__name__}).")
        if result["status"] in ("answered", "not_covered"):
            self._remember(course_id, {"job_id": job_id, "nq": nq, "fp": fp, "question": question, "at": _now(),
                                       "result": result})
        else:
            self._remember(course_id, {"job_id": job_id, "nq": nq, "fp": "", "question": question, "at": _now(),
                                       "result": result})
        self._set(job_id, status="done", stage=ASK_STAGES[-1], result=result)
        with self._lock:
            self._inflight.pop((course_id, nq), None)

    def _index_for(self, course_id: str, passages: list[Passage]) -> R.AskIndex:
        fp = fingerprint(passages)
        with self._lock:
            hit = self._index.get(course_id)
            if hit and hit[0] == fp:
                return hit[1]
        idx = R.build_index(passages)
        with self._lock:
            self._index[course_id] = (fp, idx)
        return idx

    @staticmethod
    def _error(question: str, message: str) -> dict:
        return {"status": "error", "question": question, "message": message, "sentences": [], "removed": [],
                "closest": [], "cached": False, "model_calls": 0, "model_seconds": 0.0}

    @staticmethod
    def _closest(ranked: list[R.Ranked]) -> list[dict]:
        return [{"passage_id": r.passage.id, "doc_id": r.passage.doc_id, "location": r.passage.location,
                 "text": r.passage.text, "label": CLOSEST_LABEL} for r in ranked[:R.CLOSEST]]

    def answer(self, course_id: str, question: str, *, passages: list[Passage] | None = None, stage=None) -> dict:
        """Blocking. Used by the job thread and by the Hermes tool. Never raises on model trouble."""
        say = stage or (lambda s: None)
        course = self.svc._course(course_id)
        why = self.unavailable(course)
        if why:
            return {"status": "unavailable", "reason": why, "question": question, "sentences": [], "removed": [],
                    "closest": [], "cached": False, "model_calls": 0, "model_seconds": 0.0}
        passages = passages if passages is not None else self.svc.store.list_passages(course_id=course_id)
        say(ASK_STAGES[0])
        pasted = A.pasted_text(question)
        if pasted:                                  # the student's own sentence: show the guide's rules, no model
            quotes = A.writing_help(question, pasted, passages)
            if quotes:
                return {"status": "answered", "kind": "writing_help", "question": question, "message": "",
                        "notes": [A.WRITING_LINE], "sentences": quotes, "removed": [], "closest": [],
                        "cached": False, "model_calls": 0, "model_seconds": 0.0}
        ranked, cleared = R.retrieve(self._index_for(course_id, passages), question)
        out = {"status": "not_covered", "question": question, "message": NOT_COVERED_MESSAGE, "sentences": [],
               "removed": [], "closest": self._closest(ranked), "cached": False, "model_calls": 0, "model_seconds": 0.0}
        if pasted:
            return out
        if not cleared or self.svc.llm is None:
            if self.svc.llm is None and cleared:
                return self._error(question, ERROR_UNREACHABLE)
            return out
        removed: list[dict] = []
        plan: list[tuple[int, str]] = [(R.TOP_K, "")]
        while plan:
            top, feedback = plan.pop(0)
            offered = [r.passage for r in ranked[:top]]
            if R.is_why(question):
                offered = with_followers(offered, ranked, passages)
            say(ASK_STAGES[1])
            t0 = time.time()
            try:
                reply = structured_call(self.svc.llm, Reply, instructions=INSTRUCTIONS,
                                        text=prompt_text(question, offered) + feedback, purpose=PURPOSE, retries=2,
                                        timeout=getattr(self.svc.cfg, "llm_timeout_s", None))
            except LLMBadOutput:
                out["model_calls"] += 1
                out["model_seconds"] += round(time.time() - t0, 2)
                return self._error(question, ERROR_BAD_JSON) | {"removed": removed}
            except LLMError:
                out["model_calls"] += 1
                out["model_seconds"] += round(time.time() - t0, 2)
                return self._error(question, ERROR_UNREACHABLE) | {"removed": removed}
            out["model_calls"] += 1
            out["model_seconds"] = round(out["model_seconds"] + time.time() - t0, 2)
            say(ASK_STAGES[2])
            kept, dropped = self._gate(question, reply, {p.id: p for p in offered})
            for v in dropped:
                if v.text not in {r["text"] for r in removed}:
                    removed.append({"text": v.text, "gate": v.gate, "why": v.why})
                    self._log_drop(course_id, question, v)
            if reply.answerable and kept:
                kept = order_by_relevance(question, kept)
                out.update(status="answered", message="", removed=removed, closest=[],
                           sentences=[{"text": v.text, "cites": [c.as_dict() for c in v.cites]} for v in kept])
                quotes = [c.quote for v in kept for c in v.cites]
                notes = [n for n in (A.premise_note(question, [v.text for v in kept], quotes, removed),
                                     A.language_note(question, quotes)) if n]
                if notes:
                    out["notes"] = notes
                return out
            if not reply.answerable:                # "not covered" gets one more look, with more passages
                if top == R.TOP_K:
                    plan.append((R.TOP_RETRY, ""))
            elif dropped and top == R.TOP_K and not plan and not feedback:
                # it tried to answer and the gate removed all of it: one repair try that says why (nothing is
                # trusted from the first reply)
                plan.append((top, "\n\nYour previous sentences were rejected by an automatic check: "
                             + "; ".join(f"\"{v.text[:80]}\" ({v.why})" for v in dropped[:4])
                             + ". Write them again closer to the quote's own words; if a name, number or date is in another part of the "
                             "passage, add a second cite that contains it."))
        out["removed"] = removed
        return out

    @staticmethod
    def _gate(question: str, reply: Reply, offered: dict[str, Passage]) -> tuple[list[Verdict], list[Verdict]]:
        kept: list[Verdict] = []
        dropped: list[Verdict] = []
        seen: set[str] = set()
        for i, s in enumerate(reply.sentences):
            if i >= MAX_SENTENCES:
                dropped.append(Verdict(s.text.strip(), "ask_too_long", f"sentence {i + 1}: at most {MAX_SENTENCES}"))
                continue
            if not s.text.strip():
                continue
            cites = [(c.passage_id, A.strip_location_label(c.quote, offered[c.passage_id.strip()])
                      if (c.passage_id or "").strip() in offered else c.quote) for c in s.cites]
            v = check_sentence(s.text, cites, offered)
            if v.kept:
                parts = A.split_mashup(v, offered)
                if parts:
                    for pv in parts:
                        key = normalize(pv.text)
                        if key not in seen:
                            seen.add(key)
                            kept.append(pv)
                    continue
            key = normalize(v.text)
            if v.kept:
                if key in seen:
                    continue
                seen.add(key)
            (kept if v.kept else dropped).append(v)
        return kept, dropped

    def _log_drop(self, course_id: str, question: str, v: Verdict) -> None:
        try:
            self.svc.store.log_drop(Drop(course_id=course_id, objective_id="ask", format="ask", gate_code=v.gate,
                                         detail=f"{v.text} [{v.why}]"[:600],
                                         draft_hash=hashlib.sha1(question.encode()).hexdigest()[:12]))
        except Exception:
            pass
