"""Practice, predict-the-output drills. Hermes-free; the API module delegates here.

The model writes a short program for (language, topic). The sandbox runs it. The REAL stdout is the answer key; the
learner types what they think it prints and CODE compares. The model never states an output. Contract: section 6 of
docs/V11-CONTRACT.md (the Drill half; the Test half is a later card and reuses `view`, `grade_prediction` and the
drill records below).

Gates, in order (a failure gives one rebuild, then the drill is `unavailable` with a plain reason):
    P1 runs in the sandbox, exit 0, inside the limits      P2 two runs print identical stdout
    P3 1-8 output lines, each <= 80 characters, not blank   P4 at most 25 non-blank lines of code
    P5 some output line is not verbatim in the source       P6 not a copy of a stored drill (same language, topic)
    P7 the view sent before an answer carries no stdout and no explanation (`view` copies a whitelist; test-enforced)

Storage is store meta, JSON, no migration:
    practice:index        list of drill ids, oldest first
    drill:<id>            the drill INCLUDING server-only fields (stdout, explanation, hash)
    practice:attempts     the answer log, newest last (capped); counts come from the drill records
"""

from __future__ import annotations

import re
import secrets
import threading
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, field_validator

from codecoach.adapt.mastery import iso
from codecoach.check.code_runner import run_snippet, sandbox_status
from codecoach.core.llm import LLMBadOutput, LLMError, LLMUnavailable, structured_call
from codecoach.practice.explain_check import real_line_breaks, shown_explanation
from codecoach.project import _BUILD_LOCK

DRILL_PURPOSE = "codecoach.practice.drill"
PRACTICE_PROMPT_VERSION = "1"
KIND = "predict"
TOPICS = [("variables", "Variables"), ("arithmetic", "Arithmetic"), ("strings", "Strings"),
          ("conditionals", "If and else"), ("loops", "Loops"), ("functions", "Functions"),
          ("lists", "Lists and arrays"), ("tracing", "Mixed tracing")]
TOPIC_IDS = [t for t, _ in TOPICS]
MAX_OUT_LINES, MAX_LINE_CHARS, MAX_CODE_LINES = 8, 80, 25
MAX_CODE_CHARS, MAX_STDIN_CHARS, MAX_PREDICTION_CHARS = 8000, 500, 4000
MAX_ROWS = 50
MAX_ATTEMPTS_LOG = 1000
BUILD_TRIES = 2                                     # the first build and ONE rebuild
EARLIER_SHOWN = 3                                   # earlier drills quoted to the model so it writes something else


# ------------------------------------------------------------------ the language seam (one function, one line to merge)

def _languages() -> dict[str, tuple[str, str]]:
    """{ready language id: (display name, one-line style note)}. Uses codecoach.lang when that package exists."""
    try:
        from codecoach.lang import ready_ids, spec
    except ImportError:
        return {"c": ("C", "Plain C11, standard library only."), "python": ("Python", "Python 3, standard library only.")}
    out = {}
    for lid in ready_ids():
        s = spec(lid)
        out[lid] = (s.name, s.style_note)
    return out


# ------------------------------------------------------------------ the model's reply and the prompt

class DrillReply(BaseModel):
    code: str
    stdin: str = ""
    explanation: list[str] = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("code must not be empty")
        if len(v) > MAX_CODE_CHARS:
            raise ValueError("code is too long")
        return v

    @field_validator("stdin")
    @classmethod
    def _stdin(cls, v: str) -> str:
        if len(v) > MAX_STDIN_CHARS:
            raise ValueError("stdin is too long")
        return v

    @field_validator("explanation")
    @classmethod
    def _expl(cls, v: list[str]) -> list[str]:
        if not 2 <= len(v) <= 5 or not all(s.strip() for s in v):
            raise ValueError("explanation must be 2 to 5 non-empty sentences")
        return v


DRILL_INSTRUCTIONS = (
    "You write ONE short program for a 'predict the output' drill. The learner reads it, types what they think it "
    "prints, and CodeCoach compares that with what the program really prints. Reply with JSON: {\"code\": \"the whole "
    "program\", \"stdin\": \"\", \"explanation\": [\"sentence\", ...]}.\n"
    "Rules:\n"
    "- The program is complete, runs, and exits with status 0. Standard library only. Beginner level; use only the "
    "topic you are given.\n"
    "- At most 25 non-blank lines. It prints 1 to 8 lines, each at most 80 characters.\n"
    "- Every run must print exactly the same thing: no random numbers, no clock or time, no process ids, no memory "
    "addresses, no files, no network. stdin is normally empty; if the program reads input, put that input in stdin.\n"
    "- The learner must have to WORK OUT the output: do not print text that already appears word for word in the "
    "code. Print computed values.\n"
    "- Write no comments at all. Do not write the output anywhere.\n"
    "- explanation: 2 to 5 short plain sentences that walk through why the program prints what it prints. The learner "
    "sees them only after answering."
)


def build_prompt(name: str, style: str, topic: str, title: str, earlier: list[str], feedback: str = "") -> str:
    rows = [f"Language: {name}", f"Style: {style}", f"Topic: {title} ({topic})"]
    if earlier:
        rows.append("Earlier drills on this topic. Write something clearly different:\n" +
                    "\n---\n".join(e[:300] for e in earlier))
    if feedback:
        rows.append(f"Your previous attempt was rejected: {feedback}\nWrite a new drill and fix that.")
    return "\n\n".join(rows)


# ------------------------------------------------------------------ pure helpers (testable alone)

def norm_lines(text: str) -> list[str]:
    """Lines of `text` as graded: trailing spaces stripped per line, trailing blank lines dropped. Nothing else."""
    lines = [ln.rstrip(" ") for ln in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def grade_prediction(stdout: str, prediction: str) -> dict:
    """{correct, lines}: per-line expected/got. Rows are capped; `correct` never depends on the cap."""
    exp, got = norm_lines(stdout), norm_lines(prediction)
    rows = []
    for i in range(min(max(len(exp), len(got)), MAX_ROWS)):
        e = exp[i] if i < len(exp) else ""
        g = got[i] if i < len(got) else ""
        rows.append({"n": i + 1, "expected": e, "got": g, "ok": i < len(exp) and i < len(got) and e == g})
    return {"correct": exp == got, "lines": rows}


def code_hash(code: str) -> str:
    return sha256(" ".join(code.split()).encode("utf-8")).hexdigest()[:24]


def nonblank_count(code: str) -> int:
    return sum(1 for ln in code.splitlines() if ln.strip())


class GateFail(Exception):
    def __init__(self, gate: str, reason: str, detail: str = ""):
        super().__init__(f"{gate}: {reason}")
        self.gate, self.reason, self.detail = gate, reason, detail   # detail goes back to the model, never to the learner


class SandboxGone(Exception):
    pass


def gate_runs(language: str, reply: DrillReply):
    """P1 and P2. Returns the real stdout."""
    a = run_snippet(language, reply.code, reply.stdin)
    if a is None:
        raise SandboxGone()
    if a.compile_error:
        raise GateFail("P1", "The program did not compile.", a.compile_error.splitlines()[0][:200])
    if a.timed_out:
        raise GateFail("P1", "The program ran too long.")
    if not a.ok:
        raise GateFail("P1", "The program crashed or exited with an error.")
    b = run_snippet(language, reply.code, reply.stdin)
    if b is None:
        raise SandboxGone()
    if not b.ok or b.stdout != a.stdout:
        raise GateFail("P2", "The program printed different output on two runs. The output must be the same every "
                             "time: no random values, times, process ids or memory addresses.")
    return a.stdout


def gate_output_shape(stdout: str) -> None:
    """P3."""
    lines = norm_lines(stdout)
    if not lines or all(not ln.strip() for ln in lines):
        raise GateFail("P3", "The program printed nothing to predict.")
    if len(lines) > MAX_OUT_LINES:
        raise GateFail("P3", f"The program printed {len(lines)} lines; at most {MAX_OUT_LINES} are allowed.")
    if any(len(ln) > MAX_LINE_CHARS for ln in lines):
        raise GateFail("P3", f"An output line is longer than {MAX_LINE_CHARS} characters.")


def gate_code_size(code: str) -> None:
    """P4."""
    n = nonblank_count(code)
    if n > MAX_CODE_LINES:
        raise GateFail("P4", f"The program has {n} lines of code; at most {MAX_CODE_LINES} are allowed.")


def gate_something_to_work_out(code: str, stdout: str) -> None:
    """P5: some output line is not written out in the source as a string literal. A bare substring test would drop
    good drills: an output of 6 'appears' in `x = 16`, and `b = a` drills print a number the source also holds."""
    lines = [ln.strip() for ln in norm_lines(stdout) if ln.strip()]
    if all(_written_out(ln, code) for ln in lines):
        raise GateFail("P5", "Every output line is written out in the code, so there is nothing to work out.")


def _written_out(line: str, code: str) -> bool:
    """True when the output line sits in the source as a whole string literal (with or without a final \\n)."""
    return any(q + line + end + q in code for q in ('"', "'", "`") for end in ("", "\\n"))


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _comment_prefix(lang: str) -> str:
    """The language's line-comment marker: from codecoach.lang when that package exists, else c and python."""
    try:
        from codecoach.lang import spec
        return str(spec(lang).comment or "")
    except Exception:
        return {"c": "//", "python": "#"}.get(lang, "")


def comment_text(lang: str, code: str) -> list[str]:
    """What the program's comments say: the rest of each line after the line-comment marker, plus /* */ blocks.
    Naive on purpose. A marker inside a string also counts, which can only make the gate stricter."""
    out = [m.group(0)[2:-2] for m in _BLOCK_COMMENT.finditer(code)]
    prefix = _comment_prefix(lang)
    if prefix:
        for line in code.splitlines():
            _before, found, tail = line.partition(prefix)
            if found and tail.strip():
                out.append(tail)
    return out


def gate_no_answer_in_comments(lang: str, code: str, stdout: str) -> None:
    """P8: no comment holds a line of the output. P5 lets a drill through when ONE output line is computed, so a
    comment such as `# 42` could still give away another line. The learner reads the code, comments included."""
    outs = {ln.strip() for ln in norm_lines(stdout) if ln.strip()}
    for tail in comment_text(lang, code):
        for o in outs:
            if re.search(r"(?<![\w.])" + re.escape(o) + r"(?![\w.])", tail):
                raise GateFail("P8", "A comment in the program gives away part of the output.",
                               "Write no comments at all.")


# ------------------------------------------------------------------ the engine

def _plain_llm_reason(e: Exception) -> str:
    if isinstance(e, LLMUnavailable):
        return "The model could not be reached. Check that your model is running, then try again."
    return "The model's reply could not be used. Try again."


class PracticeEngine:
    """One per CodeCoachService (see engine_for). Builds run one at a time per process (codecoach.project._BUILD_LOCK)."""

    def __init__(self, svc):
        self.svc = svc
        self._lock = threading.Lock()
        self._building: set[str] = set()                 # drill ids being built right now in this process

    # ------------------------------------------------------------ storage

    @staticmethod
    def _dkey(did: str) -> str:
        return f"drill:{did}"

    def _index(self) -> list[str]:
        ix = self.svc.store.get_meta("practice:index", [])
        return [x for x in ix if isinstance(x, str)] if isinstance(ix, list) else []

    def record(self, did: Any) -> dict | None:
        """The stored drill with server-only fields. Server use only."""
        if not isinstance(did, str) or not did or ":" in did:
            return None
        r = self.svc.store.get_meta(self._dkey(did))
        return r if isinstance(r, dict) else None

    def _save(self, rec: dict) -> None:
        self.svc.store.set_meta(self._dkey(rec["id"]), rec)

    def _records(self, language: str, topic: str | None = None) -> list[dict]:
        out = []
        for did in self._index():
            r = self.record(did)
            if r and r.get("language") == language and (topic is None or r.get("topic") == topic):
                out.append(r)
        return out

    def _settle(self, rec: dict) -> dict:
        """A drill still `building` that no thread of this process is building lost its worker: say so."""
        if rec.get("status") == "building":
            with self._lock:
                alive = rec["id"] in self._building
                if not alive:
                    # the record we hold may be stale: the build can finish between our read and this lock
                    fresh = self.record(rec["id"])
                    if fresh is not None and fresh.get("status") != "building":
                        return fresh
                    rec = dict(rec, status="unavailable", reason="Building this drill was interrupted. Press Next drill "
                                                                 "to get another.")
                    self._save(rec)
        return rec

    # ------------------------------------------------------------ topics

    def topics(self, language: Any) -> dict:
        langs = _languages()
        lang = self._lang(language, langs)
        recs = self._records(lang)
        rows = []
        for tid, title in TOPICS:
            mine = [self._settle(r) for r in recs if r.get("topic") == tid]
            rows.append({"id": tid, "title": title,
                         "seen": sum(1 for r in mine if r.get("answered")),
                         "correct": sum(1 for r in mine if r.get("answered") and r.get("first_correct")),
                         "ready": sum(1 for r in mine if r.get("status") == "ready" and not r.get("answered")
                                      and r.get("test_id") is None)})
        return {"sandbox": sandbox_status(), "language": lang, "topics": rows}

    @staticmethod
    def _lang(language: Any, langs: dict) -> str:
        from codecoach.service import ServiceError
        if language is None or language == "":
            if langs:
                return next(iter(langs))
        elif isinstance(language, str) and language in langs:
            return language
        raise ServiceError(422, "bad_language", "That language is not set up on this machine yet.")

    # ------------------------------------------------------------ next, lazily and boundedly

    def next(self, language: Any, topic: Any) -> dict:
        """Serve an unseen stored drill, else start building one in the background; then make sure ONE more exists
        (built or building). Never builds a bank and never waits on the model unless the service runs inline."""
        from codecoach.service import ServiceError
        lang = self._lang(language, _languages())
        if not isinstance(topic, str) or topic not in TOPIC_IDS:
            raise ServiceError(422, "bad_topic", "Pick one of the practice topics.")
        if sandbox_status() != "bwrap":
            raise ServiceError(503, "sandbox_unavailable",
                               "Drills run a program in a sandbox. Install bubblewrap (bwrap) to use Practice.")
        with self._lock:
            unseen = [r for r in self._records(lang, topic) if not r.get("answered") and r.get("test_id") is None]
            ready = [r for r in unseen if r.get("status") == "ready"]
            building = [r for r in unseen if r.get("status") == "building" and r["id"] in self._building]
            start_served = False
            if ready:
                served = ready[0]
            elif building:
                served = building[0]
            else:
                if self.svc.llm is None:
                    raise ServiceError(503, "no_model", "No model is connected, so CodeCoach cannot write a drill.")
                served, start_served = self._placeholder(lang, topic), True
            others = [r for r in ready + building if r["id"] != served["id"]]
            more = not others and self.svc.llm is not None
        sid = served["id"]
        if start_served:
            self.svc._spawn(f"codecoach-practice-{sid}", lambda: self._job(sid, prefetch=more))
        elif more and served["status"] == "ready":
            self.svc._spawn(f"codecoach-practice-more-{sid}", lambda: self._prefetch(lang, topic, sid))
        cur = self.record(sid) or served
        return {"drill_id": sid, "status": "building" if cur.get("status") == "building" else "ready"}

    def _placeholder(self, lang: str, topic: str, test_id: str | None = None) -> dict:
        """A new `building` record. Caller holds self._lock."""
        taken = set(self._index())
        did = "d_" + secrets.token_hex(2)
        while did in taken:
            did = "d_" + secrets.token_hex(2)
        rec = {"id": did, "language": lang, "topic": topic, "kind": KIND, "status": "building", "reason": None,
               "version": PRACTICE_PROMPT_VERSION, "code": "", "stdin": "", "stdout": "", "explanation": [],
               "hash": None, "answered": False, "first_correct": None, "created_at": iso(self.svc.now())}
        if test_id is not None:
            rec["test_id"] = test_id
        self.svc.store.set_meta("practice:index", self._index() + [did])
        self._save(rec)
        self._building.add(did)
        return rec

    def _prefetch(self, lang: str, topic: str, after: str) -> None:
        """Start the one extra drill, unless one is already built or building for this language and topic."""
        with self._lock:
            for r in self._records(lang, topic):
                if r["id"] != after and not r.get("answered") and r.get("test_id") is None and (
                        r.get("status") == "ready" or (r.get("status") == "building" and r["id"] in self._building)):
                    return
            rec = self._placeholder(lang, topic)
        self._job(rec["id"], prefetch=False)

    def _job(self, did: str, *, prefetch: bool) -> None:
        rec = None
        try:
            with _BUILD_LOCK:
                rec = self._build(did)
        except Exception:
            rec = None
        done = None
        with self._lock:
            cur = self.record(did)
            if cur is not None:
                if rec is None:
                    rec = {"status": "unavailable", "reason": "Building this drill stopped unexpectedly. Try again."}
                done = dict(cur, **rec)
                self._save(done)
            self._building.discard(did)
        if done is not None:                               # the model wrote this drill: the service may measure it once
            getattr(self.svc, "language_used", lambda _l: None)(done.get("language"))
        if prefetch and done is not None and done["status"] == "ready":
            self._prefetch(done["language"], done["topic"], did)

    # ------------------------------------------------------------ the build

    def _build(self, did: str) -> dict:
        """The fields to store on drill `did`, status ready or unavailable. The caller holds _BUILD_LOCK."""
        cur = self.record(did)
        if cur is None:
            return {"status": "unavailable", "reason": "That drill no longer exists."}
        lang, topic = cur["language"], cur["topic"]
        langs = _languages()
        if lang not in langs:
            return self._fail("That language is not set up on this machine any more.")
        name, style = langs[lang]
        if self.svc.llm is None:
            return self._fail("No model is connected, so CodeCoach cannot write a drill.")
        title = dict(TOPICS)[topic]
        stored = [r for r in self._records(lang, topic) if r["id"] != did and r.get("status") == "ready"]
        seen_hashes = {r.get("hash") for r in stored}
        earlier = [r["code"] for r in stored[-EARLIER_SHOWN:]]
        feedback, reason = "", "The drill could not be built."
        for _ in range(BUILD_TRIES):
            try:
                reply = structured_call(self.svc.llm, DrillReply, instructions=DRILL_INSTRUCTIONS,
                                        text=build_prompt(name, style, topic, title, earlier, feedback),
                                        purpose=DRILL_PURPOSE, retries=1,
                                        timeout=getattr(self.svc.cfg, "llm_timeout_s", None))
            except LLMUnavailable as e:
                return self._fail(_plain_llm_reason(e))
            except LLMBadOutput as e:
                reason, feedback = _plain_llm_reason(e), e.last_error
                continue
            except LLMError as e:
                return self._fail(_plain_llm_reason(e))
            # A model sometimes escapes the input twice: the learner would see a stray backslash and n.
            reply = reply.model_copy(update={"stdin": real_line_breaks(reply.stdin)})
            try:
                stdout = self._gate(lang, reply, seen_hashes)
            except GateFail as g:
                reason, feedback = g.reason, (g.reason + " " + g.detail).strip()
                continue
            except SandboxGone:
                return self._fail("The code sandbox stopped working. Install or fix bubblewrap, then try again.")
            return {"status": "ready", "reason": None, "code": reply.code, "stdin": reply.stdin, "stdout": stdout,
                    "explanation": [s.strip() for s in reply.explanation], "hash": code_hash(reply.code)}
        return self._fail(reason)

    @staticmethod
    def _fail(reason: str) -> dict:
        return {"status": "unavailable", "reason": reason}

    @staticmethod
    def _gate(lang: str, reply: DrillReply, seen_hashes: set) -> str:
        stdout = gate_runs(lang, reply)                                  # P1, P2
        gate_output_shape(stdout)                                        # P3
        gate_code_size(reply.code)                                       # P4
        gate_something_to_work_out(reply.code, stdout)                   # P5
        gate_no_answer_in_comments(lang, reply.code, stdout)             # P8
        if code_hash(reply.code) in seen_hashes:                         # P6
            raise GateFail("P6", "That program is a copy of an earlier drill on this topic.")
        return stdout

    # ------------------------------------------------------------ views and answers

    def view(self, did: Any) -> dict:
        """P7: built from a whitelist. Stdout and the explanation are never copied, so they cannot leak."""
        from codecoach.service import ServiceError
        rec = self.record(did)
        if rec is None:
            raise ServiceError(404, "unknown_drill", "That drill does not exist.")
        rec = self._settle(rec)
        ready = rec.get("status") == "ready"
        return {"id": rec["id"], "language": rec["language"], "topic": rec["topic"], "kind": rec.get("kind", KIND),
                "status": rec["status"], "code": rec.get("code", "") if ready else "",
                "stdin": rec.get("stdin", "") if ready else "",
                "line_count": len(norm_lines(rec.get("stdout") or "")) if ready else 0,
                "reason": rec.get("reason") if rec["status"] == "unavailable" else None,
                "answered": bool(rec.get("answered"))}

    def answer(self, did: Any, prediction: Any) -> dict:
        from codecoach.service import ServiceError
        if not isinstance(prediction, str):
            raise ServiceError(422, "bad_request", "Send your prediction as text.")
        if len(prediction) > MAX_PREDICTION_CHARS:
            raise ServiceError(413, "too_large", "That answer is too long.")
        rec = self.record(did)
        if rec is None:
            raise ServiceError(404, "unknown_drill", "That drill does not exist.")
        rec = self._settle(rec)
        if rec.get("status") != "ready":
            raise ServiceError(409, "drill_not_ready", "This drill is not ready yet." if rec["status"] == "building"
                               else "This drill could not be built, so it cannot be answered.")
        if rec.get("test_id") is not None:
            raise ServiceError(409, "in_test", "This drill belongs to a test that is not finished.")
        graded = grade_prediction(rec["stdout"], prediction)
        with self._lock:
            cur = self.record(did) or rec
            if not cur.get("answered"):
                cur = dict(cur, answered=True, first_correct=graded["correct"])
                self._save(cur)
            log = self.svc.store.get_meta("practice:attempts", [])
            log = log if isinstance(log, list) else []
            log.append({"drill_id": did, "language": rec["language"], "topic": rec["topic"],
                        "correct": graded["correct"], "at": iso(self.svc.now())})
            self.svc.store.set_meta("practice:attempts", log[-MAX_ATTEMPTS_LOG:])
        return {"correct": graded["correct"], "expected_stdout": rec["stdout"], "lines": graded["lines"],
                "explanation": shown_explanation(rec)}


def engine_for(svc) -> PracticeEngine:
    """The engine cached on the service object (no edit to service.py)."""
    with svc._lock:
        eng = getattr(svc, "_practice_engine", None)
        if eng is None:
            eng = svc._practice_engine = PracticeEngine(svc)
        return eng


# ------------------------------------------------------------------ request bodies (routes)

class PracticeNextRequest(BaseModel):
    language: str = ""
    topic: str = ""


class PracticeAnswerRequest(BaseModel):
    prediction: str = ""
