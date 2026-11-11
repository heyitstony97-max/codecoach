"""The explanation check (codecoach/practice/explain_check.py): a drill's explanation that disagrees with what the
program really printed is left out; the drill stays. The WRONG and RIGHT cases below are real drills written by a
small free model on the dev box (program, input, real output and explanation copied as stored)."""

import pytest
from prac_helpers import C_CODE, C_EXPL, C_OUT, PY_CODE, PY_EXPL, PY_OUT, engine, needs_sb, reply

from codecoach.practice.explain_check import LEFT_OUT, explanation_problem, real_line_breaks, shown_explanation

RUST_READ = ('use std::io::{self, BufRead};\n\nfn main() {\n    let stdin = io::stdin();\n    let mut %s = 0;\n'
             '    for line in stdin.lock().lines() {\n        let s = line.unwrap();\n'
             '        let n: i32 = s.trim().parse().unwrap();\n%s    }\n    println!("{}", %s);\n}\n')
COUNT_POSITIVE = RUST_READ % ("count", "        if n == 0 {\n            break;\n        }\n        if n > 0 {\n"
                                       "            count += 1;\n        }\n", "count")
ADD_AND_FLIP = RUST_READ % ("total", "        if n == 0 {\n            break;\n        }\n        if n > 0 {\n"
                                     "            total += n;\n        } else {\n            total -= n;\n        }\n", "total")
ARITH = ("fn main() {\n    let a = 7 * 8;\n    let b = 56 / 4;\n    let c = 3 + 4 * 2;\n    let d = 20 - 6 / 2;\n"
         "    let e = 2 + 3 * 4 - 1;\n    println!(\"{}\", a + b + c + d + e);\n}\n")
PHP = '<?php\nfscanf(STDIN, "%d", $n);\n$s = str_repeat("ab", $n);\nfor ($i = 0; $i < $n; $i++) {\n    echo $s[$i];\n}\necho "\\n";\n'
RUBY = 'name = gets.chomp\ngreeting = "Hello, " + name + "!"\nputs greeting\nage = 20\nage = age + 5\nputs "Next year you will be #{age + 1}"\n'
JS_CHAIN = ("let a = 1;\nlet b = a + 2;\nlet c = b * 2;\nlet d = c - 3;\nlet e = d / 2;\nlet f = e + 1;\nlet g = f * 3;\n"
            "let h = g - 4;\nconsole.log(a, b, c, d, e, f, g, h);\n")
PY_CALLS = ("def f(x):\n    return x * 2\n\ndef g(y):\n    return f(y) + 1\n\ndef h(z):\n    return g(z) - 3\n\na = 5\nb = h(a)\n"
            "print(b)\nprint(f(b))\nprint(g(f(b)))\nprint(h(g(f(b))))\nprint(h(a) + f(a))\nprint(g(a) - h(a))\n"
            "print(f(g(h(a))))\nprint(h(f(g(a))))\n")
TABLE = "for i in range(1, 6):\n    row = ''\n    for j in range(1, 6):\n        row += str(i * j) + ' '\n    print(row)\n"
TABLE_OUT = "1 2 3 4 5 \n2 4 6 8 10 \n3 6 9 12 15 \n4 8 12 16 20 \n5 10 15 20 25 \n"

WRONG = {
    "says 2, printed 1": (COUNT_POSITIVE, "3\n-1\n0\n", "1\n", [
        "The program reads lines from standard input until it sees a line containing 0, which makes it break out of the loop.",
        "Each non-zero line is parsed as an i32, and only positive numbers increment the count variable.",
        "The final println! prints the count of positive numbers read before the zero terminator.",
        "For the given input, two positive numbers are read before the 0, so the output is 2."], "E1"),
    "says 98, printed 111": (ARITH, "", "111\n", [
        "The program evaluates each arithmetic expression step by step, following the usual order of operations where "
        "multiplication and division are done before addition and subtraction.",
        "The first four values are 56, 14, 11, and 17, which are then summed together.",
        "Adding those results gives 98, so the program prints exactly that number.",
        "No input is needed because all values are computed directly in the code."], "E2"),
    "lists eight values, printed eight others": (JS_CHAIN, "", "1 3 6 3 1.5 2.5 7.5 3.5\n", [
        "Each variable is assigned a value based on the previous variable, starting with a = 1.",
        "The chain of arithmetic operations produces the sequence 1, 3, 6, 9, 4.5, 5.5, 16.5, 12.5.",
        "console.log prints all eight values separated by spaces."], "E4"),
    "says 7, printed 8 (held-out run)": (
        "int x = 0;\nif (a > 0) x++;\nif (b < 0) x++;\nif (c == 0) x++;\nif (d % 2 == 0) x++;\nif (e > 5) x++;\n"
        "if (f <= 3) x++;\nif (g != 7) x++;\nif (h >= 10) x++;\nSystem.out.println(x);\n", "3 -5 0 4 6 2 8 10\n", "8\n", [
            "The program reads eight integers from standard input and counts how many of them satisfy the given conditions.",
            "Each if statement increments x only when its condition is true, so x equals the number of true conditions.",
            "For the input 3 -5 0 4 6 2 8 10, the true conditions are a>0, b<0, c==0, d%2==0, e>5, f<=3, and h>=10, giving a total of 7.",
            "The final value of x is printed, which is 7."], "E1"),
    "says one per line, printed one line": (PHP, "3\n", "aba\n", [
        "The program reads one integer from standard input.", "It builds a string by repeating \"ab\" that many times.",
        "Then it prints only the first n characters of that string, one per line.",
        "For n=3, the repeated string is \"ababab\" and its first 3 characters are a, b, a."], "E3"),
}
RIGHT = {
    "intermediate values, right total": (ADD_AND_FLIP, "5\n-3\n0\n", "8\n", [
        "The program reads lines from standard input until it encounters a line containing 0, which triggers a break.",
        "For each non-zero number, it adds the number to total if it is positive, and subtracts the number from total if it is negative.",
        "With the input 5, -3, and 0, the total becomes 5 - (-3) = 8, and then the loop stops at 0.",
        "Finally, the program prints the computed total, which is 8."]),
    "a value on the way (25) that is never printed": (RUBY, "Ada\n", "Hello, Ada!\nNext year you will be 26\n", [
        "The program reads a line from standard input and removes the trailing newline with chomp, storing it in name.",
        "It builds a greeting string by concatenating \"Hello, \", the name, and \"!\", then prints that greeting.",
        "The age variable starts at 20, then is increased by 5 to become 25.",
        "The second puts prints a string that interpolates age plus 1, which is 26."]),
    "giving 8, which is printed": (ADD_AND_FLIP, "3\n5\n0\n", "8\n", [
        "The program reads lines from standard input until it encounters a line containing 0.",
        "Each non-zero number is parsed and added to a running sum.", "When the loop breaks on 0, the accumulated sum is printed.",
        "For the given input, 3 and 5 are added, giving 8, which is printed."]),
    "several lines, each on its own": ("fn main() {\n    let x = 5;\n    let y = x + 3;\n    let z = y * 2;\n    println!(\"x = {}\", x);\n"
                                       "    println!(\"y = {}\", y);\n    println!(\"z = {}\", z);\n}\n", "", "x = 5\ny = 8\nz = 16\n", [
        "The variable x is bound to the integer 5.", "The variable y is bound to the value of x plus 3, which is 8.",
        "The variable z is bound to the value of y multiplied by 2, which is 16.",
        "Each println! prints the current value of its variable on its own line."]),
    "a table": (TABLE, "", TABLE_OUT, [
        "The outer loop runs with i taking values 1 through 5.", "For each i, the inner loop runs with j taking values 1 through 5.",
        "Each iteration appends the product i * j followed by a space to row.",
        "After the inner loop finishes, the completed row is printed, producing five lines of products.",
        "The output shows the multiplication table from 1 to 5."]),
    "the suite's own python drill": (PY_CODE, "", PY_OUT, PY_EXPL),
    "the suite's own c drill": (C_CODE, "", C_OUT, C_EXPL),
}


@pytest.mark.parametrize("name", sorted(WRONG))
def test_a_wrong_explanation_is_found(name):
    code, stdin, stdout, expl, rule = WRONG[name]
    problem = explanation_problem(code, stdin, stdout, expl)
    assert problem and problem.startswith(rule), problem
    assert shown_explanation({"code": code, "stdin": stdin, "stdout": stdout, "explanation": expl}) == [LEFT_OUT]


@pytest.mark.parametrize("name", sorted(RIGHT))
def test_a_right_explanation_is_kept(name):
    code, stdin, stdout, expl = RIGHT[name]
    assert explanation_problem(code, stdin, stdout, expl) is None
    assert shown_explanation({"code": code, "stdin": stdin, "stdout": stdout, "explanation": expl}) == expl


@pytest.mark.parametrize("sentence, stdout, bad", [
    ("So the output is 7.", "7\n", False),
    ("So the output is 7.", "8\n", True),
    ("The program prints 12.", "12\n", False),
    ("The program prints 12.", "21\n", True),
    ("The program prints exactly 3.50.", "3.5\n", False),            # the same number, written differently
    ("It prints -4.", "4\n", False),                                 # the sign never decides a mismatch
    ("The loop prints 3 lines.", "a\nb\nc\n", False),                # a count of things, not a printed value
    ("The loop prints 3 lines.", "a\nb\n", True),                    # ... but the count is still a number from nowhere (E2)
    ("It prints 5 stars.", "*****\n", False),
    ("The sum, 1,000, is printed.", "1000\n", False),
    ("The total is 9, which is printed.", "10\n", True),
    ("It prints the sum of the list, which is 6.", "6\n", False),
    ("It prints the sum of the list, which is 6.", "7\n", True),
    ("The variable is an i32 and the 2nd branch runs, so the output is ok.", "ok\n", False),   # i32 and 2nd are not numbers
    ("The list has 4 items and their sum is 10.", "11\n", False),    # not a sentence about printing: left alone
    ("Each print outputs one integer, and the program exits with status 0.", "8\n", False),   # an exit status is not output
    ("The values are 1, 2, 3 and 4.", "1 2 3 5\n", True),            # as many as were printed, and not the same (E4)
    ("The values are 1, 2, 3 and 4.", "1 2 3 4\n", False),
    ("The squares are 1, 4, 9 and 16.", "30\n", False),              # a list of steps on the way: another count, left alone
])
def test_what_counts_as_saying_a_number_is_printed(sentence, stdout, bad):
    assert bool(explanation_problem("print(x)", "", stdout, [sentence, "A second sentence."])) is bad


def test_it_does_not_prove_an_explanation_right():
    """Stated limit: wrong reasoning that ends on the right number passes, and so does a wrong number as a word."""
    assert explanation_problem("print(2 + 2)", "", "4\n", ["Two times three is 4.", "So the output is 4."]) is None
    assert explanation_problem("print(2 + 2)", "", "4\n", ["It adds the numbers.", "So the program prints five."]) is None
    # A real drill, kept as a KNOWN MISS: the seventh line prints 34 (h(5) is 8, g(8) is 17, f(17) is 34), and the
    # explanation says f(g(h(5))) = f(11) = 22. The sentence ends on a right value, so nothing here can tell.
    assert explanation_problem(PY_CALLS, "", "8\n16\n33\n64\n18\n3\n34\n42\n", [
        "The program defines three functions that chain arithmetic operations: f doubles its input, g adds 1 after calling f, "
        "and h subtracts 3 after calling g.",
        "Starting with a = 5, h(a) computes g(5) - 3, which is f(5) + 1 - 3 = 10 + 1 - 3 = 8, so the first print shows 8.",
        "The next lines print the results of applying the functions to the previous results, following the same rule: "
        "f(8) = 16, g(16) = 33, h(33) = 64.",
        "The final three prints combine these values: h(5) + f(5) = 8 + 10 = 18, g(5) - h(5) = 11 - 8 = 3, "
        "f(g(h(5))) = f(11) = 22, and h(f(g(5))) = h(22) = 42.",
        "Each print statement outputs one computed integer, and the program exits with status 0."]) is None


def test_known_miss_an_explanation_that_is_wrong_in_words():
    """A real drill from the held-out run: the program printed 0 14 -14 and the explanation says the second value
    is negative and the third positive. No number is wrong, so the check has nothing to compare."""
    code = ('#include <stdio.h>\n#include <string.h>\n\nint main(void) {\n    char s1[20] = "hello";\n    char s2[20] = "hello";\n'
            '    char s3[20] = "hella";\n    printf("%d %d %d\\n", strcmp(s1, s2), strcmp(s1, s3), strcmp(s3, s1));\n    return 0;\n}\n')
    assert explanation_problem(code, "", "0 14 -14\n", [
        "s1 and s2 both contain \"hello\", so the first call returns 0.",
        "s3 contains \"hella\", which is less than \"hello\", so the second call returns a negative value and the third "
        "returns the opposite positive value.",
        "The signs are always negative, zero, and positive in that order."]) is None


def test_known_false_alarm_numbers_printed_with_nothing_between_them():
    """A real drill from the held-out run: the program prints 01, 04, 09 and 16 as "01040916". The explanation is
    right, the check cannot find 16 in that run of digits, and the explanation is left out. Kept visible on purpose."""
    code = ('#include <stdio.h>\n\nint main(void) {\n    int i;\n    for (i = 1; i <= 4; i++) {\n        printf("%.2d", i * i);\n'
            '    }\n    printf("\\n");\n    return 0;\n}\n')
    problem = explanation_problem(code, "", "01040916\n", [
        "The loop starts with i equal to 1 and runs while i is less than or equal to 4.",
        "Each iteration prints the square of i with two digits, so the values are 01, 04, 09, and 16.",
        "After the loop, a newline is printed, producing the final output."])
    assert problem and problem.startswith("E2")


def test_nothing_to_show_and_broken_records_do_not_raise():
    assert shown_explanation({}) == []
    assert shown_explanation({"explanation": []}) == []
    assert shown_explanation({"explanation": ["So the output is 2."]}) == [LEFT_OUT]          # no output recorded at all
    assert shown_explanation({"explanation": ["Fine.", 7, ""], "stdout": "1\n", "code": None}) == ["Fine."]


def test_real_line_breaks():
    assert real_line_breaks("3\n4\n") == "3\n4\n"
    assert real_line_breaks("Hello, Rust!\\n") == "Hello, Rust!\n"
    assert real_line_breaks("3\\n4\\n") == "3\n4\n"
    assert real_line_breaks("a\\tb\\r\\n") == "a\tb\n"
    assert real_line_breaks("") == ""


def test_answer_leaves_a_wrong_explanation_out_and_keeps_the_key():
    wrong = ["range(17) gives 0 to 16.", "Their sum is 136, so the output is Total: 136, and the program prints 136."]
    _svc, eng, _ = engine(reply(explanation=wrong))
    did = eng.next("python", "loops")["drill_id"]
    assert eng.record(did)["explanation"] == wrong                       # stored as the model wrote it
    out = eng.answer(did, "Total: 137")
    assert out["correct"] is True and out["expected_stdout"] == PY_OUT
    assert out["explanation"] == [LEFT_OUT]


def test_answer_keeps_a_right_explanation():
    _svc, eng, _ = engine(reply())
    did = eng.next("python", "loops")["drill_id"]
    assert eng.answer(did, "Total: 137")["explanation"] == PY_EXPL


@needs_sb
def test_an_input_escaped_twice_is_run_and_shown_with_real_line_breaks():
    code = "a = int(input())\nb = int(input())\nprint(a * b + 1)\n"
    _svc, eng, _ = engine(reply(code=code, stdin="6\\n7\\n", explanation=["It reads two numbers.", "It multiplies them and adds 1."]))
    did = eng.next("python", "arithmetic")["drill_id"]
    v = eng.view(did)
    assert v["status"] == "ready" and v["stdin"] == "6\n7\n"
    assert eng.answer(did, "43")["correct"] is True
