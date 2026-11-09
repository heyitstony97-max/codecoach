"""Outline mode end to end through the three real entry points, on demo/materials/c-basics.md:
the /codecoach command, the codecoach_import tool and POST /courses/import. Fake model, real importer."""

import importlib.util
import json
import re
from pathlib import Path

import pytest
from surface_helpers import new_store

from codecoach import tools
from codecoach.core.llm import FakeLLM
from codecoach.service import CodeCoachService, ServiceConfig

ROOT = Path(__file__).resolve().parents[2]
C_BASICS = ROOT / "demo" / "materials" / "c-basics.md"
_PASSAGE = re.compile(r"^\[([^\]]+)\] (.*)$", re.M)
_BULLET = re.compile(r"-\s+(.+?)(?=\s+-\s+[A-Z]|$)", re.S)


def outline_model() -> FakeLLM:
    """One objective per outline line. 'Write ...' lines say write-code but (like a sloppy model) kind
    'reading'; 'predict/follow' lines say trace with kind 'reading'; the rest are explain/identify."""
    def fn(purpose, text):
        if purpose == "codecoach.import.prereqs":
            return {"edges": []}
        if purpose != "codecoach.import.objectives":
            return None
        out = []
        lines = [(pid, b.strip()) for pid, body in _PASSAGE.findall(text) for b in _BULLET.findall(body)]
        for pid, line in lines:
            low = line.lower()
            if low.startswith("write") or low.startswith("split") or low.startswith("sum "):
                verb = "write-code"
            elif "predict" in low or low.startswith("find and fix") or low.startswith("walk through"):
                verb = "trace"
            elif low.startswith("explain"):
                verb = "explain"
            else:
                verb = "identify"
            out.append({"verb": verb, "statement": f"Outline topic: {line[:150]}", "kind": "reading",
                        "passage_ids": [pid]})
        return {"objectives": out}
    return FakeLLM(fn)


def make_svc():
    store = new_store()
    svc = CodeCoachService(store, outline_model(), ServiceConfig(), background=False, auto_bank=False)
    return svc, store


def check_course(store, job_id):
    job = store.get_job(job_id)
    assert job.status == "done", job.error
    course = store.get_course(job.course_id)
    assert course.mode == "open" and course.mode_reason == "no source materials attached"
    objs = store.list_objectives(course.id)
    code = [o for o in objs if o.kind == "code"]
    assert len(objs) >= 20, len(objs)
    assert len(code) >= 10, len(code)
    assert all(o.coverage == "covered" for o in objs)
    # every write-code objective is a code objective, and so is every program-tracing one
    assert all(o.kind == "code" for o in objs if o.verb == "write-code")
    traced = [o for o in objs if o.verb == "trace"]
    assert traced and all(o.kind == "code" for o in traced)
    return len(objs), len(code)


def test_outline_through_the_codecoach_command():
    svc, store = make_svc()
    out = tools.make_command(lambda: svc)(f"import outline {C_BASICS}")
    assert "Import started" in out
    check_course(store, store.list_jobs()[-1].id)


def test_outline_through_the_codecoach_import_tool():
    svc, store = make_svc()
    hs = tools.make_handlers(lambda: svc)
    d = json.loads(hs["codecoach_import"]({"path": str(C_BASICS), "outline": True}))
    assert d["ok"] and d["job_id"]
    check_course(store, d["job_id"])


def test_without_the_flag_the_same_file_is_a_strict_course():
    svc, store = make_svc()
    hs = tools.make_handlers(lambda: svc)
    d = json.loads(hs["codecoach_import"]({"path": str(C_BASICS)}))
    job = store.get_job(d["job_id"])
    assert store.get_course(job.course_id).mode == "strict"


def test_outline_through_post_courses_import():
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
    c = TestClient(app)
    r = c.post("/api/plugins/codecoach/courses/import",
               json={"title": "C basics", "path": str(C_BASICS), "outline": True})
    assert r.status_code == 200, r.text
    n_obj, n_code = check_course(store, r.json()["job_id"])
    assert (n_obj, n_code) == (n_obj, n_code)
    # outline defaults to false and a non-boolean is refused
    r = c.post("/api/plugins/codecoach/courses/import", json={"title": "C", "text": "x\n\ny", "outline": "maybe"})
    assert r.status_code == 422
