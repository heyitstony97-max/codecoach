"""The fit engine with a scripted FakeLLM and the REAL sandbox on c and python."""

import threading
import time

import pytest
from fit_helpers import (GOOD, IDS, NEEDS_SANDBOX, PY, make_fit_svc, run_check, scripted, task_id_of, wait_done)

import codecoach.fit.engine as E
from codecoach.check.code_runner import sandbox_status
from codecoach.core.llm import FakeLLM, LLMUnavailable
from codecoach.fit.engine import engine_for, prompt_for
from codecoach.fit.tasks import BY_ID, WARMUPS
from codecoach.project import _BUILD_LOCK

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)


@pytest.mark.parametrize("language", ["python", "c"])
def test_all_five_correct_is_good_5_of_5(language):
    llm = scripted(language)
    _, _, v = run_check(language, llm)
    assert (v["status"], v["verdict"], v["passed"], v["total"], v["done_tasks"]) == ("done", "good", 5, 5, 5)
    assert [t["id"] for t in v["tasks"]] == IDS
    assert all(t["ok"] and t["reason"] is None for t in v["tasks"])
    assert v["model"] == {"provider": "custom", "model": "model-a"}
    assert v["stale"] is False and v["measured_at"].endswith("Z") and isinstance(v["median_ms"], int)
    assert v["advice"].endswith("should teach %s well." % ("Python" if language == "python" else "C"))
    assert v["advice"].startswith("This model passed 5 of 5")


def test_wrong_output_on_three_is_poor_with_reasons():
    ov = {"sum2": "print(0)\n", "fizzbuzz": "print('x')\n", "max_of_list": "print(1)\n"}
    _, _, v = run_check("python", scripted("python", ov))
    assert (v["verdict"], v["passed"]) == ("poor", 2)
    bad = {t["id"]: t["reason"] for t in v["tasks"] if not t["ok"]}
    assert bad == {k: E.WRONG for k in ov}
    assert v["advice"] == ("This model passed 2 of 5 Python warm-ups. Lessons in Python may fail to build or take long. "
                           "You can point CodeCoach at another model and check again.")


def test_verdict_bands():
    assert [E.verdict_for(n) for n in (5, 4, 3, 2, 1, 0)] == ["good", "good", "mixed", "poor", "poor", "poor"]
    _, _, v = run_check("python", scripted("python", {"sum2": "print(0)\n", "fizzbuzz": "print('x')\n"}))
    assert (v["verdict"], v["passed"]) == ("mixed", 3)
    assert "Some Python lessons" in v["advice"]
    _, _, v = run_check("python", scripted("python", {"sum2": "print(0)\n"}))
    assert (v["verdict"], v["passed"]) == ("good", 4)


def test_a_program_that_does_not_compile():
    _, _, v = run_check("c", scripted("c", {"fizzbuzz": "int main( { this is not C\n"}))
    t = {x["id"]: x for x in v["tasks"]}
    assert t["fizzbuzz"]["ok"] is False and t["fizzbuzz"]["reason"] == "Its program did not compile."
    assert v["passed"] == 4 and v["verdict"] == "good"
    assert "/" not in t["fizzbuzz"]["reason"]


def test_a_program_that_loops_forever_is_too_slow(monkeypatch):
    monkeypatch.setattr(E, "TIME_LIMIT_S", 1.0)
    _, _, v = run_check("python", scripted("python", {"sum2": "while True:\n    pass\n"}))
    t = {x["id"]: x for x in v["tasks"]}
    assert t["sum2"]["reason"] == "Its program was too slow." and t["sum2"]["ok"] is False
    assert v["passed"] == 4


def test_a_crash_and_an_empty_answer_have_plain_reasons():
    _, _, v = run_check("python", scripted("python", {"sum2": "raise SystemExit(3)\n", "fizzbuzz": "   \n"}))
    t = {x["id"]: x["reason"] for x in v["tasks"]}
    assert t["sum2"] == E.CRASHED and t["fizzbuzz"] == E.NO_ANSWER


def test_a_fenced_answer_is_still_graded():
    fenced = "```python\n" + PY["sum2"] + "```"
    _, _, v = run_check("python", scripted("python", {"sum2": fenced}))
    assert v["passed"] == 5


def test_the_model_raising_is_unknown_and_stops_asking():
    llm = scripted("python", {"sum2": LLMUnavailable("down")})
    _, eng, v = run_check("python", llm)
    assert (v["status"], v["verdict"], v["passed"]) == ("error", "unknown", 0)
    assert all(t["reason"] == E.NO_ANSWER for t in v["tasks"])
    assert len(llm.structured_calls) == 1
    assert "could not reach the model" in v["advice"]
    assert eng.is_running() is False


def test_a_plain_exception_from_the_model_is_unknown_too():
    _, _, v = run_check("python", scripted("python", {"sum2": RuntimeError("boom")}))
    assert v["verdict"] == "unknown" and v["status"] == "error"


def test_an_unusable_reply_fails_that_task_only():
    llm = FakeLLM(lambda p, t: None if task_id_of(t) == "sum2" else {"code": PY[task_id_of(t)]})
    llm.set_route("custom", "model-a")
    _, _, v = run_check("python", llm)
    assert v["status"] == "done" and v["passed"] == 4
    assert [t["reason"] for t in v["tasks"] if not t["ok"]] == [E.NO_ANSWER]
    assert len(llm.structured_calls) == 5          # no retry, even for a bad reply


@pytest.mark.parametrize("language", ["python", "c"])
def test_exactly_five_model_calls_per_check(language):
    llm = scripted(language, {"sum2": "print(0)\n"})
    run_check(language, llm)
    assert len(llm.calls) == 5 and len(llm.structured_calls) == 5
    assert {c.purpose for c in llm.calls} == {"codecoach.fit"}
    assert [task_id_of(c.text) for c in llm.calls] == IDS


def test_every_prompt_holds_only_task_text_language_name_and_style_note():
    llm = scripted("c")
    run_check("c", llm)
    name, note = E.language_table()["c"]
    for call, w in zip(llm.calls, WARMUPS):
        assert call.text == prompt_for(w, name, note)
        assert call.text == f"Language: {name}\nStyle: {note}\n\nTask: {w.text}"
        rest = call.text.replace(w.text, "").replace(note, "").replace(f"Language: {name}", "")
        assert rest.replace("Language:", "").replace("Style:", "").replace("Task:", "").strip() == ""
        assert all(o.text not in call.text for o in WARMUPS if o.id != w.id)
        assert all(t.expected_stdout not in call.text for t in w.tests)     # no key leaks
    assert all(GOOD["c"][w.id] not in c.text for c, w in zip(llm.calls, WARMUPS))


def test_checks_run_under_the_shared_build_lock():
    seen = []
    llm = FakeLLM(lambda p, t: (seen.append(_BUILD_LOCK.locked()), {"code": PY[task_id_of(t)]})[1])
    llm.set_route("custom", "model-a")
    run_check("python", llm)
    assert seen == [True] * 5


def test_running_state_shows_done_tasks_going_up_and_409_while_running():
    gate = threading.Semaphore(0)
    entered = threading.Semaphore(0)

    def fn(purpose, text):
        entered.release()
        assert gate.acquire(timeout=60)
        return {"code": PY[task_id_of(text)]}
    llm = FakeLLM(fn)
    llm.set_route("custom", "model-a")
    svc = make_fit_svc(llm)
    eng = engine_for(svc)
    assert eng.view("python")["status"] == "none"
    assert eng.start("python") == {"status": "running"}
    assert entered.acquire(timeout=30)
    v = eng.view("python")
    assert (v["status"], v["done_tasks"], v["total"]) == ("running", 0, 5)
    from codecoach.service import ServiceError
    with pytest.raises(ServiceError) as ex:
        eng.start("python")
    assert (ex.value.status, ex.value.code) == (409, "fit_running")
    with pytest.raises(ServiceError) as ex:
        eng.start("c")
    assert ex.value.code == "fit_running"
    seen = []
    for n in range(1, 6):
        gate.release()
        end = time.time() + 60
        while eng.view("python")["done_tasks"] < n and eng.view("python")["status"] == "running":
            assert time.time() < end
            time.sleep(0.02)
        seen.append(eng.view("python")["done_tasks"])
    wait_done(eng)
    assert seen == [1, 2, 3, 4, 5]
    v = eng.view("python")
    assert (v["status"], v["verdict"], v["done_tasks"]) == ("done", "good", 5)
    assert eng.start("python") == {"status": "running"}      # free again
    for _ in range(5):
        gate.release()
    wait_done(eng)


def test_stale_flips_when_the_last_route_changes():
    llm = scripted("python")
    svc, eng, v = run_check("python", llm)
    assert v["stale"] is False
    llm.set_route("custom", "model-b")
    assert eng.view("python")["stale"] is True
    assert eng.overview()["languages"]["python"]["stale"] is True
    assert eng.overview()["model"] == {"provider": "custom", "model": "model-b"}
    llm.set_route("custom", "model-a")
    assert eng.view("python")["stale"] is False


def test_records_for_two_models_are_kept_apart():
    llm = scripted("python")
    svc = make_fit_svc(llm)
    eng = engine_for(svc)
    eng.start("python")
    wait_done(eng)
    llm.set_route("other", "model-b")
    bad = {t.id: "print(0)\n" for t in WARMUPS[:3]}
    llm._script = lambda p, t: {"code": bad.get(task_id_of(t), PY[task_id_of(t)])}
    eng.start("python")
    wait_done(eng)
    a = svc.store.get_meta("fit:python:custom/model-a")
    b = svc.store.get_meta("fit:python:other/model-b")
    assert (a["passed"], a["verdict"], a["model"]["model"]) == (5, "good", "model-a")
    assert (b["passed"], b["verdict"], b["model"]["model"]) == (2, "poor", "model-b")
    assert svc.store.get_meta("fit:latest:python")["model"]["model"] == "model-b"
    assert eng.view("python")["verdict"] == "poor" and eng.view("python")["stale"] is False


def test_ensure_starts_only_without_a_record_for_the_current_model():
    llm = scripted("python")
    svc = make_fit_svc(llm)
    eng = engine_for(svc)
    assert engine_for(svc) is eng
    assert eng.ensure("python") is True
    assert eng.ensure("python") is False                 # already running: never blocks, never queues
    wait_done(eng)
    assert eng.ensure("python") is False                 # record exists for model-a
    llm.set_route("custom", "model-b")
    assert eng.ensure("python") is True                  # new model, no record
    wait_done(eng)
    assert eng.ensure("nope") is False
    assert len(llm.calls) == 10


def test_bad_language_is_422():
    from codecoach.service import ServiceError
    eng = engine_for(make_fit_svc(scripted("python")))
    with pytest.raises(ServiceError) as ex:
        eng.start("klingon")
    assert (ex.value.status, ex.value.code) == (422, "bad_language")
    assert ex.value.message == "That language is not set up on this machine yet."


def test_inline_service_runs_the_whole_check_in_the_call():
    _, eng, v = run_check("python", scripted("python"), background=False)
    assert v["status"] == "done" and eng.is_running() is False
