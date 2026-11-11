"""The four practice routes on a BARE FastAPI router (real engine, real sandbox, FakeLLM), plus the background states."""

import json
import threading
import time

import pytest
from prac_helpers import PY_CODE, PY_EXPL, PY_OUT, make_svc, needs_sb, reply

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.practice import engine as E  # noqa: E402
from codecoach.practice.api import register  # noqa: E402

B = "/api/plugins/codecoach"
VIEW_KEYS = {"id", "language", "topic", "kind", "status", "code", "stdin", "line_count", "reason", "answered"}


def other(tag):
    return PY_CODE.replace("Total", tag)


def client(script, *, background=False):
    svc = make_svc(FakeLLM(script), background=background)
    router = APIRouter()
    register(router, lambda: svc)
    app = FastAPI()
    app.include_router(router, prefix=B)
    return TestClient(app), svc


def seq(*replies):
    q = list(replies)
    return lambda purpose, text: q.pop(0) if q else None


def wait_for(fn, secs=20.0):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def test_routes_exist_on_a_bare_router():
    router = APIRouter()
    register(router, lambda: None)
    got = sorted((sorted(r.methods)[0], r.path) for r in router.routes)
    got = [g for g in got if g[1].startswith("/practice")]         # the five test routes are checked in the test file
    assert got == [("GET", "/practice"), ("GET", "/practice/drills/{drill_id}"), ("POST", "/practice/drills/{drill_id}/answer"),
                   ("POST", "/practice/next")]


def test_get_practice_shape_and_errors():
    c, _ = client(seq())
    j = c.get(f"{B}/practice", params={"language": "python"}).json()
    assert set(j) == {"sandbox", "language", "topics"} and j["language"] == "python" and len(j["topics"]) == 8
    assert j["topics"][4] == {"id": "loops", "title": "Loops", "seen": 0, "correct": 0, "ready": 0}
    assert c.get(f"{B}/practice").json()["language"] in ("c", "python")          # no language: the first ready one
    r = c.get(f"{B}/practice", params={"language": "rust-nope"})
    assert r.status_code == 422 and r.json()["code"] == "bad_language"


def test_next_errors():
    c, _ = client(seq())
    r = c.post(f"{B}/practice/next", json={"language": "nope", "topic": "loops"})
    assert r.status_code == 422 and r.json()["code"] == "bad_language"
    r = c.post(f"{B}/practice/next", json={"language": "python", "topic": "nope"})
    assert r.status_code == 422 and r.json()["code"] == "bad_topic"
    assert c.post(f"{B}/practice/next", json={"language": "python"}).status_code == 422
    assert c.post(f"{B}/practice/next", content="not json").status_code == 422
    assert c.get(f"{B}/practice/drills/d_none").status_code == 404
    assert c.post(f"{B}/practice/drills/d_none/answer", json={"prediction": "x"}).status_code == 404
    assert c.post(f"{B}/practice/drills/d_none/answer", json={"prediction": 5}).status_code == 422


@needs_sb
def test_full_flow_over_http():
    c, _ = client(seq(reply(), reply(other("Sum"))))
    r = c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"})
    assert r.status_code == 200 and set(r.json()) == {"drill_id", "status"} and r.json()["status"] == "ready"
    did = r.json()["drill_id"]

    v = c.get(f"{B}/practice/drills/{did}").json()
    assert set(v) == VIEW_KEYS and v["status"] == "ready" and v["code"] == PY_CODE and v["answered"] is False
    blob = json.dumps(v)
    assert "137" not in blob and "explanation" not in blob and "stdout" not in blob      # P7 over the wire

    wrong = c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": "Total: 138"}).json()
    assert set(wrong) == {"correct", "expected_stdout", "lines", "explanation"}
    assert wrong["correct"] is False and wrong["expected_stdout"] == PY_OUT and wrong["explanation"] == PY_EXPL
    assert wrong["lines"] == [{"n": 1, "expected": "Total: 137", "got": "Total: 138", "ok": False}]

    right = c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": "Total: 137 \n\n"}).json()
    assert right["correct"] is True

    assert c.get(f"{B}/practice/drills/{did}").json()["answered"] is True
    row = c.get(f"{B}/practice", params={"language": "python"}).json()["topics"][4]
    assert row == {"id": "loops", "title": "Loops", "seen": 1, "correct": 0, "ready": 1}


@needs_sb
def test_unavailable_drill_over_http():
    c, _ = client(seq(RuntimeError("boom")))
    did = c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"}).json()["drill_id"]
    v = c.get(f"{B}/practice/drills/{did}").json()
    assert v["status"] == "unavailable" and v["code"] == "" and v["reason"].startswith("The model could not be reached")
    r = c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": "x"})
    assert r.status_code == 409 and r.json()["code"] == "drill_not_ready"


# ------------------------------------------------------------------ background: building, then ready

@needs_sb
def test_building_then_ready_and_only_the_drill_being_built_reads_building():
    gate1, gate2 = threading.Event(), threading.Event()
    started2 = threading.Event()
    n = {"i": 0}

    def script(purpose, text):
        n["i"] += 1
        if n["i"] == 1:
            assert gate1.wait(20)
            return reply()
        started2.set()
        assert gate2.wait(20)
        return reply(other("Sum"))

    c, svc = client(script, background=True)
    r = c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"})
    did = r.json()["drill_id"]
    assert r.json()["status"] == "building"
    v = c.get(f"{B}/practice/drills/{did}").json()
    assert v["status"] == "building" and v["code"] == "" and v["line_count"] == 0 and v["reason"] is None
    a = c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": "x"})
    assert a.status_code == 409 and a.json()["code"] == "drill_not_ready"
    assert c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"}).json() == \
        {"drill_id": did, "status": "building"}                  # asking again does not start a second build

    gate1.set()                                                  # drill 1 finishes; drill 2 (the prefetch) starts
    assert started2.wait(20)
    v = wait_for(lambda: (x := c.get(f"{B}/practice/drills/{did}").json())["status"] == "ready" and x)
    assert v["code"] == PY_CODE
    # while the later drill builds, the finished one reads ready everywhere
    assert c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"}).json() == \
        {"drill_id": did, "status": "ready"}
    ids = [i for i in svc.store.get_meta("practice:index")]
    assert len(ids) == 2
    second = c.get(f"{B}/practice/drills/{ids[1]}").json()
    assert second["status"] == "building"
    assert c.get(f"{B}/practice", params={"language": "python"}).json()["topics"][4]["ready"] == 1

    gate2.set()
    assert svc.wait_idle(30)
    assert c.get(f"{B}/practice/drills/{ids[1]}").json()["status"] == "ready"
    assert n["i"] == 2                                           # one drill and one prefetch, nothing more


@needs_sb
def test_a_route_never_waits_on_a_slow_model():
    def slow(purpose, text):
        time.sleep(2.0)
        return reply() if "Total" not in text else reply(other("Sum"))

    c, svc = client(slow, background=True)
    timings = {}

    def timed(name, fn):
        t = time.time()
        r = fn()
        timings[name] = time.time() - t
        return r

    j = timed("next", lambda: c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"})).json()
    did = j["drill_id"]
    assert j["status"] == "building"
    timed("topics", lambda: c.get(f"{B}/practice", params={"language": "python"}))
    v = timed("drill", lambda: c.get(f"{B}/practice/drills/{did}")).json()
    assert v["status"] == "building"
    timed("answer", lambda: c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": "x"}))
    timed("next_again", lambda: c.post(f"{B}/practice/next", json={"language": "python", "topic": "loops"}))
    timed("unknown", lambda: c.get(f"{B}/practice/drills/d_none"))
    assert max(timings.values()) < 1.0, timings
    assert svc.wait_idle(60)
