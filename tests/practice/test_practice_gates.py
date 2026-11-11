"""Gates P1-P6 tripped by crafted replies, with the REAL sandbox. P7 is in test_practice_engine.py (the view)."""

import pytest
from prac_helpers import C_CODE, C_EXPL, PY_CODE, PY_EXPL, engine, needs_sb, reply

pytestmark = needs_sb


def build_one(*replies, lang="python", topic="loops"):
    """Run next() inline with the scripted replies; returns (engine, llm, view of the served drill)."""
    _svc, eng, llm = engine(*replies)
    got = eng.next(lang, topic)
    return eng, llm, eng.view(got["drill_id"])


def assert_gated(*replies, lang="python", words=(), calls=2):
    eng, llm, v = build_one(*replies, lang=lang)
    assert v["status"] == "unavailable", v
    assert v["code"] == "" and v["line_count"] == 0
    assert v["reason"] and v["reason"][0].isupper() and v["reason"].endswith((".", "?")), v["reason"]
    for w in words:
        assert w in v["reason"], (w, v["reason"])
    assert len(llm.calls) == calls, "one build and ONE rebuild, then unavailable"
    return eng, llm, v


def test_good_drill_passes_all_gates():
    eng, llm, v = build_one(reply(), reply(PY_CODE.replace("Total", "T")))   # the second reply is the single prefetch
    assert v["status"] == "ready" and v["code"] == PY_CODE and v["line_count"] == 1


def test_p1_crash_python():
    bad = reply('import sys\nprint(len("abc") + 4)\nsys.exit(3)\n')
    assert_gated(bad, bad, words=("crashed",))


def test_p1_does_not_compile_c():
    bad = reply('#include <stdio.h>\nint main(void) { printf("%d\\n", 1 +); return 0; }\n', explanation=C_EXPL)
    assert_gated(bad, bad, lang="c", words=("did not compile",))


def test_p1_nonzero_exit_c():
    bad = reply('#include <stdio.h>\nint main(void) { printf("%d\\n", 6 * 7); return 2; }\n', explanation=C_EXPL)
    assert_gated(bad, bad, lang="c", words=("crashed",))


def test_p1_too_slow():
    bad = reply("x = 0\nwhile True:\n    x += 1\n")
    assert_gated(bad, bad, words=("too long",))


def test_p2_address_python():
    bad = reply("print(id(object()) * 0 + id([]))\n")
    assert_gated(bad, bad, words=("different output",))


def test_p2_address_c():
    bad = reply('#include <stdio.h>\nint main(void)\n{\n    int x = 1;\n    printf("%p\\n", (void *)&x);\n'
                '    return 0;\n}\n', explanation=C_EXPL)
    assert_gated(bad, bad, lang="c", words=("different output",))


def test_p2_process_id_or_time():
    # The sandbox gives each run its own process namespace, so a pid may repeat; the clock still moves.
    bad = reply("import os, time\nprint(os.getpid() * 0 + time.perf_counter_ns())\n")
    assert_gated(bad, bad, words=("different output",))


def test_p3_nine_output_lines():
    bad = reply("for i in range(9):\n    print(i * i + 1)\n")
    assert_gated(bad, bad, words=("9 lines", "at most 8"))


def test_p3_eight_lines_pass_and_long_line_fails():
    eng, _llm, v = build_one(reply("for i in range(8):\n    print(i * i + 11)\n"), reply(PY_CODE))
    assert v["status"] == "ready" and v["line_count"] == 8
    bad = reply('print("x" * (40 + 41))\n')
    assert_gated(bad, bad, words=("80",))


def test_p3_prints_nothing():
    bad = reply("n = 3 * 4\n")
    assert_gated(bad, bad, words=("nothing to predict",))


def test_p4_thirty_lines_of_code():
    code = "".join(f"x{i} = {i * 7}\n" for i in range(29)) + "print(x1 + x28)\n"
    assert sum(1 for ln in code.splitlines() if ln.strip()) == 30
    bad = reply(code)
    assert_gated(bad, bad, words=("30 lines", "at most 25"))


def test_p4_blank_lines_do_not_count():
    code = "".join(f"x{i} = {i * 7}\n\n" for i in range(20)) + "print(x1 + x19)\n"
    eng, _llm, v = build_one(reply(code), reply(PY_CODE))
    assert v["status"] == "ready"


def test_p5_only_prints_literals():
    bad = reply('print("hello")\nprint("world")\n')
    assert_gated(bad, bad, words=("nothing to work out",))


def test_p5_one_computed_line_is_enough():
    eng, _llm, v = build_one(reply('print("hello")\nprint(6 * 7)\n'), reply(PY_CODE))
    assert v["status"] == "ready"


def test_p6_duplicate_of_a_stored_drill():
    spaced = PY_CODE.replace("n = sum", "n  =  sum").replace("\nprint", "\n\n\nprint")
    assert spaced != PY_CODE
    _svc, eng, llm = engine(reply(), reply(spaced), reply(spaced))   # drill 1; its prefetch twice a whitespace copy
    first = eng.next("python", "loops")["drill_id"]
    ids = eng._index()
    assert len(ids) == 2 and ids[0] == first
    second = eng.view(ids[1])
    assert second["status"] == "unavailable" and "copy of an earlier drill" in second["reason"]
    assert len(llm.calls) == 3


def test_p6_same_code_other_topic_or_language_is_not_a_duplicate():
    _svc, eng, _llm = engine(reply(), reply(PY_CODE.replace("Total", "Sum")), reply(), reply(PY_CODE.replace("Total", "T2")))
    eng.next("python", "loops")
    other = eng.next("python", "arithmetic")
    assert eng.view(other["drill_id"])["status"] == "ready"


def test_rebuild_gets_the_rejection_in_its_prompt_and_can_succeed():
    bad = reply('print("hello")\nprint("world")\n')
    _svc, eng, llm = engine(bad, reply(), reply(PY_CODE.replace("Total", "Sum")))
    v = eng.view(eng.next("python", "loops")["drill_id"])
    assert v["status"] == "ready" and v["code"] == PY_CODE
    assert "rejected" in llm.calls[1].text and "nothing to work out" in llm.calls[1].text
    assert "rejected" not in llm.calls[0].text


def test_malformed_replies_use_the_same_two_tries():
    short = {"code": PY_CODE, "stdin": "", "explanation": ["only one sentence"]}
    assert_gated(short, short, words=("could not be used",), calls=2)


def test_empty_stdout_lines_only_is_nothing():
    bad = reply('print("")\nprint(" ")\n')
    assert_gated(bad, bad, words=("nothing to predict",))


@pytest.mark.parametrize("lang,code,expl", [("c", C_CODE, C_EXPL), ("python", PY_CODE, PY_EXPL)])
def test_stdin_is_passed_to_the_program(lang, code, expl):
    if lang == "python":
        code = "n = int(input())\nprint(n * n + 1)\n"
    else:
        code = ('#include <stdio.h>\nint main(void)\n{\n    int n;\n    scanf("%d", &n);\n    printf("%d\\n", n * n + 1);\n'
                '    return 0;\n}\n')
    eng, _llm, v = build_one(reply(code, stdin="12\n", explanation=expl), reply(code.replace("+ 1", "+ 2")), lang=lang)
    assert v["status"] == "ready" and v["stdin"] == "12\n" and v["line_count"] == 1
    rec = eng.record(v["id"])
    assert rec["stdout"] == "145\n"
