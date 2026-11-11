"""Sandbox proofs for every Tier A language (contract section 3). Hostile programs run only inside the sandbox."""
from __future__ import annotations

import os
import time

from codecoach import lang
from codecoach.check import code_runner as cr
from lang_programs import PROGS

WALL = 8.0


def run(lid: str, key: str, stdin: str = "", wall: float = 3.0, mem: int = 256):
    r = cr.run_snippet(lid, PROGS[lid][key], stdin, time_limit_s=wall, mem_mb=mem)
    assert r is not None
    return r


def test_hello_and_stdin_echo(lid):
    r = cr.run_snippet(lid, lang.spec(lid).hello, "ping pong\n", time_limit_s=10.0)
    assert r is not None and r.ok, (r.compile_error, r.stderr)
    assert r.stdout.strip() == "echo: ping pong"


def test_error_is_plain_text_without_host_paths(lid):
    r = run(lid, "bad", wall=10.0)
    assert not r.ok
    text = (r.compile_error or "") + r.stderr
    assert text.strip(), "an error message is expected"
    for leak in (os.path.expanduser("~"), "codecoach-run-", "/home/"):
        assert leak not in text, text


def test_infinite_loop_is_stopped(lid):
    t0 = time.monotonic()
    r = run(lid, "loop", wall=1.5)
    assert r.timed_out and not r.ok
    assert time.monotonic() - t0 < WALL + 10


def test_memory_hog_is_stopped(lid):
    t0 = time.monotonic()
    r = run(lid, "hog", wall=WALL, mem=128)
    assert not r.ok
    assert time.monotonic() - t0 < WALL + 12


def test_fork_or_thread_bomb_is_stopped(lid):
    t0 = time.monotonic()
    r = run(lid, "bomb", wall=4.0)
    assert time.monotonic() - t0 < WALL + 12
    # the bomb either failed, was cut off by the process cap, or timed out; it must not have launched 5000
    assert "5000" not in r.stdout.split()
    assert cr._leftover_groups() == [] if hasattr(cr, "_leftover_groups") else True


def test_no_network(lid):
    r = run(lid, "net", wall=8.0)
    assert "CONNECTED" not in r.stdout
    assert "NONET" in r.stdout or not r.ok


def test_canary_outside_the_sandbox_is_not_readable(lid, canary):
    r = run(lid, "canary", stdin=canary + "\n", wall=8.0)
    assert "CANARY" not in r.stdout + r.stderr
    assert "LEAK" not in r.stdout


def test_output_is_capped(lid):
    r = run(lid, "flood", wall=2.0)
    assert len(r.stdout) <= cr.MAX_OUTPUT + 200
