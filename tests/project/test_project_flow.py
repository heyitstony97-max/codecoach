"""The check flow: open, run, check, hint, solution, retry, delete. FakeLLM replies and the REAL sandbox."""

import json

import pytest
from proj_helpers import NEEDS_SANDBOX, PY_STEP1, Script, project, step_reply

import codecoach.project as P
from codecoach.check.code_runner import sandbox_status
from codecoach.service import ServiceError

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)

GOOD0 = "name = input()\nprint('Hello, ' + name + '!')\n"
WRONG = "print('nope')\n"


def built(n_steps=2, plan_steps=4):
    steps = [step_reply()] + [step_reply(**PY_STEP1) for _ in range(n_steps - 1)]
    svc, eng, pid, llm = project(Script(steps=steps), "python", plan_steps)
    eng.step(pid, 0)
    return svc, eng, pid, llm


def err(fn, *a):
    with pytest.raises(ServiceError) as e:
        fn(*a)
    return e.value.status, e.value.code


def test_open_step_is_public_and_prefetches_the_next():
    _, eng, pid, _ = built()
    s = eng.step(pid, 0)
    assert s["status"] == "ready" and s["lesson"] and s["task"] and s["example"]["ran"] is True
    assert s["hidden_count"] == 1 and s["hints_shown"] == [] and s["hints_left"] == 3 and not s["can_show_solution"]
    assert s["visible_tests"] == [{"name": "ann", "stdin": "Ann\n", "expected_stdout": "Hello, Ann!\n"}]
    assert s["starting_code"] == "# Your program starts here\n"
    assert eng.get(pid)["steps"][1]["status"] == "locked"          # prefetched but not unlocked
    assert eng.step_record(pid, 1)["status"] == "ready"


def test_locked_and_unknown_steps():
    _, eng, pid, _ = built()
    assert err(eng.step, pid, 1) == (409, "step_locked")
    assert err(eng.step, pid, 4) == (404, "unknown_step")
    assert err(eng.step, pid, -1) == (404, "unknown_step")
    assert err(eng.step, "p_nope", 0) == (404, "unknown_project")
    assert err(eng.run, pid, 1, "x") == (409, "step_locked")


def test_run_visible_only_saves_code_and_shows_expected_vs_got():
    _, eng, pid, _ = built()
    r = eng.run(pid, 0, WRONG)
    assert r["ok"] is False and r["total"] == 1 and len(r["results"]) == 1
    assert r["results"][0]["stdout"] == "nope\n" and r["results"][0]["expected"].startswith("Hello, Ann!")
    assert eng.get(pid)["code"] == WRONG
    assert eng.run(pid, 0, GOOD0)["ok"] is True
    assert eng.get(pid)["steps"][0]["status"] == "ready"           # a run never passes a step


def test_run_rejects_bad_code_argument():
    _, eng, pid, _ = built()
    assert err(eng.run, pid, 0, 5) == (422, "bad_request")
    assert err(eng.run, pid, 0, "x" * (P.MAX_CODE_CHARS + 1)) == (413, "too_large")


def test_failed_check_counts_and_hides_hidden_results():
    _, eng, pid, _ = built()
    r = eng.check(pid, 0, WRONG)
    assert r["step_passed"] is False and r["next_index"] is None and r["project_done"] is False
    assert r["total"] == 2 and len(r["results"]) == 2
    hid = [t for t in r["results"] if not t["visible"]]
    assert len(hid) == 1 and hid[0]["stdout"] is None and hid[0]["expected"] is None
    st = eng.get(pid)["steps"][0]
    assert st["attempts"] == 1 and st["status"] == "ready"


def test_pass_moves_on_and_prefetches():
    _, eng, pid, _ = built(2, 4)
    r = eng.check(pid, 0, GOOD0)
    assert (r["step_passed"], r["next_index"], r["project_done"]) == (True, 1, False)
    g = eng.get(pid)
    assert g["current"] == 1 and g["steps"][0]["status"] == "passed" and g["code"] == GOOD0
    s1 = eng.step(pid, 1)
    assert s1["status"] == "ready" and s1["starting_code"] == GOOD0


def test_two_step_project_finishes():
    steps = [step_reply(), step_reply(**PY_STEP1)]
    svc, eng, pid, llm = project(Script(steps=steps, plan=_plan4()), "python")
    # rewrite the stored plan to two steps so the second one is the last
    p = eng.load(pid)
    p["plan"] = p["plan"][:2]
    svc.store.set_meta(f"project:{pid}", p)
    eng.step(pid, 0)
    assert eng.check(pid, 0, GOOD0)["step_passed"]
    eng.step(pid, 1)
    r = eng.check(pid, 1, PY_STEP1["reference_solution"])
    assert (r["step_passed"], r["next_index"], r["project_done"]) == (True, None, True)
    g = eng.get(pid)
    assert g["status"] == "done" and g["current"] == 1 and all(s["status"] == "passed" for s in g["steps"])


def _plan4():
    from proj_helpers import plan_reply
    return plan_reply(4)


def test_hint_ladder_then_no_hints():
    _, eng, pid, _ = built()
    texts = [eng.hint(pid, 0) for _ in range(3)]
    assert [t["hints_left"] for t in texts] == [2, 1, 0]
    assert texts[0]["hint"] == "Think about reading input."
    assert err(eng.hint, pid, 0) == (409, "no_hints")
    s = eng.step(pid, 0)
    assert len(s["hints_shown"]) == 3 and s["hints_left"] == 0
    assert eng.get(pid)["steps"][0]["hints_used"] == 3


def test_solution_needs_three_failed_checks_and_every_hint():
    _, eng, pid, _ = built()
    assert err(eng.solution, pid, 0) == (409, "solution_locked")
    for _ in range(3):
        eng.check(pid, 0, WRONG)
    assert err(eng.solution, pid, 0) == (409, "solution_locked")          # hints still unused
    for _ in range(3):
        eng.hint(pid, 0)
    assert eng.step(pid, 0)["can_show_solution"] is True
    assert eng.solution(pid, 0) == {"code": GOOD0}
    assert eng.get(pid)["steps"][0]["with_help"] is True


def test_solution_two_failed_checks_is_not_enough():
    _, eng, pid, _ = built()
    for _ in range(3):
        eng.hint(pid, 0)
    for _ in range(2):
        eng.check(pid, 0, WRONG)
    assert err(eng.solution, pid, 0) == (409, "solution_locked")


def test_retry_only_for_unavailable_steps():
    script = Script(steps=[step_reply()])
    svc, eng, pid, llm = project(script, "python")
    eng.step(pid, 0)
    assert err(eng.retry, pid, 0) == (409, "not_unavailable")
    svc.store.set_meta(f"project:{pid}:step:0", {"version": P.PROJECT_PROMPT_VERSION, "index": 0,
                                                 "status": "unavailable", "reason": "Broken.", "attempts": 3})
    assert eng.get(pid)["steps"][0]["status"] == "unavailable"
    assert eng.step(pid, 0)["reason"] == "Broken."
    assert err(eng.run, pid, 0, "x") == (409, "step_not_ready")
    script.steps.append(step_reply())
    eng.retry(pid, 0)
    assert eng.step_record(pid, 0)["status"] == "ready"
    assert err(eng.retry, pid, 5) == (409, "not_unavailable")


def test_delete_removes_everything():
    svc, eng, pid, _ = built()
    eng.delete(pid)
    assert err(eng.get, pid) == (404, "unknown_project")
    assert eng.list()["projects"] == []
    assert err(eng.check, pid, 0, GOOD0) == (404, "unknown_project")


def test_no_sandbox_is_503_on_run(monkeypatch):
    _, eng, pid, _ = built()
    import codecoach.check.code_runner as cr
    monkeypatch.setattr(cr, "sandbox_status", lambda *a, **k: "unavailable")
    assert err(eng.run, pid, 0, GOOD0) == (503, "sandbox_unavailable")
    assert err(eng.check, pid, 0, GOOD0) == (503, "sandbox_unavailable")


# ---------------------------------------------------------------- nothing server-only ever leaves

def test_no_public_response_leaks_server_only_fields():
    steps = [step_reply(**{"reference_solution": "name = input()\nprint('Hello, ' + name + '!')   # SECRETREF\n"}),
             step_reply(**PY_STEP1)]
    svc, eng, pid, _ = project(Script(steps=steps), "python")
    out = []

    def keep(x):
        out.append(json.dumps(x))
        return x

    keep(eng.list())
    keep(eng.get(pid))
    keep(eng.step(pid, 0))
    keep(eng.run(pid, 0, WRONG))
    for _ in range(3):
        keep(eng.check(pid, 0, WRONG))
        keep(eng.step(pid, 0))
        keep(eng.get(pid))
    keep(eng.hint(pid, 0))
    keep(eng.step(pid, 0))
    keep(eng.hint(pid, 0))
    sol_before = json.dumps(eng.step(pid, 0))
    assert "SECRETREF" not in sol_before and "Bob" not in sol_before and "print(___ + name)" not in sol_before
    blob = "\n".join(out)
    # the reference solution, hidden test input/expected, and the not-yet-shown third hint never appear
    for needle in ("SECRETREF", "print('Hello, ' + name + '!')", "Hello, Bob!", "Bob", "print(___ + name)"):
        assert needle not in blob, needle
    # the rest of the flow after unlocking, still without the solution outside its own response
    keep(eng.hint(pid, 0))
    sol = eng.solution(pid, 0)
    assert "SECRETREF" in sol["code"]
    out2 = [json.dumps(eng.step(pid, 0)), json.dumps(eng.get(pid))]
    keep(eng.check(pid, 0, "name = input()\nprint('Hello, ' + name + '!')\n"))
    out2 += [json.dumps(eng.step(pid, 1)), json.dumps(eng.get(pid)), json.dumps(eng.list())]
    blob2 = "\n".join(out2)
    for needle in ("SECRETREF", "Hello, Bob!", "stranger!')"):
        assert needle not in blob2, needle
    assert '"reference_solution"' not in blob + blob2 and '"hidden_tests"' not in blob + blob2
    assert '"hints"' not in blob + blob2
