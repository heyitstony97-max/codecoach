"""Test (checkpoint), a fixed set of predict-the-output drills with no feedback until the end.

Contract: section 6 of docs/V11-CONTRACT.md (the Test half). A test claims drills by tagging them with `test_id`;
while the tag is there the practice engine never serves them, never grades them and never shows a key. `finish`
grades the saved predictions in code, removes the tag, and only then do the drills open up to the practice routes.

Storage is store meta, JSON:
    tests:index   list of test ids, oldest first
    test:<id>     {id, language, size, status open|done, slots:[{drill_id, topic}], answers:{drill_id: text},
                   note, result, created_at}        answers and the result never leave through GET /tests/{id}
                                                    before finish
"""

from __future__ import annotations

import secrets
import threading
from typing import Any

from pydantic import BaseModel

from codecoach.adapt.mastery import iso
from codecoach.check.code_runner import sandbox_status
from codecoach.practice.engine import (MAX_PREDICTION_CHARS, TOPIC_IDS, TOPICS, PracticeEngine, _languages,
                                       engine_for, grade_prediction)

MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 3, 10, 5
NO_ANSWER = "No answer"
MAX_ATTEMPTS_LOG = 1000


class CheckpointCreateRequest(BaseModel):
    language: str = ""
    size: int = DEFAULT_SIZE


class CheckpointAnswerRequest(BaseModel):
    n: int = 0
    prediction: str = ""


def _service_error(status: int, code: str, message: str):
    from codecoach.service import ServiceError
    return ServiceError(status, code, message)


class CheckpointEngine:
    """One per service (see checkpoint_engine_for). Wraps the practice engine; drill builds queue on the shared lock."""

    def __init__(self, svc, eng: PracticeEngine):
        self.svc = svc
        self.eng = eng
        self._lock = threading.RLock()
        self._running: set[str] = set()               # test ids whose builder thread is alive in this process

    # ------------------------------------------------------------ storage

    def _index(self) -> list[str]:
        ix = self.svc.store.get_meta("tests:index", [])
        return [x for x in ix if isinstance(x, str)] if isinstance(ix, list) else []

    def _get(self, tid: Any) -> dict:
        t = None
        if isinstance(tid, str) and tid and ":" not in tid:
            t = self.svc.store.get_meta(f"test:{tid}")
        if not isinstance(t, dict):
            raise _service_error(404, "unknown_test", "That test does not exist.")
        return t

    def _put(self, t: dict) -> None:
        self.svc.store.set_meta(f"test:{t['id']}", t)

    # ------------------------------------------------------------ create

    @staticmethod
    def _topic_plan(size: int, has_stored: set[str]) -> list[str]:
        """`size` topics spread evenly (counts differ by at most one). Topics that already have an unseen stored drill
        come first, so those questions need no build."""
        order = sorted(TOPIC_IDS, key=lambda t: (t not in has_stored, TOPIC_IDS.index(t)))
        return [order[i % len(order)] for i in range(size)]

    def create(self, language: Any, size: Any) -> dict:
        lang = self.eng._lang(language, _languages())
        if isinstance(size, bool) or not isinstance(size, int) or not MIN_SIZE <= size <= MAX_SIZE:
            raise _service_error(422, "bad_size", f"Pick a test of {MIN_SIZE} to {MAX_SIZE} questions.")
        if sandbox_status() != "bwrap":
            raise _service_error(503, "sandbox_unavailable",
                                 "Drills run a program in a sandbox. Install bubblewrap (bwrap) to use Test.")
        eng = self.eng
        with self._lock, eng._lock:
            stored: dict[str, list[dict]] = {}
            for r in eng._records(lang):
                if r.get("status") == "ready" and not r.get("answered") and r.get("test_id") is None:
                    stored.setdefault(r["topic"], []).append(r)
            plan = self._topic_plan(size, set(stored))
            need_build = sum(1 for t in plan if not stored.get(t))   # an upper bound, enough to refuse with no model
            if need_build and self.svc.llm is None:
                raise _service_error(503, "no_model", "No model is connected, so CodeCoach cannot write the drills.")
            taken = set(self._index())
            tid = "t_" + secrets.token_hex(2)
            while tid in taken:
                tid = "t_" + secrets.token_hex(2)
            slots = []
            for topic in plan:
                pool = stored.get(topic) or []
                if pool:
                    rec = pool.pop(0)
                    eng._save(dict(rec, test_id=tid))
                else:
                    rec = eng._placeholder(lang, topic, test_id=tid)
                slots.append({"drill_id": rec["id"], "topic": topic})
            t = {"id": tid, "language": lang, "size": size, "status": "open", "slots": slots, "answers": {},
                 "note": None, "result": None, "created_at": iso(self.svc.now())}
            self.svc.store.set_meta("tests:index", self._index() + [tid])
            self._put(t)
            to_build = [s["drill_id"] for s in slots if (eng.record(s["drill_id"]) or {}).get("status") == "building"]
            if to_build:
                self._running.add(tid)
        if to_build:
            self.svc._spawn(f"codecoach-test-{tid}", lambda: self._run(tid, to_build))
        cur = self._view_status(self._get(tid))
        return {"test_id": tid, "status": cur}

    # ------------------------------------------------------------ the background builder

    def _run(self, tid: str, dids: list[str]) -> None:
        """Build the missing drills one at a time. A drill that comes out unavailable is replaced once; a second
        failure shrinks the test and says so."""
        try:
            for did in dids:
                self._build_slot(tid, did)
        except Exception:
            pass
        finally:
            with self._lock:
                self._running.discard(tid)
                try:
                    self._prune(self._get(tid))      # anything left unbuilt after a crash is dropped, not waited on
                except Exception:
                    pass

    def _build_slot(self, tid: str, did: str) -> None:
        eng = self.eng
        eng._job(did, prefetch=False)
        rec = eng.record(did) or {}
        if rec.get("status") == "ready":
            return
        with self._lock, eng._lock:
            t = self._get(tid)
            slot = next((s for s in t["slots"] if s["drill_id"] == did), None)
            if slot is None:
                return
            new = eng._placeholder(t["language"], slot["topic"], test_id=tid)
            slot["drill_id"] = new["id"]
            self._put(t)
        eng._job(new["id"], prefetch=False)
        rec2 = eng.record(new["id"]) or {}
        if rec2.get("status") != "ready":
            with self._lock:
                t = self._get(tid)
                self._drop(t, new["id"])
                self._put(t)

    def _drop(self, t: dict, did: str) -> None:
        before = len(t["slots"])
        t["slots"] = [s for s in t["slots"] if s["drill_id"] != did]
        if len(t["slots"]) < before:
            lost = t["size"] - len(t["slots"])
            t["size"] = len(t["slots"])
            t["note"] = (f"{lost} question{'s' if lost != 1 else ''} could not be written, so this test has "
                         f"{t['size']} question{'s' if t['size'] != 1 else ''} instead of {t['size'] + lost}.")

    def _prune(self, t: dict) -> dict:
        """On an open test that nothing is building any more, drop drills that are not ready. Caller holds _lock."""
        if t["status"] != "open" or t["id"] in self._running:
            return t
        changed = False
        for s in list(t["slots"]):
            r = self.eng.record(s["drill_id"])
            r = self.eng._settle(r) if r else None
            if r is None or r.get("status") != "ready":
                self._drop(t, s["drill_id"])
                changed = True
        if changed:
            self._put(t)
        return t

    # ------------------------------------------------------------ views

    def _statuses(self, t: dict) -> list[str]:
        out = []
        for s in t["slots"]:
            r = self.eng.record(s["drill_id"])
            out.append((self.eng._settle(r) if r else {}).get("status", "unavailable"))
        return out

    def _view_status(self, t: dict) -> str:
        if t["status"] == "done":
            return "done"
        if t["id"] in self._running or any(s == "building" for s in self._statuses(t)):
            return "building"
        return "ready"

    def get(self, tid: Any) -> dict:
        with self._lock:
            t = self._prune(self._get(tid))
            sts = self._statuses(t)
            out = {"id": t["id"], "language": t["language"], "status": self._view_status(t),
                   "ready_count": sum(1 for s in sts if s == "ready"), "size": len(t["slots"]),
                   "questions": [{"n": i + 1, "drill_id": s["drill_id"], "topic": s["topic"],
                                  "answered": s["drill_id"] in t["answers"]} for i, s in enumerate(t["slots"])],
                   "result": t["result"]}
            if t["note"]:                              # only when the test shrank: the contract shape is otherwise exact
                out["note"] = t["note"]
            return out

    def list(self) -> dict:
        rows = []
        with self._lock:
            for tid in reversed(self._index()):
                try:
                    t = self._prune(self._get(tid))
                except Exception:
                    continue
                res = t.get("result")
                rows.append({"id": t["id"], "language": t["language"], "status": self._view_status(t),
                             "score": res["score"] if res else None, "total": res["total"] if res else len(t["slots"]),
                             "created_at": t["created_at"]})
        return {"tests": rows}

    # ------------------------------------------------------------ answer and finish

    def answer(self, tid: Any, n: Any, prediction: Any) -> dict:
        if isinstance(n, bool) or not isinstance(n, int):
            raise _service_error(422, "bad_question", "Pick a question number from the test.")
        if not isinstance(prediction, str):
            raise _service_error(422, "bad_request", "Send your prediction as text.")
        if len(prediction) > MAX_PREDICTION_CHARS:
            raise _service_error(413, "too_large", "That answer is too long.")
        with self._lock:
            t = self._prune(self._get(tid))
            if t["status"] == "done":
                raise _service_error(409, "test_done", "This test is finished, so answers can no longer change.")
            if not 1 <= n <= len(t["slots"]):
                raise _service_error(422, "bad_question", "Pick a question number from the test.")
            did = t["slots"][n - 1]["drill_id"]
            r = self.eng.record(did) or {}
            if r.get("status") != "ready":
                raise _service_error(409, "drill_not_ready", "This question is not ready yet.")
            t["answers"][did] = prediction
            self._put(t)
        return {"recorded": True}

    def finish(self, tid: Any) -> dict:
        eng = self.eng
        with self._lock:
            t = self._prune(self._get(tid))
            if t["status"] == "done":
                return t["result"]
            if self._view_status(t) == "building":
                raise _service_error(409, "test_not_ready", "Some questions are still being written. "
                                                            "Finish once they are all ready.")
            titles = dict(TOPICS)
            review, per = [], {}
            for i, s in enumerate(t["slots"]):
                rec = eng.record(s["drill_id"]) or {}
                pred = t["answers"].get(s["drill_id"])
                blank = pred is None or not pred.strip()
                correct = False if pred is None or blank else grade_prediction(rec.get("stdout", ""), pred)["correct"]
                row = per.setdefault(s["topic"], [0, 0])
                row[1] += 1
                row[0] += 1 if correct else 0
                review.append({"n": i + 1, "topic": s["topic"], "correct": correct, "code": rec.get("code", ""),
                               "your_answer": NO_ANSWER if blank else pred,
                               "expected_stdout": rec.get("stdout", ""), "explanation": list(rec.get("explanation") or [])})
            by_topic = [{"topic": tp, "title": titles[tp], "correct": per[tp][0], "total": per[tp][1]}
                        for tp in TOPIC_IDS if tp in per]
            result = {"score": sum(r["correct"] for r in review), "total": len(review), "by_topic": by_topic,
                      "weak_topics": [b["topic"] for b in by_topic if b["correct"] * 2 < b["total"]],
                      "review": review}
            self._open_drills(t, review)
            t["status"], t["result"] = "done", result
            self._put(t)
            return result

    def _open_drills(self, t: dict, review: list[dict]) -> None:
        """Remove the hold on every drill of the test. The learner has now seen each key, so each counts as answered
        (first answer = the test answer; no answer counts as wrong)."""
        eng = self.eng
        with eng._lock:
            log = self.svc.store.get_meta("practice:attempts", [])
            log = log if isinstance(log, list) else []
            for s, r in zip(t["slots"], review):
                cur = eng.record(s["drill_id"])
                if not cur:
                    continue
                answered, first = cur.get("answered"), cur.get("first_correct")
                if not answered:
                    answered, first = True, r["correct"]
                eng._save(dict(cur, test_id=None, answered=answered, first_correct=first))
                if r["your_answer"] != NO_ANSWER:
                    log.append({"drill_id": cur["id"], "language": cur["language"], "topic": cur["topic"],
                                "correct": r["correct"], "at": iso(self.svc.now())})
            self.svc.store.set_meta("practice:attempts", log[-MAX_ATTEMPTS_LOG:])


def checkpoint_engine_for(svc) -> CheckpointEngine:
    """The test engine cached on the service object (no edit to service.py)."""
    practice = engine_for(svc)                 # takes svc._lock itself (not reentrant), so before we take it
    with svc._lock:
        eng = getattr(svc, "_test_engine", None)
        if eng is None:
            eng = svc._test_engine = CheckpointEngine(svc, practice)
        return eng
