"""Project mode: name a small program, build it step by step. Hermes-free; CodeCoachService delegates here.

The contract is PROJECT-CONTRACT.md. One model call plans the project (title, summary, 4-8 steps). Each step is built
lazily by one model call and then checked by CODE before a learner sees it (gates G1-G7, below): the model's own
reference solution must pass every test in the real sandbox, the step must test something new, the program must be
deterministic, and every example output shown is the REAL stdout of a sandbox run. The model never states an output.

Storage is store meta, JSON, no migration:
    projects:index                 list of project ids, newest first
    project:<pid>                  state (plan, status, learner code, per-step progress); no server-only fields
    project:<pid>:step:<i>         the built step INCLUDING reference solution, hidden tests and all hints

Server-only step fields (reference_solution, hidden_tests, hints) never leave this module in `get`/`list`.
"""

from __future__ import annotations

import difflib
import re
import secrets
import threading
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from codecoach.adapt.mastery import iso
from codecoach.check.code_runner import check_reference, grade, run_snippet, run_visible, sandbox_status
from codecoach.core.llm import LLMBadOutput, LLMError, LLMUnavailable, structured_call
from codecoach.core.models import CodePayload, CodeTest

PLAN_PURPOSE = "codecoach.project.plan"
STEP_PURPOSE = "codecoach.project.step"
EXAMPLE_PURPOSE = "codecoach.project.example"
PROJECT_PROMPT_VERSION = "2"            # stored in every built step; a mismatch rebuilds the step when opened
MIN_STEPS, MAX_STEPS = 4, 8
MAX_STEP_LINES = 40                     # G5: non-blank lines a step may add over the previous reference
MIN_LEAK_CHARS = 12                     # G7: a reference line this long (non-space characters) must not sit in a hint
MAX_RETRIES = 2                         # G1-G5: retries of the whole step call, in total
MAX_CODE_CHARS = 8000
MAX_TEXT = 1500
IDEA_MIN, IDEA_MAX = 3, 300
LANGUAGES = ("c", "python")
_BUILD_LOCK = threading.Lock()          # one model build at a time across all projects (one shared GPU)

STARTERS = {"c": "#include <stdio.h>\n\nint main(void)\n{\n    return 0;\n}\n",
            "python": "# Your program starts here\n"}

IDEAS = [
    {"title": "Number-guessing game", "language": "c",
     "pitch": "You type the secret and the guesses; it says higher or lower."},
    {"title": "Times-table printer", "language": "c",
     "pitch": "Type a number and get its times table, one line per row."},
    {"title": "Simple calculator", "language": "c",
     "pitch": "Type two numbers and an operator; it prints the answer."},
    {"title": "Temperature converter", "language": "python",
     "pitch": "Type a temperature in Celsius and get Fahrenheit back."},
    {"title": "Word counter", "language": "python",
     "pitch": "Paste some text and see how many lines and words it has."},
    {"title": "Palindrome checker", "language": "python",
     "pitch": "Type a word and it tells you whether it reads the same backwards."},
]


# ------------------------------------------------------------------ reply models

class PlanStep(BaseModel):
    title: str = ""
    goal: str = ""
    concept: str = ""


class PlanReply(BaseModel):
    feasible: bool = True
    why_not: str = ""
    title: str = ""
    summary: str = ""
    steps: list[PlanStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _plan_ok(self) -> "PlanReply":
        if not self.feasible:
            return self
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("title and summary must not be empty")
        if not MIN_STEPS <= len(self.steps) <= MAX_STEPS:
            raise ValueError(f"the plan must have {MIN_STEPS} to {MAX_STEPS} steps, not {len(self.steps)}")
        for i, s in enumerate(self.steps):
            if not (s.title.strip() and s.goal.strip() and s.concept.strip()):
                raise ValueError(f"step {i + 1} needs a non-empty title, goal and concept")
        return self


class ExampleReply(BaseModel):
    code: str = ""
    stdin: str = ""

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("example code must not be empty")
        if len(v) > MAX_CODE_CHARS:
            raise ValueError("example code is too long")
        return v


class TestReply(BaseModel):
    __test__ = False                                  # not a pytest class
    name: str = ""
    stdin: str = ""
    expected_stdout: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a test needs a name")
        return v


class StepReply(BaseModel):
    lesson: list[str] = Field(default_factory=list)
    example: ExampleReply
    task: str = ""
    reference_solution: str = ""
    visible_tests: list[TestReply] = Field(default_factory=list)
    hidden_tests: list[TestReply] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _step_ok(self) -> "StepReply":
        if not 2 <= len(self.lesson) <= 5 or not all(s.strip() for s in self.lesson):
            raise ValueError("lesson must be 2 to 5 non-empty sentences")
        if not self.task.strip():
            raise ValueError("task must not be empty")
        if not self.reference_solution.strip() or len(self.reference_solution) > MAX_CODE_CHARS:
            raise ValueError("reference_solution must be the full program, not empty and not huge")
        if len(self.hints) != 3 or not all(h.strip() for h in self.hints):
            raise ValueError("hints must be exactly 3 non-empty strings")
        return self


# ------------------------------------------------------------------ prompts

PLAN_INSTRUCTIONS = (
    "You plan a small programming project for a complete beginner who will write the code themselves, one small step "
    "at a time. Reply with JSON: {\"feasible\": true, \"why_not\": \"\", \"title\": \"...\", \"summary\": \"one line\", "
    "\"steps\": [{\"title\": \"...\", \"goal\": \"what the program can do after this step\", \"concept\": "
    "\"the ONE new idea, 1-3 words\"}]}.\n"
    "Rules:\n"
    "- 4 to 8 steps. Beginner level. One new idea per step. Each step builds on the program from the step before.\n"
    "- The program must be deterministic and use ONLY standard input and standard output: no random numbers, no clock "
    "or time, no files, no network, no graphics, no menus that wait for a key. If the idea needs a secret or a random "
    "value, take it from input instead.\n"
    "- The last step finishes the idea. Do not copy a course's problem set; invent a small program of your own.\n"
    "- If the idea cannot be done that way, reply {\"feasible\": false, \"why_not\": \"one plain sentence a beginner "
    "understands\", \"title\": \"\", \"summary\": \"\", \"steps\": []}."
)

STEP_INSTRUCTIONS = (
    "You write ONE step of a beginner programming project. The learner writes the code; you write the lesson, the "
    "tests and a reference solution. Reply with JSON: {\"lesson\": [\"sentence\", ...], \"example\": {\"code\": "
    "\"a tiny complete program\", \"stdin\": \"\"}, \"task\": \"what the learner adds\", \"reference_solution\": "
    "\"the FULL program after this step\", \"visible_tests\": [{\"name\": \"...\", \"stdin\": \"...\", "
    "\"expected_stdout\": \"...\"}], \"hidden_tests\": [same shape], \"hints\": [\"nudge\", \"which construct and "
    "why\", \"the shape with blanks\"]}.\n"
    "Rules:\n"
    "- lesson: 2 to 5 short plain sentences teaching only this step's ONE new idea. Never say what a program prints.\n"
    "- example: a small COMPLETE program (not the project) that shows the new idea; it must run and exit 0. "
    "Do not write its output; it will be run for real.\n"
    "- reference_solution: the full project program after this step. It must build on the previous reference "
    "solution you are given and add at most 40 non-blank lines. It must really pass every test below.\n"
    "- Tests are standard input -> exact standard output. At least 1 visible and 1 hidden test. A hidden test must "
    "use different input from every visible test. At least one test must FAIL for the previous reference solution "
    "(the step tests something new). Output must be the same on every run: no random values, no times, no memory "
    "addresses.\n"
    "- Do not print prompts such as \"Enter a number: \" before reading input. Print only the result lines.\n"
    "- task: say what to add and quote, in double quotes, every exact output text the tests check (write N where a "
    "number goes), e.g. print \"Too low\". Every kind of line a hidden test expects must also appear in a visible "
    "test or in the task.\n"
    "- hints: exactly 3, in order. Never write a line of the reference solution in a hint; blanks like ___ are fine.\n"
    "- Use the language you are told. Plain C11 or Python 3, standard library only."
)


def _nonblank(code: str) -> list[str]:
    return [ln.rstrip() for ln in code.splitlines() if ln.strip()]


def added_lines(prev: str, new: str) -> int:
    """Non-blank lines of `new` that are not carried over from `prev` (G5)."""
    a, b = _nonblank(prev), _nonblank(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(j2 - j1 for tag, _i1, _i2, j1, j2 in sm.get_opcodes() if tag in ("replace", "insert"))


def leaked_lines(prev: str, ref: str) -> list[str]:
    """Reference lines with 12+ non-space characters that the previous reference did not already have (G7)."""
    old = {ln.strip() for ln in prev.splitlines()}
    out, seen = [], set()
    for ln in ref.splitlines():
        s = ln.strip()
        if s and s not in old and s not in seen and len(re.sub(r"\s", "", s)) >= MIN_LEAK_CHARS:
            seen.add(s)
            out.append(s)
    return out


def _squash(text: str) -> str:
    return re.sub(r"\s", "", text)


def leaks_answer(text: str, lines: list[str]) -> bool:
    """True when `text` carries one of the step's new reference lines. Spacing does not count: 'n*2' is 'n * 2'
    (orchestrator probe, Nov 8: a hint that re-spaced the answer line got through)."""
    t = _squash(text)
    return any(_squash(ln) in t for ln in lines)


def drop_leaky_hints(hints: list[str], prev: str, ref: str) -> list[str]:
    """G7: drop any hint that contains a new reference line of 12+ non-space characters, ignoring spacing."""
    lines = leaked_lines(prev, ref)
    return [h for h in hints if not leaks_answer(h, lines)]


class GateFail(Exception):
    """A gate said no. `reason` is a plain sentence; it goes to the learner and back into the retry prompt."""

    def __init__(self, gate: str, reason: str):
        super().__init__(f"{gate}: {reason}")
        self.gate, self.reason = gate, reason


class SandboxGone(Exception):
    pass


def _tests(rows: list[TestReply]) -> list[CodeTest]:
    return [CodeTest(name=r.name.strip(), stdin=r.stdin, expected_stdout=r.expected_stdout) for r in rows]


def _payload(language: str, prev: str, reply: StepReply) -> CodePayload:
    return CodePayload(language=language, starter=prev, visible_tests=_tests(reply.visible_tests),  # type: ignore[arg-type]
                       hidden_tests=_tests(reply.hidden_tests), reference_solution=reply.reference_solution)


# ---- the gates. Each raises GateFail (or SandboxGone when the sandbox cannot decide); each is testable alone.

def gate_reference_passes(payload: CodePayload) -> None:
    """G1: the reference passes every visible and hidden test."""
    res = check_reference(payload)
    if res.undecided:
        raise SandboxGone()
    if not res.ok:
        why = f"{res.passed} of {res.total} tests"
        if res.compile_error:
            why = "it did not compile: " + res.compile_error.splitlines()[0][:200]
        raise GateFail("G1", f"CodeCoach's own solution did not pass its own tests ({why}).")


def gate_tests_something_new(payload: CodePayload, prev: str) -> None:
    """G2: the previous reference FAILS at least one of this step's tests."""
    res = grade(payload, prev)
    if res.undecided:
        raise SandboxGone()
    if res.ok:
        raise GateFail("G2", "The new tests pass without the new code, so the step teaches nothing new.")


def gate_deterministic(payload: CodePayload) -> None:
    """G3: the reference run twice prints identical stdout on every test."""
    for t in list(payload.visible_tests) + list(payload.hidden_tests):
        a = run_snippet(payload.language, payload.reference_solution, t.stdin)
        b = run_snippet(payload.language, payload.reference_solution, t.stdin)
        if a is None or b is None:
            raise SandboxGone()
        if a.stdout != b.stdout:
            raise GateFail("G3", "The solution printed different output on two runs of the same input.")


def gate_test_shape(payload: CodePayload) -> None:
    """G4: at least 1 visible and 1 hidden test; no hidden test shares its stdin with a visible one."""
    if not payload.visible_tests or not payload.hidden_tests:
        raise GateFail("G4", "The step needs at least one visible and one hidden test.")
    def key(stdin: str) -> str:                 # "5" and "5\n" are the same input to a program
        return "\n".join(ln.rstrip() for ln in stdin.rstrip().splitlines())

    seen = {key(t.stdin) for t in payload.visible_tests}
    if any(key(t.stdin) in seen for t in payload.hidden_tests):
        raise GateFail("G4", "A hidden test repeats the input of a visible test.")


_PUNCT = ".,!?:;\"'()[]{}<>"


def _digits(text: str) -> str:
    return re.sub(r"\d+", "#", text)


def _varies(tok: str, stdin_toks: set[str], task_words: set[str]) -> bool:
    """A token that may differ from the shown line: a number, something the test typed in, or a word the task names."""
    b = tok.strip(_PUNCT)
    return (not b or re.fullmatch(r"-?\d+(\.\d+)?", b) is not None or b in stdin_toks
            or b.lower() in task_words)


def unshown_output(hidden: list[CodeTest], shown: list[str], task: str) -> list[str]:
    """G8: hidden-test output lines whose kind the learner was never shown. A hidden line counts as shown when the
    task quotes it (numbers aside), or when a visible line of this or an earlier step has the same words except for
    numbers, input the test typed, or words the task names ("Grade: B" after "Grade: A" when the task lists B).
    Orchestrator probe on local Qwen, Nov 8: tests expected "Enter the secret number: The secret is 42." while the
    task only said "print a prompt", so a learner had to guess exact wording that hidden tests then checked."""
    task_d = _digits(re.sub(r"\bN\b", "#", task))     # the step prompt writes N where a number goes
    task_words = {w.strip(_PUNCT).lower() for w in task.split()} - {""}
    shown_toks = [ln.split() for ln in shown if ln.strip()]
    out = []
    for t in hidden:
        stdin_toks = {w.strip(_PUNCT) for w in t.stdin.split()} - {""}
        for line in (t.expected_stdout or "").splitlines():
            if not line.strip() or _digits(line.strip()) in task_d:
                continue
            toks = line.split()
            if any(len(st) == len(toks) and all(a == b or _varies(a, stdin_toks, task_words) for a, b in zip(toks, st))
                   for st in shown_toks):
                continue
            if line.strip() not in out:
                out.append(line.strip())
    return out


def gate_output_shown(payload: CodePayload, task: str, earlier_shown: list[str]) -> None:
    shown = list(earlier_shown)
    for t in payload.visible_tests:
        shown += (t.expected_stdout or "").splitlines()
    missing = unshown_output(payload.hidden_tests, shown, task)
    if missing:
        raise GateFail("G8", f"A hidden test expects output the learner was never shown: \"{missing[0]}\". Quote it "
                             "in the task or show that kind of line in a visible test.")


def gate_small_step(prev: str, ref: str) -> None:
    """G5: the reference adds at most 40 non-blank lines over the previous reference."""
    n = added_lines(prev, ref)
    if n > MAX_STEP_LINES:
        raise GateFail("G5", f"The step adds {n} lines of code; a step may add at most {MAX_STEP_LINES}.")


def real_example(language: str, ex: ExampleReply) -> dict | None:
    """G6: run the example in the sandbox. The stored stdout is the real one. None when it does not run clean."""
    r = run_snippet(language, ex.code, ex.stdin)
    if r is None:
        raise SandboxGone()
    if not r.ok:
        return None
    return {"code": ex.code, "stdin": ex.stdin, "stdout": r.stdout, "ran": True}


# ------------------------------------------------------------------ the engine

def _plain_llm_reason(e: Exception) -> str:
    if isinstance(e, LLMUnavailable):
        return "The model could not be reached. Check that your model is running, then try again."
    return "The model's reply could not be used. Try again, or try a simpler idea."


class ProjectEngine:
    """One per CodeCoachService. Builds run one at a time per process (module lock)."""

    def __init__(self, svc):
        self.svc = svc
        self._lock = threading.Lock()
        self._planning: set[str] = set()
        self._building: set[tuple[str, int]] = set()      # queued or running

    # ------------------------------------------------------------ storage

    @staticmethod
    def _pkey(pid: str) -> str:
        return f"project:{pid}"

    @staticmethod
    def _skey(pid: str, i: int) -> str:
        return f"project:{pid}:step:{i}"

    def load(self, pid: str) -> dict | None:
        """The raw project state (never has server-only fields). None when unknown."""
        if not isinstance(pid, str) or not pid or ":" in pid:
            return None
        p = self.svc.store.get_meta(self._pkey(pid))
        return p if isinstance(p, dict) else None

    def save(self, state: dict) -> None:
        state["updated_at"] = iso(self.svc.now())
        self.svc.store.set_meta(self._pkey(state["id"]), state)

    def step_record(self, pid: str, i: int) -> dict | None:
        """The built step with server-only fields. Server use only."""
        if isinstance(i, bool) or not isinstance(i, int) or i < 0:
            return None
        r = self.svc.store.get_meta(self._skey(pid, i))
        return r if isinstance(r, dict) else None

    def _index(self) -> list[str]:
        ix = self.svc.store.get_meta("projects:index", [])
        return [x for x in ix if isinstance(x, str)] if isinstance(ix, list) else []

    def _require(self, pid: str) -> dict:
        from codecoach.service import ServiceError
        p = self.load(pid)
        if p is None:
            raise ServiceError(404, "unknown_project", "That project does not exist (it may have been deleted).")
        return p

    # ------------------------------------------------------------ create, get, list, delete

    def ideas(self) -> list[dict]:
        return [dict(i) for i in IDEAS]

    def sandbox(self) -> str:
        return sandbox_status()

    def create(self, idea: Any, language: Any) -> str:
        from codecoach.service import ServiceError
        text = idea.strip() if isinstance(idea, str) else ""
        if not IDEA_MIN <= len(text) <= IDEA_MAX:
            raise ServiceError(422, "bad_idea", f"Describe your project in {IDEA_MIN} to {IDEA_MAX} characters.")
        if language not in LANGUAGES:
            raise ServiceError(422, "bad_language", "Pick C or Python.")
        if sandbox_status() != "bwrap":
            raise ServiceError(503, "sandbox_unavailable",
                               "Projects run your code in a sandbox. Install bubblewrap (bwrap) to use them.")
        with self._lock:
            taken = set(self._index())
            pid = "p_" + secrets.token_hex(3)
            while pid in taken:
                pid = "p_" + secrets.token_hex(3)
            now = iso(self.svc.now())
            state = {"id": pid, "title": text[:60], "language": language, "idea": text, "status": "planning",
                     "reason": None, "summary": "", "plan": [], "current": 0, "code": STARTERS[language],
                     "progress": {}, "created_at": now, "updated_at": now}
            self.svc.store.set_meta(self._pkey(pid), state)
            self.svc.store.set_meta("projects:index", [pid] + self._index())
            self._planning.add(pid)
        self.svc._spawn(f"codecoach-project-plan-{pid}", lambda: self._plan_job(pid))
        return pid

    def list(self) -> dict:
        rows = []
        for pid in self._index():
            p = self.load(pid)
            if p is None:
                continue
            p = self._settle(p)
            prog = p.get("progress") or {}
            rows.append({"id": p["id"], "title": p["title"], "language": p["language"], "status": p["status"],
                         "steps_total": len(p.get("plan") or []),
                         "steps_passed": sum(1 for v in prog.values() if isinstance(v, dict) and v.get("passed")),
                         "updated_at": p.get("updated_at")})
        return {"sandbox": sandbox_status(), "ideas": self.ideas(), "projects": rows}

    def delete(self, pid: str) -> None:
        self._require(pid)
        with self._lock:
            self.svc.store.delete_meta_keys(self._pkey(pid))        # the state and every step key under it
            self.svc.store.set_meta("projects:index", [x for x in self._index() if x != pid])

    def _settle(self, p: dict) -> dict:
        """A project still `planning` that no thread of this process is planning lost its worker: say so."""
        if p.get("status") == "planning":
            with self._lock:
                alive = p["id"] in self._planning
            if not alive:
                p = dict(p, status="failed", reason="Planning was interrupted. Start the project again.")
                self.save(p)
        return p

    def get(self, pid: str) -> dict:
        p = self._settle(self._require(pid))
        prog = p.get("progress") or {}
        cur = int(p.get("current") or 0)
        steps = []
        for i, s in enumerate(p.get("plan") or []):
            pr = prog.get(str(i)) or {}
            steps.append({"index": i, "title": s["title"], "goal": s["goal"], "concept": s["concept"],
                          "status": self._step_status(p["id"], i, cur, pr),
                          "attempts": int(pr.get("attempts") or 0), "hints_used": int(pr.get("hints_used") or 0),
                          "with_help": bool(pr.get("with_help"))})
        return {"id": p["id"], "title": p["title"], "language": p["language"], "idea": p["idea"],
                "summary": p.get("summary") or "", "status": p["status"], "reason": p.get("reason"),
                "current": cur, "code": p.get("code") or "", "steps": steps}

    def _step_status(self, pid: str, i: int, cur: int, pr: dict) -> str:
        if pr.get("passed"):
            return "passed"
        if i > cur:
            return "locked"
        with self._lock:
            if (pid, i) in self._building:
                return "building"
        rec = self.step_record(pid, i)
        if rec is None or rec.get("version") != PROJECT_PROMPT_VERSION:
            return "planned"
        return "ready" if rec.get("status") == "ready" else "unavailable"

    # ------------------------------------------------------------ the plan

    def _plan_job(self, pid: str) -> None:
        try:
            with _BUILD_LOCK:
                self._plan(pid)
        except Exception:
            self._finish_plan(pid, status="failed", reason="Planning stopped unexpectedly. Try again.")
        finally:
            with self._lock:
                self._planning.discard(pid)

    def _finish_plan(self, pid: str, **fields: Any) -> None:
        with self._lock:
            p = self.load(pid)
            if p is None:                       # deleted while planning: do not bring it back
                return
            p.update(fields)
            self.save(p)

    def _plan(self, pid: str) -> None:
        p = self.load(pid)
        if p is None:
            return
        if self.svc.llm is None:
            return self._finish_plan(pid, status="failed", reason="No model is connected, so CodeCoach cannot plan a project.")
        lang = "C" if p["language"] == "c" else "Python"
        text = f"Language: {lang}\nProject idea: {p['idea']}"
        try:
            r = structured_call(self.svc.llm, PlanReply, instructions=PLAN_INSTRUCTIONS, text=text,
                                purpose=PLAN_PURPOSE, retries=3, timeout=getattr(self.svc.cfg, "llm_timeout_s", None))
        except (LLMBadOutput, LLMError) as e:
            return self._finish_plan(pid, status="failed", reason=_plain_llm_reason(e))
        if not r.feasible:
            why = r.why_not.strip() or "That idea does not fit a small project that runs from input to output."
            return self._finish_plan(pid, status="refused", reason=why)
        plan = [{"title": s.title.strip(), "goal": s.goal.strip(), "concept": s.concept.strip()} for s in r.steps]
        self._finish_plan(pid, status="ready", reason=None, title=r.title.strip()[:100], summary=r.summary.strip(),
                          plan=plan)
        if self.svc.background:         # start step 0 (and 1) now: on local Qwen that saves the learner a minute
            self.ensure_built(pid, 0, prefetch=True)

    # ------------------------------------------------------------ step builds

    def _shown_before(self, p: dict, i: int) -> list[str]:
        """Expected output lines of the visible tests of steps 0..i-1 (what the learner has already seen)."""
        out: list[str] = []
        for j in range(i):
            rec = self.step_record(p["id"], j) or {}
            for t in rec.get("visible_tests") or []:
                out += (t.get("expected_stdout") or "").splitlines()
        return out

    def _base_code(self, p: dict, i: int) -> str | None:
        """CodeCoach's previous reference solution (the starter for step 0). None when step i-1 is not built."""
        if i == 0:
            return STARTERS[p["language"]]
        rec = self.step_record(p["id"], i - 1)
        if rec and rec.get("status") == "ready" and isinstance(rec.get("reference_solution"), str):
            return rec["reference_solution"]
        return None

    def _current_record(self, pid: str, i: int) -> dict | None:
        rec = self.step_record(pid, i)
        return rec if rec and rec.get("version") == PROJECT_PROMPT_VERSION else None

    def ensure_built(self, pid: str, i: int, *, prefetch: bool = True) -> str:
        """Start whatever builds are missing up to step i (and i+1 when `prefetch`), in the background.
        Never blocks on the model. Returns the state of step i right now: building | ready | unavailable | none
        (none = nothing to do: sandbox missing, no model, plan not ready, or the index is outside the plan)."""
        p = self.load(pid)
        if p is None or p.get("status") not in ("ready", "done") or sandbox_status() != "bwrap" or self.svc.llm is None:
            return "none"
        n = len(p.get("plan") or [])
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < n:
            return "none"
        last = min(i + 1, n - 1) if prefetch else i
        if self._missing(p, last) is None:
            return self._state(pid, i)
        with self._lock:
            if (pid, last) in self._building:
                return self._state_locked(pid, i)
            # Only steps that really need a build are "building": a built step stays "ready" while a later one builds
            # (the video run showed a finished next step as "Building the next step..." for 37 s).
            for j in self._needs_build(p, last):
                self._building.add((pid, j))
        self.svc._spawn(f"codecoach-project-step-{pid}-{i}", lambda: self._chain_job(pid, i, last))
        return self._state(pid, i)

    def _needs_build(self, p: dict, last: int) -> list[int]:
        prog = p.get("progress") or {}
        return [j for j in range(last + 1)
                if not (prog.get(str(j)) or {}).get("passed") and self._current_record(p["id"], j) is None]

    def _missing(self, p: dict, last: int) -> int | None:
        """First step index <= last that needs a build (no current record; passed steps are left alone)."""
        prog = p.get("progress") or {}
        for j in range(last + 1):
            if (prog.get(str(j)) or {}).get("passed"):
                continue
            if self._current_record(p["id"], j) is None:
                return j
        return None

    def _state(self, pid: str, i: int) -> str:
        with self._lock:
            return self._state_locked(pid, i)

    def _state_locked(self, pid: str, i: int) -> str:
        if (pid, i) in self._building:
            return "building"
        rec = self._current_record(pid, i)
        return "none" if rec is None else ("ready" if rec.get("status") == "ready" else "unavailable")

    def retry(self, pid: str, i: int) -> str:
        """Rebuild an `unavailable` step. ServiceError 409 `not_unavailable` for any other step."""
        from codecoach.service import ServiceError
        p = self._require(pid)
        rec = self._current_record(pid, i)
        if rec is None or rec.get("status") != "unavailable":
            raise ServiceError(409, "not_unavailable", "That step is not broken, so there is nothing to retry.")
        self.svc.store.delete_meta_keys(self._skey(pid, i))
        return self.ensure_built(p["id"], i, prefetch=False)

    def _chain_job(self, pid: str, i: int, last: int) -> None:
        try:
            for j in range(last + 1):
                p = self.load(pid)
                if p is None:
                    return
                if (p.get("progress") or {}).get(str(j), {}).get("passed"):
                    continue
                rec = self._current_record(pid, j)
                if rec is not None:
                    if rec.get("status") == "unavailable":
                        return                  # a broken step stops the chain; its retry rebuilds it
                    continue
                with _BUILD_LOCK:
                    rec = self._build_step(p, j)
                self._store_step(pid, j, rec)
                with self._lock:
                    self._building.discard((pid, j))       # this step is settled; later ones may still build
                if rec["status"] != "ready":
                    return
        except Exception:
            pass
        finally:
            with self._lock:
                for j in range(last + 1):
                    self._building.discard((pid, j))

    def _store_step(self, pid: str, i: int, rec: dict) -> None:
        with self._lock:
            if self.load(pid) is None:          # deleted while building
                return
            self.svc.store.set_meta(self._skey(pid, i), rec)

    def _unavailable(self, i: int, reason: str, attempts: int) -> dict:
        return {"version": PROJECT_PROMPT_VERSION, "index": i, "status": "unavailable", "reason": reason,
                "attempts": attempts}

    @staticmethod
    def step_prompt(p: dict, i: int, prev: str, feedback: str = "") -> str:
        plan = "\n".join(f"{n + 1}. {s['title']} - {s['goal']} (new idea: {s['concept']})"
                         for n, s in enumerate(p["plan"]))
        s = p["plan"][i]
        rows = [f"Language: {'C' if p['language'] == 'c' else 'Python'}", f"Project idea: {p['idea']}",
                f"Plan:\n{plan}", f"Write step {i + 1} of {len(p['plan'])}: {s['title']}",
                f"Goal: {s['goal']}", f"New idea: {s['concept']}",
                "Previous reference solution (the program before this step"
                + (", an empty starter" if i == 0 else "") + "):\n" + prev]
        if feedback:
            rows.append(f"Your previous attempt was rejected: {feedback}\nWrite the whole step again and fix that.")
        return "\n\n".join(rows)

    def _build_step(self, p: dict, i: int) -> dict:
        """One built step record, `ready` or `unavailable`. Holds no lock; the caller holds _BUILD_LOCK."""
        if self.svc.llm is None:
            return self._unavailable(i, "No model is connected, so CodeCoach cannot build this step.", 0)
        prev = self._base_code(p, i)
        if prev is None:
            return self._unavailable(i, "The step before this one is not built yet.", 0)
        feedback, reason = "", "The step could not be built."
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                reply = structured_call(self.svc.llm, StepReply, instructions=STEP_INSTRUCTIONS,
                                        text=self.step_prompt(p, i, prev, feedback), purpose=STEP_PURPOSE, retries=2,
                                        timeout=getattr(self.svc.cfg, "llm_timeout_s", None))
            except LLMUnavailable as e:
                return self._unavailable(i, _plain_llm_reason(e), attempt)
            except LLMBadOutput as e:
                reason = _plain_llm_reason(e)
                feedback = e.last_error
                continue
            try:
                rec = self._gate(p, i, prev, reply, attempt)
            except GateFail as g:
                reason, feedback = g.reason, g.reason
                continue
            except SandboxGone:
                return self._unavailable(i, "The code sandbox stopped working. Install or fix bubblewrap, then retry.",
                                         attempt)
            if rec is not None:
                return rec
            # a repaired example that still failed is not a retry: _gate returns the record with example null
        return self._unavailable(i, reason, MAX_RETRIES + 1)

    def _gate(self, p: dict, i: int, prev: str, reply: StepReply, attempt: int) -> dict:
        lang = p["language"]
        if not reply.visible_tests:
            raise GateFail("G4", "The step needs at least one visible and one hidden test.")
        try:
            payload = _payload(lang, prev, reply)
        except ValidationError:
            raise GateFail("G4", "A test was malformed (it needs a name and an expected output).") from None
        gate_reference_passes(payload)                                  # G1
        gate_tests_something_new(payload, prev)                         # G2
        gate_deterministic(payload)                                     # G3
        gate_test_shape(payload)                                        # G4
        gate_small_step(prev, reply.reference_solution)                 # G5
        gate_output_shown(payload, reply.task, self._shown_before(p, i))  # G8
        answer = leaked_lines(prev, reply.reference_solution)
        example = self._example(p, i, reply, prev, answer)              # G6 (+ G7: an example is not the answer)
        hints = drop_leaky_hints([h.strip() for h in reply.hints], prev, reply.reference_solution)    # G7
        lesson = [s.strip() for s in reply.lesson if not leaks_answer(s, answer)]                   # G7
        return {"version": PROJECT_PROMPT_VERSION, "index": i, "status": "ready", "reason": None,
                "attempts": attempt, "lesson": lesson, "example": example,
                "task": reply.task.strip(),
                "reference_solution": reply.reference_solution,
                "visible_tests": [t.model_dump() for t in payload.visible_tests],
                "hidden_tests": [t.model_dump() for t in payload.hidden_tests], "hints": hints}

    def _example(self, p: dict, i: int, reply: StepReply, prev: str, answer: list[str] | None = None) -> dict | None:
        """G6 plus the answer rule: an example that carries a line of this step's solution would hand the learner the
        answer (orchestrator probe, Nov 8: an example equal to the reference passed). One repair, then no example."""
        lang = p["language"]
        answer = answer or []
        got = real_example(lang, reply.example)
        if got is not None and not leaks_answer(got["code"], answer):
            return got
        s = p["plan"][i]
        problem = ("gives away the answer: it contains lines of the program the learner must write. Show the idea in a "
                   "DIFFERENT small program (other names, other values)") if got is not None else (
                  "did not run (it failed to compile or exited with an error)")
        text = (f"Language: {'C' if lang == 'c' else 'Python'}\nThe example program below {problem}. Write a small "
                f"COMPLETE program that shows: {s['concept']}. "
                f"Reply with JSON {{\"code\": \"...\", \"stdin\": \"\"}}.\n\nExample to replace:\n{reply.example.code}")
        try:
            fixed = structured_call(self.svc.llm, ExampleReply, instructions="You fix a tiny example program for a "
                                    "beginner lesson. It must compile, run and exit 0. Standard input and output "
                                    "only, deterministic.", text=text, purpose=EXAMPLE_PURPOSE, retries=2,
                                    timeout=getattr(self.svc.cfg, "llm_timeout_s", None))
        except (LLMBadOutput, LLMError):
            return None
        got = real_example(lang, fixed)
        return got if got is not None and not leaks_answer(got["code"], answer) else None

    # ------------------------------------------------------------ the check flow

    def _step_ctx(self, pid: str, i: Any, *, need_ready: bool = True) -> tuple[dict, int]:
        """(project state, step index) after the shared checks: known project, index in the plan, step unlocked."""
        from codecoach.service import ServiceError
        p = self._settle(self._require(pid))
        n = len(p.get("plan") or [])
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < n:
            raise ServiceError(404, "unknown_step", "That step does not exist in this project.")
        if i > int(p.get("current") or 0):
            raise ServiceError(409, "step_locked", "Finish the earlier steps first. This step opens after them.")
        return p, i

    def _ready_record(self, pid: str, i: int) -> dict:
        from codecoach.service import ServiceError
        rec = self._current_record(pid, i)
        if rec is None or self._state(pid, i) == "building":
            raise ServiceError(409, "step_not_ready", "This step is still being built. Try again in a moment.")
        if rec.get("status") != "ready":
            raise ServiceError(409, "step_not_ready", "This step could not be built. Use Try again on it.")
        return rec

    def _bump(self, pid: str, i: int, **changes: Any) -> dict:
        """Update the per-step progress under the engine lock; ints in `changes` named `add_*` are added."""
        with self._lock:
            p = self.load(pid)
            if p is None:
                return {}
            prog = p.setdefault("progress", {})
            pr = prog.setdefault(str(i), {})
            for k, v in changes.items():
                if k.startswith("add_"):
                    pr[k[4:]] = int(pr.get(k[4:]) or 0) + v
                else:
                    pr[k] = v
            self.save(p)
            return dict(pr)

    def _save_code(self, pid: str, code: str) -> None:
        with self._lock:
            p = self.load(pid)
            if p is not None:
                p["code"] = code
                self.save(p)

    @staticmethod
    def _check_code_arg(code: Any) -> str:
        from codecoach.service import ServiceError
        if not isinstance(code, str):
            raise ServiceError(422, "bad_request", "Send your code as text.")
        if len(code) > MAX_CODE_CHARS:
            raise ServiceError(413, "too_large", "That code is too long.")
        return code

    def step(self, pid: str, i: int) -> dict:
        """The public Step. Opening a step starts its build if needed and prefetches the next one."""
        p, i = self._step_ctx(pid, i)
        self.ensure_built(pid, i, prefetch=True)
        p = self.load(pid) or p
        return self._public_step(p, i)

    def _public_step(self, p: dict, i: int) -> dict:
        pid = p["id"]
        prog = (p.get("progress") or {}).get(str(i)) or {}
        cur = int(p.get("current") or 0)
        meta = p["plan"][i]
        status = self._step_status(pid, i, cur, prog)
        out = {"index": i, "title": meta["title"], "goal": meta["goal"], "concept": meta["concept"],
               "status": status, "lesson": [], "example": None, "task": "", "visible_tests": [],
               "hidden_count": 0, "hints_shown": [], "hints_left": 0, "can_show_solution": False,
               "starting_code": "", "reason": None}
        rec = self._current_record(pid, i)
        if rec is None or status in ("building", "planned", "locked"):
            return out
        if rec.get("status") != "ready":
            out["reason"] = rec.get("reason") or "The step could not be built."
            return out
        hints = [h for h in rec.get("hints") or [] if isinstance(h, str)]
        used = min(int(prog.get("hints_used") or 0), len(hints))
        ex = rec.get("example")
        out.update(
            lesson=list(rec.get("lesson") or []),
            example=({"code": ex["code"], "stdin": ex.get("stdin", ""), "stdout": ex.get("stdout", ""),
                      "ran": True} if isinstance(ex, dict) else None),
            task=rec.get("task") or "",
            visible_tests=[{"name": t["name"], "stdin": t["stdin"], "expected_stdout": t["expected_stdout"]}
                           for t in rec.get("visible_tests") or []],
            hidden_count=len(rec.get("hidden_tests") or []),
            hints_shown=hints[:used], hints_left=len(hints) - used,
            can_show_solution=self._can_show_solution(prog, len(hints)),
            starting_code=p.get("code") or STARTERS[p["language"]])
        return out

    @staticmethod
    def _can_show_solution(prog: dict, n_hints: int) -> bool:
        return int(prog.get("failed_checks") or 0) >= 3 and int(prog.get("hints_used") or 0) >= n_hints

    @staticmethod
    def _payload_of(p: dict, rec: dict) -> CodePayload:
        tests = lambda rows: [CodeTest(name=t["name"], stdin=t["stdin"], expected_stdout=t["expected_stdout"])  # noqa: E731
                              for t in rows]
        return CodePayload(language=p["language"], starter=STARTERS[p["language"]], visible_tests=tests(rec["visible_tests"]),
                           hidden_tests=tests(rec["hidden_tests"]), reference_solution=rec["reference_solution"])

    def _run_out(self, pid: str, i: int, res: Any, *, hidden: bool) -> dict:
        from codecoach.core.models import RunResponse
        rows = [t for t in (res.per_test or []) if hidden or t.visible]
        if hidden:      # a hidden result never carries what the program printed or what was expected
            rows = [t if t.visible else t.model_copy(update={"stdout": None, "expected": None}) for t in rows]
        return RunResponse(item_id=f"{pid}:{i}", ok=bool(res.ok), passed=res.passed, total=res.total, results=rows,
                           compile_error=res.compile_error, timed_out=res.timed_out).model_dump(mode="json")

    def _undecided(self, res: Any) -> None:
        from codecoach.service import ServiceError
        if res.undecided:
            raise ServiceError(503, "sandbox_unavailable", res.detail or "Install bubblewrap (bwrap) to run your code.")

    def run(self, pid: str, i: int, code: Any) -> dict:
        """Visible tests only. Saves the code. Same shape as POST /items/{id}/run."""
        code = self._check_code_arg(code)
        p, i = self._step_ctx(pid, i)
        rec = self._ready_record(pid, i)
        res = run_visible(self._payload_of(p, rec), code)
        self._undecided(res)
        self._save_code(pid, code)
        return self._run_out(pid, i, res, hidden=False)

    def check(self, pid: str, i: int, code: Any) -> dict:
        """Visible + hidden tests. A pass finishes the step, moves on and prefetches; a fail is counted."""
        code = self._check_code_arg(code)
        p, i = self._step_ctx(pid, i)
        rec = self._ready_record(pid, i)
        res = grade(self._payload_of(p, rec), code)
        self._undecided(res)
        out = self._run_out(pid, i, res, hidden=True)
        passed = bool(res.ok)
        n = len(p["plan"])
        self._save_code(pid, code)
        if not passed:
            self._bump(pid, i, add_attempts=1, add_failed_checks=1)
            out.update(step_passed=False, next_index=None, project_done=False)
            return out
        self._bump(pid, i, add_attempts=1, passed=True)
        done = False
        with self._lock:
            q = self.load(pid)
            if q is not None:
                q["current"] = max(int(q.get("current") or 0), i + 1) if i + 1 < n else int(q.get("current") or 0)
                done = all(((q.get("progress") or {}).get(str(j)) or {}).get("passed") for j in range(n))
                if done:
                    q["status"] = "done"
                self.save(q)
        nxt = i + 1 if i + 1 < n else None
        if nxt is not None:
            self.ensure_built(pid, nxt, prefetch=True)
        out.update(step_passed=True, next_index=nxt, project_done=done)
        return out

    def hint(self, pid: str, i: int) -> dict:
        from codecoach.service import ServiceError
        p, i = self._step_ctx(pid, i)
        rec = self._ready_record(pid, i)
        hints = [h for h in rec.get("hints") or [] if isinstance(h, str)]
        with self._lock:
            q = self.load(pid) or p
            used = int(((q.get("progress") or {}).get(str(i)) or {}).get("hints_used") or 0)
            if used >= len(hints):
                raise ServiceError(409, "no_hints", "There are no more hints for this step.")
            q.setdefault("progress", {}).setdefault(str(i), {})["hints_used"] = used + 1
            self.save(q)
        return {"hint": hints[used], "hints_left": len(hints) - used - 1}

    def solution(self, pid: str, i: int) -> dict:
        from codecoach.service import ServiceError
        p, i = self._step_ctx(pid, i)
        rec = self._ready_record(pid, i)
        prog = (p.get("progress") or {}).get(str(i)) or {}
        n_hints = len([h for h in rec.get("hints") or [] if isinstance(h, str)])
        if not self._can_show_solution(prog, n_hints):
            raise ServiceError(409, "solution_locked",
                               "CodeCoach's solution unlocks after three checks that did not pass and every hint used.")
        self._bump(pid, i, with_help=True)
        return {"code": rec["reference_solution"]}


# ------------------------------------------------------------------ request bodies (routes)

class ProjectCreateRequest(BaseModel):          # POST /projects
    idea: str = ""
    language: str = ""


class ProjectCodeRequest(BaseModel):            # POST /projects/{pid}/steps/{i}/run and /check
    code: str
