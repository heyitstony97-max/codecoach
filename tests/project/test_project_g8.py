"""G8: a hidden test may only expect kinds of output line the learner has been shown (a visible test of this or an
earlier step, or a quote in the task). Orchestrator probe on local Qwen (Nov 8): tests expected the exact prompt
"Enter the secret number: " that the task never quoted. Also: steps 0 and 1 start building as soon as the plan is ready."""
import pytest
from proj_helpers import NEEDS_SANDBOX, Script, plan_reply, step_reply

from codecoach.check.code_runner import sandbox_status

from codecoach.core.models import CodeTest
from codecoach.core.llm import FakeLLM
from codecoach.core.store import Store
from codecoach.project import ProjectEngine, unshown_output
from codecoach.service import CodeCoachService, ServiceConfig


def _t(stdin, out):
    return CodeTest(name="t", stdin=stdin, expected_stdout=out)


def test_numbers_and_typed_input_may_differ_from_the_shown_line():
    assert unshown_output([_t("9\n", "Double: 18\n")], ["Double: 10"], "Print the double.") == []
    assert unshown_output([_t("Bob\n", "Hello, Bob!\n")], ["Hello, Ann!"], "Greet the name.") == []


def test_a_word_the_task_names_may_differ():
    task = 'Print "Grade: " and then the letter A, B, C, D or F.'
    assert unshown_output([_t("85\n", "Grade: B\n")], ["Grade: A"], task) == []


def test_output_the_learner_never_saw_is_caught():
    assert unshown_output([_t("5\n9\n", "Too high\n")], ["Too low"], "Compare the guess with the secret.") \
        == ["Too high"]
    assert unshown_output([_t("4\n", "Enter the secret number: The secret is 4.\n")], [],
                          "Read the secret and print it.") == ["Enter the secret number: The secret is 4."]


def test_a_quote_in_the_task_counts_as_shown():
    assert unshown_output([_t("5\n9\n", "Too high\n")], ["Too low"],
                          'Print "Too low" or "Too high".') == []
    assert unshown_output([_t("4\n", "The secret is 4.\n")], [], 'Print "The secret is N."') == []


GUESS = "s = int(input())\ng = int(input())\nif g < s:\n    print('Too low')\nelif g > s:\n    print('Too high')\n" \
        "else:\n    print('Correct!')\n"


@pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)
def test_a_step_whose_hidden_test_expects_unseen_output_is_not_shown():
    from proj_helpers import project
    bad = step_reply(reference_solution=GUESS, task="Compare the guess with the secret.",
                     visible_tests=[{"name": "low", "stdin": "5\n2\n", "expected_stdout": "Too low\n"}],
                     hidden_tests=[{"name": "high", "stdin": "5\n9\n", "expected_stdout": "Too high\n"}])
    svc, eng, pid, llm = project(Script(steps=[bad, bad, bad]), "python")
    eng.ensure_built(pid, 0, prefetch=False)
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable"
    assert "never shown" in rec["reason"] and "Too high" in rec["reason"]


@pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)
def test_steps_start_building_when_the_plan_is_ready(tmp_path):
    script = Script(plan=plan_reply(4), steps=[step_reply(), step_reply()])
    svc = CodeCoachService(Store(tmp_path / "a.db"), FakeLLM(script), ServiceConfig(), background=True, auto_bank=False)
    eng = ProjectEngine(svc)
    pid = eng.create("a greeter that says hello to a name", "python")
    for _ in range(5):
        svc.wait_idle(10)
    assert eng.step_record(pid, 0) is not None and eng.step_record(pid, 0)["status"] == "ready"
