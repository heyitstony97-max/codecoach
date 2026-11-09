"""Math checker: SymPy computes the truth. SymPy is imported lazily and ONLY inside a child process.

Every SymPy call is one `python -P _sympy_worker.py` child with a wall-clock timeout (process group
killed). If the symbolic stage times out, a second child runs a numeric random-point check (>= 5 points,
rel tol 1e-6, domain errors skipped). If that is also unclear, the result is `undecided`.

Public API:
    check_math(item, response, *, mode="submit") -> CheckResult      # registered as kind "math"
    verify_key(payload) -> CheckResult           # the key_mismatch gate: does payload.given_answer hold?
    truth(payload) -> str | None                 # SymPy's answer as text (for "correct answer" reveal)
    plot_points(expr, var, lo, hi, n) -> list[tuple[float, float | None]]
"""

from __future__ import annotations

import json
import os
import re
import resource
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from codecoach.core.models import MathPayload
from codecoach.core.registry import CheckResult

SYMBOLIC_TIMEOUT_S = 3.0
NUMERIC_TIMEOUT_S = 3.0
_WORKER = str(Path(__file__).with_name("_sympy_worker.py"))
MAX_RESPONSE_LEN = 300


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (8, 9))
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024 ** 3, 3 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _child_pythonpath() -> str:
    """The package directories this process imports from, for the worker.

    Hermes puts its dependency environment on sys.path at startup; it is not the interpreter's own prefix.
    A child started with sys.executable and a clean environment therefore cannot see SymPy, even though
    this process can. Hand the child the same site-packages directories, and nothing else."""
    keep = ("site-packages", "dist-packages")
    return os.pathsep.join(p for p in sys.path if p and os.path.basename(p.rstrip(os.sep)) in keep and os.path.isdir(p))


def _call(req: dict, timeout: float) -> dict | None:
    """Run one worker. None on timeout or crash."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin"), "PYTHONHASHSEED": "0",
           **{k: v for k, v in os.environ.items() if k in ("VIRTUAL_ENV", "LANG", "HOME")}}
    if path := _child_pythonpath():
        env["PYTHONPATH"] = path
    try:
        proc = subprocess.Popen(
            [sys.executable, "-P", "-B", _WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, process_group=0, preexec_fn=_limits, env=env)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(json.dumps(req), timeout=timeout)
    except subprocess.TimeoutExpired:
        out = None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _undecided(detail: str) -> CheckResult:
    return CheckResult(ok=False, score=0.0, detail=detail, undecided=True)


def _run(payload: MathPayload, response: str | None) -> CheckResult:
    req = {"op": payload.op, "expr": payload.expr, "var": payload.var, "subs": payload.subs,
           "places": payload.places, "response": response, "mode": "symbolic"}
    r = _call(req, SYMBOLIC_TIMEOUT_S)
    via_numeric = False
    if r is None:
        if payload.op == "evaluate":
            return _undecided("The math checker timed out; try again.")
        req["mode"] = "numeric"
        r = _call(req, NUMERIC_TIMEOUT_S)
        via_numeric = True
        if r is None:
            return _undecided("The math checker timed out; your answer was not marked wrong.")
    verdict = r.get("verdict")
    fb = r.get("feedback", "")
    if verdict == "correct":
        return CheckResult(ok=True, score=1.0, detail="Correct" + (" (your form is different from the answer key but has the same value)" if via_numeric else ""))
    if verdict == "wrong":
        return CheckResult(ok=False, score=0.0, detail=fb or "Not correct")
    if verdict == "unparseable":
        return CheckResult(ok=False, score=0.0, detail=fb or "I could not read your answer.")
    if verdict == "bad_item":
        return _undecided(fb or "This problem could not be checked.")
    return _undecided(fb or "Could not decide.")


def check_math(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    """response = the learner's expression / solutions / value as a string."""
    payload = getattr(item, "payload", None)
    if not isinstance(payload, MathPayload):
        return CheckResult(ok=False, score=0.0, detail="Not a math item.")
    if isinstance(response, (int, float)) and not isinstance(response, bool):
        response = repr(response)
    if not isinstance(response, str) or not response.strip():
        return CheckResult(ok=False, score=0.0, detail="Type your answer first.")
    if len(response) > MAX_RESPONSE_LEN:
        return CheckResult(ok=False, score=0.0, detail="That answer is too long.")
    if payload.op == "evaluate":
        bare = strip_unit(response, getattr(item, "prompt", "") or "")
        if bare != response:
            # `25%` may mean 0.25 (hundredths) or 25 with its unit: right if either reading is right
            first = _run(payload, response)
            if first.ok:
                return first
            second = _run(payload, bare)
            return second if second.ok else first
    return _run(payload, response)


_TRAIL = re.compile(r"^\s*([+\-\u2212]?[\d][\d.,/ ]*?)\s*(%|[A-Za-z\u00b0\u00b5\u03bc\u03a9][A-Za-z\u00b0\u00b5\u03bc\u03a9 ./\u00b2\u00b3^0-9-]*)\s*$")
_PERCENT = {"%", "percent", "per cent", "pct"}
_PROMPT_UNIT = re.compile(r"\d\s*([A-Za-z\u00b5\u03bc\u03a9\u00b0]+)")


def strip_unit(response: str, prompt: str = "") -> str:
    """`5 m`, `5 meters`, `25 percent`, `25 %` -> the bare number, for an evaluate item (units are optional).
    A unit that converts to a DIFFERENT size of the unit the question uses (`5 km` where the question says
    metres) is left alone, so it fails as "not a number". Anything that is not number + unit is returned as is."""
    m = _TRAIL.match(response)
    if not m:
        return response
    num, unit = m.group(1).strip(), m.group(2).strip()
    if not num or num[-1] in ".,/":
        return response
    if unit.lower() in _PERCENT:
        return num
    if re.fullmatch(r"[eE]\d*", unit) or unit.lower() in {"x", "pi", "e"}:
        return response                   # 2e5, 3 pi, 2 x: an expression, not a unit
    if re.search(r"\d", unit.replace("^", "").replace("\u00b2", "").replace("\u00b3", "")) and "/" not in unit:
        return response
    from codecoach.check import numeric as N          # lazy: pint-free unit table
    try:
        N.parse_unit(unit)
    except Exception:
        return num                        # a word like "apples": not a unit we know, nothing to contradict
    for pu in _PROMPT_UNIT.findall(prompt):
        try:
            f = N.convert(1.0, unit, pu)
        except Exception:
            continue
        if abs(f - 1.0) > 1e-9:
            return response               # a different unit of the same kind
    return num


def verify_key(payload: MathPayload) -> CheckResult:
    """Gate for generated items: the LLM's `given_answer` must agree with SymPy, else the item is
    dropped as `key_mismatch`. undecided=True means SymPy could not settle it (treat as not verified)."""
    if not payload.given_answer:
        return CheckResult(ok=False, score=0.0, detail="This problem has no answer to check against.")
    return _run(payload, payload.given_answer)


def truth(payload: MathPayload) -> str | None:
    """SymPy's own answer as text, or None when it cannot be computed in time."""
    r = _call({"op": payload.op, "expr": payload.expr, "var": payload.var, "subs": payload.subs,
               "response": None, "mode": "symbolic"}, SYMBOLIC_TIMEOUT_S)
    if r and r.get("verdict") == "truth":
        return r.get("truth")
    return None


def plot_points(expr: str, var: str, lo: float, hi: float, n: int = 200) -> list[tuple[float, float | None]]:
    """[(x, y|None)] for the diagram endpoint; None marks a gap (undefined, complex or huge).
    Raises ValueError if the expression cannot be read or computed in time."""
    if not (lo < hi):
        raise ValueError("lo must be below hi")
    n = max(2, min(int(n), 2000))
    r = _call({"op": "plot", "expr": expr, "var": var or "x", "lo": lo, "hi": hi, "n": n}, SYMBOLIC_TIMEOUT_S)
    if r is None:
        raise ValueError("plot timed out")
    if r.get("verdict") != "plot":
        raise ValueError(r.get("feedback") or "cannot plot")
    return [(float(x), None if y is None else float(y)) for x, y in r["points"]]
