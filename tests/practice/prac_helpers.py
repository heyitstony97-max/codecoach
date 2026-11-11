"""Shared setup for the practice tests: a service over a temp store, a scripted FakeLLM, canned drill replies."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "surface", HERE.parent / "adapt"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from adapt_helpers import new_store  # noqa: E402,F401

from codecoach.check.code_runner import sandbox_status  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.practice.engine import DRILL_PURPOSE, engine_for  # noqa: E402,F401
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

import pytest  # noqa: E402

needs_sb = pytest.mark.skipif(sandbox_status() != "bwrap", reason="the real bwrap sandbox is needed")

# A python drill whose output ("Total: 137") is computed, never written in the source.
PY_CODE = 'n = sum(range(17))\nprint(f"Total: {n + 1}")\n'
PY_OUT = "Total: 137\n"
PY_EXPL = ["range(17) gives 0 to 16.", "Their sum is 136, and the program adds 1 before printing."]

C_CODE = ('#include <stdio.h>\n\nint main(void)\n{\n    int t = 0;\n    for (int i = 1; i <= 4; i++)\n'
          '        t += i * i;\n    printf("%d\\n", t);\n    return 0;\n}\n')
C_OUT = "30\n"
C_EXPL = ["The loop adds the squares 1, 4, 9 and 16.", "That makes 30, and printf shows it."]


def reply(code=PY_CODE, stdin="", explanation=None):
    return {"code": code, "stdin": stdin, "explanation": explanation or PY_EXPL}


class Script:
    """FakeLLM script function: replies are consumed in order; an empty queue gives None (a bad reply)."""

    def __init__(self, *replies):
        self.q = list(replies)

    def __call__(self, purpose, text):
        assert purpose == DRILL_PURPOSE, purpose
        return self.q.pop(0) if self.q else None


def make_svc(llm=None, *, background=False):
    return CodeCoachService(new_store(), llm, ServiceConfig(), background=background, auto_bank=False)


def engine(*replies, background=False):
    llm = FakeLLM(Script(*replies))
    svc = make_svc(llm, background=background)
    return svc, engine_for(svc), llm


def first_drill(eng, lang, topic):
    """The first drill id in the index for (lang, topic)."""
    for did in eng._index():
        r = eng.record(did)
        if r["language"] == lang and r["topic"] == topic:
            return did
    return None
