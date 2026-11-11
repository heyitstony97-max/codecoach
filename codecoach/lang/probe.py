"""Probing (probe.py): a language is `ready` only after a hello-world compiled and ran INSIDE the sandbox on this machine.

Results are cached in memory and, when `configure(cache_dir)` was given a directory, in `lang-probe.json`
there. The cache key is the main tool's path + mtime + size (+ the version it reported), so a page never
waits on a probe twice and an upgraded toolchain is probed again.
"""

from __future__ import annotations

import atexit
import dataclasses
import json
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import codecoach.lang.toolchain as tcm
from codecoach.lang.spec import LangSpec, all_ids, spec as get_spec

PROBE_STDIN = "codecoach-probe\n"
PROBE_EXPECT = "echo: codecoach-probe"
PROBE_WALL_S = 12.0
LIVE_RUN_MAX_MS = 1500
CACHE_FILE = "lang-probe.json"
CACHE_VERSION = 1
PROBE_WORKERS = 4


@dataclass
class LangStatus:
    id: str
    name: str
    status: str                       # ready | missing | failed
    version: str | None
    reason: str | None
    hello_ms: int | None              # warm compile-and-run of hello
    live_run: bool
    ext: str
    tier: str
    cold_ms: int | None = None        # first compile-and-run (not part of the API shape)
    warm_ms: int | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status, "version": self.version,
                "reason": self.reason, "hello_ms": self.hello_ms, "live_run": self.live_run,
                "ext": self.ext, "tier": self.tier}


_lock = threading.RLock()
_cache_dir: Path | None = None
_status: dict[str, LangStatus] = {}
_tcs: dict[str, tcm.Toolchain | None] = {}
_disk: dict[str, dict] = {}
_lang_locks: dict[str, threading.Lock] = {}
_tmp_cache: list[str] = []


def configure(cache_dir: Path | str | None) -> None:
    """Where the probe cache (and persistent build caches) live. None = memory only. Forgets what was learned."""
    global _cache_dir, _disk
    with _lock:
        _cache_dir = Path(cache_dir) if cache_dir is not None else None
        _status.clear()
        _tcs.clear()
        _transient.clear()
        _disk = _load_disk()


def _load_disk() -> dict[str, dict]:
    if _cache_dir is None:
        return {}
    try:
        data = json.loads((_cache_dir / CACHE_FILE).read_text(encoding="utf-8"))
        if data.get("v") == CACHE_VERSION and isinstance(data.get("langs"), dict):
            return data["langs"]
    except (OSError, ValueError):
        pass
    return {}


def _save_disk() -> None:
    if _cache_dir is None:
        return
    try:
        _cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = _cache_dir / (CACHE_FILE + ".tmp")
        tmp.write_text(json.dumps({"v": CACHE_VERSION, "langs": _disk}, indent=1), encoding="utf-8")
        os.replace(tmp, _cache_dir / CACHE_FILE)
    except OSError:
        pass


def build_cache_dir(spec: LangSpec) -> str | None:
    """The one read-write dir a compile of this language may use for its build cache, or None."""
    if not spec.cache_name:
        return None
    with _lock:
        if _cache_dir is not None:
            d = _cache_dir / "build" / spec.cache_name
        else:
            if not _tmp_cache:
                _tmp_cache.append(tempfile.mkdtemp(prefix="codecoach-lang-cache-"))
            d = Path(_tmp_cache[0]) / spec.cache_name
        try:
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o755)
        except OSError:
            return None
        return str(d)


@atexit.register
def _cleanup() -> None:
    for d in _tmp_cache:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- toolchain access (used by the runner)

def toolchain_for(lang_id: str, *, refresh: bool = False) -> tcm.Toolchain | None:
    """The discovered toolchain, or None (missing / refused). Cheap after the first call."""
    get_spec(lang_id)
    with _lock:
        if not refresh and lang_id in _tcs:
            return _tcs[lang_id]
    d = tcm.discover(lang_id, want_version=False)
    with _lock:
        _tcs[lang_id] = d.toolchain if d.status == "found" else None
        _refusals[lang_id] = d
        return _tcs[lang_id]


_refusals: dict[str, tcm.Discovery] = {}


def not_ready_reason(lang_id: str) -> str:
    toolchain_for(lang_id)
    d = _refusals.get(lang_id)
    sp = get_spec(lang_id)
    return (d.reason if d and d.reason else None) or f"{sp.name} is not set up on this machine yet."


# --------------------------------------------------------------------------- probing

def _mk(sp: LangSpec, status: str, version: str | None, reason: str | None, cold: int | None = None,
        warm: int | None = None) -> LangStatus:
    live = bool(warm is not None and warm <= LIVE_RUN_MAX_MS) if status == "ready" else False
    return LangStatus(id=sp.id, name=sp.name, status=status, version=version, reason=reason,
                      hello_ms=warm if status == "ready" else None, live_run=live, ext=sp.ext, tier=sp.tier,
                      cold_ms=cold, warm_ms=warm)


_transient: set[str] = set()     # languages whose last failure may be the machine's, not the toolchain's (kept off disk)


def _hello(sp: LangSpec) -> tuple[bool, int, bool]:
    """(printed the expected line, milliseconds, ran out of time)."""
    from codecoach.check import code_runner as cr
    t0 = time.monotonic()
    r = cr.run_snippet(sp.id, sp.hello, PROBE_STDIN, time_limit_s=PROBE_WALL_S, mem_mb=256)
    ms = int((time.monotonic() - t0) * 1000)
    ok = bool(r is not None and r.ok and r.stdout.strip() == PROBE_EXPECT)
    return ok, ms, bool(r is not None and r.timed_out)


def _hello_patient(sp: LangSpec) -> tuple[bool, int, bool]:
    """A hello, and one more try when it fails: the first run after a boot can lose to a cold disk or a busy
    machine, and a language wrongly marked "not set up" stays that way until the learner presses Look again."""
    ok, ms, slow = _hello(sp)
    if not ok:
        ok, ms, slow_again = _hello(sp)
        slow = slow or slow_again
    return ok, ms, slow


def _probe(sp: LangSpec) -> LangStatus:
    from codecoach.check import code_runner as cr
    d = tcm.discover(sp.id)
    with _lock:
        _tcs[sp.id] = d.toolchain if d.status == "found" else None
        _refusals[sp.id] = d
    if d.status == "missing":
        return _mk(sp, "missing", None, d.reason)
    if d.status == "failed" or d.toolchain is None:
        return _mk(sp, "failed", None, d.reason)
    tc = d.toolchain
    if cr.sandbox_status() != "bwrap":
        return _mk(sp, "failed", tc.version, "The sandbox (bubblewrap) is not available on this machine.")
    where = tc.main
    with _lock:
        _transient.discard(sp.id)

    def failed(slow: bool, cold: int | None) -> LangStatus:
        if not slow:
            return _mk(sp, "failed", tc.version, f"Found {sp.name} at {where}, but it could not run inside the "
                                                 f"sandbox.", cold)
        with _lock:
            _transient.add(sp.id)       # not the toolchain's fault for certain: do not remember it across restarts
        return _mk(sp, "failed", tc.version, f"Found {sp.name} at {where}, but its test program did not finish in "
                                             f"time. The machine may be busy. Press Look again.", cold)
    try:
        ok1, cold, slow = _hello_patient(sp)
        if not ok1:
            return failed(slow, cold)
        ok2, warm, slow = _hello_patient(sp)
        if not ok2:
            return failed(slow, cold)
    except Exception:       # a probe must never take the page down
        return _mk(sp, "failed", tc.version, f"Found {sp.name} at {where}, but it could not run inside the "
                                             f"sandbox.")
    return _mk(sp, "ready", tc.version, None, cold, warm)


def _from_disk(sp: LangSpec, key: str) -> LangStatus | None:
    e = _disk.get(sp.id)
    if not e or e.get("key") != key:
        return None
    try:
        return _mk(sp, e["status"], e.get("version"), e.get("reason"), e.get("cold_ms"), e.get("warm_ms"))
    except KeyError:
        return None


def _remember(sp: LangSpec, st: LangStatus, key: str | None) -> None:
    with _lock:
        _status[sp.id] = st
        if key and (st.status == "ready" or (st.status == "failed" and sp.id not in _transient)):
            _disk[sp.id] = {"key": key, "status": st.status, "version": st.version, "reason": st.reason,
                            "cold_ms": st.cold_ms, "warm_ms": st.warm_ms}
            _save_disk()
        elif sp.id in _disk and st.status == "missing":
            _disk.pop(sp.id, None)
            _save_disk()


def status(lang_id: str, *, refresh: bool = False) -> LangStatus:
    sp = get_spec(lang_id)
    with _lock:
        if not refresh and lang_id in _status:
            return _status[lang_id]
        lk = _lang_locks.setdefault(lang_id, threading.Lock())
    with lk:
        with _lock:
            if not refresh and lang_id in _status:
                return _status[lang_id]
        if not refresh and _disk.get(lang_id):
            d = tcm.discover(lang_id, want_version=False)
            if d.status == "found" and d.toolchain is not None:
                hit = _from_disk(sp, d.toolchain.key)
                if hit is not None:
                    full = tcm.discover(lang_id)           # the version string, for the runner's use
                    with _lock:
                        _tcs[lang_id] = full.toolchain if full.status == "found" else d.toolchain
                        _refusals[lang_id] = full
                        _status[lang_id] = hit
                    return hit
        st = _probe(sp)
        tc = _tcs.get(lang_id)
        _remember(sp, st, tc.key if tc else None)
        return st


def statuses(*, refresh: bool = False) -> list[LangStatus]:
    ids = all_ids()
    with _lock:
        todo = [i for i in ids if refresh or i not in _status]
    if len(todo) > 1:
        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
            list(pool.map(lambda i: status(i, refresh=refresh), todo))
    elif todo:
        status(todo[0], refresh=refresh)
    return [status(i) for i in ids]


def ready_ids() -> list[str]:
    return [s.id for s in statuses() if s.status == "ready"]


def measured_live_run(lang_id: str) -> bool | None:
    with _lock:
        st = _status.get(lang_id)
    return st.live_run if st is not None and st.status == "ready" else None


def spec_with_measure(lang_id: str) -> LangSpec:
    """The spec, with `live_run` replaced by the measured value once a probe has run."""
    sp = get_spec(lang_id)
    m = measured_live_run(lang_id)
    return sp if m is None else dataclasses.replace(sp, live_run=m)
