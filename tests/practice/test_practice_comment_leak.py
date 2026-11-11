"""P8: a comment in a drill's program must not give away a line of the output (real sandbox).

P5 passes a drill as soon as ONE output line is computed, so before P8 a trailing comment could still hand the
learner another line. The learner reads the code, comments included.
"""

import pytest
from prac_helpers import C_CODE, C_EXPL, PY_CODE, engine, needs_sb, reply

from codecoach.practice.engine import comment_text

pytestmark = needs_sb

# Prints 8, 42 and 50. "8" and "50" are computed and absent from the source, so P5 passes; "42" sits in a comment.
PY_LEAK = "x = 7\nprint(x + 1)\nprint(x * 6)  # 42\nprint(x * x + 1)\n"


def served(*replies, lang="python"):
    _svc, eng, llm = engine(*replies)
    got = eng.next(lang, "loops")
    return eng.view(got["drill_id"]), llm


def gated(code, lang="python", explanation=None):
    bad = reply(code, explanation=explanation)
    v, llm = served(bad, bad, lang=lang)
    assert v["status"] == "unavailable", v
    assert "comment" in v["reason"] and v["code"] == "", v
    assert len(llm.calls) == 2, "one build and one rebuild"
    return llm


def test_trailing_comment_with_one_output_line_is_gated():
    gated(PY_LEAK)


def test_p5_keeps_a_drill_whose_number_also_sits_in_the_source():
    # Prints 5: does b follow a? The digit 5 is in the source, but not as a printed string literal.
    v, _ = served(reply("a = 5\nb = a\na = 7\nprint(b)\n"), reply(PY_CODE))
    assert v["status"] == "ready", v
    # Prints 6, which a substring test would 'find' inside 16.
    v, _ = served(reply("x = 16\nprint(x // 2 - 2)\n"), reply(PY_CODE))
    assert v["status"] == "ready", v


def test_p5_still_drops_a_program_that_only_prints_literals():
    bad = reply('print("Hello")\nprint(\'Bye\')\n')
    v, llm = served(bad, bad)
    assert v["status"] == "unavailable" and "nothing to work out" in v["reason"], v


@pytest.mark.parametrize("comment", ["# prints 8 then 42 then 50", "#42", "# -> 50"])
def test_full_line_python_comment_is_gated(comment):
    gated(PY_LEAK.replace("  # 42", "") + comment + "\n")


def test_c_line_comment_is_gated():
    gated(C_CODE.replace('printf("%d\\n", t);', 'printf("%d\\n", t); // 30'), lang="c", explanation=C_EXPL)


def test_c_block_comment_is_gated():
    gated(C_CODE.replace("int t = 0;", "int t = 0; /* the answer\n is 30 */"), lang="c", explanation=C_EXPL)


def test_comment_that_gives_nothing_away_passes():
    v, _ = served(reply(PY_CODE + "# add one more\n"), reply(PY_CODE.replace("Total", "T")))
    assert v["status"] == "ready", v


def test_number_inside_a_longer_number_is_not_a_leak():
    # Output is 137 inside "Total: 137"; a comment holding 1370 or 13.7 does not contain that output line.
    v, _ = served(reply(PY_CODE + "# 1370 and 13.7 are other numbers\n"), reply(PY_CODE.replace("Total", "T")))
    assert v["status"] == "ready", v


def test_marker_inside_a_string_does_not_drop_a_good_drill():
    code = 'n = 2 + 2\nprint("#" * 3 + str(n))\n'              # prints ###4
    v, _ = served(reply(code), reply(PY_CODE))
    assert v["status"] == "ready", v


def test_comment_text_reads_line_and_block_comments():
    assert comment_text("python", "a = 1  # one\nb = 2\n") == [" one"]
    assert comment_text("c", "int a; // one\n/* two\nthree */\n") == [" two\nthree ", " one"]
    assert comment_text("python", "print(1)\n") == []
