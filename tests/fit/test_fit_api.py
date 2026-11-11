"""The fit routes on a bare FastAPI router (not dashboard/plugin_api.py), real sandbox, scripted FakeLLM."""

import threading
import time

import pytest
from fit_helpers import NEEDS_SANDBOX, PY, make_fit_svc, scripted, task_id_of, wait_done

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.check.code_runner import sandbox_status  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.fit.api import register  # noqa: E402
from codecoach.fit.engine import engine_for  # noqa: E402

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)
FIT_KEYS = {"language", "status", "verdict", "passed", "total", "done_tasks", "tasks", "model", "median_ms",
            "measured_at", "stale", "advice"}


def client(llm):
    svc = make_fit_svc(llm)
    router = APIRouter()
    register(router, lambda: svc)
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/codecoach")
    return TestClient(app), svc


B = "/api/plugins/codecoach"


def test_nothing_checked_yet():
    llm = scripted("python")
    llm.set_route(None)                      # nothing has answered yet
    c, _ = client(llm)
    r = c.get(B + "/fit")
    assert r.status_code == 200
    body = r.json()
    assert body["model"] is None and body["languages"] == {}
    assert body["switch"] == {"commands": ["hermes config set auxiliary.codecoach.provider <provider>",
                                           "hermes config set auxiliary.codecoach.model <model>"],
                              "note": "Then press Check again."}
    one = c.get(B + "/fit/python").json()
    assert set(one) == FIT_KEYS
    assert (one["status"], one["total"], one["done_tasks"], one["tasks"], one["model"]) == ("none", 5, 0, [], None)


def test_post_then_get_shapes():
    llm = scripted("python", {"fizzbuzz": "print('x')\n"})
    c, svc = client(llm)
    r = c.post(B + "/fit/python")
    assert (r.status_code, r.json()) == (202, {"status": "running"})
    wait_done(engine_for(svc))
    one = c.get(B + "/fit/python").json()
    assert set(one) == FIT_KEYS
    assert (one["status"], one["verdict"], one["passed"], one["total"], one["done_tasks"]) == ("done", "good", 4, 5, 5)
    assert one["tasks"][2] == {"id": "fizzbuzz", "title": "FizzBuzz", "ok": False, "reason": "Its program printed the wrong output."}
    assert one["tasks"][0] == {"id": "sum2", "title": "Add two numbers", "ok": True, "reason": None}
    assert one["model"] == {"provider": "custom", "model": "model-a"} and one["stale"] is False
    assert one["advice"] == "This model passed 4 of 5 Python warm-ups. It should teach Python well."
    allfit = c.get(B + "/fit").json()
    assert allfit["model"] == {"provider": "custom", "model": "model-a"}
    assert set(allfit["languages"]) == {"python"} and allfit["languages"]["python"] == one
    llm.set_route("custom", "model-b")
    assert c.get(B + "/fit/python").json()["stale"] is True


def test_409_while_running_and_422_for_a_bad_language():
    gate = threading.Semaphore(0)

    def fn(p, t):
        assert gate.acquire(timeout=60)
        return {"code": PY[task_id_of(t)]}
    llm = FakeLLM(fn)
    llm.set_route("custom", "model-a")
    c, svc = client(llm)
    assert c.post(B + "/fit/python").status_code == 202
    r = c.post(B + "/fit/python")
    assert r.status_code == 409 and r.json()["code"] == "fit_running"
    assert c.post(B + "/fit/c").status_code == 409
    g = c.get(B + "/fit/python").json()
    assert (g["status"], g["done_tasks"]) == ("running", 0)
    gate.release()
    end = time.time() + 60
    while c.get(B + "/fit/python").json()["done_tasks"] < 1:
        assert time.time() < end
        time.sleep(0.02)
    assert c.get(B + "/fit").json()["languages"]["python"]["status"] == "running"
    for _ in range(4):
        gate.release()
    wait_done(engine_for(svc))
    assert c.get(B + "/fit/python").json()["status"] == "done"
    for bad in (c.post(B + "/fit/klingon"), c.get(B + "/fit/klingon")):
        assert bad.status_code == 422 and bad.json()["code"] == "bad_language"
        assert bad.json()["detail"] == "That language is not set up on this machine yet."


def test_model_unreachable_reads_as_unknown_through_the_api():
    c, svc = client(scripted("python", {"sum2": RuntimeError("down")}))
    c.post(B + "/fit/python")
    wait_done(engine_for(svc))
    one = c.get(B + "/fit/python").json()
    assert (one["status"], one["verdict"]) == ("error", "unknown")
