"""Project engine skeleton: ideas, create/get/list/delete, storage keys, the plan build. FakeLLM only."""

import json

import pytest
from proj_helpers import Script, make_svc, plan_reply, project

import codecoach.project as P
from codecoach.core.llm import FakeLLM, LLMUnavailable
from codecoach.service import ServiceError


def new_engine(script=None, llm=True):
    fake = FakeLLM(script or Script(plan=plan_reply(5))) if llm else None
    svc = make_svc(fake)
    return P.ProjectEngine(svc), svc, fake


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch):
    monkeypatch.setattr(P, "sandbox_status", lambda *a, **k: "bwrap")     # skeleton tests never run code


def test_ideas_fixed_list_every_tier_a_language():
    ids = P.ProjectEngine(make_svc()).ideas()
    assert 4 <= len(ids) <= 20               # v1.1: at least one idea per Tier A language
    assert {"c", "python"} <= {i["language"] for i in ids}
    assert all(i["title"] and i["pitch"] for i in ids)


@pytest.mark.parametrize("idea", ["", "  ab  ", "x" * 301, None, 5])
def test_create_rejects_bad_idea(idea):
    eng, *_ = new_engine()
    with pytest.raises(ServiceError) as e:
        eng.create(idea, "c")
    assert e.value.status == 422


def test_create_rejects_bad_language_and_trims():
    eng, *_ = new_engine()
    with pytest.raises(ServiceError) as e:
        eng.create("a guessing game", "brainfuck")
    assert e.value.status == 422
    pid = eng.create("   a guessing game   ", "c")
    assert eng.get(pid)["idea"] == "a guessing game"
    eng.create("x" * 300, "python")                 # 300 is allowed


def test_create_needs_sandbox(monkeypatch):
    monkeypatch.setattr(P, "sandbox_status", lambda *a, **k: "unavailable")
    eng, _svc, llm = new_engine()
    with pytest.raises(ServiceError) as e:
        eng.create("a guessing game", "c")
    assert (e.value.status, e.value.code) == (503, "sandbox_unavailable")
    assert llm.calls == [] and eng.list()["sandbox"] == "unavailable"


def test_plan_ready_and_storage_keys():
    eng, svc, llm = new_engine()
    pid = eng.create("number guessing game where the secret comes from input", "c")
    p = eng.get(pid)
    assert p["status"] == "ready" and p["title"] == "Greeter" and p["reason"] is None
    assert [s["title"] for s in p["steps"]] == [f"Step {i}" for i in range(1, 6)]
    assert p["code"] == P.STARTERS["c"] and p["current"] == 0
    assert [s["status"] for s in p["steps"]] == ["planned", "locked", "locked", "locked", "locked"]
    assert svc.store.get_meta("projects:index") == [pid]
    assert svc.store.get_meta(f"project:{pid}")["plan"][0]["concept"] == "idea 1"
    assert [c.purpose for c in llm.calls] == [P.PLAN_PURPOSE]
    assert "number guessing game" in llm.calls[0].text and "C" in llm.calls[0].text


def test_plan_retries_a_bad_step_count_then_succeeds():
    s = Script()
    replies = iter([plan_reply(2), plan_reply(9), plan_reply(6)])
    s.plan = None
    llm = FakeLLM(lambda purpose, text: next(replies))
    eng = P.ProjectEngine(make_svc(llm))
    pid = eng.create("a word counter", "python")
    p = eng.get(pid)
    assert p["status"] == "ready" and len(p["steps"]) == 6
    assert len(llm.calls) == 3 and "4 to 8 steps" in llm.calls[1].text


def test_plan_empty_field_is_retried_then_failed_with_plain_reason():
    bad = plan_reply(4)
    bad["steps"][2]["concept"] = " "
    eng, svc, llm = new_engine(Script(plan=bad))
    pid = eng.create("a word counter", "python")
    p = eng.get(pid)
    assert p["status"] == "failed" and "could not be used" in p["reason"] and p["steps"] == []
    assert len(llm.calls) == 3


def test_plan_refused_shows_why_not():
    eng, *_ = new_engine(Script(plan={"feasible": False, "why_not": "That needs a graphics window.", "title": "",
                                      "summary": "", "steps": []}))
    p = eng.get(eng.create("a 3D racing game", "c"))
    assert p["status"] == "refused" and p["reason"] == "That needs a graphics window." and p["steps"] == []


def test_plan_model_unreachable_and_no_model():
    eng, *_ = new_engine(lambda *_: LLMUnavailable("down"))
    p = eng.get(eng.create("a word counter", "python"))
    assert p["status"] == "failed" and "could not be reached" in p["reason"]
    eng2, *_ = new_engine(llm=False)
    p2 = eng2.get(eng2.create("a word counter", "python"))
    assert p2["status"] == "failed" and "No model" in p2["reason"]


def test_list_newest_first_and_counts():
    eng, svc, _ = new_engine()
    a = eng.create("first idea here", "c")
    b = eng.create("second idea here", "python")
    st = svc.store.get_meta(f"project:{b}")
    st["progress"] = {"0": {"passed": True}, "1": {"passed": True}, "2": {"attempts": 3}}
    svc.store.set_meta(f"project:{b}", st)
    out = eng.list()
    assert [r["id"] for r in out["projects"]] == [b, a]
    assert out["projects"][0]["steps_total"] == 5 and out["projects"][0]["steps_passed"] == 2
    assert out["sandbox"] == "bwrap" and len(out["ideas"]) >= 4


def test_delete_removes_all_three_key_kinds_and_only_that_project():
    eng, svc, _ = new_engine()
    a = eng.create("first idea here", "c")
    b = eng.create("second idea here", "python")
    for pid in (a, b):
        svc.store.set_meta(f"project:{pid}:step:0", {"x": 1})
        svc.store.set_meta(f"project:{pid}:step:1", {"x": 1})
    eng.delete(a)
    assert svc.store.get_meta(f"project:{a}") is None
    assert svc.store.get_meta(f"project:{a}:step:0") is None and svc.store.get_meta(f"project:{a}:step:1") is None
    assert svc.store.get_meta("projects:index") == [b]
    assert svc.store.get_meta(f"project:{b}:step:1") == {"x": 1}
    with pytest.raises(ServiceError) as e:
        eng.get(a)
    assert e.value.status == 404
    with pytest.raises(ServiceError):
        eng.delete(a)


def test_unknown_or_malformed_id():
    eng, *_ = new_engine()
    for bad in ("p_nope", "", None, "p_1:step:0", 7):
        with pytest.raises(ServiceError) as e:
            eng.get(bad)
        assert e.value.status == 404


def test_planning_without_a_worker_settles_as_failed():
    eng, svc, _ = new_engine()
    state = {"id": "p_dead", "title": "x", "language": "c", "idea": "x", "status": "planning", "reason": None,
             "summary": "", "plan": [], "current": 0, "code": "", "progress": {}, "created_at": "t", "updated_at": "t"}
    svc.store.set_meta("project:p_dead", state)
    svc.store.set_meta("projects:index", ["p_dead"])
    assert eng.get("p_dead")["status"] == "failed"
    assert eng.list()["projects"][0]["status"] == "failed"


def test_get_and_list_never_carry_server_only_fields():
    eng, svc, _ = new_engine()
    pid = eng.create("a word counter", "python")
    svc.store.set_meta(f"project:{pid}:step:0", {"version": P.PROJECT_PROMPT_VERSION, "status": "ready",
                                                 "reference_solution": "SECRET_REF", "hidden_tests": [{"stdin": "SECRET_H"}],
                                                 "hints": ["SECRET_HINT"]})
    blob = json.dumps([eng.get(pid), eng.list()])
    for s in ("SECRET_REF", "SECRET_H", "SECRET_HINT", "reference_solution", "hidden_tests"):
        assert s not in blob


def test_ensure_built_does_nothing_without_sandbox_or_plan(monkeypatch):
    eng, svc, llm = new_engine()
    pid = eng.create("a word counter", "python")
    n = len(llm.calls)
    monkeypatch.setattr(P, "sandbox_status", lambda *a, **k: "unavailable")
    assert eng.ensure_built(pid, 0) == "none"
    monkeypatch.setattr(P, "sandbox_status", lambda *a, **k: "bwrap")
    assert eng.ensure_built(pid, 99) == "none" and eng.ensure_built("p_nope", 0) == "none"
    assert len(llm.calls) == n


def test_build_lock_is_module_level():
    assert hasattr(P._BUILD_LOCK, "acquire") and hasattr(P._BUILD_LOCK, "release")
    assert P.ProjectEngine(make_svc())._lock is not P._BUILD_LOCK
