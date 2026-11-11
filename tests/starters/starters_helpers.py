"""Shared setup for the ready-made project tests: a service over a temp store, and a model that must never be called."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "surface", HERE.parent / "adapt", HERE.parent / "project"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from adapt_helpers import new_store  # noqa: E402

from codecoach import lang, starters  # noqa: E402
from codecoach.check.code_runner import sandbox_status  # noqa: E402,F401
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.project import ProjectEngine  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

NEEDS_SANDBOX = "the real bwrap sandbox is needed"
STARTER = "grade-book"


def forbidden(purpose, text):
    raise AssertionError(f"a ready-made project made a model call ({purpose})")


def make(llm="fake"):
    """(service, engine, model). `llm='fake'` is a FakeLLM that fails if called; None means no model at all."""
    model = FakeLLM(forbidden) if llm == "fake" else llm
    svc = CodeCoachService(new_store(), model, ServiceConfig(), background=False, auto_bank=False)
    return svc, ProjectEngine(svc), model


def skip_unless_ready(lid):
    if lid not in lang.ready_ids():
        pytest.skip(f"{lid} toolchain is not ready on this machine: {lang.not_ready_reason(lid)}")


def shipped():
    return starters.languages(STARTER)


def new_lines(prev: str, ref: str, lid: str) -> list[str]:
    """The engine's own G7 answer lines: new reference lines of 12+ characters (comments and imports aside)."""
    from codecoach.project import leaked_lines
    return leaked_lines(prev, ref, lid)
