"""A two-step project end to end in Rust, Java and JavaScript: scripted FakeLLM, the REAL sandbox and toolchains.
Plan, build step 0 and 1 through every gate, run, a failing check, a passing check, code carried forward, download
extension. Plus: a Go reference that does not compile is refused by G1 with a plain reason."""

import pytest
from proj_helpers import NEEDS_SANDBOX, Script, plan_reply, project, step_reply

import codecoach.project as P
from codecoach import lang
from codecoach.check.code_runner import sandbox_status

pytestmark = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)

HINTS = ["Think about where the name comes from.", "The program reads one line from the keyboard.",
         "Read the line, then greet it with the same words as the example."]

PROGRAMS = {
    "javascript": dict(
        ext="js",
        ex="console.log('hi');\n",
        r0="const name = require('fs').readFileSync(0, 'utf8').split('\\n')[0];\nconsole.log('Hello, ' + name + '!');\n",
        r1="const name = require('fs').readFileSync(0, 'utf8').split('\\n')[0];\nif (name === '') {\n"
           "  console.log('Hello, stranger!');\n} else {\n  console.log('Hello, ' + name + '!');\n}\n",
        wrong="console.log('nope');\n", broken="console.log('unclosed\n"),
    "rust": dict(
        ext="rs",
        ex='fn main() {\n    println!("hi");\n}\n',
        r0='use std::io;\n\nfn main() {\n    let mut name = String::new();\n    io::stdin().read_line(&mut name).unwrap();\n'
           '    println!("Hello, {}!", name.trim_end());\n}\n',
        r1='use std::io;\n\nfn main() {\n    let mut name = String::new();\n    io::stdin().read_line(&mut name).unwrap();\n'
           '    let name = name.trim_end();\n    if name.is_empty() {\n        println!("Hello, stranger!");\n'
           '    } else {\n        println!("Hello, {}!", name);\n    }\n}\n',
        wrong='fn main() {\n    println!("nope");\n}\n', broken='fn main() {\n    println!("unclosed);\n}\n'),
    "java": dict(
        ext="java",
        ex='public class Main {\n    public static void main(String[] args) {\n        System.out.println("hi");\n    }\n}\n',
        r0='import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n'
           '        Scanner in = new Scanner(System.in);\n        String name = in.hasNextLine() ? in.nextLine() : "";\n'
           '        System.out.println("Hello, " + name + "!");\n    }\n}\n',
        r1='import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n'
           '        Scanner in = new Scanner(System.in);\n        String name = in.hasNextLine() ? in.nextLine() : "";\n'
           '        if (name.isEmpty()) {\n            System.out.println("Hello, stranger!");\n        } else {\n'
           '            System.out.println("Hello, " + name + "!");\n        }\n    }\n}\n',
        wrong='public class Main {\n    public static void main(String[] args) {\n        System.out.println("nope");\n    }\n}\n',
        broken='public class Main {\n    public static void main(String[] args) {\n        System.out.println("x")\n    }\n}\n'),
}

STEP0_TESTS = dict(visible_tests=[{"name": "ann", "stdin": "Ann\n", "expected_stdout": "Hello, Ann!\n"}],
                   hidden_tests=[{"name": "bob", "stdin": "Bob\n", "expected_stdout": "Hello, Bob!\n"}])
STEP1_TESTS = dict(visible_tests=[{"name": "empty", "stdin": "\n", "expected_stdout": "Hello, stranger!\n"}],
                   hidden_tests=[{"name": "bob", "stdin": "Bob\n", "expected_stdout": "Hello, Bob!\n"}])


def _need(lid):
    if lid not in lang.ready_ids():
        pytest.skip(f"{lid} toolchain is not ready on this machine: {lang.not_ready_reason(lid)}")


def _two_step(lid):
    pr = PROGRAMS[lid]
    steps = [step_reply(example={"code": pr["ex"], "stdin": ""}, reference_solution=pr["r0"], hints=HINTS, **STEP0_TESTS),
             step_reply(example={"code": pr["ex"], "stdin": ""}, reference_solution=pr["r1"], hints=HINTS,
                        task="Greet a blank line as a stranger.", **STEP1_TESTS)]
    svc, eng, pid, llm = project(Script(steps=steps, plan=plan_reply(4)), lid)
    p = eng.load(pid)                                              # a plan has 4+ steps; keep two so step 1 is the last
    p["plan"] = p["plan"][:2]
    svc.store.set_meta(f"project:{pid}", p)
    return svc, eng, pid, llm, pr


@pytest.mark.parametrize("lid", ["rust", "java", "javascript"])
def test_two_step_project_end_to_end(lid):
    _need(lid)
    svc, eng, pid, llm, pr = _two_step(lid)
    g = eng.get(pid)
    assert g["language"] == lid and g["ext"] == pr["ext"] and g["language_name"] == lang.spec(lid).name
    assert len(g["steps"]) == 2 and g["code"] == lang.spec(lid).starter

    # the prompt that went to the model names the language and carries its style line, not "C or Python"
    s0 = eng.step(pid, 0)
    assert s0["status"] == "ready", s0.get("reason")
    assert s0["example"]["ran"] is True and s0["example"]["stdout"] == "hi\n"
    assert s0["starting_code"] == lang.spec(lid).starter and s0["hidden_count"] == 1
    call = [c for c in llm.calls if c.purpose == P.STEP_PURPOSE][0]
    assert call.text.startswith(f"Language: {lang.spec(lid).name}\n")
    assert lang.spec(lid).style_note in call.instructions and "Plain C11 or Python 3" not in call.instructions
    assert eng.step_record(pid, 1)["status"] == "ready"            # the next step was built ahead, gates and all

    # run: visible tests only, never passes the step
    bad = eng.run(pid, 0, pr["wrong"])
    assert bad["ok"] is False and bad["results"][0]["stdout"] == "nope\n"
    assert eng.get(pid)["code"] == pr["wrong"]
    broke = eng.run(pid, 0, pr["broken"])
    assert broke["ok"] is False and eng.get(pid)["steps"][0]["status"] == "ready"
    assert eng.run(pid, 0, pr["r0"])["ok"] is True

    # check: a failing one counts an attempt, a passing one moves on and carries the code forward
    f = eng.check(pid, 0, pr["wrong"])
    assert f["step_passed"] is False and f["next_index"] is None
    assert [t for t in f["results"] if not t["visible"]][0]["stdout"] is None
    assert eng.get(pid)["steps"][0]["attempts"] == 1
    ok = eng.check(pid, 0, pr["r0"])
    assert (ok["step_passed"], ok["next_index"], ok["project_done"]) == (True, 1, False)
    assert eng.get(pid)["code"] == pr["r0"]
    s1 = eng.step(pid, 1)
    assert s1["status"] == "ready" and s1["starting_code"] == pr["r0"]

    # step 1: the old program no longer passes (G2 proved that at build time), the new one does
    assert eng.check(pid, 1, pr["r0"])["step_passed"] is False
    done = eng.check(pid, 1, pr["r1"])
    assert (done["step_passed"], done["next_index"], done["project_done"]) == (True, None, True)
    g = eng.get(pid)
    assert g["status"] == "done" and g["code"] == pr["r1"] and all(s["status"] == "passed" for s in g["steps"])
    assert f"project.{g['ext']}".endswith("." + pr["ext"])


@pytest.mark.parametrize("lid", ["rust", "java", "javascript"])
def test_a_syntax_slip_in_the_learners_code_is_reported_in_plain_words(lid):
    _need(lid)
    _, eng, pid, _, pr = _two_step(lid)
    eng.step(pid, 0)
    r = eng.run(pid, 0, pr["broken"])
    assert r["ok"] is False and r.get("compile_error")
    c = eng.check(pid, 0, pr["broken"])
    assert c["step_passed"] is False and c["passed"] == 0


def test_go_reference_that_does_not_compile_is_rejected_by_g1_with_a_plain_reason():
    _need("go")
    bad = "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(undefinedName)\n}\n"
    steps = [step_reply(example={"code": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("hi")\n}\n', "stdin": ""},
                        reference_solution=bad, hints=HINTS, **STEP0_TESTS) for _ in range(3)]
    _, eng, pid, llm = project(Script(steps=steps), "go")
    eng.ensure_built(pid, 0, prefetch=False)
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable"
    assert "own solution did not pass its own tests" in rec["reason"] and "it did not compile:" in rec["reason"]
    assert "undefined" in rec["reason"].lower()
    assert len([c for c in llm.calls if c.purpose == P.STEP_PURPOSE]) == 3
    assert "rejected" in [c for c in llm.calls if c.purpose == P.STEP_PURPOSE][1].text


def test_javascript_reference_with_a_syntax_error_says_syntax_error():
    _need("javascript")
    steps = [step_reply(example={"code": "console.log('hi');\n", "stdin": ""}, reference_solution="console.log('x'\n",
                        hints=HINTS, **STEP0_TESTS) for _ in range(3)]
    _, eng, pid, _ = project(Script(steps=steps), "javascript")
    eng.ensure_built(pid, 0, prefetch=False)
    rec = eng.step_record(pid, 0)
    assert rec["status"] == "unavailable" and "it has a syntax error:" in rec["reason"]
