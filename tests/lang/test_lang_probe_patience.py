"""A language is not written off for a hiccup, and a language that is not ready says why in plain words.

- not_ready_reason() answers for every language (it raised NameError, which turned "Ruby is not set up" into a crash)
- one failed hello is tried once more before the language is marked failed
- a failure that ran out of time is the machine's fault as far as we know: it is not remembered across restarts
"""

import json

import pytest

from codecoach import lang
from codecoach.check import code_runner as cr
from codecoach.lang import probe
from codecoach.lang import toolchain as tcm

needs_sb = pytest.mark.skipif(cr.sandbox_status() != "bwrap", reason="the real bwrap sandbox is needed")


def test_every_language_can_say_why_it_is_not_ready():
    for lid in lang.all_ids():
        reason = lang.not_ready_reason(lid)
        assert isinstance(reason, str) and reason.strip().endswith("."), (lid, reason)


@needs_sb
def test_running_a_language_that_is_not_set_up_gives_the_reason_instead_of_crashing(monkeypatch):
    lid = "ruby"
    try:
        with monkeypatch.context() as m:
            m.setattr(tcm, "discover", lambda *a, **k: tcm.Discovery("missing", None, "Ruby was not found on this machine."))
            assert lang.toolchain_for(lid, refresh=True) is None
            r = cr.run_snippet(lid, "puts 1\n")
            assert r is not None and r.ok is False and r.compile_error == "Ruby was not found on this machine."
    finally:
        lang.toolchain_for(lid, refresh=True)          # put the real answer back for the tests that follow


@pytest.fixture
def cache(tmp_path):
    lang.configure(tmp_path)
    try:
        yield tmp_path / "lang-probe.json"
    finally:
        lang.configure(None)


def saved(path):
    return json.loads(path.read_text())["langs"] if path.exists() else {}


def scripted(monkeypatch, answers):
    answers = list(answers)
    calls = []

    def fake(sp):
        calls.append(sp.id)
        return answers.pop(0)
    monkeypatch.setattr(probe, "_hello", fake)
    return calls


@needs_sb
def test_one_failed_hello_is_tried_again_before_the_language_is_written_off(monkeypatch, cache):
    if lang.toolchain_for("python") is None:
        pytest.skip("python is not on this machine")
    calls = scripted(monkeypatch, [(False, 5, False), (True, 80, False), (True, 40, False)])
    st = lang.status("python", refresh=True)
    assert st.status == "ready" and st.reason is None and len(calls) == 3
    assert saved(cache)["python"]["status"] == "ready"


@needs_sb
def test_a_language_that_fails_twice_is_failed_and_that_is_remembered(monkeypatch, cache):
    if lang.toolchain_for("python") is None:
        pytest.skip("python is not on this machine")
    calls = scripted(monkeypatch, [(False, 5, False), (False, 6, False)])
    st = lang.status("python", refresh=True)
    assert st.status == "failed" and "could not run inside the sandbox" in (st.reason or "") and len(calls) == 2
    assert saved(cache)["python"]["status"] == "failed"


@needs_sb
def test_running_out_of_time_is_not_remembered_and_says_so(monkeypatch, cache):
    if lang.toolchain_for("python") is None:
        pytest.skip("python is not on this machine")
    scripted(monkeypatch, [(False, 12000, True), (False, 12000, True)])
    st = lang.status("python", refresh=True)
    assert st.status == "failed"
    assert "did not finish in time" in (st.reason or "") and "Look again" in (st.reason or "")
    assert "python" not in saved(cache), "a busy-machine failure must not survive a restart"
    assert lang.status("python").status == "failed"                 # this run still knows, without probing again

    scripted(monkeypatch, [(True, 80, False), (True, 40, False)])     # Look again, on a quieter machine
    st = lang.status("python", refresh=True)
    assert st.status == "ready"
    assert saved(cache)["python"]["status"] == "ready"
