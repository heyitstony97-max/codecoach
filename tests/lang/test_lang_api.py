"""The /languages routes on a bare FastAPI router."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from codecoach import lang
from codecoach.lang import api

KEYS = {"id", "name", "status", "version", "reason", "hello_ms", "live_run", "ext", "tier"}


def _client() -> TestClient:
    router = APIRouter()
    api.register(router, lambda: None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_languages_shape():
    body = _client().get("/languages").json()
    assert body["sandbox"] in ("bwrap", "none") or isinstance(body["sandbox"], str)
    assert [x["id"] for x in body["languages"]] == lang.all_ids()
    for x in body["languages"]:
        assert set(x) == KEYS
        assert x["status"] in ("ready", "missing", "failed")
        assert (x["reason"] is None) == (x["status"] == "ready")


def test_refresh_returns_same_shape():
    c = _client()
    a = c.get("/languages").json()
    b = c.post("/languages/refresh").json()
    assert [x["id"] for x in b["languages"]] == [x["id"] for x in a["languages"]]
    assert [x["status"] for x in b["languages"]] == [x["status"] for x in a["languages"]]
