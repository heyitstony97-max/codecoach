"""The one-time model check starts on its own: after the model's first lesson or drill in a language, never for a
ready-made project, never twice for the same model, and never when the setting is off. Real sandbox, FakeLLM."""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("fit", "project", "practice", "adapt"):
    _p = str(ROOT / "tests" / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapt_helpers import new_store  # noqa: E402
from fit_helpers import GOOD, task_id_of  # noqa: E402
from prac_helpers import reply as drill_reply  # noqa: E402
from proj_helpers import PY_STEP1, plan_reply, step_reply  # noqa: E402

from codecoach.check.code_runner import sandbox_status  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.fit.engine import FIT_PURPOSE, engine_for  # noqa: E402
from codecoach.practice.engine import DRILL_PURPOSE  # noqa: E402
from codecoach.practice.engine import engine_for as practice_for  # noqa: E402
from codecoach.project import PLAN_PURPOSE, STEP_PURPOSE  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

needs_sb = pytest.mark.skipif(sandbox_status() != "bwrap", reason="the real bwrap sandbox is needed")
ON = ServiceConfig(auto_fit_check=True)


def model(steps=(), drills=()):
    """A FakeLLM that answers every kind of call: the fit warm-ups with working Python, the rest from the lists."""
    steps, drills = list(steps), list(drills)

    def fn(purpose, text):
        if purpose == FIT_PURPOSE:
            return {"code": GOOD["python"][task_id_of(text)]}
        if purpose == PLAN_PURPOSE:
            return plan_reply(4)
        if purpose == STEP_PURPOSE:
            return steps.pop(0) if steps else None
        if purpose == DRILL_PURPOSE:
            return drills.pop(0) if drills else None
        return None
    llm = FakeLLM(fn)
    llm.set_route("custom", "model-a")
    return llm


def service(llm, cfg=ON, background=True):
    return CodeCoachService(new_store(), llm, cfg, background=background, auto_bank=False)


def settle(svc, seconds=90):
    """Wait until every background job, and any check one of them started, has finished."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        svc.wait_idle(10)
        if not any(t.is_alive() for t in svc._threads) and not engine_for(svc).is_running():
            return
        time.sleep(0.05)
    raise AssertionError("background work did not finish")


def fit_calls(llm):
    return [c for c in llm.calls if c.purpose == FIT_PURPOSE]


def test_the_plugin_default_is_on_and_a_bare_config_is_off():
    assert ServiceConfig().auto_fit_check is False
    assert ServiceConfig.from_mapping({}.get).auto_fit_check is True
    assert ServiceConfig.from_mapping({"auto_fit_check": False}.get).auto_fit_check is False
    assert ServiceConfig.from_mapping({"auto_fit_check": "no"}.get).auto_fit_check is True     # only a real false turns it off
    text = (ROOT / "plugin.yaml").read_text()
    block = text[text.index("  auto_fit_check:"):]
    assert "type: bool" in block and "default: true" in block


@needs_sb
def test_first_use_starts_one_check_and_the_same_model_is_never_checked_twice():
    llm = model()
    svc = service(llm)
    svc.language_used("python")
    settle(svc)
    v = engine_for(svc).view("python")
    assert v["status"] == "done" and v["verdict"] == "good" and v["passed"] == 5
    assert v["model"] == {"provider": "custom", "model": "model-a"}
    assert len(fit_calls(llm)) == 5
    for _ in range(3):
        svc.language_used("python")
    settle(svc)
    assert len(fit_calls(llm)) == 5


@needs_sb
def test_another_model_is_checked_once_more():
    llm = model()
    svc = service(llm)
    svc.language_used("python")
    settle(svc)
    llm.set_route("custom", "model-b")
    svc.language_used("python")
    settle(svc)
    assert len(fit_calls(llm)) == 10
    assert engine_for(svc).view("python")["model"] == {"provider": "custom", "model": "model-b"}


@pytest.mark.parametrize("why", ["setting off", "no model", "cannot run in the background"])
def test_nothing_starts_when_it_should_not(why):
    llm = model()
    svc = {"setting off": lambda: service(llm, ServiceConfig(auto_fit_check=False)),
           "no model": lambda: service(None),
           "cannot run in the background": lambda: service(llm, background=False)}[why]()
    for lang in ("python", "klingon", None, 7):
        svc.language_used(lang)
    settle(svc, 10)
    assert llm.calls == []
    assert engine_for(svc).view("python")["status"] == "none"


def test_an_unknown_language_or_a_broken_engine_never_raises(monkeypatch):
    llm = model()
    svc = service(llm)
    svc.language_used("klingon")
    import codecoach.fit.engine as fit_engine

    def boom(_svc):
        raise RuntimeError("no engine")
    monkeypatch.setattr(fit_engine, "engine_for", boom)
    svc.language_used("python")
    assert llm.calls == []


@needs_sb
def test_a_model_built_project_step_starts_the_check_after_the_step_is_stored():
    llm = model(steps=[step_reply(), step_reply(**PY_STEP1)])
    svc = service(llm)
    pid = svc.project_create("a greeter that says hello", "python")["project_id"]
    settle(svc)                                  # Start plans the project and builds the first lesson
    svc.project_step(pid, 0)
    settle(svc)
    assert svc.project_step(pid, 0)["status"] == "ready"
    purposes = [c.purpose for c in llm.calls]
    # the check never runs ahead of the first lesson: plan, then the first step, and only then a warm-up
    assert purposes.index(PLAN_PURPOSE) < purposes.index(STEP_PURPOSE) < purposes.index(FIT_PURPOSE)
    assert len(fit_calls(llm)) == 5
    assert engine_for(svc).view("python")["verdict"] == "good"


@needs_sb
def test_a_ready_made_project_never_starts_a_check():
    llm = model()
    svc = service(llm)
    pid = svc._project.create("", "python", starter="grade-book")
    settle(svc)
    svc.project_step(pid, 0)
    settle(svc)
    assert svc.project_step(pid, 0)["status"] == "ready"
    assert llm.calls == []
    assert engine_for(svc).view("python")["status"] == "none"


@needs_sb
def test_a_model_built_drill_starts_the_check():
    llm = model(drills=[drill_reply()])
    svc = service(llm)
    out = practice_for(svc).next("python", "loops")
    settle(svc)
    assert practice_for(svc).view(out["drill_id"])["status"] == "ready"
    purposes = [c.purpose for c in llm.calls]
    assert purposes.index(DRILL_PURPOSE) < purposes.index(FIT_PURPOSE)
    assert len(fit_calls(llm)) == 5
