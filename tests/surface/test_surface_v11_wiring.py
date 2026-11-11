"""v1.1 wiring: the new routes are mounted on the real router, /meta tells the screen what to show, the version
is one string in three places, and the notes switch is off unless the learner turns it on."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "project"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from proj_helpers import new_store  # noqa: E402

import codecoach  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

B = "/api/plugins/codecoach"


def _load_api():
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_v11", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def client(cfg=None):
    api = _load_api()
    svc = CodeCoachService(new_store(), FakeLLM(lambda purpose, text: None), cfg or ServiceConfig(),
                           background=False, auto_bank=False)
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix=B)
    return TestClient(app), api


def test_meta_hides_the_notes_screens_by_default():
    c, _ = client()
    r = c.get(f"{B}/meta")
    assert r.status_code == 200
    assert r.json() == {"version": "1.1.0", "features": {"notes": False}}


def test_meta_shows_the_notes_screens_only_when_the_setting_is_exactly_true():
    assert ServiceConfig.from_mapping({"show_notes": True}.get).show_notes is True
    for not_true in ("true", "yes", 1, None, "on"):
        assert ServiceConfig.from_mapping({"show_notes": not_true}.get).show_notes is False
    assert ServiceConfig.from_mapping({}.get).show_notes is False
    c, _ = client(ServiceConfig(show_notes=True))
    assert c.get(f"{B}/meta").json()["features"] == {"notes": True}


def test_meta_answers_plainly_when_the_service_cannot_start():
    api = _load_api()

    def broken():
        raise RuntimeError("no store")
    api.set_service(None, factory=broken)
    app = FastAPI()
    app.include_router(api.router, prefix=B)
    r = TestClient(app).get(f"{B}/meta")
    assert r.status_code == 500 and r.json()["code"] == "internal"       # the screen reads any failure as notes off


def test_the_version_is_one_string_in_three_places():
    yaml_text = (ROOT / "plugin.yaml").read_text()
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text())
    assert f'version: "{codecoach.__version__}"' in yaml_text.splitlines()
    assert manifest["version"] == codecoach.__version__ == "1.1.0"


def test_plugin_yaml_declares_show_notes_off_by_default():
    text = (ROOT / "plugin.yaml").read_text()
    block = text[text.index("  show_notes:"):]
    assert "type: bool" in block and "default: false" in block


V11_ROUTES = {
    ("GET", "/languages"), ("POST", "/languages/refresh"),
    ("GET", "/fit"), ("POST", "/fit/{language}"), ("GET", "/fit/{language}"),
    ("GET", "/practice"), ("POST", "/practice/next"), ("GET", "/practice/drills/{drill_id}"),
    ("POST", "/practice/drills/{drill_id}/answer"),
    ("POST", "/tests"), ("GET", "/tests"), ("GET", "/tests/{test_id}"), ("POST", "/tests/{test_id}/answer"),
    ("POST", "/tests/{test_id}/finish"),
    ("GET", "/meta"),
}


def _routes(api):
    return [(m, r.path) for r in api.router.routes for m in sorted(r.methods - {"HEAD", "OPTIONS"})]


def test_every_v11_route_is_mounted_exactly_once_and_no_v10_route_was_lost():
    _, api = client()
    routes = _routes(api)
    assert len(routes) == len(set(routes)), "a method and path is registered twice"
    assert V11_ROUTES <= set(routes), sorted(V11_ROUTES - set(routes))
    v10 = {("GET", "/courses"), ("POST", "/courses/import"), ("GET", "/projects"), ("POST", "/projects"),
           ("GET", "/projects/{project_id}"), ("DELETE", "/projects/{project_id}"),
           ("POST", "/projects/{project_id}/steps/{index}/check"), ("POST", "/items/{item_id}/answer")}
    assert v10 <= set(routes)
    assert len(routes) == 40 + len(V11_ROUTES), len(routes)          # 40 routes shipped in v1.0


def test_the_new_routes_answer_through_the_real_router_with_coded_errors():
    c, _ = client()
    langs = c.get(f"{B}/languages").json()
    from codecoach.check.code_runner import sandbox_status
    assert langs["sandbox"] == sandbox_status()
    assert {"c", "python"} <= {row["id"] for row in langs["languages"]}
    fit = c.get(f"{B}/fit")
    assert fit.status_code == 200 and set(fit.json()) >= {"model", "languages", "switch"}
    assert c.get(f"{B}/tests").json() == {"tests": []}
    for method, path in (("get", "/fit/klingon"), ("post", "/fit/klingon")):
        r = getattr(c, method)(f"{B}{path}")
        assert r.status_code == 422 and r.json()["code"] == "bad_language", (path, r.text)
    for path in ("/practice/drills/nope", "/tests/nope"):
        r = c.get(f"{B}{path}")
        assert r.status_code == 404 and set(r.json()) == {"detail", "code"}, (path, r.text)
    r = c.post(f"{B}/tests", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json() == {"detail": "That request was not understood.", "code": "bad_request"}
