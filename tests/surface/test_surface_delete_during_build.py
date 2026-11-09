"""Deleting a course while its question bank is being built must not make the learner wait out the whole
build (e2e J5: a Qwen build ran past 15 minutes and every delete answered 409). The delete asks the build to
stop between drafts; the next delete goes through."""

import time

import pytest
from surface_helpers import Clock, build_course, new_store  # noqa: F401

from codecoach.core.llm import FakeLLM
from codecoach.service import CodeCoachService, ServiceConfig, ServiceError


def test_delete_stops_a_running_question_build():
    def slow_junk(purpose, text):
        time.sleep(0.1)
        return "not json"
    store = new_store()
    cid = build_course(store, None, items_per=1)
    svc = CodeCoachService(store, FakeLLM(slow_junk), ServiceConfig(), now_fn=Clock(), background=True, auto_bank=False)
    assert svc.start_bank(cid)
    t0 = time.monotonic()
    with pytest.raises(ServiceError) as e:
        svc.delete_course(cid, confirm=True)
    assert e.value.status == 409 and e.value.code == "course_stopping"
    assert svc.wait_idle(30)
    assert time.monotonic() - t0 < 20, "the build was not stopped between drafts"
    out = svc.delete_course(cid, confirm=True)
    assert out["deleted"] is True and store.get_course(cid) is None


def test_delete_without_confirm_does_not_stop_anything():
    store = new_store()
    cid = build_course(store, None, items_per=1)
    svc = CodeCoachService(store, FakeLLM(lambda p, t: (time.sleep(0.02), "x")[1]), ServiceConfig(), now_fn=Clock(),
                        background=True, auto_bank=False)
    svc.start_bank(cid)
    with pytest.raises(ServiceError) as e:
        svc.delete_course(cid)
    assert e.value.code == "course_busy"
    assert cid not in svc._bank_cancel
    svc.wait_idle(30)
