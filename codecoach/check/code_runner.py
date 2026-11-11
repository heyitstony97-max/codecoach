"""Sandboxed runner for code exercises in every language of `codecoach.lang` (stdlib only).

Sandbox = bubblewrap (`bwrap`): no network, no pid/ipc/uts view of the host, read-only system dirs, a
tmpfs /tmp, and a work dir. Compile runs with the work dir read-write; tests run with it read-only (a
learner program cannot rewrite the harness or binary the next test uses) and a scratch tmpfs as cwd.
Plain `unshare -rn` is not used: AppArmor blocks unprivileged user namespaces on the dev box, bwrap is
allowed.

Limits (preexec_fn rlimits + a wall clock): RLIMIT_CPU, RLIMIT_AS (bigger for compiling than running),
RLIMIT_FSIZE, and a process limit (RLIMIT_NPROC). The process limit is set INSIDE the sandbox where the kernel
counts it per user namespace (Linux 5.14 and later; see `nproc_inside`), so it bounds the sandbox's own tasks and
nothing else on the machine can use it up. Where that does not hold it is set from outside as the user's current
task count plus headroom, as in v1.0. The child runs in its own process group, and a timeout kills the group.
stdout/stderr go to files (never pipes), so output volume cannot stall or exhaust us; the files are read back
truncated.

File names, compile and run commands, extra read-only binds and per-language limits come from the language's
`LangSpec` and discovered toolchain (`codecoach/lang/`). Python and C keep exactly their v1.0 commands and limits.

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

from codecoach import lang
from codecoach.core.models import CodePayload, CodeTest, PerTestResult
from codecoach.core.registry import CheckResult

UNAVAILABLE_DETAIL = "Install bubblewrap to run code exercises"

MAX_OUTPUT = 16 * 1024          # bytes of stdout/stderr kept per run
COMPILE_WALL_S = 30.0
COMPILE_CPU_S = 30
COMPILE_AS = 3 * 1024 ** 3
COMPILE_FSIZE = 64 * 1024 ** 2
RUN_FSIZE = 4 * 1024 ** 2       # also caps the stdout/stderr capture files
NPROC_HEADROOM = 96             # processes and threads one sandboxed run may have (see nproc_inside)
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


def _bwrap_base(extra_ro: tuple[str, ...] = ()) -> list[str]:
    """The fixed part of every bwrap command line (no work dir yet). `extra_ro`: a toolchain's own root."""
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
    for d in _SYSTEM_BINDS:
        argv += ["--ro-bind", d, d]
    for d in extra_ro:
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


def _limits(cpu_s: int, as_bytes: int, nproc: int | None, fsize: int):
    """`nproc` None: the process limit is set inside the sandbox instead (see nproc_inside)."""
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        if nproc is not None:
            resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


# --------------------------------------------------------------------------- where the process limit is set

PRLIMIT = "/usr/bin/prlimit"            # util-linux. /usr inside the sandbox is the host's /usr, read-only
_SELFTEST_PY = "/usr/bin/python3"
_SELFTEST_CAP = 24
# Forks up to 60 children that wait on a pipe (they end when this program does) and prints how many forks worked.
_SELFTEST = ("import os\nr, w = os.pipe()\nn = 0\nfor _ in range(60):\n    try:\n        pid = os.fork()\n"
             "    except OSError:\n        break\n    if pid == 0:\n        os.close(w)\n        os.read(r, 1)\n"
             "        os._exit(0)\n    n += 1\nprint(n)\n")
_nproc_inside_cache: bool | None = None


def _nproc_selftest() -> int | None:
    """How many forks a program managed inside the sandbox under a process limit of _SELFTEST_CAP set inside it.
    None when the test could not run (no sandbox, no prlimit, no python3 under /usr, or it did not finish)."""
    if sandbox_status() != "bwrap" or not (os.path.isfile(PRLIMIT) and os.path.isfile(_SELFTEST_PY)):
        return None
    argv = _bwrap_base() + ["--setenv", "PATH", "/usr/bin:/bin", PRLIMIT, f"--nproc={_SELFTEST_CAP}:{_SELFTEST_CAP}",
                            "--", _SELFTEST_PY, "-I", "-S", "-c", _SELFTEST]
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                process_group=0, close_fds=True, preexec_fn=_limits(5, 512 * 1024 ** 2, None, 1024 ** 2))
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        out = b""
    finally:
        _kill_group(proc)
        proc.wait()
    try:
        return int(out.decode("ascii", "replace").strip())
    except ValueError:
        return None


def nproc_inside(*, refresh: bool = False) -> bool:
    """True when the process limit can be set inside the sandbox and then counts only the sandbox's own tasks.

    Linux 5.14 and later count RLIMIT_NPROC per user namespace, so a limit set after bwrap has made its namespace
    bounds that sandbox alone. Set from outside (the only way on older kernels) it is checked against every task
    the user has: a busy desktop, or a few sandboxes side by side, can then make a correct program fail to start
    ("Resource temporarily unavailable"). Decided once per process by a test in the real sandbox: under a limit of
    24 a program must manage at least one fork and must be stopped before 24. Any other answer (an old kernel
    refuses the first fork, root is never stopped, the test cannot run) keeps the outside limit."""
    global _nproc_inside_cache
    if _nproc_inside_cache is None or refresh:
        n = _nproc_selftest()
        _nproc_inside_cache = n is not None and 1 <= n < _SELFTEST_CAP
    return _nproc_inside_cache


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
         cpu_s: int, as_bytes: int, fsize: int, nproc_headroom: int, extra_ro: tuple[str, ...] = (),
         rw_cache: str | None = None, env_extra: dict[str, str] | None = None) -> RunOut:
    """Run `cmd` (paths as seen inside: /work/... when sandboxed, real paths otherwise).

    `extra_ro`: read-only binds (a toolchain's own root); `rw_cache`: the ONE extra read-write dir, mounted at
    /cache (compile steps only); `env_extra`: environment on top of the fixed one."""
    assert as_bytes <= lang.AS_CEILING_MB * 1024 ** 2, "RLIMIT_AS is never above 4 GiB"
    scratch = tempfile.mkdtemp(prefix="codecoach-io-", dir=None)
    try:
        in_path = os.path.join(scratch, "stdin")
        out_path = os.path.join(scratch, "stdout")
        err_path = os.path.join(scratch, "stderr")
        with open(in_path, "w", encoding="utf-8") as f:
            f.write(stdin_text)
        inside = sandboxed and nproc_inside()
        if sandboxed:
            argv = _bwrap_base(extra_ro)
            for k, v in {**_ENV, **(env_extra or {})}.items():
                argv += ["--setenv", k, v]
            if rw_cache:
                argv += ["--bind", rw_cache, "/cache"]
            argv += ["--bind" if rw_work else "--ro-bind", workdir, "/work",
                     "--chdir", "/work" if rw_work else "/tmp"]
            if inside:          # the first thing in the sandbox caps its own task count, then becomes the command
                argv += [PRLIMIT, f"--nproc={nproc_headroom}:{nproc_headroom}", "--"]
            argv += cmd
            cwd = None
        else:
            argv = cmd
            cwd = workdir
        nproc = None if inside else _user_process_count() + nproc_headroom
        t0 = time.monotonic()
        with open(in_path, "rb") as fin, open(out_path, "wb") as fout, open(err_path, "wb") as ferr:
            proc = subprocess.Popen(
                argv, stdin=fin, stdout=fout, stderr=ferr, cwd=cwd, env=None if sandboxed else {**_plain_env(), **(env_extra or {})},
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

def _env_pairs(pairs: tuple[tuple[str, str], ...], **vals: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in pairs:
        for name, val in vals.items():
            v = v.replace("{" + name + "}", val)
        out[k] = v
    return out


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
        self.spec = lang.spec(self.lang)
        self.tc = lang.toolchain_for(self.lang)
        self._bins: dict[str, str | None] = {}
        self._errors: dict[str, str] = {}
        self.compile_error: str | None = None
        self._cache = lang.build_cache_dir(self.spec) if self.tc is not None else None

    def close(self) -> None:
        shutil.rmtree(self.wd, ignore_errors=True)

    # paths inside the sandbox
    def _in(self, name: str) -> str:
        return f"{self.root}/{name}"

    def _write(self, name: str, text: str) -> None:
        Path(self.wd, name).write_text(text, encoding="utf-8")

    @property
    def _hide(self) -> tuple[str, ...]:
        """Host paths to strip from anything shown to the learner."""
        return (self.wd, self.tc.root or "") if self.tc is not None else (self.wd,)

    # -- command building
    def _expand(self, tmpl: tuple[str, ...], *, sources: list[str] | None = None, src: str | None = None,
                out: str | None = None, mem_mb: int = 0, flags: tuple[str, ...] = ()) -> list[str]:
        assert self.tc is not None
        names = dict(self.tc.exes)
        names.update({"src": src or self._in(self.spec.source_name), "out": out or self._in("prog_main"),
                      "dir": self.root, "mem_mb": str(mem_mb), "cache": "/cache" if self.sandboxed
                      else (self._cache or "")})
        argv: list[str] = []
        for item in tmpl:
            if item == "{sources}":
                argv += sources or [self._in(self.spec.source_name)]
            elif item == "{flags}":
                argv += flags
            else:
                for k, v in names.items():
                    item = item.replace("{" + k + "}", v)
                argv.append(item)
        return argv

    def _binds(self) -> tuple[str, ...]:
        if self.tc is None or not self.sandboxed:
            return ()
        return tuple(b for b in ((self.tc.root,) if self.tc.root else ()) + self.tc.extra_ro)

    def _env(self, which: str, mem_mb: int = 0) -> dict[str, str]:
        extra = self.spec.compile_env if which == "compile" else self.spec.run_env
        cache = "/cache" if self.sandboxed else (self._cache or "")
        return _env_pairs(self.spec.env + extra, cache=cache, mem_mb=str(mem_mb))

    def _compile_run(self, cmd: list[str]) -> RunOut:
        lim = self.spec.limits
        return _run(cmd, workdir=self.wd, sandboxed=self.sandboxed, rw_work=True, stdin_text="",
                    wall_s=lim.compile_wall_s, cpu_s=COMPILE_CPU_S + lim.cpu_extra_s,
                    as_bytes=min(lim.compile_as_mb, lang.AS_CEILING_MB) * 1024 ** 2, fsize=COMPILE_FSIZE,
                    nproc_headroom=lim.compile_nproc, extra_ro=self._binds(),
                    rw_cache=self._cache if (self.sandboxed and self.spec.cache_name) else None,
                    env_extra=self._env("compile"))

    def run_budget(self, mem_mb: int) -> tuple[int, int]:
        """(address-space bytes, runtime heap MB) for a run with `mem_mb` of memory. AS is finite, <= 4 GiB."""
        mem_mb = max(32, int(mem_mb))
        over = self.spec.limits.overhead_mb
        as_mb = min(lang.AS_CEILING_MB, mem_mb + over)
        return as_mb * 1024 ** 2, max(16, min(mem_mb, as_mb - over)) if over else mem_mb

    def run_cmd(self, key: str, mem_mb: int, src: str | None = None) -> tuple[list[str], dict[str, str]]:
        _, heap = self.run_budget(mem_mb)
        out = self._in(f"prog_{key}")
        return self._expand(self.spec.run, src=src, out=out, mem_mb=heap), self._env("run", heap)

    def run_one(self, cmd: list[str], env: dict[str, str], *, stdin_text: str, wall_s: float, mem_mb: int) -> RunOut:
        lim = self.spec.limits
        as_bytes, _ = self.run_budget(mem_mb)
        return _run(cmd, workdir=self.wd, sandboxed=self.sandboxed, rw_work=False, stdin_text=stdin_text,
                    wall_s=wall_s, cpu_s=int(math.ceil(wall_s)) + 1 + lim.cpu_extra_s, as_bytes=as_bytes,
                    fsize=RUN_FSIZE, nproc_headroom=lim.run_nproc, extra_ro=self._binds(), env_extra=env)

    # -- preparing artifacts: returns the command to execute (or None + error)
    def prepare_main(self) -> bool:
        """Stdio form needs the learner's program. Interpreted: syntax check. Compiled: compile."""
        self._write(self.spec.source_name, self.code)
        if not self.spec.compiled:
            return self._syntax_check()
        return self._compile("main", [self._in(self.spec.source_name)]) is not None

    def _syntax_check(self) -> bool:
        if "py_ok" in self._bins:
            return self._bins["py_ok"] is not None
        self._write(self.spec.source_name, self.code)
        if self.spec.check is None:
            self._bins["py_ok"] = "ok"
            return True
        r = self._compile_run(self._expand(self.spec.check))
        if r.returncode != 0:
            self.compile_error = _readable_error(r.stderr or r.stdout, *self._hide) or "Syntax error"
            self._bins["py_ok"] = None
            return False
        self._bins["py_ok"] = "ok"
        return True

    def _compile(self, key: str, sources: list[str]) -> str | None:
        if key in self._bins:
            return self._bins[key]
        assert self.spec.compile is not None
        flags: tuple[str, ...] = ()
        if self.spec.optional_flags and _ubsan_works():
            flags = self.spec.optional_flags
        out = f"prog_{key}"
        r = self._compile_run(self._expand(self.spec.compile, sources=sources, out=self._in(out), flags=flags))
        if r.returncode != 0:
            raw = r.stderr or r.stdout
            msg = _readable_error(raw, *self._hide)
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
        if t.harness is not None:
            if self.spec.harness is None:
                return PerTestResult(name=t.name, passed=False, visible=visible,
                                     detail=f"{self.spec.name} exercises are checked through input and output.")
            cmd = self._harness_cmd(t, idx)
            if cmd is None:
                return PerTestResult(name=t.name, passed=False, visible=visible,
                                     detail=self._test_compile_message(f"h{idx}", visible))
            stdin = ""
        else:
            if not self.spec.compiled:
                if not self._syntax_check():
                    return PerTestResult(name=t.name, passed=False, visible=visible,
                                         detail=("Syntax error:\n" + (self.compile_error or "")) if visible
                                         else "Syntax error")
                self._write(self.spec.source_name, self.code)
                cmd, env = self.run_cmd("main", p.mem_mb)
            else:
                if "main" not in self._bins:
                    self._write(self.spec.source_name, self.code)
                bin_ = self._compile("main", [self._in(self.spec.source_name)])
                if bin_ is None:
                    return PerTestResult(name=t.name, passed=False, visible=visible,
                                         detail=self._test_compile_message("main", visible))
                cmd, env = self.run_cmd("main", p.mem_mb)
            stdin = t.stdin
        if t.harness is not None:
            env = self.run_cmd("main", p.mem_mb)[1]
        r = self.run_one(cmd, env, stdin_text=stdin, wall_s=wall, mem_mb=p.mem_mb)
        return self._judge(t, r, visible)

    def _test_compile_message(self, key: str, visible: bool) -> str:
        msg = self._errors.get(key) or self.compile_error or "Compilation failed"
        return ("Compile error:\n" + msg) if visible else "Compile error"

    def _harness_cmd(self, t: CodeTest, idx: int) -> list[str] | None:
        assert t.harness is not None
        hname = f"harness_{idx}{self.spec.harness_ext}"
        if self.spec.harness == "script":
            if not self._syntax_check():
                self._errors[f"h{idx}"] = self.compile_error or ""
                return None
            self._write(self.spec.source_name, self.code)
            self._write(hname, t.harness)
            return self.run_cmd("main", self.p.mem_mb, src=self._in(hname))[0]
        self._write(self.spec.source_name, self.code)
        self._write(hname, t.harness)
        bin_ = self._compile(f"h{idx}", [self._in(self.spec.source_name), self._in(hname)])
        return None if bin_ is None else [bin_]

    def _judge(self, t: CodeTest, r: RunOut, visible: bool) -> PerTestResult:
        out = r.stdout
        err = _readable_error(r.stderr, *self._hide)
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
    if lang.toolchain_for(payload.language) is None:
        return CodeResult(ok=False, score=0.0, detail=lang.not_ready_reason(payload.language), undecided=True,
                          per_test=None, passed=0, total=total)
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
    label = "Compile error" if sess.spec.compiled else "Syntax error"
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
    if lang.toolchain_for(language) is None:
        return SnippetResult(False, "", "", None, False, lang.not_ready_reason(language))
    sess = _Session(payload, code, sandboxed)
    try:
        if not sess.prepare_main():
            return SnippetResult(False, "", "", None, False, sess.compile_error)
        cmd, env = sess.run_cmd("main", mem_mb)
        r = sess.run_one(cmd, env, stdin_text=stdin, wall_s=time_limit_s, mem_mb=mem_mb)
        return SnippetResult(r.returncode == 0 and not r.timed_out, r.stdout, r.stderr, r.returncode,
                             r.timed_out, None, r.time_ms)
    finally:
        sess.close()
