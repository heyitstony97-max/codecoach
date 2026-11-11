"""Gates G1-G8 and their helpers no longer assume C or Python. Every fix is proven on a third language
(JavaScript, Java, Ruby, Rust, Bash), and C/Python keep their v1.0 behaviour. Pure functions: no sandbox runs."""

import pytest

import codecoach.project as P
from codecoach.check.code_runner import CodeResult
from codecoach.core.models import CodePayload, CodeTest


def _payload(language, ref="x"):
    return CodePayload(language=language, starter="", visible_tests=[CodeTest(name="a", stdin="", expected_stdout="1\n")],
                       hidden_tests=[CodeTest(name="b", stdin="2\n", expected_stdout="2\n")], reference_solution=ref)


def _failed(compile_error):
    return CodeResult(ok=False, score=0.0, detail="", undecided=False, per_test=[], passed=0, total=2,
                      compile_error=compile_error)


def _g1_reason(monkeypatch, language, compile_error):
    monkeypatch.setattr(P, "check_reference", lambda payload: _failed(compile_error))
    with pytest.raises(P.GateFail) as e:
        P.gate_reference_passes(_payload(language))
    assert e.value.gate == "G1"
    return e.value.reason


# ---- G1: the label follows the language

@pytest.mark.parametrize("lid", ["javascript", "ruby", "bash"])
def test_g1_says_syntax_error_for_an_interpreted_language(monkeypatch, lid):
    why = _g1_reason(monkeypatch, lid, "unexpected token '}'\nmore")
    assert "it has a syntax error: unexpected token '}'" in why and "did not compile" not in why
    assert "more" not in why                                   # one line, as before


@pytest.mark.parametrize("lid", ["rust", "java", "go", "cpp"])
def test_g1_says_did_not_compile_for_a_compiled_language(monkeypatch, lid):
    why = _g1_reason(monkeypatch, lid, "solution.rs:3: cannot find value `x`")
    assert "it did not compile: solution.rs:3" in why and "syntax error" not in why


@pytest.mark.parametrize("lid", ["c", "python"])
def test_g1_wording_for_c_and_python_is_unchanged(monkeypatch, lid):
    assert "it did not compile: boom" in _g1_reason(monkeypatch, lid, "boom")


def test_g1_empty_compile_error_text_does_not_crash(monkeypatch):
    assert "(0 of 2 tests)" in _g1_reason(monkeypatch, "ruby", "")           # no message: the plain test count


# ---- G5: comment-only lines are not code in the new languages; C and Python count them as before

def test_g5_comment_lines_do_not_count_in_javascript():
    prev = "// Your program starts here\n"
    ref = prev + "\n".join(f"// note {i}" for i in range(60)) + "\nconsole.log(1);\nconsole.log(2);\n"
    assert P.added_lines(prev, ref, "javascript") == 2
    P.gate_small_step(prev, ref, "javascript")


def test_g5_hash_comments_in_ruby_and_bash_are_skipped_too():
    ref = "# a\n# b\nputs 1\n"
    assert P.added_lines("", ref, "ruby") == 1 and P.added_lines("", ref, "bash") == 1


def test_g5_code_lines_still_count_in_java():
    ref = "\n".join(f"        int a{i} = {i};" for i in range(45))
    with pytest.raises(P.GateFail) as e:
        P.gate_small_step("", ref, "java")
    assert e.value.gate == "G5" and "45" in e.value.reason


@pytest.mark.parametrize("lid", ["c", "python", None])
def test_g5_c_and_python_count_comment_lines_like_before(lid):
    assert P.added_lines("", "// a\n# b\nx\n", lid) == 3


# ---- G7: boilerplate and comments are not the answer in the new languages

JAVA_PREV = "public class Main {\n    public static void main(String[] args) {\n    }\n}\n"
JAVA_REF = ("import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n"
            "        // read the number and print its double\n"
            "        Scanner in = new Scanner(System.in);\n        System.out.println(in.nextInt() * 2);\n    }\n}\n")


def test_g7_java_import_and_comment_lines_are_not_leaks():
    got = P.leaked_lines(JAVA_PREV, JAVA_REF, "java")
    assert "import java.util.Scanner;" not in got and not any(g.startswith("//") for g in got)
    assert got == ["Scanner in = new Scanner(System.in);", "System.out.println(in.nextInt() * 2);"]


def test_g7_java_hint_naming_the_import_survives_but_the_real_line_is_dropped():
    hints = ["Add import java.util.Scanner; at the top.", "Print System.out.println(in.nextInt() * 2); at the end.",
             "Use a Scanner on System.in."]
    kept = P.drop_leaky_hints(hints, JAVA_PREV, JAVA_REF, "java")
    assert kept == [hints[0], hints[2]]


def test_g7_c_include_line_keeps_the_v10_rule():
    prev = "int main(void)\n{\n    return 0;\n}\n"
    ref = "#include <string.h>\n" + prev
    assert P.leaked_lines(prev, ref) == ["#include <string.h>"]              # C and Python: unchanged
    assert P.leaked_lines(prev, ref, "c") == ["#include <string.h>"]
    assert P.leaked_lines(prev, "#include <string.h>\n" + prev, "cpp") == []


@pytest.mark.parametrize("lid,line", [("rust", "use std::io::{self, BufRead};"), ("go", 'import "bufio"'),
                                      ("ruby", "require 'set'  # sets"), ("bash", "set -euo pipefail"),
                                      ("javascript", "const fs = require('fs');"),
                                      ("cpp", "using namespace std;")])
def test_g7_import_style_lines_per_language(lid, line):
    assert P.leaked_lines("", line + "\n", lid) == []
    assert P.leaked_lines("", line + "\n", None) == [line]                    # the v1.0 rule sees it


def test_g7_an_example_with_only_an_import_does_not_count_as_giving_the_answer():
    answer = P.leaked_lines(JAVA_PREV, JAVA_REF, "java")
    example = "import java.util.Scanner;\npublic class Main { public static void main(String[] a) { } }\n"
    assert not P.leaks_answer(example, answer)


# ---- G8 is about output text, which no language changes

def test_g8_output_of_a_rust_step_is_judged_like_any_other():
    hidden = [CodeTest(name="h", stdin="3\n", expected_stdout="3 is odd\n")]
    assert P.unshown_output(hidden, ["5 is odd"], "Print N is odd.") == []
    assert P.unshown_output(hidden, [], "Print something.") == ["3 is odd"]


# ---- the runner's own labels (lang-a) reach the learner for a third language

def test_run_labels_syntax_vs_compile_error_by_language():
    from codecoach.lang import spec
    assert spec("javascript").compiled is False and spec("rust").compiled is True
    assert P._compiles("javascript") is False and P._compiles("rust") is True
    assert P._compiles("python") is True                                      # kept for v1.0 wording
    assert P._compiles("nonsense") is True


def test_g1_skips_a_banner_line_in_go_output_but_not_in_c(monkeypatch):
    msg = "# command-line-arguments\n./solution.go:6:14: undefined: x"
    assert "undefined: x" in _g1_reason(monkeypatch, "go", msg)
    assert "it did not compile: # command-line-arguments" in _g1_reason(monkeypatch, "c", msg)     # v1.0 rule
