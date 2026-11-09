"""Answer-leak rules (orchestrator probe, Nov 8). The learner writes the step's new lines; no hint, example or lesson
sentence may hand them over, whatever the spacing. And "5" vs "5\\n" is one input, not a hidden test."""
import pytest

from codecoach.check.code_runner import sandbox_status
from codecoach.project import leaks_answer
from proj_helpers import C_STEP0, Script, project, step_reply

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason="needs the bwrap sandbox")

ANSWER = 'printf("Double: %d\\n", n * 2);'
OTHER_EXAMPLE = {"code": '#include <stdio.h>\nint main(void)\n{\n    int k = 4;\n    printf("%d\\n", k + 1);\n'
                         '    return 0;\n}\n', "stdin": ""}


def _built(reply, examples=()):
    svc, eng, pid, llm = project(Script(steps=[reply, reply, reply], examples=list(examples)), language="c")
    eng.ensure_built(pid, 0, prefetch=False)
    return svc.store.get_meta(f"project:{pid}:step:0"), llm


def test_spacing_does_not_hide_the_answer():
    assert leaks_answer('Try printf("Double: %d\\n",n*2); next.', [ANSWER])
    assert not leaks_answer("Use printf with %d to print a number.", [ANSWER])


def test_a_hint_that_re_spaces_the_answer_line_is_dropped():
    rec, _ = _built(step_reply(**{**C_STEP0, "hints": ["You need to read a number.",
                                                       'Write printf("Double: %d\\n",n*2); after reading.',
                                                       'scanf("%d", &___);']}))
    assert rec["status"] == "ready"
    assert not any("Double: %d" in h for h in rec["hints"])
    assert len(rec["hints"]) == 2


def test_an_example_that_is_the_answer_is_replaced():
    rec, llm = _built(step_reply(**{**C_STEP0, "example": {"code": C_STEP0["reference_solution"], "stdin": "4\n"}}),
                      examples=[OTHER_EXAMPLE])
    assert rec["status"] == "ready"
    assert "Double: %d" not in rec["example"]["code"]
    assert rec["example"]["stdout"] == "5\n"                      # the real run of the replacement


def test_an_example_that_stays_the_answer_is_left_out():
    leaky = {"code": C_STEP0["reference_solution"], "stdin": "4\n"}
    rec, _ = _built(step_reply(**{**C_STEP0, "example": leaky}), examples=[leaky])
    assert rec["status"] == "ready" and rec["example"] is None


def test_a_lesson_sentence_with_the_answer_line_is_dropped():
    rec, _ = _built(step_reply(**{**C_STEP0, "lesson": ["Read the number with scanf.",
                                                        f"Then write {ANSWER} to print it."]}))
    assert rec["lesson"] == ["Read the number with scanf."]


def test_a_hidden_test_that_only_drops_the_newline_is_a_repeat():
    dup = step_reply(**{**C_STEP0, "hidden_tests": [{"name": "five again", "stdin": "5",
                                                      "expected_stdout": "Double: 10\n"}]})
    rec, _ = _built(dup)
    assert rec["status"] == "unavailable"
    assert "repeats the input" in rec["reason"]
