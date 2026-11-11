"""Shared setup for the fit tests: a threaded service over a temp store, a scripted FakeLLM, working programs."""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPT = HERE.parent / "adapt"
if str(ADAPT) not in sys.path:
    sys.path.insert(0, str(ADAPT))

from adapt_helpers import new_store  # noqa: E402

from codecoach.check.code_runner import sandbox_status  # noqa: E402
from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.fit.engine import engine_for  # noqa: E402
from codecoach.fit.tasks import WARMUPS  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

NEEDS_SANDBOX = "the real bwrap sandbox is needed"
IDS = [w.id for w in WARMUPS]

PY = {
    "sum2": "a, b = map(int, input().split())\nprint(a + b)\n",
    "count_vowels": "print(sum(c in 'aeiouAEIOU' for c in input()))\n",
    "fizzbuzz": ("n = int(input())\nfor i in range(1, n + 1):\n"
                 "    print('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else i)\n"),
    "reverse_words": "print(' '.join(input().split()[::-1]))\n",
    "max_of_list": "input()\nprint(max(map(int, input().split())))\n",
}

C = {
    "sum2": '#include <stdio.h>\nint main(void)\n{\n    long a, b;\n    if (scanf("%ld %ld", &a, &b) != 2)\n'
            '        return 1;\n    printf("%ld\\n", a + b);\n    return 0;\n}\n',
    "count_vowels": '#include <stdio.h>\n#include <string.h>\nint main(void)\n{\n    char s[1024];\n    int n = 0;\n'
                    '    if (!fgets(s, sizeof s, stdin))\n        return 1;\n'
                    '    for (char *p = s; *p; p++)\n        if (*p != \'\\n\' && strchr("aeiouAEIOU", *p))\n'
                    '            n++;\n    printf("%d\\n", n);\n    return 0;\n}\n',
    "fizzbuzz": '#include <stdio.h>\nint main(void)\n{\n    int n;\n    if (scanf("%d", &n) != 1)\n        return 1;\n'
                '    for (int i = 1; i <= n; i++) {\n        if (i % 15 == 0)\n            printf("FizzBuzz\\n");\n'
                '        else if (i % 3 == 0)\n            printf("Fizz\\n");\n        else if (i % 5 == 0)\n'
                '            printf("Buzz\\n");\n        else\n            printf("%d\\n", i);\n    }\n    return 0;\n}\n',
    "reverse_words": '#include <stdio.h>\n#include <string.h>\nint main(void)\n{\n    char s[1024];\n    char *w[512];\n'
                     '    int n = 0;\n    if (!fgets(s, sizeof s, stdin))\n        return 1;\n'
                     '    for (char *t = strtok(s, " \\n"); t && n < 512; t = strtok(NULL, " \\n"))\n        w[n++] = t;\n'
                     '    for (int i = n - 1; i >= 0; i--)\n        printf("%s%s", w[i], i ? " " : "\\n");\n'
                     '    return 0;\n}\n',
    "max_of_list": '#include <stdio.h>\nint main(void)\n{\n    int n, x, best;\n    if (scanf("%d", &n) != 1 || n < 1)\n'
                   '        return 1;\n    scanf("%d", &best);\n    for (int i = 1; i < n; i++) {\n'
                   '        scanf("%d", &x);\n        if (x > best)\n            best = x;\n    }\n'
                   '    printf("%d\\n", best);\n    return 0;\n}\n',
}

GOOD = {"python": PY, "c": C}


def task_id_of(text):
    """Which warm-up a prompt is for, found by its task text."""
    for w in WARMUPS:
        if w.text in text:
            return w.id
    raise AssertionError("prompt holds no known task text: " + text[:80])


def make_fit_svc(llm, background=True):
    return CodeCoachService(new_store(), llm, ServiceConfig(), background=background, auto_bank=False)


def scripted(language, overrides=None):
    """FakeLLM whose answer to each warm-up is the good program, except where `overrides` {task id: code | Exception}."""
    overrides = overrides or {}

    def fn(purpose, text):
        tid = task_id_of(text)
        out = overrides.get(tid, GOOD[language][tid])
        return out if isinstance(out, BaseException) else {"code": out}
    llm = FakeLLM(fn)
    llm.set_route("custom", "model-a")
    return llm


def wait_done(eng, timeout=120.0):
    end = time.time() + timeout
    while eng.is_running():
        assert time.time() < end, "the check did not finish"
        time.sleep(0.02)


def run_check(language, llm, **kw):
    svc = make_fit_svc(llm, **kw)
    eng = engine_for(svc)
    eng.start(language)
    wait_done(eng)
    return svc, eng, eng.view(language)


__all__ = ["sandbox_status"]
