"""Model fit check (V11-CONTRACT section 5).

One check = five structured calls, one at a time under codecoach.project._BUILD_LOCK, no retries (a retry would
hide weakness). Each answer runs on its three tests in the sandbox; a task passes only when all three match
after the runner's normal stdout normalization. Records live in store meta:

    fit:<language>:<provider>/<model>   one per language and model
    fit:latest:<language>               the newest record for that language

Only the fixed task text, the language name and its style note ever go to the model. Learner code never does.
"""

from __future__ import annotations

import statistics
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel

from codecoach.check.code_runner import _normalize_stdout, run_snippet, sandbox_status
from codecoach.core.llm import LLMError, LLMUnavailable, structured_call
from codecoach.fit.tasks import WARMUPS, Warmup
from codecoach.project import _BUILD_LOCK

FIT_PURPOSE = "codecoach.fit"
TIME_LIMIT_S = 5.0                      # wall clock per test run
MAX_CODE_CHARS = 20000

NO_ANSWER = "The model gave no answer."
NO_COMPILE = "Its program did not compile."
WRONG = "Its program printed the wrong output."
SLOW = "Its program was too slow."
CRASHED = "Its program crashed while running."

SWITCH = {"commands": ["hermes config set auxiliary.codecoach.provider <provider>",
                       "hermes config set auxiliary.codecoach.model <model>"],
          "note": "Then press Check again."}

INSTRUCTIONS = ("You write one small complete program for a fixed exercise. Reply with a JSON object with one "
                "field, code, holding the whole program as plain source text. The program reads standard "
                "input and prints to standard output. It prints exactly what the task asks for and nothing else: "
                "no prompts, no labels, no extra lines. No explanation, no markdown fences.")


class FitReply(BaseModel):
    code: str = ""


# ------------------------------------------------------------------ the language seam (one function)

def language_table() -> dict[str, tuple[str, str]]:
    """{language id: (display name, style note)} for every language that can be checked, in display order.
    Uses codecoach.lang (ready_ids, spec) when it imports; until then it knows c and python."""
    try:
        from codecoach.lang import ready_ids, spec
        return {i: (spec(i).name, spec(i).style_note) for i in ready_ids()}
    except ImportError:
        return {"c": ("C", "Plain C11, standard library only."),
                "python": ("Python", "Python 3, standard library only. Read input with input().")}


# ------------------------------------------------------------------ small pure helpers

def verdict_for(passed: int) -> str:
    return "good" if passed >= 4 else "mixed" if passed == 3 else "poor"


def advice_for(verdict: str, passed: int, total: int, name: str) -> str:
    head = f"This model passed {passed} of {total} {name} warm-ups."
    if verdict == "good":
        return f"{head} It should teach {name} well."
    if verdict == "mixed":
        return (f"{head} Some {name} lessons may fail to build or take a while. "
                "You can point CodeCoach at another model and check again.")
    if verdict == "poor":
        return (f"{head} Lessons in {name} may fail to build or take long. "
                "You can point CodeCoach at another model and check again.")
    return (f"CodeCoach could not reach the model, so {name} was not checked. "
            "Check the model connection and press Check again.")


def prompt_for(task: Warmup, name: str, style_note: str) -> str:
    """The whole prompt: the task text, the language name and its style note. Nothing else."""
    return f"Language: {name}\nStyle: {style_note}\n\nTask: {task.text}"


def _strip_fence(code: str) -> str:
    """Models sometimes wrap the program in one markdown fence despite being told not to; take it off."""
    s = code.strip()
    if s.startswith("```") and s.endswith("```") and len(s) > 6:
        body = s[3:-3]
        first, nl, rest = body.partition("\n")
        s = rest if nl and (not first.strip() or first.strip().isalnum()) else body
    return s.strip("\n") + "\n" if s.strip() else ""


def grade_task(language: str, task: Warmup, code: str) -> str | None:
    """None when all three tests match, else the plain reason."""
    if not code.strip() or len(code) > MAX_CODE_CHARS:
        return NO_ANSWER if not code.strip() else WRONG
    for t in task.tests:
        r = run_snippet(language, code, t.stdin, time_limit_s=TIME_LIMIT_S)
        if r is None:
            return CRASHED
        if r.compile_error:
            return NO_COMPILE
        if r.timed_out:
            return SLOW
        if r.returncode != 0:
            return CRASHED
        if _normalize_stdout(r.stdout) != _normalize_stdout(t.expected_stdout):
            return WRONG
    return None


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _model_key(route: dict | None) -> str:
    return f"{route['provider']}/{route['model']}" if route else "unknown"


def _same_model(a: dict | None, b: dict | None) -> bool:
    return bool(a) and bool(b) and a.get("provider") == b.get("provider") and a.get("model") == b.get("model")


# ------------------------------------------------------------------ the engine

class FitEngine:
    """One per CodeCoachService (engine_for). One check runs at a time across languages."""

    def __init__(self, svc: Any, *, now_fn: Callable[[], datetime] | None = None):
        self.svc = svc
        self._now = now_fn or svc.now_fn
        self._lock = threading.Lock()
        self._running: dict | None = None       # {"language", "tasks": [done task dicts], "model": route|None}

    # -------------------------------------------------------- storage

    @staticmethod
    def record_key(language: str, route: dict | None) -> str:
        return f"fit:{language}:{_model_key(route)}"

    @staticmethod
    def latest_key(language: str) -> str:
        return f"fit:latest:{language}"

    def record(self, language: str, route: dict | None) -> dict | None:
        return self.svc.store.get_meta(self.record_key(language, route))

    def latest(self, language: str) -> dict | None:
        return self.svc.store.get_meta(self.latest_key(language))

    def current_model(self) -> dict | None:
        fn = getattr(self.svc.llm, "last_route", None)
        try:
            r = fn() if callable(fn) else None
        except Exception:
            r = None
        return r if isinstance(r, dict) and r.get("provider") and r.get("model") else None

    def last_model(self) -> dict | None:
        """The last model that answered; before any call in this process, the model of the newest record."""
        cur = self.current_model()
        if cur:
            return cur
        newest = None
        for lang in language_table():
            rec = self.latest(lang)
            if rec and rec.get("model") and (newest is None or rec.get("measured_at", "") > newest.get("measured_at", "")):
                newest = rec
        return dict(newest["model"]) if newest else None

    # -------------------------------------------------------- views

    def _name(self, language: str) -> str:
        return language_table().get(language, (language, ""))[0]

    def view(self, language: str) -> dict:
        """The Fit shape for one language."""
        with self._lock:
            run = dict(self._running) if self._running and self._running["language"] == language else None
            if run:
                run["tasks"] = list(run["tasks"])
        total = len(WARMUPS)
        if run:
            done = run["tasks"]
            return {"language": language, "status": "running", "verdict": "unknown",
                    "passed": sum(1 for t in done if t["ok"]), "total": total, "done_tasks": len(done),
                    "tasks": done, "model": run["model"], "median_ms": None, "measured_at": None,
                    "stale": False, "advice": None}
        rec = self.latest(language)
        if not rec:
            return {"language": language, "status": "none", "verdict": "unknown", "passed": 0, "total": total,
                    "done_tasks": 0, "tasks": [], "model": None, "median_ms": None, "measured_at": None,
                    "stale": False, "advice": None}
        out = dict(rec)
        out["stale"] = self.is_stale(rec)
        return out

    def is_stale(self, rec: dict) -> bool:
        cur = self.current_model()
        return bool(cur) and not _same_model(cur, rec.get("model"))

    def overview(self) -> dict:
        """The GET /fit shape."""
        langs: dict[str, dict] = {}
        for lang in language_table():
            v = self.view(lang)
            if v["status"] != "none":
                langs[lang] = v
        return {"model": self.last_model(), "languages": langs,
                "switch": {"commands": list(SWITCH["commands"]), "note": SWITCH["note"]}}

    # -------------------------------------------------------- starting a check

    def is_running(self) -> bool:
        with self._lock:
            return self._running is not None

    def start(self, language: str) -> dict:
        """Start a check in the background. ServiceError 422 bad_language, 503 sandbox_unavailable, 409 fit_running."""
        from codecoach.service import ServiceError
        if language not in language_table():
            raise ServiceError(422, "bad_language", "That language is not set up on this machine yet.")
        if sandbox_status() != "bwrap":
            raise ServiceError(503, "sandbox_unavailable", "Install bubblewrap (bwrap) to check how well the model writes code.")
        with self._lock:
            if self._running is not None:
                raise ServiceError(409, "fit_running", "A model check is already running. Wait for it to finish.")
            self._running = {"language": language, "tasks": [], "model": self.current_model()}
        self.svc._spawn(f"codecoach-fit-{language}", lambda: self._job(language))
        return {"status": "running"}

    def ensure(self, language: str) -> bool:
        """Start a check when there is no record for the current model. Never blocks. True when one started."""
        from codecoach.service import ServiceError
        if language not in language_table():
            return False
        cur = self.current_model()
        have = self.record(language, cur) if cur else self.latest(language)
        if have is not None:
            return False
        try:
            self.start(language)
        except ServiceError:
            return False
        return True

    # -------------------------------------------------------- the check itself

    def _job(self, language: str) -> None:
        try:
            self._check(language)
        except Exception:
            # Never leave the engine stuck on 'running'. Whatever was done is dropped; the learner can try again.
            pass
        finally:
            with self._lock:
                self._running = None

    def _check(self, language: str) -> None:
        name, note = language_table()[language]
        tasks: list[dict] = []
        times: list[int] = []
        unreachable = False
        for w in WARMUPS:
            if unreachable:
                item = {"id": w.id, "title": w.title, "ok": False, "reason": NO_ANSWER}
            else:
                item = self._one(language, name, note, w, times)
                unreachable = item.pop("_unreachable", False)
            tasks.append(item)
            with self._lock:
                if self._running is not None:
                    self._running["tasks"] = list(tasks)
                    self._running["model"] = self.current_model() or self._running["model"]
        passed = sum(1 for t in tasks if t["ok"])
        route = self.current_model()
        verdict = "unknown" if unreachable else verdict_for(passed)
        rec = {"language": language, "status": "error" if unreachable else "done", "verdict": verdict,
               "passed": passed, "total": len(WARMUPS), "done_tasks": len(WARMUPS), "tasks": tasks,
               "model": route, "median_ms": int(statistics.median(times)) if times else None,
               "measured_at": _ts(self._now()), "advice": advice_for(verdict, passed, len(WARMUPS), name)}
        self.svc.store.set_meta(self.record_key(language, route), rec)
        self.svc.store.set_meta(self.latest_key(language), rec)

    def _one(self, language: str, name: str, note: str, w: Warmup, times: list[int]) -> dict:
        item: dict[str, Any] = {"id": w.id, "title": w.title, "ok": False, "reason": NO_ANSWER}
        try:
            with _BUILD_LOCK:
                t0 = time.monotonic()
                reply = structured_call(self.svc.llm, FitReply, instructions=INSTRUCTIONS,
                                        text=prompt_for(w, name, note), purpose=FIT_PURPOSE, retries=1)
                times.append(int((time.monotonic() - t0) * 1000))
        except LLMUnavailable:
            item["_unreachable"] = True
            return item
        except LLMError:
            return item                       # the model answered with something unusable: this task fails
        code = _strip_fence(reply.code)
        reason = grade_task(language, w, code)
        item["ok"], item["reason"] = reason is None, reason
        return item


_ENGINE_LOCK = threading.Lock()


def engine_for(svc: Any) -> FitEngine:
    """The FitEngine for this service, cached on the service object (no edit to service.py)."""
    with _ENGINE_LOCK:
        eng = getattr(svc, "_fit_engine", None)
        if eng is None:
            eng = svc._fit_engine = FitEngine(svc)
        return eng
