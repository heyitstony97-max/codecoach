"""Shared setup for the project-engine tests: a service over a temp store, a scripted FakeLLM, canned replies."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SURFACE = HERE.parent / "surface"
for _p in (SURFACE, HERE.parent / "adapt"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from adapt_helpers import new_store  # noqa: E402,F401

from codecoach.check.code_runner import sandbox_status  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.project import (EXAMPLE_PURPOSE, PLAN_PURPOSE, STEP_PURPOSE, ProjectEngine)  # noqa: E402,F401
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

NEEDS_SANDBOX = "the real bwrap sandbox is needed"


def make_svc(llm=None):
    return CodeCoachService(new_store(), llm, ServiceConfig(), background=False, auto_bank=False)


class Script:
    """FakeLLM script function: plan / step / example replies are consumed in order per purpose."""

    def __init__(self, plan=None, steps=(), examples=()):
        self.plan = plan
        self.steps = list(steps)
        self.examples = list(examples)

    def __call__(self, purpose, text):
        if purpose == PLAN_PURPOSE:
            return self.plan
        q = self.steps if purpose == STEP_PURPOSE else self.examples
        return q.pop(0) if q else None


def plan_reply(n=4, **kw):
    r = {"feasible": True, "why_not": "", "title": "Greeter", "summary": "A tiny program that greets you.",
         "steps": [{"title": f"Step {i + 1}", "goal": f"goal {i + 1}", "concept": f"idea {i + 1}"} for i in range(n)]}
    r.update(kw)
    return r


def step_reply(**kw):
    r = {"lesson": ["Input comes from the keyboard.", "A variable remembers it."],
         "example": {"code": "print('hi')\n", "stdin": ""},
         "task": "Read a name and greet it.",
         "reference_solution": "name = input()\nprint('Hello, ' + name + '!')\n",
         "visible_tests": [{"name": "ann", "stdin": "Ann\n", "expected_stdout": "Hello, Ann!\n"}],
         "hidden_tests": [{"name": "bob", "stdin": "Bob\n", "expected_stdout": "Hello, Bob!\n"}],
         "hints": ["Think about reading input.", "input() reads one line into a variable.", "print(___ + name)"]}
    r.update(kw)
    return r


PY_STEP1 = {"reference_solution": "name = input()\nif name == '':\n    print('Hello, stranger!')\nelse:\n"
                                  "    print('Hello, ' + name + '!')\n",
            "visible_tests": [{"name": "empty", "stdin": "\n", "expected_stdout": "Hello, stranger!\n"}],
            "hidden_tests": [{"name": "bob", "stdin": "Bob\n", "expected_stdout": "Hello, Bob!\n"}]}

C_STEP0 = {"example": {"code": '#include <stdio.h>\nint main(void)\n{\n    printf("hi\\n");\n    return 0;\n}\n',
                       "stdin": ""},
           "reference_solution": '#include <stdio.h>\n\nint main(void)\n{\n    int n;\n    if (scanf("%d", &n) != 1)\n'
                                 '        return 1;\n    printf("Double: %d\\n", n * 2);\n    return 0;\n}\n',
           "visible_tests": [{"name": "five", "stdin": "5\n", "expected_stdout": "Double: 10\n"}],
           "hidden_tests": [{"name": "nine", "stdin": "9\n", "expected_stdout": "Double: 18\n"}],
           "hints": ["You need to read a number.", "scanf reads a number into an int variable.", "scanf(\"%d\", &___);"]}

C_STEP1 = {"reference_solution": '#include <stdio.h>\n\nint main(void)\n{\n    int n;\n    if (scanf("%d", &n) != 1)\n'
                                 '        return 1;\n    printf("Double: %d\\n", n * 2);\n    if (n > 10)\n'
                                 '        printf("Big\\n");\n    else\n        printf("Small\\n");\n    return 0;\n}\n',
           "visible_tests": [{"name": "five", "stdin": "5\n", "expected_stdout": "Double: 10\nSmall\n"}],
           "hidden_tests": [{"name": "twenty", "stdin": "20\n", "expected_stdout": "Double: 40\nBig\n"}],
           "task": 'After the double, print "Big" when the number is over 10, else print "Small".',
           "example": {"code": '#include <stdio.h>\nint main(void)\n{\n    int n = 3;\n    if (n > 1)\n'
                               '        printf("yes\\n");\n    return 0;\n}\n', "stdin": ""}}


def project(script, language="python", plan_steps=4):
    """(svc, engine, pid, llm) with a ready project already planned on the script."""
    script.plan = script.plan or plan_reply(plan_steps)
    llm = FakeLLM(script)
    svc = make_svc(llm)
    eng = ProjectEngine(svc)
    pid = eng.create("a greeter that says hello to a name", language)
    assert eng.get(pid)["status"] == "ready"
    return svc, eng, pid, llm
