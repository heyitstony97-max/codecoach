"""Grade book, end to end in the REAL sandbox, once per language that ships starter data.

Create (no idea, no model), every step built through the gates G1-G8, the starting code fails the step's tests, the
reference passes them, a wrong program fails, hints carry no solution line, finish, download. The model is a FakeLLM
that fails the test if it is called, or is missing altogether.
"""

import pytest
from starters_helpers import NEEDS_SANDBOX, STARTER, make, new_lines, shipped, skip_unless_ready

from codecoach import lang, starters
from codecoach.check.code_runner import sandbox_status
from codecoach.project import leaks_answer

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)

LANGS = shipped()


def _steps(lid):
    return starters.part(STARTER, lid)["steps"]


@pytest.mark.parametrize("lid", LANGS)
def test_whole_project_with_no_model_call(lid):
    skip_unless_ready(lid)
    svc, eng, model = make("fake")
    pid = eng.create("", lid, starter=STARTER)
    g = eng.get(pid)
    assert g["status"] == "ready" and g["starter"] == STARTER and g["language"] == lid
    assert len(g["steps"]) == 4 and g["title"] == "Grade book" and g["code"] == lang.spec(lid).starter

    data = _steps(lid)
    prev = lang.spec(lid).starter
    for i in range(4):
        s = eng.step(pid, i)
        assert s["status"] == "ready", f"step {i + 1}: {s.get('reason')}"
        assert 2 <= len(s["lesson"]) <= 5 and s["example"] and s["example"]["ran"] is True
        assert s["example"]["stdout"].strip() != ""
        assert s["hints_left"] == 3 and s["hidden_count"] == 2 and len(s["visible_tests"]) == 2
        assert s["starting_code"] == prev

        rec = eng.step_record(pid, i)
        assert rec["hints"] == data[i]["hints"] and rec["lesson"] == data[i]["lesson"]   # no hint or sentence was dropped
        for h in rec["hints"]:
            assert not leaks_answer(h, new_lines(prev, data[i]["solution"], lid)), f"step {i + 1}: a hint carries the answer"
        assert rec["example"]["code"] == data[i]["example"]["code"]

        # the code the learner starts from, and an empty program, fail
        for bad in (prev, lang.spec(lid).starter):
            r = eng.check(pid, i, bad)
            assert r["step_passed"] is False and r["next_index"] is None
        assert eng.get(pid)["steps"][i]["attempts"] == 2
        assert eng.run(pid, i, prev)["ok"] is False

        # the reference passes, the step moves on and the code is carried forward
        ok = eng.check(pid, i, data[i]["solution"])
        assert ok["step_passed"] is True and ok["total"] == 4, ok
        assert ok["next_index"] == (i + 1 if i < 3 else None) and ok["project_done"] is (i == 3)
        prev = data[i]["solution"]
        assert eng.get(pid)["code"] == prev

    g = eng.get(pid)
    assert g["status"] == "done" and all(s["status"] == "passed" for s in g["steps"])
    assert g["code"] == data[3]["solution"] and g["ext"] == lang.spec(lid).ext.lstrip(".")
    assert model.calls == []


@pytest.mark.parametrize("lid", LANGS)
def test_works_with_no_model_configured(lid):
    skip_unless_ready(lid)
    svc, eng, _ = make(None)
    pid = eng.create("", lid, starter=STARTER)
    assert eng.get(pid)["status"] == "ready"
    s0 = eng.step(pid, 0)
    assert s0["status"] == "ready", s0.get("reason")
    assert eng.step_record(pid, 1)["status"] == "ready"        # the next one was built ahead
    assert s0["task"].startswith("Read one whole number")


@pytest.mark.parametrize("lid", LANGS)
def test_hint_ladder_and_solution_lock(lid):
    skip_unless_ready(lid)
    svc, eng, model = make("fake")
    pid = eng.create("", lid, starter=STARTER)
    eng.step(pid, 0)
    got = []
    for left in (2, 1, 0):
        h = eng.hint(pid, 0)
        got.append(h["hint"])
        assert h["hints_left"] == left
    assert got == _steps(lid)[0]["hints"]
    assert len(set(got)) == 3
    with pytest.raises(Exception) as e:
        eng.hint(pid, 0)
    assert getattr(e.value, "code", "") == "no_hints"
    with pytest.raises(Exception) as e:
        eng.solution(pid, 0)
    assert getattr(e.value, "code", "") == "solution_locked"
    for _ in range(3):
        eng.check(pid, 0, lang.spec(lid).starter)
    assert eng.solution(pid, 0)["code"] == _steps(lid)[0]["solution"]
    assert model.calls == []


@pytest.mark.parametrize("lid", LANGS)
def test_lessons_are_plain_text(lid):
    for st in _steps(lid):
        assert 2 <= len(st["lesson"]) <= 5 and len(st["hints"]) == 3
        text = " ".join(st["lesson"] + st["hints"])
        assert text.isascii() and "AI" not in text.split()
        assert all(s.strip().endswith((".", "?")) for s in st["lesson"])
