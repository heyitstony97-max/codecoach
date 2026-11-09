"""Learn mode: a topic taught in small bites. Hermes-free; CodeCoachService delegates here.

One step = one passage (or two short neighbours), a plain-words explanation that passed Ask's per-sentence quote
gate (or the passage itself when nothing passed), ONE existing bank question whose citations sit inside the step,
then the next step. The contract is LEARN-CONTRACT.md; docs/SPEC.md has the short version.

Nothing here writes attempts, mastery or review state, except the one first-Review seeding when a topic becomes
learned. Progress and explanation caches live in store meta, keyed `...:<course_id>` so deleting a course
deletes them.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from codecoach.adapt import fsrs
from codecoach.adapt.mastery import iso
from codecoach.ask.engine import AskEngine, Reply
from codecoach.core.llm import LLMBadOutput, LLMError, structured_call
from codecoach.core.models import Item, Passage, ReviewState
from codecoach.core.passages import is_quotable
from codecoach.core.public import public_view

PURPOSE = "codecoach.learn"
PROMPT_VERSION = "4"                       # part of the cache key: a better prompt invalidates old results
MAX_STEPS = 6
MERGE_UNDER_WORDS = 120
MIN_STEP_WORDS = 40       # a step thinner than this keeps taking short neighbours (up to MERGE_UNDER_WORDS)
MAX_SUMMARY = 6
MAX_SENTENCES_SHOWN = 4
_BUILD_LOCK = threading.Lock()             # one explanation build at a time in this process (GPU safety)
FIRST_REVIEW_DAYS = 1
FAIL_TTL_S = 300                           # a model failure is not cached for good, but not retried on every poll
CHECK_FORMATS = ("cloze", "mc", "short", "numeric", "order")      # preference order
MAX_RESPONSE_JSON = 100_000

INSTRUCTIONS = (
    "You are a patient tutor. Explain ONE passage from a learner's own study materials in plain words a "
    "14-year-old understands. Reply with JSON: {\"answerable\": true, \"sentences\": [{\"text\": \"...\", "
    "\"cites\": [{\"passage_id\": \"...\", \"quote\": \"...\"}]}]}.\n"
    "Rules:\n"
    "- Write 2 to 4 short sentences (each at most 25 words), each with at least one cite.\n"
    "- passage_id must be exactly the id in square brackets. quote must be copied WORD FOR WORD from the passage: "
    "one sentence or part of one, at least 5 words, never the whole passage.\n"
    "- Say what the quote says in shorter, simpler sentences. Keep the quote's own key words, because they are the "
    "words the learner will meet in the questions. You may split a long sentence in two, drop side details, and "
    "use plain linking words (so, then, this, because), but only if the quote says it.\n"
    "- To explain a hard term, use the passage's own words for it (\"X means ...\" only if the passage says what X "
    "is). Never explain a term from your own knowledge.\n"
    "- Add NOTHING that is not in the quote: no number, date or name the quote lacks, no cause, no example, no "
    "background. A quote must contain every name, number and date its sentence uses.\n"
    "- Keep 'not', 'never' and 'no' exactly as the quote has them. Do not turn a negative into a positive.\n"
    "- One fact per sentence, each with its own quote. Never join facts from different parts into one sentence.\n"
    "- Write complete, grammatical sentences with a subject and a verb. No note-style fragments.\n"
    "- When the passage gives an example (a sample sentence or phrase), say what the example shows and quote the "
    "example in that same sentence. Never write an example sentence on its own as if it were a fact.\n"
    "- If the passage is too thin to explain, reply {\"answerable\": false, \"sentences\": []}."
)

UNAVAILABLE = {"status": "unavailable", "removed": 0, "sentences": []}


def _hash(step: list[Passage]) -> str:
    h = hashlib.sha1(PROMPT_VERSION.encode())
    for p in step:
        h.update(b"\0" + p.id.encode() + b"\1" + p.text.encode())
    return h.hexdigest()[:20]


# A limit the quote states and the sentence leaves out turns a rule into a different rule: the quote "use a comma
# after each term except the last" became "Use a comma after each term in a series" (live Strunk run). The quote
# gate checks that a sentence adds nothing; this checks that it does not drop a limit. Learn only, on top of the gate.
_LIMIT = re.compile(r"\b(?:except|unless|excluding|other than|apart from|but not)\b", re.I)


def dropped_limit(sentence: str, quotes: list[str]) -> bool:
    """True when a cited quote states a limit (except, unless, ...) that the sentence does not carry."""
    said = {m.group(0).lower() for m in _LIMIT.finditer(sentence)}
    return any(m.group(0).lower() not in said for q in quotes for m in _LIMIT.finditer(q))


def _words(text: str) -> int:
    return len(text.split())


def plan_steps(passages: list[Passage]) -> list[list[Passage]]:
    """`passages` are the topic's, in document order. Skips what cannot be quoted, merges short neighbours,
    keeps at most MAX_STEPS. Two neighbours merge when they fit under MERGE_UNDER_WORDS; a third or later one
    joins only while the step is still thin (under MIN_STEP_WORDS). A run of one-line rules or example lines
    (Strunk) otherwise became fragments too thin to explain: half the Strunk steps were 'unavailable'."""
    usable = [p for p in passages if is_quotable(p.text) and not p.formula_missing]
    steps: list[list[Passage]] = []
    cur: list[Passage] = []
    for p in usable:
        size = sum(_words(q.text) for q in cur)
        fits = bool(cur) and p.doc_id == cur[0].doc_id and size + _words(p.text) < MERGE_UNDER_WORDS
        if fits and (len(cur) < 2 or size < MIN_STEP_WORDS):
            cur.append(p)
            continue
        if cur:
            steps.append(cur)
        cur = [p]
    if cur:
        steps.append(cur)
    return steps[:MAX_STEPS]


def _passage_view(p: Passage) -> dict:
    return {"passage_id": p.id, "doc_id": p.doc_id, "location": p.location, "text": p.text}


class LearnCheckRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    item_id: str
    response: Any = None


class LearnStepRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    index: int


class LearnEngine:
    """One per CodeCoachService. Shared state is behind `_lock`; builds run one at a time per process."""

    def __init__(self, svc):
        self.svc = svc
        self._lock = threading.Lock()
        self._queue: deque[tuple[str, str]] = deque()      # (course_id, objective_id), FIFO
        self._draining = False
        self._pending: set[str] = set()                     # step hashes queued or being built
        self._topics_busy: dict[tuple[str, str], int] = {}  # topics with a queued or running build job
        self._failed: dict[str, float] = {}                 # step hash -> time of the last model failure

    # ------------------------------------------------------------------ storage

    @staticmethod
    def _pkey(cid: str) -> str:
        return f"learn:progress:{cid}"

    @staticmethod
    def _ekey(h: str, cid: str) -> str:
        return f"learn:ex:{h}:{cid}"

    def _progress(self, cid: str) -> dict:
        p = self.svc.store.get_meta(self._pkey(cid), {})
        return p if isinstance(p, dict) else {}

    def _save_progress(self, cid: str, p: dict) -> None:
        self.svc.store.set_meta(self._pkey(cid), p)

    # ------------------------------------------------------------------ topics and steps

    def _objectives(self, cid: str) -> list:
        cmap = self.svc.store.get_map(cid)
        if cmap is None:
            return []
        return [o for u in cmap.units for l in u.lessons for o in l.objectives]

    def _objective(self, cid: str, oid: str):
        from codecoach.service import ServiceError
        self.svc._course(cid)
        obj = self.svc.store.get_objective(oid) if isinstance(oid, str) and oid else None
        if obj is None or self.svc.store.course_of_objective(oid) != cid:
            raise ServiceError(404, "unknown_objective", f"No topic with id {oid!r} in this course.")
        return obj

    def _steps(self, cid: str, obj, order: dict[str, int] | None = None) -> list[list[Passage]]:
        if obj.coverage != "covered":
            return []
        if order is None:
            order = {p.id: i for i, p in enumerate(self.svc.store.list_passages(course_id=cid))}
        ps = [p for pid in dict.fromkeys(obj.passage_ids) if (p := self.svc.store.get_passage(pid)) and p.id in order]
        ps.sort(key=lambda p: order[p.id])
        return plan_steps(self._on_topic(obj, ps))

    def _on_topic(self, obj, ps: list[Passage]) -> list[Passage]:
        """The passages Learn teaches for this topic. Coverage gives every paragraph to some topic, so a book's
        preface landed in its first topic: the "possessive singular" lesson opened with the author thanking his
        Cornell colleagues. Only an off-topic run at the START or END of the topic is trimmed (a preface, a
        bibliography); everything between the first and last on-topic passage stays, because a passage can
        belong to a topic without sharing a word with its title ("Crews cut tunnels through solid granite" in
        "How the railroad was built"). On-topic = shares a content word with the topic's statement, or a bank
        item of the topic cites it. Trimmed passages are still in Read and Sources; nothing is deleted."""
        if len(ps) <= 2:
            return ps
        from codecoach.importer.syllabus import content_words
        topic = set(content_words(obj.statement))
        cited = {c.passage_id for i in self.svc.store.list_items(objective_id=obj.id, status="active")
                 for c in (i.citations or [])}
        on = [i for i, p in enumerate(ps) if p.id in cited or topic & set(content_words(p.text))]
        if not on:
            return ps
        keep = ps[on[0]:on[-1] + 1]
        return keep if len(keep) >= 2 else ps

    @staticmethod
    def _status(obj, steps_n: int, prog: dict) -> str:
        if obj.coverage != "covered" or steps_n == 0:
            return "not_in_materials"
        e = prog.get(obj.id) or {}
        if e.get("learned_at"):
            return "learned"
        return "in_progress" if e.get("done") else "new"

    def overview(self, cid: str) -> dict:
        course = self.svc._course(cid)
        order = {p.id: i for i, p in enumerate(self.svc.store.list_passages(course_id=cid))}
        prog = self._progress(cid)
        topics, nxt = [], None
        for o in self._objectives(cid):
            n = len(self._steps(cid, o, order))
            st = self._status(o, n, prog)
            e = prog.get(o.id) or {}
            total = e.get("total") if st != "not_in_materials" else None
            topics.append({"objective_id": o.id, "title": o.statement, "status": st, "steps_total": total,
                           "steps_done": min(len(e.get("done") or []), total or 0)})
            if nxt is None and st in ("new", "in_progress"):
                nxt = o.id
        # outline: the course was built from an outline with no study materials, so a step can only show the
        # outline's own lines ("By the end you can ..."). The screen says so instead of passing that off as a lesson.
        from codecoach.importer.build import OUTLINE_MODE_REASON
        return {"course_id": cid, "next_objective_id": nxt, "topics": topics,
                "outline": bool(course and getattr(course, "mode_reason", "") == OUTLINE_MODE_REASON)}

    def _next_topic(self, cid: str, oid: str) -> str | None:
        objs = self._objectives(cid)
        ids = [o.id for o in objs]
        if oid not in ids:
            return None
        order = {p.id: i for i, p in enumerate(self.svc.store.list_passages(course_id=cid))}
        for o in objs[ids.index(oid) + 1:]:
            if self._steps(cid, o, order):
                return o.id
        return None

    # ------------------------------------------------------------------ check items

    def _check_items(self, obj, steps: list[list[Passage]]) -> list[Item | None]:
        """For each step: an active bank item whose citations all sit inside the step; never one twice."""
        items = [i for i in self.svc.store.list_items(objective_id=obj.id, status="active")
                 if i.format in CHECK_FORMATS and i.citations]
        items.sort(key=lambda i: (CHECK_FORMATS.index(i.format), i.id))
        used: set[str] = set()
        out: list[Item | None] = []
        for step in steps:
            ids = {p.id for p in step}
            pick = next((i for i in items if i.id not in used and all(c.passage_id in ids for c in i.citations)), None)
            if pick is not None:
                used.add(pick.id)
            out.append(pick)
        return out

    # ------------------------------------------------------------------ the topic view

    def topic(self, cid: str, oid: str) -> dict:
        from codecoach.service import ServiceError
        obj = self._objective(cid, oid)
        if obj.coverage == "not_covered":
            raise ServiceError(409, "not_covered", "This topic is not in your materials.")
        steps = self._steps(cid, obj)
        prog = self._progress(cid)
        e = prog.get(oid) or {}
        if steps and e.get("total") != len(steps):         # opened: remember how many steps it has
            prog[oid] = e = {**e, "total": len(steps)}
            self._save_progress(cid, prog)
        if steps:
            self._ensure(cid, oid)
            nxt = self._next_topic(cid, oid)
            if nxt:
                self._ensure(cid, nxt)
        checks = self._check_items(obj, steps)
        views, ready = [], []
        for i, (step, item) in enumerate(zip(steps, checks)):
            ex = self._explain_for(cid, step)
            if ex["status"] == "ready":
                ready.extend(ex["sentences"])
            views.append({"index": i, "passages": [_passage_view(p) for p in step], "explain": ex,
                          "check": {"item": public_view(item).model_dump(mode="json")} if item else None})
        st = self._status(obj, len(steps), prog)
        with self._lock:
            building = self._topics_busy.get((cid, oid), 0) > 0
        return {"objective_id": oid, "title": obj.statement, "status": st,
                "steps_done": min(len(e.get("done") or []), len(steps)), "building": building, "steps": views,
                "summary": ready[:MAX_SUMMARY] if st == "learned" else []}

    # ------------------------------------------------------------------ explanations

    def _cached(self, cid: str, h: str) -> dict | None:
        c = self.svc.store.get_meta(self._ekey(h, cid))
        if isinstance(c, dict) and c.get("status") in ("ready", "unavailable") and isinstance(c.get("sentences"), list):
            return {"status": c["status"], "removed": int(c.get("removed") or 0), "sentences": c["sentences"]}
        return None

    def _explain_for(self, cid: str, step: list[Passage]) -> dict:
        """The explanation of a step as it stands now. Never blocks on the model."""
        h = _hash(step)
        hit = self._cached(cid, h)
        if hit is not None:
            return hit
        if self.svc.llm is None:
            return dict(UNAVAILABLE, sentences=[])
        with self._lock:
            if h in self._pending:
                return {"status": "pending", "removed": 0, "sentences": []}
        return dict(UNAVAILABLE, sentences=[])

    def _ensure(self, cid: str, oid: str) -> None:
        """Queue this topic's missing explanations (lazy: only the topic the learner opened, and the next one)."""
        if self.svc.llm is None:
            return
        obj = self.svc.store.get_objective(oid)
        if obj is None:
            return
        todo = []
        for step in self._steps(cid, obj):
            h = _hash(step)
            if self._cached(cid, h) is not None:
                continue
            with self._lock:
                if h in self._pending or time.time() - self._failed.get(h, -FAIL_TTL_S) < FAIL_TTL_S:
                    continue
                self._pending.add(h)
            todo.append(h)
        if not todo:
            return
        with self._lock:
            self._topics_busy[(cid, oid)] = self._topics_busy.get((cid, oid), 0) + 1
            self._queue.append((cid, oid, tuple(todo)))
            if self._draining:
                return
            self._draining = True
        self.svc._spawn(f"codecoach-learn-{oid}", self._drain)

    def _drain(self) -> None:
        """One worker empties the queue, first in first out; the module lock keeps it to one build per process."""
        while True:
            with self._lock:
                if not self._queue:
                    self._draining = False
                    return
                cid, oid, hashes = self._queue.popleft()
            try:
                with _BUILD_LOCK:
                    self._run_job(cid, oid, set(hashes))
            except Exception:
                pass
            finally:
                with self._lock:
                    self._pending.difference_update(hashes)
                    n = self._topics_busy.get((cid, oid), 1) - 1
                    if n > 0:
                        self._topics_busy[(cid, oid)] = n
                    else:
                        self._topics_busy.pop((cid, oid), None)

    def _run_job(self, cid: str, oid: str, hashes: set[str]) -> None:
        obj = self.svc.store.get_objective(oid)
        if obj is None:
            return
        for step in self._steps(cid, obj):
            h = _hash(step)
            if h not in hashes or self._cached(cid, h) is not None:
                continue
            result, cacheable = self._build_step(step)
            if cacheable:
                self.svc.store.set_meta(self._ekey(h, cid), result)
            else:
                with self._lock:
                    self._failed[h] = time.time()

    @staticmethod
    def prompt_text(step: list[Passage]) -> str:
        rows = ["Passage to explain:"]
        rows += [f"[{p.id}] ({p.location}) {p.text}" for p in step]
        return "\n".join(rows)

    def _build_step(self, step: list[Passage]) -> tuple[dict, bool]:
        """(result, worth caching). A model failure is not cached; a reply the gate emptied is."""
        try:
            reply = structured_call(self.svc.llm, Reply, instructions=INSTRUCTIONS, text=self.prompt_text(step),
                                    purpose=PURPOSE, retries=2, timeout=getattr(self.svc.cfg, "llm_timeout_s", None))
        except (LLMBadOutput, LLMError):
            return dict(UNAVAILABLE, sentences=[]), False
        kept, dropped = AskEngine._gate("", reply, {p.id: p for p in step})   # noqa: SLF001 (Ask's own gate)
        if not reply.answerable:
            return dict(UNAVAILABLE, sentences=[]), True
        lost = [v for v in kept if dropped_limit(v.text, [c.quote for c in v.cites])]
        kept = [v for v in kept if v not in lost]
        dropped = list(dropped) + lost
        extra = max(0, len(kept) - MAX_SENTENCES_SHOWN)
        kept = kept[:MAX_SENTENCES_SHOWN]
        removed = len(dropped) + extra
        if not kept:
            return {"status": "unavailable", "removed": removed, "sentences": []}, True
        return {"status": "ready", "removed": removed,
                "sentences": [{"text": v.text, "cites": [c.as_dict() for c in v.cites]} for v in kept]}, True

    # ------------------------------------------------------------------ check (no state written)

    def check(self, cid: str, oid: str, item_id: str, response: Any) -> dict:
        from codecoach.service import ServiceError
        self._objective(cid, oid)
        item = self.svc._active_item(item_id)
        if item.objective_id != oid:
            raise ServiceError(422, "wrong_topic", "That question belongs to a different topic.")
        try:
            if len(json.dumps(response, default=str)) > MAX_RESPONSE_JSON:
                raise ServiceError(413, "too_large", "That answer is too long.")
        except (TypeError, ValueError):
            raise ServiceError(422, "bad_request", "That answer is not valid JSON.") from None
        with self.svc._exclusive(item_id):
            res = self.svc._grade(item, response)
        if res.undecided:
            raise ServiceError(503, "undecided", res.detail or "The checker could not decide. Try again; nothing was recorded.")
        correct = None if item.format == "explain" else bool(res.ok)
        out = self.svc._answer_view(item, res, correct, None, None)
        return {**out.model_dump(mode="json"), "learn": True}

    # ------------------------------------------------------------------ step done -> progress -> first Review

    def step(self, cid: str, oid: str, index: Any) -> dict:
        from codecoach.service import ServiceError
        obj = self._objective(cid, oid)
        steps = self._steps(cid, obj)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(steps):
            raise ServiceError(422, "bad_step", "That step does not exist in this topic.")
        with self._lock:
            prog = self._progress(cid)
            e = dict(prog.get(oid) or {})
            done = sorted(set(e.get("done") or []) | set(range(index + 1)))
            e.update(done=done, total=len(steps))
            if index == len(steps) - 1:
                e.setdefault("learned_at", iso(self.svc.now()))
            prog[oid] = e
            self._save_progress(cid, prog)
        if index == len(steps) - 1:
            self._seed_first_review(oid)
        return {"steps_done": len(done), "status": self._status(obj, len(steps), prog)}

    def _seed_first_review(self, oid: str) -> None:
        """Items of a learned topic that have no review state yet come up for their first Review in about a day.
        Existing review state is never touched."""
        store, now = self.svc.store, self.svc.now()
        for it in store.list_items(objective_id=oid, status="active"):
            if store.get_review_state(it.id) is None:
                store.put_review_state(ReviewState(item_id=it.id, due=now + timedelta(days=FIRST_REVIEW_DAYS),
                                                   stability=fsrs.init_stability(fsrs.GOOD),
                                                   difficulty=fsrs.init_difficulty(fsrs.GOOD), reps=0, lapses=0,
                                                   last_review=None))
