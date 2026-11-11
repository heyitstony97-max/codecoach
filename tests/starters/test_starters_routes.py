"""POST /projects with {"starter", "language"} through the real router: no idea, no model call, 422 bad_starter."""

import importlib.util
from pathlib import Path

import pytest
from starters_helpers import NEEDS_SANDBOX, STARTER, forbidden, shipped

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starters_helpers import CodeCoachService, FakeLLM, ServiceConfig, lang, new_store, sandbox_status  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
B = "/api/plugins/codecoach"
pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)


def _client(llm):
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_starters", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    svc = CodeCoachService(new_store(), llm, ServiceConfig(), background=False, auto_bank=False)
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix=B)
    return TestClient(app)


@pytest.mark.parametrize("llm", [FakeLLM(forbidden), None], ids=["model-that-must-not-be-called", "no-model"])
def test_post_a_starter_with_no_idea(llm):
    ready = [x for x in shipped() if x in lang.ready_ids()]
    if not ready:
        pytest.skip("no ready starter language")
    c = _client(llm)
    lid = ready[0]
    listing = c.get(f"{B}/projects").json()
    assert listing["starters"][0]["id"] == STARTER and lid in listing["starters"][0]["languages"]
    r = c.post(f"{B}/projects", json={"starter": STARTER, "language": lid})
    assert r.status_code == 200 and r.json()["status"] == "planning"
    pid = r.json()["project_id"]
    j = c.get(f"{B}/projects/{pid}").json()
    assert j["status"] == "ready" and j["starter"] == STARTER and len(j["steps"]) == 4
    s = c.get(f"{B}/projects/{pid}/steps/0").json()
    assert s["status"] == "ready", s.get("reason")
    assert c.get(f"{B}/projects").json()["projects"][0]["starter"] == STARTER
    if llm is not None:
        assert llm.calls == []


def test_bad_starter_through_the_route():
    c = _client(FakeLLM(forbidden))
    for body in ({"starter": "nope", "language": "python"}, {"starter": STARTER, "language": "cobol"}):
        r = c.post(f"{B}/projects", json=body)
        assert r.status_code == 422, r.text
        assert r.json().get("code") == "bad_starter"
