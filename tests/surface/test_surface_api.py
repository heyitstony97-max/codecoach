"""FastAPI routes against a real service with a fake LLM. Skipped when fastapi/httpx are missing."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from surface_helpers import make_service  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "api"


def _load_api():
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env():
    api = _load_api()
    svc, store, cid = make_service()
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/codecoach")
    return TestClient(app), svc, cid, api


def fx(name):
    return json.loads((FIX / f"{name}.json").read_text())


def keys_cover(got, want, path=""):
    """Every key the fixture promises is present in the response (recursively, first list element)."""
    if got is None:       # nullable fields (next_review, lesson, item ...) may be null in a live reply
        return
    if isinstance(want, dict):
        assert isinstance(got, dict), f"{path}: expected object"
        for k, v in want.items():
            assert k in got, f"{path}.{k} missing"
            keys_cover(got[k], v, f"{path}.{k}")
    elif isinstance(want, list) and want and isinstance(got, list) and got:
        keys_cover(got[0], want[0], path + "[0]")


P = "/api/plugins/codecoach"


def test_courses_and_import_shapes(env):
    c, svc, cid, api = env
    r = c.get(f"{P}/courses")
    assert r.status_code == 200
    keys_cover(r.json(), fx("courses"))
    r = c.post(f"{P}/courses/import", json={"title": "T", "text": "Some text here.\n\nMore text."})
    assert r.status_code == 200 and set(r.json()) == {"job_id"}
    j = c.get(f"{P}/jobs/{r.json()['job_id']}")
    assert j.status_code == 200
    keys_cover(j.json(), fx("job"))


def test_map_progress_drops_timeline_shapes(env):
    c, svc, cid, api = env
    for path, name in (("map", "map"), ("progress", "progress"), ("drops", "drops"), ("timeline", "timeline")):
        r = c.get(f"{P}/courses/{cid}/{path}")
        assert r.status_code == 200, (path, r.text)
        keys_cover(r.json(), fx(name), path)


def test_next_answer_hint_report_cycle(env):
    c, svc, cid, api = env
    r = c.get(f"{P}/courses/{cid}/next")
    assert r.status_code == 200
    body = r.json()
    keys_cover(body, fx("next_lesson") if body["kind"] == "lesson" else fx("next_item_mc"))
    for _ in range(20):
        if body["kind"] == "item":
            break
        body = c.get(f"{P}/courses/{cid}/next").json()
    assert body["kind"] == "item"
    iid = body["item"]["id"]
    h = c.post(f"{P}/items/{iid}/hint", json={"level": 1})
    assert h.status_code == 200
    keys_cover(h.json(), fx("hint"))
    a = c.post(f"{P}/items/{iid}/answer", json={"response": 0})
    assert a.status_code == 200
    keys_cover(a.json(), fx("answer_mc"))
    rep = c.post(f"{P}/items/{iid}/report", json={"reason": "wrong"})
    assert rep.status_code == 200
    keys_cover(rep.json(), fx("report"))


def test_error_shapes_have_code(env):
    c, svc, cid, api = env
    calls = [("get", f"{P}/courses/zz/map", None), ("get", f"{P}/courses/zz/next", None),
             ("get", f"{P}/jobs/zz", None), ("get", f"{P}/items/zz/plot", None),
             ("get", f"{P}/passages/zz", None), ("post", f"{P}/items/zz/answer", {"response": 1}),
             ("post", f"{P}/items/zz/run", {"code": "x"}), ("post", f"{P}/items/zz/hint", {"level": 1}),
             ("post", f"{P}/items/zz/report", {"reason": ""}),
             ("post", f"{P}/courses/import", {"title": "", "text": ""})]
    for m, url, body in calls:
        r = getattr(c, m)(url, **({"json": body} if body is not None else {}))
        assert r.status_code in (404, 422), (url, r.status_code)
        assert "detail" in r.json()


def test_bad_bodies_are_422_not_500(env):
    c, svc, cid, api = env
    iid = c.get(f"{P}/courses/{cid}/next").json()
    for url in (f"{P}/items/x/hint", f"{P}/items/x/answer", f"{P}/items/x/run", f"{P}/courses/import"):
        for bad in ("garbage", [1, 2], {"nope": 1}, None):
            r = c.post(url, content=json.dumps(bad), headers={"content-type": "application/json"})
            assert r.status_code in (404, 422), (url, bad, r.status_code)


def test_request_size_limit(env):
    c, svc, cid, api = env
    r = c.post(f"{P}/courses/import", content=b"{}", headers={"content-type": "application/json",
                                                              "content-length": str(api.MAX_BODY_BYTES + 1)})
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_leak_over_every_endpoint_before_answering(env):
    """Nothing the learner must not see (citation quotes, answer keys) may appear before answering."""
    c, svc, cid, api = env
    secrets = []
    for it in svc.store.list_items(course_id=cid):
        secrets += [x.quote for x in it.citations]
    assert secrets
    blobs = [c.get(f"{P}/courses").text]
    for path in ("map", "progress", "drops", "timeline"):
        blobs.append(c.get(f"{P}/courses/{cid}/{path}").text)
    body = None
    for _ in range(20):
        r = c.get(f"{P}/courses/{cid}/next")
        blobs.append(r.text)
        body = r.json()
        if body["kind"] == "item":
            blobs.append(c.post(f"{P}/items/{body['item']['id']}/hint", json={"level": 1}).text)
            blobs.append(c.get(f"{P}/items/{body['item']['id']}/plot").text)
            break
    assert body and body["kind"] == "item"
    item = body["item"]
    assert "correct_index" not in json.dumps(item) and "citations" not in item
    assert "answer" not in item["payload"] and "correct" not in item["payload"]
    text = "\n".join(blobs)
    assert "correct_index" not in text
    for s in secrets:
        assert s not in text, f"leaked: {s[:40]}"
