"""Step build gates G1-G7 and the retry policy, on FakeLLM canned replies and the REAL sandbox."""

from types import SimpleNamespace

import pytest
from proj_helpers import (C_STEP0, C_STEP1, NEEDS_SANDBOX, PY_STEP1, Script, project, step_reply)

import codecoach.project as P
from codecoach.check.code_runner import sandbox_status
from codecoach.core.models import CodePayload, CodeTest

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)


def build(script, i=0, language="python", prefetch=False):
    svc, eng, pid, llm = project(script, language)
    eng.ensure_built(pid, i, prefetch=prefetch)
    return eng, pid, llm, svc


def step_calls(llm):
    return [c for c in llm.calls if c.purpose == P.STEP_PURPOSE]


def test_good_step_is_ready_with_real_example_and_version():
    eng, pid, llm, _ = build(Script(steps=[step_reply()]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "ready" and rec["version"] == P.PROJECT_PROMPT_VERSION and rec["attempts"] == 1
    assert rec["example"] == {"code": "print('hi')\n", "stdin": "", "stdout": "hi\n", "ran": True}
    assert len(rec["hints"]) == 3 and len(rec["hidden_tests"]) == 1 and rec["reference_solution"].startswith("name")
    assert eng.get(pid)["steps"][0]["status"] == "ready"


# ---- G1
def test_g1_failing_reference_is_retried_then_unavailable():
    bad = step_reply(reference_solution="print('Hello')\n")
    eng, pid, llm, _ = build(Script(steps=[bad, bad, bad, step_reply()]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "own solution did not pass" in rec["reason"]
    assert len(step_calls(llm)) == 3                                  # first try + at most 2 retries
    assert "rejected" in step_calls(llm)[1].text and "own solution" in step_calls(llm)[1].text
    assert eng.get(pid)["steps"][0]["status"] == "unavailable"
    assert eng.ensure_built(pid, 0, prefetch=False) == "unavailable" and len(step_calls(llm)) == 3   # not rebuilt


def test_g1_one_retry_then_success_and_retry_endpoint_rebuilds():
    bad = step_reply(reference_solution="print('Hello')\n")
    eng, pid, llm, _ = build(Script(steps=[bad, step_reply()]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "ready" and rec["attempts"] == 2
    # retry is only for unavailable steps
    with pytest.raises(Exception) as e:
        eng.retry(pid, 0)
    assert getattr(e.value, "code", "") == "not_unavailable"


def test_retry_rebuilds_an_unavailable_step():
    bad = step_reply(reference_solution="print('Hello')\n")
    eng, pid, llm, _ = build(Script(steps=[bad, bad, bad]))
    assert eng.step_record(pid, 0)["status"] == "unavailable"
    llm._script.steps.append(step_reply())            # type: ignore[attr-defined]
    assert eng.retry(pid, 0) == "ready"
    assert eng.step_record(pid, 0)["status"] == "ready"


def test_compile_error_reference_is_a_g1_failure():
    c = step_reply(**dict(C_STEP0, reference_solution="int main(void) { return }\n"))
    eng, pid, llm, _ = build(Script(steps=[c, c, c]), language="c")
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "did not compile" in rec["reason"]


# ---- G2
def test_g2_previous_reference_already_passes_every_new_test():
    same = dict(PY_STEP1, reference_solution="name = input()\nprint('Hello, ' + name + '!')\n# again\n",
                visible_tests=step_reply()["visible_tests"], hidden_tests=step_reply()["hidden_tests"])
    eng, pid, llm, _ = build(Script(steps=[step_reply(), step_reply(**same), step_reply(**same),
                                           step_reply(**same)]), i=1)
    assert eng.step_record(pid, 0)["status"] == "ready"
    rec = eng.step_record(pid, 1)
    assert rec["status"] == "unavailable" and "teaches nothing new" in rec["reason"]
    assert len(step_calls(llm)) == 4                                   # step 0 once, step 1 three times


def test_g2_starter_counts_as_previous_for_step_zero():
    assert P.STARTERS["python"].startswith("#")
    eng, pid, llm, _ = build(Script(steps=[step_reply()]))             # starter prints nothing, so the test is new
    assert eng.step_record(pid, 0)["status"] == "ready"
    assert "# Your program starts here" in step_calls(llm)[0].text


# ---- G3
PY_ADDR = "print(id(object()))\n"
C_ADDR = '#include <stdio.h>\nint main(void)\n{\n    int x = 0;\n    printf("%p\\n", (void *)&x);\n    return 0;\n}\n'


@pytest.mark.parametrize("lang,code", [("python", PY_ADDR), ("c", C_ADDR)])
def test_g3_gate_catches_a_nondeterministic_reference(lang, code):
    payload = CodePayload(language=lang, visible_tests=[CodeTest(name="a", stdin="", expected_stdout="x")],
                          hidden_tests=[CodeTest(name="b", stdin="1\n", expected_stdout="x")], reference_solution=code)
    with pytest.raises(P.GateFail) as e:
        P.gate_deterministic(payload)
    assert e.value.gate == "G3" and "different output" in e.value.reason


def test_g3_a_deterministic_reference_passes_the_gate():
    P.gate_deterministic(CodePayload(language="python", visible_tests=[CodeTest(name="a", stdin="", expected_stdout="x")],
                                     reference_solution="print(42)\n"))


def test_g3_rejects_in_the_build_and_retries(monkeypatch):
    monkeypatch.setattr(P, "check_reference", lambda payload: SimpleNamespace(ok=True, undecided=False))
    r = step_reply(reference_solution=PY_ADDR)
    eng, pid, llm, _ = build(Script(steps=[r, r, r]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "different output" in rec["reason"] and len(step_calls(llm)) == 3


# ---- G4
def test_g4_missing_hidden_test():
    r = step_reply(hidden_tests=[])
    eng, pid, llm, _ = build(Script(steps=[r, r, r]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "visible and one hidden" in rec["reason"]


def test_g4_missing_visible_test():
    r = step_reply(visible_tests=[])
    eng, pid, llm, _ = build(Script(steps=[r, r, r]))
    assert eng.step_record(pid, 0)["status"] == "unavailable"


def test_g4_hidden_stdin_equal_to_a_visible_one():
    r = step_reply(hidden_tests=[{"name": "same", "stdin": "Ann\n", "expected_stdout": "Hello, Ann!\n"}])
    eng, pid, llm, _ = build(Script(steps=[r, r, r]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "repeats the input" in rec["reason"]


# ---- G5
def test_g5_step_adding_41_lines_is_rejected_and_40_is_fine():
    def prog(n):
        return "".join(f"print('line {k}')\n" for k in range(n))

    def reply(n):
        out = "".join(f"line {k}\n" for k in range(n))
        return step_reply(reference_solution=prog(n),
                          visible_tests=[{"name": "v", "stdin": "1\n", "expected_stdout": out}],
                          hidden_tests=[{"name": "h", "stdin": "2\n", "expected_stdout": out}])

    eng, pid, llm, _ = build(Script(steps=[reply(41)] * 3))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "41 lines" in rec["reason"]
    eng2, pid2, _, _ = build(Script(steps=[reply(40)]))
    assert eng2.step_record(pid2, 0)["status"] == "ready"


def test_added_lines_counts_only_new_nonblank_lines():
    assert P.added_lines("a\nb\n", "a\n\n\nb\nc\n") == 1
    assert P.added_lines("a\nb\n", "x\ny\n") == 2
    assert P.added_lines("", "") == 0


# ---- G6
def test_g6_stored_stdout_is_the_real_one_not_the_models_claim():
    r = step_reply(example={"code": "print('hello')\n", "stdin": "", "stdout": "WRONG CLAIM\n"})
    eng, pid, llm, _ = build(Script(steps=[r]))
    ex = eng.step_record(pid, 0)["example"]
    assert ex["stdout"] == "hello\n" and ex["ran"] is True and "WRONG" not in str(eng.step_record(pid, 0)["example"])


def test_g6_example_uses_its_stdin():
    r = step_reply(example={"code": "print(input().upper())\n", "stdin": "abc\n"})
    eng, pid, _, _ = build(Script(steps=[r]))
    assert eng.step_record(pid, 0)["example"]["stdout"] == "ABC\n"


def test_g6_crashing_example_is_repaired_once():
    crash = step_reply(example={"code": "raise SystemExit(3)\n", "stdin": ""})
    eng, pid, llm, _ = build(Script(steps=[crash], examples=[{"code": "print('fixed')\n", "stdin": ""}]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "ready" and rec["example"]["stdout"] == "fixed\n"
    assert [c.purpose for c in llm.calls].count(P.EXAMPLE_PURPOSE) == 1


def test_g6_example_still_crashing_after_repair_is_null_and_step_goes_on():
    crash = step_reply(example={"code": "raise SystemExit(3)\n", "stdin": ""})
    eng, pid, llm, _ = build(Script(steps=[crash], examples=[{"code": "1/0\n", "stdin": ""}]))
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "ready" and rec["example"] is None
    assert [c.purpose for c in llm.calls].count(P.EXAMPLE_PURPOSE) == 1       # one repair, not more
    assert len(step_calls(llm)) == 1                                            # and no whole-step retry


def test_g6_repair_call_failing_leaves_example_null():
    crash = step_reply(example={"code": "raise SystemExit(3)\n", "stdin": ""})
    eng, pid, llm, _ = build(Script(steps=[crash], examples=[]))               # repair gets None -> bad output
    assert eng.step_record(pid, 0)["example"] is None


# ---- G7
def test_g7_hint_with_a_new_reference_line_is_dropped():
    leak = "Just write print('Hello, ' + name + '!') here."
    r = step_reply(hints=["Think about input.", leak, "print(___ + name)"])
    eng, pid, _, _ = build(Script(steps=[r]))
    assert eng.step_record(pid, 0)["hints"] == ["Think about input.", "print(___ + name)"]


def test_g7_line_already_in_previous_reference_or_short_lines_are_fine():
    prev, ref = "name = input()\n", "name = input()\nx = 1\nprint('Hello, ' + name + '!')\n"
    hints = ["Keep name = input() as it is.", "Then x = 1 comes next.", "print('Hello, ' + name + '!')"]
    assert P.drop_leaky_hints(hints, prev, ref) == hints[:2]
    assert P.leaked_lines(prev, ref) == ["print('Hello, ' + name + '!')"]


def test_g7_all_three_may_go():
    ref = "name = input()\nprint('Hello, ' + name + '!')\n"
    hs = ["print('Hello, ' + name + '!')"] * 3
    assert P.drop_leaky_hints(hs, "", ref) == []


# ---- end to end
def test_python_project_builds_two_steps_end_to_end():
    script = Script(steps=[step_reply(), step_reply(**PY_STEP1)])
    svc, eng, pid, llm = project(script, "python")
    assert eng.ensure_built(pid, 0) == "ready"                          # builds step 0, prefetches step 1
    r0, r1 = eng.step_record(pid, 0), eng.step_record(pid, 1)
    assert r0["status"] == r1["status"] == "ready"
    assert "Hello, ' + name" in step_calls(llm)[1].text                 # step 1 was built from step 0's reference
    assert [s["status"] for s in eng.get(pid)["steps"]] == ["ready", "locked", "locked", "locked"]
    assert eng.ensure_built(pid, 0) == "ready" and len(step_calls(llm)) == 2     # nothing rebuilt


def test_c_project_builds_two_steps_end_to_end():
    script = Script(steps=[step_reply(**C_STEP0), step_reply(**C_STEP1)])
    svc, eng, pid, llm = project(script, "c")
    assert eng.ensure_built(pid, 0) == "ready"
    r0, r1 = eng.step_record(pid, 0), eng.step_record(pid, 1)
    assert r0["status"] == r1["status"] == "ready", (r0.get("reason"), r1.get("reason"))
    assert r0["example"]["stdout"] == "hi\n" and r1["example"]["stdout"] == "yes\n"
    assert "#include <stdio.h>" in step_calls(llm)[0].text and "int main(void)" in step_calls(llm)[0].text
    assert "Double: %d" in step_calls(llm)[1].text


def test_prompt_version_mismatch_rebuilds_the_step_when_opened(monkeypatch):
    script = Script(steps=[step_reply(), step_reply()])
    svc, eng, pid, llm = project(script, "python")
    eng.ensure_built(pid, 0, prefetch=False)
    monkeypatch.setattr(P, "PROJECT_PROMPT_VERSION", "99")
    assert eng.get(pid)["steps"][0]["status"] == "planned"
    eng.ensure_built(pid, 0, prefetch=False)
    assert eng.step_record(pid, 0)["version"] == "99" and len(step_calls(llm)) == 2


def test_a_passed_step_is_not_rebuilt_on_version_change(monkeypatch):
    script = Script(steps=[step_reply(), step_reply()])
    svc, eng, pid, llm = project(script, "python")
    eng.ensure_built(pid, 0, prefetch=False)
    st = eng.load(pid)
    st["progress"] = {"0": {"passed": True}}
    st["current"] = 1
    eng.save(st)
    monkeypatch.setattr(P, "PROJECT_PROMPT_VERSION", "2")
    eng.ensure_built(pid, 0, prefetch=False)
    assert len(step_calls(llm)) == 1


def test_bad_model_output_counts_as_a_retry_then_unavailable():
    eng, pid, llm, _ = build(Script(steps=[]))                         # every call returns None
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "could not be used" in rec["reason"]
    assert len(step_calls(llm)) == 3 * 2                               # 3 attempts, each structured_call asks twice


def test_model_unreachable_is_unavailable_at_once():
    from codecoach.core.llm import LLMUnavailable
    s = Script(steps=[LLMUnavailable("down")])
    svc, eng, pid, llm = project(s, "python")
    eng.ensure_built(pid, 0, prefetch=False)
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "could not be reached" in rec["reason"] and len(step_calls(llm)) == 1


def test_delete_while_nothing_is_left_behind():
    script = Script(steps=[step_reply(), step_reply(**PY_STEP1)])
    svc, eng, pid, llm = project(script, "python")
    eng.ensure_built(pid, 0)
    eng.delete(pid)
    assert svc.store.get_meta(f"project:{pid}:step:0") is None and svc.store.get_meta(f"project:{pid}:step:1") is None


def test_builds_serialise_on_the_module_lock():
    import threading
    script = Script(steps=[step_reply()])
    svc, eng, pid, llm = project(script, "python")
    got = []
    P._BUILD_LOCK.acquire()
    t = threading.Thread(target=lambda: (eng.ensure_built(pid, 0, prefetch=False), got.append(1)))
    t.start()
    t.join(0.5)
    assert t.is_alive() or eng.step_record(pid, 0) is None             # blocked behind the held lock
    P._BUILD_LOCK.release()
    t.join(30)
    assert eng.step_record(pid, 0)["status"] == "ready"
