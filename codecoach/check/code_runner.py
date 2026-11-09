"""Sandboxed runner for Python 3 and C exercises (stdlib only).

Sandbox = bubblewrap (`bwrap`): no network, no pid/ipc/uts view of the host, read-only system dirs, a
tmpfs /tmp, and a work dir. Compile runs with the work dir read-write; tests run with it read-only (a
learner program cannot rewrite the harness or binary the next test uses) and a scratch tmpfs as cwd.
Plain `unshare -rn` is not used: AppArmor blocks unprivileged user namespaces on the dev box, bwrap is
allowed.

Limits (preexec_fn rlimits + a wall clock): RLIMIT_CPU, RLIMIT_AS (bigger for compiling than running),
RLIMIT_NPROC (current process count of this user + headroom, because the kernel counts per real uid),
RLIMIT_FSIZE. The child runs in its own process group, and a timeout kills the group. stdout/stderr go to
files (never pipes), so output volume cannot stall or exhaust us; the files are read back truncated.

Public API:
    sandbox_status() -> "bwrap" | "unavailable"
    run_visible(payload, code, *, allow_unsandboxed=False) -> CodeResult    # visible tests only
    grade(payload, code, *, allow_unsandboxed=False) -> CodeResult          # visible + hidden
    check_code(item, response, *, mode="submit") -> CodeResult             # registry checker
    run_snippet(language, code, stdin="", ...)                               # reference-solution / ad-hoc use
"""

from __future__ import annotations

import collections
import functools
import math
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codecoach.core.models import CodePayload, CodeTest, PerTestResult
from codecoach.core.registry import CheckResult

UNAVAILABLE_DETAIL = "Install bubblewrap to run code exercises"

MAX_OUTPUT = 16 * 1024          # bytes of stdout/stderr kept per run
COMPILE_WALL_S = 30.0
COMPILE_CPU_S = 30
COMPILE_AS = 3 * 1024 ** 3
COMPILE_FSIZE = 64 * 1024 ** 2
RUN_FSIZE = 4 * 1024 ** 2       # also caps the stdout/stderr capture files
NPROC_HEADROOM = 96             # processes the sandboxed run may add on top of what the user already has
COMPILE_NPROC_HEADROOM = 160
ERROR_LINES = 20

C_FLAGS = ["-std=c11", "-Wall", "-Wextra"]
UBSAN_FLAGS = ["-fsanitize=undefined", "-fno-sanitize-recover=undefined"]

_SYSTEM_BINDS = ["/usr"]
_SYSTEM_LINKS = [("usr/bin", "/bin"), ("usr/sbin", "/sbin"), ("usr/lib", "/lib"), ("usr/lib64", "/lib64"),
                 ("usr/lib32", "/lib32")]
_ETC_BINDS = ["/etc/alternatives", "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
              "/etc/localtime"]


class CodeResult(CheckResult):
    """A CheckResult plus what POST /items/{id}/run needs (RunResponse fields)."""

    passed: int = 0
    total: int = 0
    compile_error: str | None = None
    timed_out: bool = False


# --------------------------------------------------------------------------- sandbox availability

_status_cache: str | None = None


def _bwrap_base() -> list[str]:
    """The fixed part of every bwrap command line (no work dir yet)."""
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
    for d in _SYSTEM_BINDS:
        argv += ["--ro-bind", d, d]
    for target, link in _SYSTEM_LINKS:
        if not os.path.lexists(link) or os.path.islink(link):
            if os.path.exists("/" + target.split("/", 1)[0] + "/" + target.split("/", 1)[1]):
                argv += ["--symlink", target, link]
    for p in _ETC_BINDS:
        if os.path.exists(p):
            argv += ["--ro-bind", p, p]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--clearenv"]
    return argv


def sandbox_status(*, refresh: bool = False) -> str:
    """"bwrap" when a bwrap sandbox actually starts here, else "unavailable"."""
    global _status_cache
    if _status_cache is not None and not refresh:
        return _status_cache
    status = "unavailable"
    exe = shutil.which("bwrap")
    if exe:
        try:
            r = subprocess.run(
                _bwrap_base() + ["--setenv", "PATH", "/usr/bin", "/usr/bin/true"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if r.returncode == 0:
                status = "bwrap"
        except (OSError, subprocess.SubprocessError):
            pass
    _status_cache = status
    return status


# --------------------------------------------------------------------------- low-level run

@dataclass
class RunOut:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    time_ms: int
    signal_name: str | None = None


def _user_process_count() -> int:
    """Tasks (threads count!) owned by this uid; RLIMIT_NPROC is checked against that number."""
    uid = os.getuid()
    n = 0
    try:
        with os.scandir("/proc") as it:
            for e in it:
                if e.name.isdigit():
                    try:
                        if e.stat().st_uid == uid:
                            n += len(os.listdir(f"/proc/{e.name}/task"))
                    except OSError:
                        pass
    except OSError:
        return 256
    return n


def _limits(cpu_s: int, as_bytes: int, nproc: int, fsize: int):
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def _read_capped(path: str) -> tuple[str, bool]:
    with open(path, "rb") as f:
        data = f.read(MAX_OUTPUT + 1)
    truncated = len(data) > MAX_OUTPUT
    text = data[:MAX_OUTPUT].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[output truncated]"
    return text, truncated


RECENT_PGIDS: "collections.deque[int]" = collections.deque(maxlen=256)   # process groups we started (tests)

_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONHASHSEED": "0"}


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _run(cmd: list[str], *, workdir: str, sandboxed: bool, rw_work: bool, stdin_text: str, wall_s: float,
         cpu_s: int, as_bytes: int, fsize: int, nproc_headroom: int) -> RunOut:
    """Run `cmd` (paths as seen inside: /work/... when sandboxed, real paths otherwise)."""
    scratch = tempfile.mkdtemp(prefix="codecoach-io-", dir=None)
    try:
        in_path = os.path.join(scratch, "stdin")
        out_path = os.path.join(scratch, "stdout")
        err_path = os.path.join(scratch, "stderr")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write(stdin_text)
        if sandboxed:
            argv = _bwrap_base()
            for k, v in _ENV.items():
                argv += ["--setenv", k, v]
            argv += ["--bind" if rw_work else "--ro-bind", workdir, "/work",
                     "--chdir", "/work" if rw_work else "/tmp"]
            argv += cmd
            cwd = None
        else:
            argv = cmd
            cwd = workdir
        nproc = _user_process_count() + nproc_headroom
        t0 = time.monotonic()
        with open(in_path, "rb") as fin, open(out_path, "wb") as fout, open(err_path, "wb") as ferr:
            proc = subprocess.Popen(
                argv, stdin=fin, stdout=fout, stderr=ferr, cwd=cwd, env=None if sandboxed else _plain_env(),
                process_group=0, close_fds=True,
                preexec_fn=_limits(cpu_s, as_bytes, nproc, fsize))
            timed_out = False
            try:
                proc.wait(timeout=wall_s)
            except subprocess.TimeoutExpired:
                timed_out = True
            RECENT_PGIDS.append(proc.pid)
            # Always sweep the group: a surviving grandchild (fork bomb, daemonized child) must not outlive us.
            _kill_group(proc)
            proc.wait()
        ms = int((time.monotonic() - t0) * 1000)
        out, t1 = _read_capped(out_path)
        err, t2 = _read_capped(err_path)
        rc = proc.returncode
        sig = None
        if rc is not None and rc < 0:
            sig = _signame(-rc)
        elif rc is not None and rc > 128 and sandboxed and rc - 128 in (9, 11, 6, 8, 24, 25, 4, 7):
            sig = _signame(rc - 128)       # bwrap reports a signalled child as 128 + signo
        if sig in ("SIGXCPU",):
            timed_out = True
        return RunOut(rc, out, err, timed_out, t1 or t2, ms, sig)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _plain_env() -> dict[str, str]:
    env = dict(_ENV)
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    return env


def _signame(n: int) -> str:
    try:
        return signal.Signals(n).name
    except ValueError:
        return f"signal {n}"


# --------------------------------------------------------------------------- helpers

def _strip_paths(text: str, *dirs: str) -> str:
    for d in dirs:
        if d:
            text = text.replace(d.rstrip("/") + "/", "")
    text = re.sub(r"(?<![\w.])/(?:work|tmp)/", "", text)
    return text


def _readable_error(text: str, *dirs: str) -> str:
    lines = _strip_paths(text, *dirs).replace("\r", "").strip().splitlines()
    out = lines[:ERROR_LINES]
    if len(lines) > ERROR_LINES:
        out.append(f"... ({len(lines) - ERROR_LINES} more lines)")
    return "\n".join(out)


def _normalize_stdout(s: str) -> str:
    lines = [ln.rstrip() for ln in s.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _clip(s: str, n: int = 2000) -> str:
    return s if len(s) <= n else s[:n] + "\n[truncated]"


@functools.lru_cache(maxsize=1)
def _ubsan_works() -> bool:
    """One-time probe: does -fsanitize=undefined compile and run inside this sandbox?"""
    if sandbox_status() != "bwrap":
        return False
    wd = tempfile.mkdtemp(prefix="codecoach-ub-")
    try:
        Path(wd, "t.c").write_text("int main(void){return 0;}\n")
        r = _run(["/usr/bin/gcc", *C_FLAGS, *UBSAN_FLAGS, "t.c", "-o", "t"], workdir=wd, sandboxed=True,
                 rw_work=True, stdin_text="", wall_s=COMPILE_WALL_S, cpu_s=COMPILE_CPU_S, as_bytes=COMPILE_AS,
                 fsize=COMPILE_FSIZE, nproc_headroom=COMPILE_NPROC_HEADROOM)
        if r.returncode != 0:
            return False
        r = _run(["/work/t"], workdir=wd, sandboxed=True, rw_work=False, stdin_text="", wall_s=10, cpu_s=10,
                 as_bytes=512 * 1024 ** 2, fsize=RUN_FSIZE, nproc_headroom=NPROC_HEADROOM)
        return r.returncode == 0
    finally:
        shutil.rmtree(wd, ignore_errors=True)


# --------------------------------------------------------------------------- one grading session

class _Session:
    """One temp dir, one learner submission, many tests."""

    def __init__(self, payload: CodePayload, code: str, sandboxed: bool):
        self.p = payload
        self.code = code
        self.sandboxed = sandboxed
        self.wd = tempfile.mkdtemp(prefix="codecoach-run-")
        os.chmod(self.wd, 0o755)
        self.root = "/work" if sandboxed else self.wd
        self.lang = payload.language
        self._bins: dict[str, str | None] = {}
        self._errors: dict[str, str] = {}
        self.compile_error: str | None = None

    def close(self) -> None:
        shutil.rmtree(self.wd, ignore_errors=True)

    # paths inside the sandbox
    def _in(self, name: str) -> str:
        return f"{self.root}/{name}"

    def _write(self, name: str, text: str) -> None:
        Path(self.wd, name).write_text(text, encoding="utf-8")

    def _py(self) -> str:
        return "/usr/bin/python3" if self.sandboxed else (shutil.which("python3") or sys.executable)

    def _gcc(self) -> str:
        return "/usr/bin/gcc" if self.sandboxed else (shutil.which("gcc") or "gcc")

    def _compile_run(self, cmd: list[str]) -> RunOut:
        return _run(cmd, workdir=self.wd, sandboxed=self.sandboxed, rw_work=True, stdin_text="",
                    wall_s=COMPILE_WALL_S, cpu_s=COMPILE_CPU_S, as_bytes=COMPILE_AS, fsize=COMPILE_FSIZE,
                    nproc_headroom=COMPILE_NPROC_HEADROOM)

    # -- preparing artifacts: returns the command to execute (or None + error)
    def prepare_main(self) -> bool:
        """Stdio form needs the learner's program. Python: syntax check. C: compile."""
        if self.lang == "python":
            self._write("solution.py", self.code)
            return self._syntax_check()
        self._write("solution.c", self.code)
        return self._compile_c("main", ["solution.c"]) is not None

    def _syntax_check(self) -> bool:
        if "py_ok" in self._bins:
            return self._bins["py_ok"] is not None
        self._write("solution.py", self.code)
        r = self._compile_run([self._py(), "-B", "-c",
                               "import sys;compile(open(sys.argv[1],encoding='utf-8').read(),sys.argv[1],'exec')",
                               self._in("solution.py")])
        if r.returncode != 0:
            self.compile_error = _readable_error(r.stderr or r.stdout, self.wd) or "Syntax error"
            self._bins["py_ok"] = None
            return False
        self._bins["py_ok"] = "ok"
        return True

    def _compile_c(self, key: str, sources: list[str]) -> str | None:
        if key in self._bins:
            return self._bins[key]
        flags = list(C_FLAGS)
        if _ubsan_works():
            flags += UBSAN_FLAGS
        out = f"prog_{key}"
        r = self._compile_run([self._gcc(), *flags, *[self._in(s) for s in sources], "-o", self._in(out), "-lm"])
        if r.returncode != 0:
            raw = r.stderr or r.stdout
            msg = _readable_error(raw, self.wd)
            if re.search(r"multiple definition of .?main", raw) and len(sources) > 1:
                msg = ("Remove your main(); the tests provide one. Keep only the functions the exercise "
                       "asks for.\n" + msg)
            if r.timed_out:
                msg = "Compilation took too long.\n" + msg
            self._errors[key] = msg or "Compilation failed"
            if self.compile_error is None:
                self.compile_error = self._errors[key]
            self._bins[key] = None
        else:
            self._bins[key] = self._in(out)
        return self._bins[key]

    # -- running tests
    def run_test(self, t: CodeTest, idx: int, visible: bool) -> PerTestResult:
        p = self.p
        wall = max(0.2, float(p.time_limit_s))
        cpu = int(math.ceil(wall)) + 1
        mem = max(32, int(p.mem_mb)) * 1024 ** 2
        if t.harness is not None:
            cmd = self._harness_cmd(t, idx)
            if cmd is None:
                return PerTestResult(name=t.name, passed=False, visible=visible,
                                     detail=self._test_compile_message(f"h{idx}", visible))
            stdin = ""
        else:
            if self.lang == "python":
                if not self._syntax_check():
                    return PerTestResult(name=t.name, passed=False, visible=visible,
                                         detail=("Syntax error:\n" + (self.compile_error or "")) if visible
                                         else "Syntax error")
                self._write("solution.py", self.code)
                cmd = [self._py(), "-B", self._in("solution.py")]
            else:
                bin_ = self._compile_c("main", ["solution.c"]) if "solution.c" in os.listdir(self.wd) else None
                if "solution.c" not in os.listdir(self.wd):
                    self._write("solution.c", self.code)
                    bin_ = self._compile_c("main", ["solution.c"])
                if bin_ is None:
                    return PerTestResult(name=t.name, passed=False, visible=visible,
                                         detail=self._test_compile_message("main", visible))
                cmd = [bin_]
            stdin = t.stdin
        r = _run(cmd, workdir=self.wd, sandboxed=self.sandboxed, rw_work=False, stdin_text=stdin, wall_s=wall,
                 cpu_s=cpu, as_bytes=mem, fsize=RUN_FSIZE, nproc_headroom=NPROC_HEADROOM)
        return self._judge(t, r, visible)

    def _test_compile_message(self, key: str, visible: bool) -> str:
        msg = self._errors.get(key) or self.compile_error or "Compilation failed"
        return ("Compile error:\n" + msg) if visible else "Compile error"

    def _harness_cmd(self, t: CodeTest, idx: int) -> list[str] | None:
        assert t.harness is not None
        if self.lang == "python":
            if not self._syntax_check():
                self._errors[f"h{idx}"] = self.compile_error or ""
                return None
            self._write("solution.py", self.code)
            self._write(f"harness_{idx}.py", t.harness)
            return [self._py(), "-B", self._in(f"harness_{idx}.py")]
        self._write("solution.c", self.code)
        self._write(f"harness_{idx}.c", t.harness)
        bin_ = self._compile_c(f"h{idx}", ["solution.c", f"harness_{idx}.c"])
        return None if bin_ is None else [bin_]

    def _judge(self, t: CodeTest, r: RunOut, visible: bool) -> PerTestResult:
        out = r.stdout
        err = _readable_error(r.stderr, self.wd)
        def mk(**kw: Any) -> PerTestResult:
            return PerTestResult(name=t.name, visible=visible, time_ms=r.time_ms, **kw)

        if r.timed_out:
            return mk(passed=False, detail=f"Time limit exceeded ({self.p.time_limit_s:g} s)",
                      stdout=_clip(out) if visible else None)
        if r.signal_name == "SIGXFSZ":
            return mk(passed=False, detail="Output limit exceeded")
        if r.returncode != 0:
            reason = f"Program stopped: {r.signal_name}" if r.signal_name else f"Program exited with status {r.returncode}"
            if r.signal_name == "SIGSEGV":
                reason += " (invalid memory access)"
            elif r.signal_name == "SIGABRT" and "runtime error" in err:
                reason = "Undefined behavior detected"
            elif r.signal_name == "SIGKILL" or (r.returncode in (-9, 137)):
                reason += " (out of memory or killed)"
            detail = reason + (("\n" + _clip(err, 1500)) if visible and err else "")
            return mk(passed=False, detail=detail,
                      stdout=_clip(out) if visible and t.expected_stdout is not None else None,
                      expected=t.expected_stdout if visible else None)
        if t.harness is not None:
            return mk(passed=True, detail="")
        assert t.expected_stdout is not None
        got = _normalize_stdout(out)
        want = _normalize_stdout(t.expected_stdout)
        if r.truncated:
            return mk(passed=False, detail="Output too long", stdout=_clip(out) if visible else None)
        ok = got == want
        return mk(passed=ok, detail="" if ok else ("Output differs" if visible else "Wrong output"),
                  stdout=_clip(out) if visible else None, expected=t.expected_stdout if visible else None)


# --------------------------------------------------------------------------- public API

def _undecided(total: int = 0) -> CodeResult:
    return CodeResult(ok=False, score=0.0, detail=UNAVAILABLE_DETAIL, undecided=True, per_test=None,
                      passed=0, total=total)


def _execute(payload: CodePayload, code: str, include_hidden: bool, allow_unsandboxed: bool) -> CodeResult:
    tests: list[tuple[CodeTest, bool]] = [(t, True) for t in payload.visible_tests]
    if include_hidden:
        tests += [(t, False) for t in payload.hidden_tests]
    total = len(tests)
    sandboxed = sandbox_status() == "bwrap"
    if not sandboxed and not allow_unsandboxed:
        return _undecided(total)
    if not isinstance(code, str) or not code.strip():
        return CodeResult(ok=False, score=0.0, detail="No code to run.", passed=0, total=total,
                          per_test=[PerTestResult(name=t.name, passed=False, visible=v, detail="No code submitted")
                                    for t, v in tests])
    sess = _Session(payload, code, sandboxed)
    try:
        # Learner code is compiled up front when any stdio test exists (a compile error then stops early).
        if tests and all(t.harness is None for t, _ in tests):
            if not sess.prepare_main():
                return _compile_failed(sess, tests, total)
        results: list[PerTestResult] = []
        for i, (t, vis) in enumerate(tests):
            results.append(sess.run_test(t, i, vis))
        if sess.compile_error and not any(r.passed for r in results) and all(
                r.detail.startswith(("Compile error", "Syntax error")) for r in results):
            return _compile_failed(sess, tests, total)
        passed = sum(r.passed for r in results)
        timed = any(r.detail.startswith("Time limit") for r in results)
        detail = f"{passed}/{total} tests passed"
        if timed:
            detail += " (time limit exceeded on at least one test)"
        return CodeResult(ok=passed == total and total > 0, score=(passed / total) if total else 0.0,
                          detail=detail, per_test=results, passed=passed, total=total, timed_out=timed)
    finally:
        sess.close()


def _compile_failed(sess: _Session, tests: list[tuple[CodeTest, bool]], total: int) -> CodeResult:
    msg = sess.compile_error or "Compilation failed"
    label = "Syntax error" if sess.lang == "python" else "Compile error"
    per = [PerTestResult(name=t.name, passed=False, visible=v, detail=label) for t, v in tests]
    return CodeResult(ok=False, score=0.0, detail=f"{label}:\n{msg}", per_test=per, passed=0, total=total,
                      compile_error=msg)


def run_visible(payload: CodePayload, code: str, *, allow_unsandboxed: bool = False) -> CodeResult:
    """Live run: visible tests only. Hidden tests are never read or run."""
    return _execute(payload, code, False, allow_unsandboxed)


def grade(payload: CodePayload, code: str, *, allow_unsandboxed: bool = False) -> CodeResult:
    """Submit: visible + hidden tests. Hidden results carry no stdout/expected."""
    return _execute(payload, code, True, allow_unsandboxed)


def check_reference(payload: CodePayload, *, allow_unsandboxed: bool = False) -> CodeResult:
    """The weak_tests gate: the reference solution must pass every visible and hidden test."""
    return grade(payload, payload.reference_solution, allow_unsandboxed=allow_unsandboxed)


def check_code(item: Any, response: Any, *, mode: str = "submit") -> CodeResult:
    """Registered checker for kind "code". response = source string."""
    payload = getattr(item, "payload", None)
    if not isinstance(payload, CodePayload):
        return CodeResult(ok=False, score=0.0, detail="Not a code item.")
    code = response if isinstance(response, str) else ""
    if mode == "visible":
        return run_visible(payload, code)
    return grade(payload, code)


@dataclass
class SnippetResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    compile_error: str | None = None
    time_ms: int = 0


def run_snippet(language: str, code: str, stdin: str = "", *, time_limit_s: float = 5.0, mem_mb: int = 256,
                allow_unsandboxed: bool = False) -> SnippetResult | None:
    """Run one program with stdin and return raw output. None when no sandbox is available."""
    sandboxed = sandbox_status() == "bwrap"
    if not sandboxed and not allow_unsandboxed:
        return None
    payload = CodePayload(language=language, starter="", visible_tests=[CodeTest(name="x", stdin=stdin,
                          expected_stdout="")], reference_solution="-", time_limit_s=time_limit_s, mem_mb=mem_mb)
    sess = _Session(payload, code, sandboxed)
    try:
        if not sess.prepare_main():
            return SnippetResult(False, "", "", None, False, sess.compile_error)
        if language == "python":
            cmd = [sess._py(), "-B", sess._in("solution.py")]
        else:
            cmd = [sess._bins["main"]]  # type: ignore[list-item]
        r = _run(cmd, workdir=sess.wd, sandboxed=sandboxed, rw_work=False, stdin_text=stdin,
                 wall_s=time_limit_s, cpu_s=int(math.ceil(time_limit_s)) + 1, as_bytes=mem_mb * 1024 ** 2,
                 fsize=RUN_FSIZE, nproc_headroom=NPROC_HEADROOM)
        return SnippetResult(r.returncode == 0 and not r.timed_out, r.stdout, r.stderr, r.returncode,
                             r.timed_out, None, r.time_ms)
    finally:
        sess.close()
