"""Read-aloud audio: a content-addressed cache over a synthesizer.

The synthesizer is a port (``Synthesizer``): the real one lives in codecoach.hermes_glue and speaks with the
learner's Hermes TTS setting; tests pass a fake. The text handed to ``AudioCache.get`` is the passage text
verbatim: this module never rewrites it and no model ever sees it.

Cache layout: ``<root>/<sha256(text + NUL + voice)>.<ext>``. Last use is the file's mtime (touched on every
hit); when the folder passes ``cap_bytes`` the least recently used files go first. The clip just served is
never evicted by its own insertion.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

DEFAULT_CAP_BYTES = 200 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "opus": "audio/ogg", "m4a": "audio/mp4",
        "flac": "audio/flac", "aac": "audio/aac", "webm": "audio/webm"}


class TTSUnavailable(Exception):
    """Hermes TTS cannot speak right now (not configured, provider down, no network, empty result)."""


class Synthesizer(Protocol):
    def voice(self) -> str:
        """A stable label for the configured provider and voice; part of the cache key."""
        ...

    def synthesize(self, text: str) -> tuple[bytes, str]:
        """(audio bytes, file extension without dot). Raise TTSUnavailable on any failure."""
        ...


@dataclass(frozen=True)
class Clip:
    path: Path
    ext: str
    mime: str
    voice: str
    cached: bool
    size: int


def clip_key(text: str, voice: str) -> str:
    return hashlib.sha256((text + "\x00" + voice).encode("utf-8")).hexdigest()


class AudioCache:
    def __init__(self, root: Path, synth: Synthesizer, *, cap_bytes: int = DEFAULT_CAP_BYTES,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.synth = synth
        self.cap = int(cap_bytes)
        self.clock = clock
        self._lock = threading.Lock()                 # guards _key_locks and eviction
        self._key_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ lookup / synthesis

    def _find(self, key: str) -> Path | None:
        if not self.root.is_dir():
            return None
        for p in self.root.glob(key + ".*"):
            if p.suffix != ".tmp" and p.is_file():
                return p
        return None

    def _touch(self, p: Path) -> None:
        now = self.clock()
        try:
            os.utime(p, (now, now))
        except OSError:
            pass

    def get(self, text: str) -> Clip:
        if not isinstance(text, str) or not text.strip():
            raise TTSUnavailable("There is no text to read.")
        if len(text) > MAX_TEXT_CHARS:
            raise TTSUnavailable("That passage is too long to read aloud in one piece.")
        voice = self.synth.voice()
        key = clip_key(text, voice)
        hit = self._find(key)
        if hit is not None:
            self._touch(hit)
            return self._clip(hit, voice, True)
        with self._lock:
            klock = self._key_locks.setdefault(key, threading.Lock())
        with klock:                                   # a prefetch and a play of the same paragraph synthesize once
            hit = self._find(key)
            if hit is not None:
                self._touch(hit)
                return self._clip(hit, voice, True)
            data, ext = self.synth.synthesize(text)
            ext = re.sub(r"[^a-z0-9]", "", (ext or "mp3").lower())[:5] or "mp3"     # a file-name part, never a path
            if not data:
                raise TTSUnavailable("The voice returned no audio.")
            self.root.mkdir(parents=True, exist_ok=True)
            final = self.root / f"{key}.{ext}"
            tmp = self.root / f"{key}.{ext}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, final)
            self._touch(final)
            self._evict(keep=final)
        with self._lock:
            self._key_locks.pop(key, None)
        return self._clip(final, voice, False)

    @staticmethod
    def _clip(p: Path, voice: str, cached: bool) -> Clip:
        ext = p.suffix.lstrip(".")
        return Clip(path=p, ext=ext, mime=MIME.get(ext, "application/octet-stream"), voice=voice, cached=cached,
                    size=p.stat().st_size)

    # ------------------------------------------------------------------ eviction

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [p for p in self.root.iterdir() if p.is_file() and p.suffix != ".tmp"]

    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self._files())

    def _evict(self, keep: Path | None = None) -> list[str]:
        """Delete least-recently-used clips until the folder fits the cap. Returns the names removed."""
        gone: list[str] = []
        with self._lock:
            files = []
            for p in self._files():
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, p.name, p, st.st_size))
            total = sum(f[3] for f in files)
            for _mt, _name, p, size in sorted(files):
                if total <= self.cap:
                    break
                if keep is not None and p == keep:
                    continue
                try:
                    p.unlink()
                except OSError:
                    continue
                total -= size
                gone.append(p.name)
        return gone
