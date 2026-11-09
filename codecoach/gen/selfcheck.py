"""Self-checks for the formats whose truth is computed, not quoted: code, math, numeric.

The real checkers are registered by codecoach/check (a2b) at runtime and reached ONLY through
core.registry. If no checker is registered, or it errors, or it cannot decide: drop (fail closed).
"""

from __future__ import annotations

from ..core.models import CodePayload, Item, MathPayload, NumericPayload
from ..core.registry import CheckResult, check
from ..ground.gate import Failure


def _run(item: Item, response) -> tuple[CheckResult | None, Failure | None]:
    try:
        r = check(item, response, mode="submit")
    except Exception as e:  # NotRegistered, checker crash: never serve what we could not verify
        return None, Failure("self_check_failed", f"checker {item.checker!r}: {type(e).__name__}: {e}"[:200])
    if r.undecided:
        return None, Failure("self_check_failed", f"checker could not decide: {r.detail}"[:200])
    return r, None


def _test_key(t) -> tuple:
    return (t.stdin.strip(), (t.expected_stdout or "").strip() if t.expected_stdout is not None else None,
            " ".join(t.harness.split()) if t.harness is not None else None)


def _has_fresh_hidden(p: CodePayload) -> bool:
    """At least one hidden test differs from every visible test (stdin + expected + harness)."""
    seen = {_test_key(t) for t in p.visible_tests}
    return any(_test_key(t) not in seen for t in p.hidden_tests)


def self_check(item: Item) -> list[Failure]:
    p = item.payload
    if isinstance(p, CodePayload):
        if not p.hidden_tests or not p.visible_tests:
            return [Failure("weak_tests", "needs at least one visible and one hidden test")]
        ref, err = _run(item, p.reference_solution)
        if err:
            return [err]
        assert ref is not None
        if not ref.ok:
            return [Failure("weak_tests", f"reference solution does not pass its own tests: {ref.detail}"[:200])]
        starter, err = _run(item, p.starter)
        if err:
            return [err]
        assert starter is not None
        if starter.per_test is not None:
            hidden_failed = any(not t.passed for t in starter.per_test if not t.visible)
        else:
            hidden_failed = not starter.ok
        if not hidden_failed:
            return [Failure("weak_tests", "the starter code already passes every hidden test")]
        # the learner must not see every test green before typing anything
        if starter.per_test is not None and not any(not t.passed for t in starter.per_test if t.visible):
            return [Failure("weak_tests", "starter already passes the visible tests")]
        if not _has_fresh_hidden(p):
            return [Failure("weak_tests", "every hidden test is a copy of a visible test")]
        return []
    if isinstance(p, MathPayload):
        if not p.given_answer or not p.given_answer.strip():
            return [Failure("key_mismatch", "no answer key given")]
        r, err = _run(item, p.given_answer)
        if err:
            return [err]
        assert r is not None
        if not r.ok:
            return [Failure("key_mismatch", f"computed answer disagrees with the key: {r.detail}"[:200])]
        return []
    if isinstance(p, NumericPayload):
        r, err = _run(item, f"{p.answer_value!r} {p.unit}".strip())
        if err:
            return [err]
        assert r is not None
        if not r.ok:
            return [Failure("key_mismatch", f"computed value is outside tolerance: {r.detail}"[:200])]
        return []
    return []
