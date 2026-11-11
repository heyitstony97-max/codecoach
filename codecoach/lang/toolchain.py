"""Find a language's toolchain on this machine and decide what, if anything, the sandbox may bind for it.

Rules (contract section 3):
  * A toolchain under /usr needs no extra bind: the sandbox already has /usr read-only.
  * A toolchain elsewhere gets ONLY its own resolved root bound read-only (the real prefix of the binary, the
    Rust sysroot, GOROOT). The real binary is called, never a wrapper such as ~/.cargo/bin/rustc.
  * Never bind $HOME or a parent of it, "/", or a root that is, is inside, or contains ~/.cargo, ~/.npm,
    ~/.config, ~/.ssh, ~/.gnupg or ~/.aws. Such a toolchain is reported as failed with a plain reason.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from codecoach.lang.spec import LangSpec, spec as get_spec

SECRET_DIRS = (".cargo", ".npm", ".config", ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".password-store",
               ".local/share/keyrings")
# Shared app-data folders: a root may sit INSIDE them (mise, fnm and uv install toolchains under ~/.local/share)
# but may never be one of them or hold one.
BROAD_HOME_DIRS = (".local/share", ".local/state")
# Names that mark a folder as holding credentials or personal state when they sit at its top level.
SECRET_NAMES = (".env", "auth.json", ".netrc", ".git-credentials", ".npmrc", ".pypirc", "keyrings")
# The only top-level folders a toolchain root inside the home folder may have (files are not restricted).
TOOLCHAIN_DIRS = frozenset({
    "bin", "sbin", "lib", "lib32", "lib64", "libexec", "include", "share", "etc", "man", "doc", "docs", "pkg",
    "src", "api", "misc", "test", "tests", "conf", "jmods", "legal", "licenses", "releases", "usr", "tools",
    "std", "node_modules"})
TOO_BROAD = ("/", "/home", "/root", "/etc", "/var", "/proc", "/sys", "/dev", "/run", "/boot", "/tmp", "/mnt",
             "/media", "/srv", "/opt")       # system roots are never a toolchain's own folder
_SYSTEM_PREFIXES = ("/usr/",)                 # already bound read-only
VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Toolchain:
    lang_id: str
    exes: dict[str, str]              # tool name -> absolute path to call (inside the sandbox too)
    root: str | None                  # extra read-only bind (same path inside), or None when under /usr
    version: str | None
    key: str                          # path + mtime + size of the main tool: the probe cache key
    extra_ro: tuple[str, ...] = ()    # further read-only binds (config dirs a runtime reads)

    @property
    def main(self) -> str:
        return self.exes[get_spec(self.lang_id).tool]


@dataclass(frozen=True)
class Discovery:
    status: str                       # "found" | "missing" | "failed"
    toolchain: Toolchain | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- bind rules

def _norm(p: str | os.PathLike) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(p)))


def _is_within(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def bind_refusal(root: str | os.PathLike, home: str | os.PathLike | None = None) -> str | None:
    """None when `root` may be bound read-only into the sandbox, else a short plain phrase saying why not."""
    r = _norm(root)
    h = _norm(home if home is not None else os.path.expanduser("~"))
    if r == "/":
        return "is the whole disk"
    if r == h:
        return "is your home folder"
    if _is_within(h, r):
        return "holds your home folder"
    for name in SECRET_DIRS:
        d = os.path.join(h, name)
        if _is_within(r, d):
            return f"is inside your {name} folder"
        if _is_within(d, r):
            return f"holds your {name} folder"
    for name in SECRET_DIRS:
        if os.path.lexists(os.path.join(r, name)):
            return f"holds a {name} folder"
    if r in TOO_BROAD:
        return "is a system folder that is much too broad to share"
    if _is_within(r, h):
        # Inside the home folder a root must look like a toolchain and nothing else. Without these checks a
        # binary at ~/.local/bin/go would have shared all of ~/.local (keyrings, app data) with the sandbox.
        if os.path.dirname(r) == h and os.path.basename(r).startswith("."):
            return "is a settings folder in your home folder, not a toolchain's own folder"
        for name in BROAD_HOME_DIRS:
            if _is_within(os.path.join(h, name), r):
                return f"holds your {name} folder"
        for name in SECRET_NAMES:
            if os.path.lexists(os.path.join(r, name)):
                return f"holds a {name} file or folder"
        odd = _odd_dirs(r)
        if odd:
            return f"holds other things besides the toolchain ({', '.join(odd[:3])})"
    return None


def _odd_dirs(root: str) -> list[str]:
    """Top-level folders of `root` that a toolchain's own folder does not normally have (sorted names)."""
    try:
        with os.scandir(root) as it:
            names = [e.name for e in it if e.is_dir(follow_symlinks=True)]
    except OSError:
        return []
    return sorted(n for n in names if n not in TOOLCHAIN_DIRS and not n.startswith("erts-"))


def needs_bind(real_path: str) -> bool:
    return not any(real_path.startswith(p) for p in _SYSTEM_PREFIXES)


# --------------------------------------------------------------------------- finding tools

def _clean_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    return env


def _run_quiet(argv: list[str], timeout: float = 10.0) -> str:
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=_clean_env(), text=True, errors="replace")
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _find(name: str) -> str | None:
    """Prefer a system install (no bind needed), then anything on PATH."""
    for d in ("/usr/bin", "/usr/local/bin"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which(name)


def _real(p: str) -> str:
    return os.path.realpath(p)


def _prefix_of(real_bin: str) -> str:
    d = os.path.dirname(real_bin)
    return os.path.dirname(d) if os.path.basename(d) == "bin" else d


def _version(exe: str, spec: LangSpec) -> str | None:
    m = VERSION_RE.search(_run_quiet([exe, *spec.version_args]))
    return m.group(0) if m else None


def _stat_key(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{path}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return f"{path}|?|?"


def discover(lang_id: str, *, home: str | os.PathLike | None = None, want_version: bool = True) -> Discovery:
    """Look for the language's tools. Never runs learner code; runs `--version` style commands only."""
    spec = get_spec(lang_id)
    exes: dict[str, str] = {}
    root: str | None = None
    for name in spec.tools:
        found = _find(name)
        if not found:
            return Discovery("missing", reason=f"{spec.name} was not found on this machine.")
        call, real = found, _real(found)
        r: str | None
        if lang_id == "rust" and name == "rustc":
            sysroot = _run_quiet([found, "--print", "sysroot"]).strip()
            cand = os.path.join(sysroot, "bin", "rustc") if sysroot else ""
            if not (cand and os.path.isfile(cand)):
                return Discovery("failed", reason=f"Found {spec.name} at {found}, but could not find the real "
                                                  f"compiler behind it.")
            call, real = cand, _real(cand)
            r = sysroot if needs_bind(_real(sysroot)) else None
        else:
            r = _prefix_of(real) if needs_bind(real) else None
        if r is not None:
            if call == found and lang_id != "rust":
                call = real
            if root is not None and root != r:
                return Discovery("failed", reason=f"Found {spec.name} at {found}, but its tools live in more "
                                                  f"than one folder.")
            root = r
        exes[name] = call
    if root is not None:
        why = bind_refusal(root, home)
        if why:
            return Discovery("failed", reason=f"Found {spec.name} at {exes[spec.tool]}, but its folder "
                                              f"({root}) {why}, so CodeCoach will not share it with the sandbox.")
        if not os.path.isdir(root):
            return Discovery("failed", reason=f"Found {spec.name} at {exes[spec.tool]}, but its folder is missing.")
    extra: tuple[str, ...] = ()
    if lang_id == "java":
        extra = tuple(sorted(p for p in glob.glob("/etc/java-*") if os.path.isdir(p)))
    elif lang_id == "haskell":
        # The Debian ghc keeps its package database outside /usr; that one folder is the toolchain's own.
        extra = tuple(p for p in ("/var/lib/ghc",) if os.path.isdir(p))
    main = exes[spec.tool]
    tc = Toolchain(lang_id=lang_id, exes=exes, root=root, version=_version(main, spec) if want_version else None, key=_stat_key(main),
                   extra_ro=extra)
    return Discovery("found", tc)
