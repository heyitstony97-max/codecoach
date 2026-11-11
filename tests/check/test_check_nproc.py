"""The process limit lives inside the sandbox where the kernel allows it (Linux 5.14 and later).

Set from outside, the limit is checked against every task the user has, so a busy desktop or a few sandboxes side by
side made a correct program fail to start ("bwrap: Creating new namespace failed: Resource temporarily unavailable").
Set inside, it bounds that one sandbox: a fork bomb is still stopped at the cap, and nothing next door can use the
cap up. Real sandbox; the unit test at the top needs none.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from codecoach import lang
from codecoach.check import code_runner as cr
from codecoach.lang import probe

needs_sb = pytest.mark.skipif(cr.sandbox_status() != "bwrap", reason="the real bwrap sandbox is needed")
inside_only = pytest.mark.skipif(not cr.nproc_inside(),
                                 reason="on this machine the process limit cannot be set inside the sandbox")

# Forks until refused (at most 200 children, each waiting on a pipe that closes when this program ends).
FORKER = ("import os\nr, w = os.pipe()\nn = 0\nfor _ in range(200):\n    try:\n        pid = os.fork()\n"
          "    except OSError:\n        break\n    if pid == 0:\n        os.close(w)\n        os.read(r, 1)\n"
          "        os._exit(0)\n    n += 1\nprint(n)\n")
BOMB = "import os,time\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        time.sleep(0.01)\n"


def hello(lid):
    r = cr.run_snippet(lid, lang.spec(lid).hello, probe.PROBE_STDIN, time_limit_s=probe.PROBE_WALL_S, mem_mb=256)
    if r is None:
        return False, "no sandbox"
    ok = bool(r.ok and r.stdout.strip() == probe.PROBE_EXPECT)
    return ok, None if ok else (r.compile_error or r.stderr or "no output").strip()[-200:]


@pytest.mark.parametrize("forks, expect", [(None, False), (0, False), (1, True), (21, True), (23, True),
                                           (24, False), (60, False)])
def test_the_inside_limit_is_used_only_when_the_self_test_proves_it(monkeypatch, forks, expect):
    """No answer, no fork at all (an old kernel counts the whole user) or no stop (root): keep the outside limit."""
    monkeypatch.setattr(cr, "_nproc_selftest", lambda: forks)
    monkeypatch.setattr(cr, "_nproc_inside_cache", None)
    assert cr.nproc_inside() is expect
    assert cr.nproc_inside() is expect            # and the answer is remembered


@needs_sb
def test_the_self_test_runs_here_and_agrees_with_the_mode_in_use():
    n = cr._nproc_selftest()
    if n is None:
        assert cr.nproc_inside() is False
    else:
        assert cr.nproc_inside() is (1 <= n < cr._SELFTEST_CAP)


@needs_sb
@inside_only
def test_a_program_is_stopped_at_the_cap_and_cannot_lift_it():
    cap = lang.spec("python").limits.run_nproc
    r = cr.run_snippet("python", FORKER, time_limit_s=5.0)
    assert r is not None and r.ok, r
    n = int(r.stdout.strip())
    assert cap - 8 <= n < cap, f"{n} forks under a cap of {cap}"       # bwrap's own few processes count too
    lift = ("import resource\ntry:\n    resource.setrlimit(resource.RLIMIT_NPROC, (5000, 5000))\n    print('lifted')\n"
            "except (ValueError, OSError):\n    print('refused')\n")
    r = cr.run_snippet("python", lift, time_limit_s=5.0)
    assert r is not None and r.stdout.strip() == "refused", r


@needs_sb
@inside_only
def test_many_correct_programs_side_by_side_all_run():
    """With the outside limit this failed about one run in twenty-five at 16 at a time."""
    ready = lang.ready_ids()
    mix = [i for i in ("python", "c", "javascript", "java", "go") if i in ready]
    with ThreadPoolExecutor(max_workers=32) as pool:
        rows = list(pool.map(lambda k: (mix[k % len(mix)],) + hello(mix[k % len(mix)]), range(128)))
    bad = [(lid, why) for lid, ok, why in rows if not ok]
    assert bad == [], f"{len(bad)} of 128 correct programs failed: {bad[:3]}"


@needs_sb
@inside_only
def test_a_fork_bomb_next_door_does_not_break_a_correct_program():
    out = {}
    t = threading.Thread(target=lambda: out.update(r=cr.run_snippet("python", BOMB, time_limit_s=3.0)))
    t.start()
    try:
        time.sleep(0.6)                       # the bomb has filled its own sandbox by now
        results = [hello(lid) for lid in ("python", "c") * 6 if lid in lang.ready_ids()]
    finally:
        t.join(30)
    assert results and all(ok for ok, _ in results), [why for ok, why in results if not ok][:3]
    assert out["r"] is not None and (out["r"].timed_out or not out["r"].ok)      # and the bomb itself was stopped


@needs_sb
def test_the_outside_limit_still_runs_programs_and_still_stops_a_bomb(monkeypatch):
    """The fallback for old kernels: v1.0's limit, the user's task count plus headroom."""
    monkeypatch.setattr(cr, "_nproc_inside_cache", False)
    ok, why = hello("python")
    assert ok, why
    r = cr.run_snippet("python", FORKER, time_limit_s=5.0)
    assert r is not None and r.ok and int(r.stdout.strip()) < 200, r
    t0 = time.monotonic()
    r = cr.run_snippet("python", BOMB, time_limit_s=2.0)
    assert r is not None and (r.timed_out or not r.ok) and time.monotonic() - t0 < 12
