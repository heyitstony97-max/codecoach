"""Syllabus coverage through the three real entry points on demo/materials/history.md + syllabus.md:
the /codecoach command (--syllabus), the codecoach_import tool (syllabus_path) and POST /courses/import
(syllabus_text / syllabus_path). Fake model that quotes the best sentence, real importer."""

import importlib.util
import json
import re
from pathlib import Path

import pytest
from surface_helpers import new_store

from codecoach import tools
from codecoach.core.llm import FakeLLM
from codecoach.importer import get_import_report
from codecoach.importer import syllabus as SY
from codecoach.service import CodeCoachService, ServiceConfig

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "demo" / "materials" / "history.md"
SYLLABUS = ROOT / "demo" / "materials" / "syllabus.md"
_PASSAGE = re.compile(r"^\[([^\]]+)\] (.*)$", re.M)
PONY = "Explain how the Pony Express"


def honest_model() -> FakeLLM:
    """Answers like a careful model: the sentence that best matches the topic, or nothing."""
    def fn(purpose, text):
        if purpose == SY.PURPOSE_VERB:
            return {"verb": "explain"}
        if purpose != SY.PURPOSE_COVER:
            return {"edges": []}
        topic = set(SY.content_words(re.search(r"^Topic: (.*)$", text, re.M).group(1)))
        best, pick = 0, None
        for pid, body in _PASSAGE.findall(text):
            for sent in re.split(r"(?<=[.!?])\s+", body):
                n = len(topic & set(SY.content_words(sent)))
                if n > best and len(sent.split()) >= 5:
                    best, pick = n, {"passage_id": pid, "quote": sent}
        return {"support": [pick] if pick and best >= 2 else []}
    return FakeLLM(fn)


def make_svc():
    store = new_store()
    return CodeCoachService(store, honest_model(), ServiceConfig(), background=False, auto_bank=False), store


def check(store, job_id):
    job = store.get_job(job_id)
    assert job.status == "done", job.error
    objs = store.list_objectives(job.course_id)
    assert len(objs) == 8
    by = {o.statement: o for o in objs}
    pony = next(o for s, o in by.items() if s.startswith(PONY))
    assert pony.coverage == "not_covered" and pony.passage_ids == []
    assert sum(o.coverage == "covered" for o in objs) >= 4
    rep = get_import_report(store, job.course_id)
    assert rep["source"] == "syllabus" and len(rep["syllabus"]) == 8
    row = next(r for r in rep["syllabus"] if r["line"].startswith(PONY))
    assert row["reason"] == "no passage in your materials covers this" and row["quote"] == ""
    covered = [r for r in rep["syllabus"] if r["coverage"] == "covered"]
    assert all(r["quote"] and r["passage_ids"] for r in covered)
    return job.course_id


def test_command_with_syllabus_flag():
    svc, store = make_svc()
    out = tools.make_command(lambda: svc)(f"import {HISTORY} --syllabus {SYLLABUS}")
    assert "Import started" in out and "syllabus" in out
    check(store, store.list_jobs()[-1].id)


def test_command_usage_mentions_the_flag():
    svc, _ = make_svc()
    assert "--syllabus" in tools.make_command(lambda: svc)("import")
    assert "--syllabus" in tools.HELP


def test_tool_with_syllabus_path():
    svc, store = make_svc()
    hs = tools.make_handlers(lambda: svc)
    d = json.loads(hs["codecoach_import"]({"path": str(HISTORY), "syllabus_path": str(SYLLABUS)}))
    assert d["ok"] and d["job_id"]
    check(store, d["job_id"])
    assert "syllabus_path" in tools.SCHEMAS["codecoach_import"]["parameters"]["properties"]


def test_tool_without_a_syllabus_is_the_plain_import():
    svc, store = make_svc()
    hs = tools.make_handlers(lambda: svc)
    # the honest model has no objective drafter, so a plain import fails: proof the syllabus path was not taken
    d = json.loads(hs["codecoach_import"]({"path": str(HISTORY)}))
    assert store.get_job(d["job_id"]).status == "error"


def test_tool_missing_syllabus_file_is_a_clean_error():
    svc, _ = make_svc()
    hs = tools.make_handlers(lambda: svc)
    d = json.loads(hs["codecoach_import"]({"path": str(HISTORY), "syllabus_path": str(ROOT / "nope.md")}))
    assert d["ok"] is False


@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("codecoach_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    svc, store = make_svc()
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/codecoach")
    return TestClient(app), store


def test_post_courses_import_with_syllabus_text(client):
    c, store = client
    r = c.post("/api/plugins/codecoach/courses/import",
               json={"title": "Railroad", "text": HISTORY.read_text(), "syllabus_text": SYLLABUS.read_text()})
    assert r.status_code == 200, r.text
    check(store, r.json()["job_id"])


def test_post_courses_import_with_syllabus_path(client):
    c, store = client
    r = c.post("/api/plugins/codecoach/courses/import",
               json={"title": "Railroad", "path": str(HISTORY), "syllabus_path": str(SYLLABUS)})
    assert r.status_code == 200, r.text
    check(store, r.json()["job_id"])


def test_post_rejects_both_syllabus_sources_and_outline_plus_syllabus(client):
    c, _ = client
    base = {"title": "R", "text": "One.\n\nTwo."}
    r = c.post("/api/plugins/codecoach/courses/import",
               json={**base, "syllabus_text": "1. a topic", "syllabus_path": str(SYLLABUS)})
    assert r.status_code == 422
    r = c.post("/api/plugins/codecoach/courses/import", json={**base, "syllabus_text": "1. a topic", "outline": True})
    assert r.status_code == 422


def test_blank_syllabus_text_means_no_syllabus(client):
    c, store = client
    r = c.post("/api/plugins/codecoach/courses/import", json={"title": "R", "text": "x", "syllabus_text": "   "})
    # no syllabus: a plain import, which this fake model cannot draft; the point is it did not 422
    assert r.status_code == 200
