"""A lesson must never wait on the model: the passage is served at once, the framing line is written
in the background, cached, and shown from then on. (Live bug: on a local 27B model the framing call
took minutes and the desktop gave up after 30 s.)"""

import json
import time

from surface_helpers import T0, Clock, build_course, new_store, oid  # noqa: F401

from codecoach.core.llm import FakeLLM
from codecoach.gen.lesson import framing_key
from codecoach.service import CodeCoachService, ServiceConfig

FRAMING = "Read this passage, then answer the questions about it."


def _slow_llm(delay: float, calls: list):
    def respond(purpose, text):
        calls.append(purpose)
        time.sleep(delay)
        return json.dumps({"framing": FRAMING})
    return FakeLLM(respond)


def _service(llm, *, background=True):
    store = new_store()
    cid = build_course(store, None, items_per=2)
    svc = CodeCoachService(store, llm, ServiceConfig(), now_fn=Clock(), background=background, auto_bank=False)
    return svc, store, cid


def test_first_lesson_returns_at_once_while_the_model_is_slow():
    calls: list = []
    svc, store, cid = _service(_slow_llm(2.0, calls))
    obj = store.list_objectives(cid)[0]
    t = time.monotonic()
    r = svc.next(cid, obj.id)
    took = time.monotonic() - t
    assert r.kind == "lesson" and r.lesson is not None
    assert r.lesson.passages, "the passage itself is the lesson"
    assert r.lesson.framing is None, "no framing yet: it is still being written"
    assert took < 1.0, f"next() waited on the model ({took:.2f}s)"
    assert svc.wait_idle(10)
    assert store.get_meta(framing_key(obj.id)) == {"framing": FRAMING}
    again = svc._lesson(store.get_course(cid), obj.id)
    assert again.framing == FRAMING
    assert calls.count("codecoach.gen.framing") == 1, "the framing is cached, not rewritten per view"


def test_inline_mode_still_frames_the_first_lesson():
    svc, store, cid = _service(_slow_llm(0.0, []), background=False)
    obj = store.list_objectives(cid)[0]
    r = svc.next(cid, obj.id)
    assert r.kind == "lesson" and r.lesson.framing == FRAMING


def test_model_failure_still_serves_the_passage():
    def boom(purpose, text):
        raise RuntimeError("model down")
    svc, store, cid = _service(FakeLLM(boom), background=False)
    obj = store.list_objectives(cid)[0]
    r = svc.next(cid, obj.id)
    assert r.kind == "lesson" and r.lesson.passages and r.lesson.framing is None


def test_warm_lessons_caches_every_covered_objective_once():
    calls: list = []
    svc, store, cid = _service(_slow_llm(0.0, calls), background=False)
    covered = [o for o in store.list_objectives(cid) if o.coverage == "covered"]
    assert svc.warm_lessons(cid) == len(covered)
    assert svc.warm_lessons(cid) == 0
    assert all(store.get_meta(framing_key(o.id)) == {"framing": FRAMING} for o in covered)
