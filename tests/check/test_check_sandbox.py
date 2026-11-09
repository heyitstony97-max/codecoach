"""Sandbox acceptance tests for codecoach.check.code_runner (real bwrap, real gcc, no mocks)."""

import json
import os
import time
from pathlib import Path

import pytest

from codecoach.check import code_runner as cr
from codecoach.core.models import CodePayload, CodeTest
from codecoach.core.registry import check
from codecoach.core.samples import sample_item

pytestmark = pytest.mark.skipif(cr.sandbox_status() != "bwrap", reason="bwrap not available")

TABLE: dict[str, dict] = {}


def _record(name: str, result: str, t0: float, **extra) -> None:
    TABLE[name] = {"result": result, "seconds": round(time.monotonic() - t0, 2), **extra}
    out = os.environ.get("CODECOACH_SANDBOX_TABLE")
    if out:
        Path(out).write_text(json.dumps(TABLE, indent=1))


def _procs_matching(*needles: str) -> list[str]:
    hits = []
    me = os.getpid()
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            cmd = Path(f"/proc/{d}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(n in cmd for n in needles):
            hits.append(f"{d}: {cmd[:120]}")
    return hits


def _children() -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
        except OSError:
            continue
        rest = stat.rsplit(")", 1)[1].split()
        if int(rest[1]) == os.getpid() and rest[0] != "Z":
            out.append(int(d))
    return out


def _wait_gone(needles, secs=3.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if not _procs_matching(*needles):
            return []
        time.sleep(0.05)
    return _procs_matching(*needles)


NEEDLES = ["codecoach-run-", "/work/solution.", "/work/prog_", "/work/harness_"]


def _leftover_groups() -> list[int]:
    """Process groups we started that still have live members (zombies excluded)."""
    mine = set(cr.RECENT_PGIDS)
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            rest = Path(f"/proc/{d}/stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if rest[0] != "Z" and int(rest[2]) in mine:
            out.append(int(d))
    return out


# --------------------------------------------------------------------------- probes

LOOP = {"python": "while True:\n    pass\n",
        "c": "int main(void){ for(;;){} return 0; }\n"}


@pytest.mark.parametrize("lang", ["python", "c"])
def test_infinite_loop_killed_within_timeout(lang):
    t0 = time.monotonic()
    r = cr.run_snippet(lang, LOOP[lang], time_limit_s=1.0)
    elapsed = time.monotonic() - t0
    assert r is not None and r.timed_out and not r.ok
    assert elapsed < 1.0 + 4.0          # wall limit + (compile for C) + slack
    assert _wait_gone(NEEDLES) == []
    assert _leftover_groups() == []
    _record(f"infinite_loop_{lang}", "killed (time limit exceeded), process group gone", t0)


NET = {
    "python": ("import socket\ns=socket.socket()\ns.settimeout(2)\n"
               "try:\n    s.connect(('93.184.216.34',80))\n    print('CONNECTED')\n"
               "except OSError as e:\n    print('BLOCKED', e.errno)\n"),
    "c": ("#include <stdio.h>\n#include <errno.h>\n#include <sys/socket.h>\n#include <netinet/in.h>\n"
          "#include <arpa/inet.h>\n#include <unistd.h>\n"
          "int main(void){int s=socket(AF_INET,SOCK_STREAM,0);struct sockaddr_in a={0};a.sin_family=AF_INET;"
          "a.sin_port=htons(80);inet_pton(AF_INET,\"93.184.216.34\",&a.sin_addr);"
          "if(connect(s,(struct sockaddr*)&a,sizeof a)==0){puts(\"CONNECTED\");}else{printf(\"BLOCKED %d\\n\",errno);}"
          "return 0;}\n"),
}


@pytest.mark.parametrize("lang", ["python", "c"])
def test_network_connect_fails(lang):
    t0 = time.monotonic()
    r = cr.run_snippet(lang, NET[lang], time_limit_s=5.0)
    assert r is not None and r.compile_error is None, r
    assert "BLOCKED" in r.stdout and "CONNECTED" not in r.stdout, r.stdout + r.stderr
    _record(f"network_connect_{lang}", "connect failed: " + r.stdout.strip(), t0)


def _write_probe(lang: str, target: str, tmp_probe: str) -> str:
    if lang == "python":
        return (f"import os\nres=[]\n"
                f"for p in ({target!r}, '/usr/codecoach_pwn', '/work/codecoach_pwn', {tmp_probe!r}):\n"
                f"    try:\n        open(p,'w').write('x'); res.append(p+':WROTE')\n"
                f"    except OSError as e:\n        res.append(p+':ERR%d' % e.errno)\n"
                f"print('\\n'.join(res))\n")
    return ("#include <stdio.h>\n#include <errno.h>\nint main(void){const char*ps[]={\"%s\",\"/usr/codecoach_pwn\","
            "\"/work/codecoach_pwn\",\"%s\"};for(int i=0;i<4;i++){FILE*f=fopen(ps[i],\"w\");"
            "if(f){fputs(\"x\",f);fclose(f);printf(\"%%s:WROTE\\n\",ps[i]);}else printf(\"%%s:ERR%%d\\n\",ps[i],errno);}"
            "return 0;}\n") % (target, tmp_probe)


@pytest.mark.parametrize("lang", ["python", "c"])
def test_write_outside_workdir_fails_and_nothing_persists(lang):
    t0 = time.monotonic()
    home_target = os.path.join(os.path.expanduser("~"), "codecoach_sandbox_pwn.txt")
    tmp_probe = "/tmp/codecoach_sandbox_tmp_leak.txt"
    for p in (home_target, tmp_probe, "/usr/codecoach_pwn"):
        assert not os.path.exists(p)
    r = cr.run_snippet(lang, _write_probe(lang, home_target, tmp_probe), time_limit_s=5.0)
    assert r is not None and r.compile_error is None, r
    lines = dict(line.rsplit(":", 1) for line in r.stdout.strip().splitlines())
    assert lines[home_target].startswith("ERR"), r.stdout
    assert lines["/usr/codecoach_pwn"].startswith("ERR")
    assert lines["/work/codecoach_pwn"].startswith("ERR")          # the work dir is read-only while tests run
    # /tmp inside the sandbox is a private tmpfs: writing succeeds there but never reaches the host
    assert not os.path.exists(home_target) and not os.path.exists(tmp_probe)
    assert not os.path.exists("/usr/codecoach_pwn")
    assert _procs_matching(*NEEDLES) == []
    _record(f"write_outside_{lang}", "HOME/usr/work writes failed; /tmp write stayed in private tmpfs; "
            "nothing on host after run", t0)


BOMB = {
    "python": "import os,time\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        time.sleep(0.01)\n",
    "c": "#include <unistd.h>\nint main(void){for(;;){fork();}}\n",
}


@pytest.mark.parametrize("lang", ["python", "c"])
def test_fork_bomb_is_contained(lang):
    t0 = time.monotonic()
    r = cr.run_snippet(lang, BOMB[lang], time_limit_s=2.0)
    elapsed = time.monotonic() - t0
    assert r is not None and (r.timed_out or not r.ok)
    assert elapsed < 12
    assert _wait_gone(NEEDLES, 5) == []
    assert _leftover_groups() == []
    # the machine still works afterwards
    ok = cr.run_snippet("python", "print('alive')\n")
    assert ok is not None and ok.stdout.strip() == "alive"
    _record(f"fork_bomb_{lang}", "contained by RLIMIT_NPROC + group kill; host process count restored", t0)


HUGE = {"python": "import sys\nwhile True:\n    sys.stdout.write('x'*65536)\n",
        "c": "#include <stdio.h>\nint main(void){for(;;){fputs(\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\",stdout);}}\n"}


@pytest.mark.parametrize("lang", ["python", "c"])
def test_huge_output_truncated(lang):
    t0 = time.monotonic()
    r = cr.run_snippet(lang, HUGE[lang], time_limit_s=2.0)
    assert r is not None
    assert len(r.stdout) <= cr.MAX_OUTPUT + 64
    assert r.stdout.endswith("[output truncated]")
    assert not r.ok
    _record(f"huge_output_{lang}", f"stdout cut to {len(r.stdout)} chars", t0)


def test_huge_output_fails_the_test_not_the_machine():
    p = CodePayload(language="python", visible_tests=[CodeTest(name="a", expected_stdout="x\n")],
                    reference_solution="print('x')", time_limit_s=2.0)
    res = cr.run_visible(p, HUGE["python"])
    assert not res.ok and not res.per_test[0].passed
    assert len(res.per_test[0].stdout or "") < 3000


def test_compile_error_is_readable():
    t0 = time.monotonic()
    p = CodePayload(language="c", visible_tests=[CodeTest(name="a", stdin="", expected_stdout="1\n")],
                    reference_solution="int main(void){return 0;}")
    bad = "#include <stdio.h>\nint main(void) {\n    int x = ;\n    printf(\"%d\\n\", y);\n    return 0;\n}\n"
    res = cr.run_visible(p, bad)
    assert not res.ok and res.compile_error
    lines = res.compile_error.splitlines()
    assert len(lines) <= 21
    assert "codecoach-run" not in res.compile_error and "/work/" not in res.compile_error
    assert "solution.c:3" in res.compile_error and "error" in res.compile_error
    assert res.passed == 0 and res.total == 1
    _record("compile_error_c", "readable: " + lines[0][:60], t0)


def test_python_syntax_error_is_readable():
    p = CodePayload(language="python", visible_tests=[CodeTest(name="a", expected_stdout="1\n")],
                    reference_solution="print(1)")
    res = cr.run_visible(p, "def f(:\n    pass\n")
    assert not res.ok and res.compile_error and "SyntaxError" in res.compile_error
    assert "codecoach-run" not in res.compile_error and "/work" not in res.compile_error


def test_c_harness_with_learner_main_gives_clear_message():
    p = CodePayload(language="c", visible_tests=[CodeTest(name="a", harness=(
        "int digit_sum(int);\nint main(void){return digit_sum(123)==6?0:1;}\n"))],
        reference_solution="int digit_sum(int n){int s=0;while(n){s+=n%10;n/=10;}return s;}\n")
    res = cr.run_visible(p, "int digit_sum(int n){return 0;}\nint main(void){return 0;}\n")
    assert not res.ok and res.compile_error and "Remove your main()" in res.compile_error


# --------------------------------------------------------------------------- grading

PY_PAYLOAD = CodePayload(
    language="python", starter="def digit_sum(n):\n    pass\n",
    visible_tests=[CodeTest(name="small", harness="from solution import *\nassert digit_sum(123) == 6\n")],
    hidden_tests=[CodeTest(name="zero", harness="from solution import *\nassert digit_sum(0) == 0\n"),
                  CodeTest(name="big", harness="from solution import *\nassert digit_sum(99999) == 45\n")],
    reference_solution="def digit_sum(n):\n    return sum(int(c) for c in str(n))\n", time_limit_s=5.0, mem_mb=256)

C_PAYLOAD = CodePayload(
    language="c",
    visible_tests=[CodeTest(name="small", stdin="123\n", expected_stdout="6\n")],
    hidden_tests=[CodeTest(name="zero", stdin="0\n", expected_stdout="0\n"),
                  CodeTest(name="big", stdin="99999\n", expected_stdout="45\n")],
    reference_solution=("#include <stdio.h>\nint main(void){int n,s=0;scanf(\"%d\",&n);"
                        "while(n){s+=n%10;n/=10;}printf(\"%d\\n\",s);return 0;}\n"))

C_HARNESS = CodePayload(
    language="c",
    visible_tests=[CodeTest(name="small", harness="int digit_sum(int);\nint main(void){return digit_sum(123)==6?0:1;}\n")],
    hidden_tests=[CodeTest(name="big", harness="int digit_sum(int);\nint main(void){return digit_sum(99999)==45?0:1;}\n")],
    reference_solution="int digit_sum(int n){int s=0;while(n){s+=n%10;n/=10;}return s;}\n")


def test_python_correct_and_incorrect():
    t0 = time.monotonic()
    good = cr.grade(PY_PAYLOAD, PY_PAYLOAD.reference_solution)
    assert good.ok and good.passed == good.total == 3 and good.score == 1.0
    bad = cr.grade(PY_PAYLOAD, "def digit_sum(n):\n    return n % 10\n")
    assert not bad.ok and bad.passed == 1 and bad.total == 3        # only digit_sum(0) coincides
    assert bad.per_test[0].visible and not bad.per_test[1].visible
    _record("grade_python", f"correct {good.passed}/{good.total}, incorrect {bad.passed}/{bad.total}", t0)


def test_c_stdio_correct_and_incorrect():
    t0 = time.monotonic()
    good = cr.grade(C_PAYLOAD, C_PAYLOAD.reference_solution)
    assert good.ok and good.passed == good.total == 3, good
    wrong = ("#include <stdio.h>\nint main(void){int n;scanf(\"%d\",&n);printf(\"%d\\n\",n%10);return 0;}\n")
    bad = cr.grade(C_PAYLOAD, wrong)
    assert not bad.ok and bad.passed == 1 and bad.total == 3
    _record("grade_c_stdio", f"correct {good.passed}/{good.total}, incorrect {bad.passed}/{bad.total}", t0)


def test_c_harness_correct_and_incorrect():
    good = cr.grade(C_HARNESS, C_HARNESS.reference_solution)
    assert good.ok and good.total == 2
    bad = cr.grade(C_HARNESS, "int digit_sum(int n){return 0;}\n")
    assert not bad.ok and bad.passed == 0


def test_reference_solutions_pass_their_own_tests():
    for p in (PY_PAYLOAD, C_PAYLOAD, C_HARNESS):
        assert cr.check_reference(p).ok


def test_run_visible_never_runs_hidden_and_hides_nothing_extra():
    res = cr.run_visible(PY_PAYLOAD, PY_PAYLOAD.reference_solution)
    assert res.total == 1 and len(res.per_test) == 1 and res.ok
    sub = cr.grade(C_PAYLOAD, "#include <stdio.h>\nint main(void){puts(\"7\");return 0;}\n")
    hidden = [t for t in sub.per_test if not t.visible]
    assert hidden and all(t.stdout is None and t.expected is None for t in hidden)


def test_runtime_error_and_ub_reported_not_crashing():
    res = cr.run_visible(C_PAYLOAD, "int main(void){int*p=0;return *p;}\n")
    d = res.per_test[0].detail
    assert not res.ok and ("SIGSEGV" in d or "null pointer" in d)
    res = cr.run_visible(C_PAYLOAD, "#include <limits.h>\n#include <stdio.h>\n"
                         "int main(int c,char**v){int x=INT_MAX;x+=c;printf(\"%d\\n\",x);return 0;}\n")
    assert not res.ok            # UBSan (when active) aborts; otherwise output is simply wrong


def test_registered_checker_dispatch_and_modes():
    from codecoach.check import register_all
    register_all()
    item = sample_item("code", payload=PY_PAYLOAD)
    assert check(item, PY_PAYLOAD.reference_solution, mode="submit").ok
    vis = check(item, "def digit_sum(n):\n    return 6\n", mode="visible")
    assert vis.ok                       # passes the one visible test...
    assert not check(item, "def digit_sum(n):\n    return 6\n", mode="submit").ok      # ...but not the hidden ones
    assert not check(item, 42).ok                                                       # non-string response


def test_memory_limit_enforced():
    p = CodePayload(language="python", visible_tests=[CodeTest(name="a", expected_stdout="ok\n")],
                    reference_solution="print('ok')", mem_mb=64, time_limit_s=3.0)
    res = cr.run_visible(p, "x = bytearray(600 * 1024 * 1024)\nprint('ok')\n")
    assert not res.ok


# --------------------------------------------------------------------------- availability

def test_unavailable_without_bwrap_is_undecided(monkeypatch):
    monkeypatch.setattr(cr, "sandbox_status", lambda **k: "unavailable")
    res = cr.grade(PY_PAYLOAD, PY_PAYLOAD.reference_solution)
    assert res.undecided and res.detail == "Install bubblewrap to run code exercises" and not res.ok
    assert cr.run_visible(C_PAYLOAD, "x").undecided


def test_allow_unsandboxed_runs_with_limits_only(monkeypatch):
    monkeypatch.setattr(cr, "sandbox_status", lambda **k: "unavailable")
    res = cr.grade(PY_PAYLOAD, PY_PAYLOAD.reference_solution, allow_unsandboxed=True)
    assert res.ok and not res.undecided
    t0 = time.monotonic()
    res = cr.run_visible(PY_PAYLOAD.model_copy(update={"time_limit_s": 1.0}), "while True: pass\n",
                         allow_unsandboxed=True)
    assert not res.ok and time.monotonic() - t0 < 6
    assert _wait_gone(NEEDLES) == []


def test_sandbox_status_value():
    assert cr.sandbox_status() == "bwrap"


# --------------------------------------------------------------------------- last: nothing left behind

def test_zz_no_stray_processes_or_dirs():
    assert _wait_gone(NEEDLES + ["_sympy_worker"], 5) == []
    assert _leftover_groups() == []
    assert _children() == [], _children()
    leftovers = [p for p in os.listdir(os.environ.get("TMPDIR") or "/tmp") if p.startswith(("codecoach-run-", "codecoach-io-", "codecoach-ub-"))]
    assert leftovers == []
