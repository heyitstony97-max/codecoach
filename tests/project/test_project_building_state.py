"""A step that is already built reads "ready" while a later step builds. Before the fix, every step up to the one being
prefetched read "building" until the whole chain finished, so a finished next step sat behind "Building the next
step..." (37 s in the demo recording, while another project held the model)."""
import threading

import pytest
from proj_helpers import NEEDS_SANDBOX, PY_STEP1, Script, plan_reply, step_reply

from codecoach.check.code_runner import sandbox_status
from codecoach.core.llm import FakeLLM
from codecoach.core.store import Store
from codecoach.project import ProjectEngine
from codecoach.service import CodeCoachService, ServiceConfig

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)


def test_a_built_step_stays_ready_while_the_next_one_builds(tmp_path, monkeypatch):
    script = Script(plan=plan_reply(4), steps=[step_reply(), step_reply(**PY_STEP1)])
    svc = CodeCoachService(Store(tmp_path / "a.db"), FakeLLM(script), ServiceConfig(), background=True, auto_bank=False)
    eng = ProjectEngine(svc)
    pid = eng.create("a greeter that says hello to a name", "python")
    for _ in range(5):
        svc.wait_idle(10)
    assert eng.step_record(pid, 1)["status"] == "ready"          # steps 1 and 2 built when the plan landed

    gate, started = threading.Event(), threading.Event()
    real = eng._build_step

    def slow(p, j):
        started.set()
        gate.wait(10)
        return real(p, j)

    monkeypatch.setattr(eng, "_build_step", slow)
    eng.check(pid, 0, "name = input()\nprint('Hello, ' + name + '!')\n")    # passes step 1: step 3 starts building
    assert started.wait(5)
    try:
        steps = eng.get(pid)["steps"]
        assert steps[1]["status"] == "ready", steps[1]                 # the next step is not held behind step 3
        assert steps[2]["status"] in ("building", "locked"), steps[2]
    finally:
        gate.set()
        svc.wait_idle(10)
