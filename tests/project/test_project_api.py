"""Project routes through the real router (FastAPI TestClient), FakeLLM replies and the REAL sandbox."""

import importlib.util
import json
from pathlib import Path

import pytest
from proj_helpers import PY_STEP1, Script, make_svc, plan_reply, step_reply

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import codecoach.project as P  # noqa: E402
from codecoach.check.code_runner import sandbox_status  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
needs_sb = pytest.mark.skipif(sandbox_status() != "bwrap", reason="the real bwrap sandbox is needed")
GOOD0 = "name = input()\nprint('Hello, ' + name + '!')\n"
WRONG = "print('nope')\n"
B = "/api/plugins/codecoach"


def _load_api():
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_proj", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def client(script):
    api = _load_api()
    svc = make_svc(FakeLLM(script))
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix=B)
    return TestClient(app), svc


def started(script=None):
    script = script or Script(plan=plan_reply(4), steps=[step_reply(), step_reply(**PY_STEP1)])
    c, svc = client(script)
    r = c.post(f"{B}/projects", json={"idea": "a greeter that says hello", "language": "python"})
    assert r.status_code == 200, r.text
    return c, svc, r.json()["project_id"]


def code_of(r):
    return r.status_code, r.json().get("code")


def test_list_has_ideas_sandbox_and_projects():
    c, _ = client(Script())
    j = c.get(f"{B}/projects").json()
    assert set(j) == {"sandbox", "ideas", "projects"} and j["projects"] == [] and 4 <= len(j["ideas"]) <= 6


@needs_sb
def test_create_get_list_delete():
    c, _, pid = started()
    j = c.get(f"{B}/projects/{pid}").json()
    assert j["status"] == "ready" and len(j["steps"]) == 4 and j["current"] == 0
    row = c.get(f"{B}/projects").json()["projects"][0]
    assert row["id"] == pid and row["steps_total"] == 4 and row["steps_passed"] == 0
    assert c.delete(f"{B}/projects/{pid}").status_code == 422                       # no confirm
    assert code_of(c.delete(f"{B}/projects/{pid}?confirm=false")) == (422, "confirm_required")
    assert c.delete(f"{B}/projects/{pid}?confirm=true").json() == {"deleted": True}
    assert code_of(c.get(f"{B}/projects/{pid}")) == (404, "unknown_project")
    assert code_of(c.delete(f"{B}/projects/{pid}?confirm=true")) == (404, "unknown_project")


def test_create_validation_errors():
    c, _ = client(Script(plan=plan_reply(4)))
    assert code_of(c.post(f"{B}/projects", json={"idea": "ab", "language": "c"})) == (422, "bad_idea")
    assert code_of(c.post(f"{B}/projects", json={"idea": "x" * 301, "language": "c"})) == (422, "bad_idea")
    assert code_of(c.post(f"{B}/projects", json={"idea": "a guessing game", "language": "rust"})) == (422, "bad_language")
    assert code_of(c.post(f"{B}/projects", json={"idea": 5, "language": "c"})) == (422, "bad_request")
    assert code_of(c.post(f"{B}/projects", json={})) == (422, "bad_idea")


def test_create_without_sandbox_is_503(monkeypatch):
    monkeypatch.setattr(P, "sandbox_status", lambda *a, **k: "unavailable")
    c, _ = client(Script(plan=plan_reply(4)))
    assert code_of(c.post(f"{B}/projects", json={"idea": "a guessing game", "language": "c"})) == (503, "sandbox_unavailable")
    assert c.get(f"{B}/projects").json()["sandbox"] == "unavailable"


def test_unknown_project_everywhere_is_404():
    c, _ = client(Script())
    for method, path, body in [("get", "/projects/p_nope", None), ("get", "/projects/p_nope/steps/0", None),
                               ("post", "/projects/p_nope/steps/0/run", {"code": "x"}),
                               ("post", "/projects/p_nope/steps/0/check", {"code": "x"}),
                               ("post", "/projects/p_nope/steps/0/hint", None),
                               ("post", "/projects/p_nope/steps/0/solution", None),
                               ("post", "/projects/p_nope/steps/0/retry", None),
                               ("delete", "/projects/p_nope?confirm=true", None)]:
        r = getattr(c, method)(B + path, **({"json": body} if body is not None else {}))
        assert code_of(r) == (404, "unknown_project"), path


@needs_sb
def test_step_open_locked_and_unknown():
    c, _, pid = started()
    s = c.get(f"{B}/projects/{pid}/steps/0").json()
    assert s["status"] == "ready" and s["lesson"] and s["hidden_count"] == 1 and s["hints_left"] == 3
    assert code_of(c.get(f"{B}/projects/{pid}/steps/1")) == (409, "step_locked")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/2/run", json={"code": "x"})) == (409, "step_locked")
    assert code_of(c.get(f"{B}/projects/{pid}/steps/9")) == (404, "unknown_step")
    assert code_of(c.get(f"{B}/projects/{pid}/steps/abc")) == (422, "bad_request")


@needs_sb
def test_run_has_the_item_run_shape_and_check_adds_three_fields():
    c, _, pid = started()
    c.get(f"{B}/projects/{pid}/steps/0")
    run = c.post(f"{B}/projects/{pid}/steps/0/run", json={"code": WRONG}).json()
    assert set(run) == {"item_id", "ok", "passed", "total", "results", "compile_error", "timed_out"}
    assert run["ok"] is False and run["results"][0]["stdout"] == "nope\n" and run["results"][0]["expected"]
    bad = c.post(f"{B}/projects/{pid}/steps/0/check", json={"code": WRONG}).json()
    assert set(bad) == set(run) | {"step_passed", "next_index", "project_done"}
    assert bad["step_passed"] is False and bad["next_index"] is None and bad["total"] == 2
    ok = c.post(f"{B}/projects/{pid}/steps/0/check", json={"code": GOOD0}).json()
    assert (ok["step_passed"], ok["next_index"], ok["project_done"]) == (True, 1, False)
    assert c.get(f"{B}/projects/{pid}").json()["current"] == 1
    assert c.get(f"{B}/projects/{pid}/steps/1").json()["status"] == "ready"


@needs_sb
def test_code_body_errors():
    c, _, pid = started()
    c.get(f"{B}/projects/{pid}/steps/0")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/run", json={})) == (422, "bad_request")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/run", json={"code": 5})) == (422, "bad_request")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/check", json={"code": "x" * 9000})) == (413, "too_large")


@needs_sb
def test_hint_solution_and_their_409s():
    c, _, pid = started()
    c.get(f"{B}/projects/{pid}/steps/0")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/solution")) == (409, "solution_locked")
    hs = [c.post(f"{B}/projects/{pid}/steps/0/hint").json() for _ in range(3)]
    assert [h["hints_left"] for h in hs] == [2, 1, 0] and hs[0]["hint"] == "Think about reading input."
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/hint")) == (409, "no_hints")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/solution")) == (409, "solution_locked")      # no failed checks yet
    for _ in range(3):
        c.post(f"{B}/projects/{pid}/steps/0/check", json={"code": WRONG})
    s = c.get(f"{B}/projects/{pid}/steps/0").json()
    assert s["can_show_solution"] is True and s["hints_shown"] and s["hints_left"] == 0
    assert c.post(f"{B}/projects/{pid}/steps/0/solution").json() == {"code": GOOD0}
    assert c.get(f"{B}/projects/{pid}").json()["steps"][0]["with_help"] is True


@needs_sb
def test_step_not_ready_and_retry():
    c, svc, pid = started()
    c.get(f"{B}/projects/{pid}/steps/0")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/retry")) == (409, "not_unavailable")
    svc.store.set_meta(f"project:{pid}:step:0", {"version": P.PROJECT_PROMPT_VERSION, "index": 0,
                                                 "status": "unavailable", "reason": "Broken.", "attempts": 3})
    s = c.get(f"{B}/projects/{pid}/steps/0").json()
    assert s["status"] == "unavailable" and s["reason"] == "Broken." and s["lesson"] == []
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/run", json={"code": "x"})) == (409, "step_not_ready")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/hint")) == (409, "step_not_ready")
    assert c.post(f"{B}/projects/{pid}/steps/0/retry").status_code == 200
    assert c.get(f"{B}/projects/{pid}/steps/0").json()["status"] in ("ready", "unavailable")


@needs_sb
def test_run_without_sandbox_is_503(monkeypatch):
    c, _, pid = started()
    c.get(f"{B}/projects/{pid}/steps/0")
    import codecoach.check.code_runner as cr
    monkeypatch.setattr(cr, "sandbox_status", lambda *a, **k: "unavailable")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/run", json={"code": GOOD0})) == (503, "sandbox_unavailable")
    assert code_of(c.post(f"{B}/projects/{pid}/steps/0/check", json={"code": GOOD0})) == (503, "sandbox_unavailable")


@needs_sb
def test_responses_never_carry_server_only_text():
    steps = [step_reply(reference_solution="name = input()\nprint('Hello, ' + name + '!')   # SECRETREF\n"),
             step_reply(**PY_STEP1)]
    c, _, pid = started(Script(plan=plan_reply(4), steps=steps))
    seen = [c.get(f"{B}/projects").text, c.get(f"{B}/projects/{pid}").text, c.get(f"{B}/projects/{pid}/steps/0").text]
    seen.append(c.post(f"{B}/projects/{pid}/steps/0/run", json={"code": WRONG}).text)
    seen.append(c.post(f"{B}/projects/{pid}/steps/0/check", json={"code": WRONG}).text)
    seen.append(c.post(f"{B}/projects/{pid}/steps/0/hint").text)
    seen.append(c.get(f"{B}/projects/{pid}/steps/0").text)
    blob = "\n".join(seen)
    for needle in ("SECRETREF", "Bob", "print(___ + name)", "reference_solution", "hidden_tests", '"hints"'):
        assert needle not in blob, needle
    assert json.loads(seen[-1])["hints_shown"] == ["Think about reading input."]
