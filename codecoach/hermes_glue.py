"""The only module under codecoach/ that touches Hermes (CONSTRAINTS 11.3 rules 1 and 9).

Two LLM doors, one per process:
  * tools and the /codecoach command run in the plugin-host child and use ``ctx.llm`` (CtxLLM);
  * dashboard/plugin_api.py runs in the gateway process and uses
    ``agent.plugin_llm.PluginLlm(plugin_id="codecoach")`` (PluginLLM).
Both map onto codecoach.core.llm.LLMPort. Hermes imports are lazy so this file imports without Hermes.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from time import time_ns
from typing import Any, Callable

from codecoach import pdf
from codecoach.audio import AudioCache, TTSUnavailable
from codecoach.core.llm import LLMPort, LLMUnavailable
from codecoach.core.store import Store
from codecoach.service import CodeCoachService, ServiceConfig, ServiceError, check_local_path, read_local_path

PLUGIN_ID = "codecoach"
AUX_TASK = "codecoach"
AUX_DISPLAY = "CodeCoach tutor"
AUX_DESCRIPTION = "Course import, question drafting and lesson framing for the CodeCoach tutor plugin."
DB_NAME = "codecoach.db"
DEFAULT_TIMEOUT = 600.0     # same as the plugin.yaml llm_timeout_s default; services pass the configured value
PDF_PAGE_LINES = 2000
PDF_MAX_PAGES = 200
_LINE_PREFIX = re.compile(r"^\s*\d+\|", re.M)


# ------------------------------------------------------------------ result mapping

def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def parsed_to_dict(result: Any) -> dict | None:
    """Structured-call result -> the parsed JSON object, or None when it was not valid JSON."""
    parsed = _field(result, "parsed")
    if isinstance(parsed, dict):
        return parsed
    if parsed is None and _field(result, "content_type", "text") == "json":
        return None
    text = _field(result, "text", "") or ""
    if isinstance(parsed, (list, str, int, float)):  # JSON, but not an object
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


class _BaseLLM:
    """Shared LLMPort behaviour over an object with complete()/complete_structured() (ctx.llm or PluginLlm)."""

    def __init__(self, caller: Any, *, task: str | None = AUX_TASK, default_timeout: float = DEFAULT_TIMEOUT):
        self._caller = caller
        self.task = task
        self.default_timeout = default_timeout
        self.task_applied: bool | None = None   # filled by the first call: did the task route work?
        self.last_error: str = ""

    def _kw(self, purpose: str, max_tokens: int | None, timeout: float | None, with_task: bool) -> dict:
        kw: dict[str, Any] = {"purpose": purpose, "timeout": timeout or self.default_timeout}
        if max_tokens:
            kw["max_tokens"] = int(max_tokens)
        if with_task and self.task:
            kw["task"] = self.task
        return kw

    def _call(self, fn: Callable[..., Any], base: dict, purpose: str, max_tokens, timeout) -> Any:
        try:
            out = fn(**base, **self._kw(purpose, max_tokens, timeout, True))
            self.task_applied = bool(self.task)
            return out
        except Exception as e:
            if self.task and _is_task_denial(e):
                # The aux task is only known to a process whose register() ran. Fall back to the
                # active main model rather than failing the learner.
                self.task_applied = False
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                try:
                    return fn(**base, **self._kw(purpose, max_tokens, timeout, False))
                except Exception as e2:
                    raise LLMUnavailable(f"{purpose}: {type(e2).__name__}: {e2}") from e2
            raise LLMUnavailable(f"{purpose}: {type(e).__name__}: {e}") from e

    def complete_structured(self, *, instructions: str, text: str, schema: dict, purpose: str,
                            max_tokens: int | None = None, timeout: float | None = None) -> dict | None:
        base = {"instructions": instructions, "input": [{"type": "text", "text": text}],
                "json_schema": schema, "json_mode": True}
        res = self._call(self._caller.complete_structured, base, purpose, max_tokens, timeout)
        return parsed_to_dict(res)

    def complete(self, *, messages: list[dict], purpose: str, max_tokens: int | None = None,
                 timeout: float | None = None) -> str:
        res = self._call(self._caller.complete, {"messages": messages}, purpose, max_tokens, timeout)
        return str(_field(res, "text", "") or "")


def _is_task_denial(e: BaseException) -> bool:
    name = type(e).__name__
    msg = str(e)
    return name == "PluginLlmTrustError" or ("auxiliary task" in msg and "register" in msg)


class CtxLLM(_BaseLLM):
    """LLMPort over ctx.llm (plugin-host child: tools and the /codecoach command)."""

    def __init__(self, ctx: Any, **kw):
        super().__init__(ctx.llm, **kw)


class PluginLLM(_BaseLLM):
    """LLMPort over agent.plugin_llm.PluginLlm(plugin_id="codecoach") (gateway process: plugin_api)."""

    def __init__(self, plugin_llm: Any | None = None, **kw):
        if plugin_llm is None:
            from agent.plugin_llm import PluginLlm
            plugin_llm = PluginLlm(plugin_id=PLUGIN_ID)
        super().__init__(plugin_llm, **kw)


# ------------------------------------------------------------------ storage and config

def store_path() -> Path:
    from plugins.plugin_storage import plugin_data_dir
    return Path(plugin_data_dir(PLUGIN_ID)) / DB_NAME


_stores: dict[str, Store] = {}
_stores_lock = threading.Lock()


def open_store(path: Path | None = None) -> Store:
    """One Store per path per process (the Store is thread-safe: WAL, busy_timeout 60 s)."""
    p = Path(path) if path else store_path()
    with _stores_lock:
        s = _stores.get(str(p))
        if s is None:
            s = _stores[str(p)] = Store(p)
        return s


def config_from_ctx(ctx: Any) -> ServiceConfig:
    def get(key: str, default: Any) -> Any:
        try:
            return ctx.get_config(key, default)
        except Exception:
            return default
    return ServiceConfig.from_mapping(get)


def config_from_hermes() -> ServiceConfig:
    """Gateway process: read plugins.entries.codecoach.settings from the on-disk config."""
    settings: dict = {}
    try:
        from hermes_cli.config import load_config
        entry = ((load_config() or {}).get("plugins") or {}).get("entries", {}).get(PLUGIN_ID) or {}
        settings = dict(entry.get("settings") or entry.get("config") or {})
    except Exception:
        settings = {}
    return ServiceConfig.from_mapping(settings.get)


# ------------------------------------------------------------------ PDF through Hermes read_file

def strip_line_numbers(content: str) -> str:
    return _LINE_PREFIX.sub("", content)


def make_ctx_reader(ctx: Any) -> Callable[[str], str]:
    """Path reader: text files directly; PDFs through pdftotext when installed (every page with a text
    layer, page numbers kept), else through ctx.dispatch_tool("read_file") (refuses mixed scanned PDFs)."""
    def reader(path: str) -> str:
        if not isinstance(path, str) or not path.strip().lower().endswith(".pdf") or pdf.available():
            return read_local_path(path)
        path, _ext = check_local_path(path)         # same policy as every other read; Hermes read_file gets the resolved path
        parts: list[str] = []
        offset = 1
        for _ in range(PDF_MAX_PAGES):
            try:
                raw = ctx.dispatch_tool("read_file", {"path": path, "offset": offset, "limit": PDF_PAGE_LINES})
                data = json.loads(raw) if isinstance(raw, str) else raw
            except Exception as e:
                raise ServiceError(422, "pdf_unavailable",
                                   f"Could not read the PDF through Hermes read_file ({type(e).__name__}). "
                                   "Paste the text instead.") from None
            if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("content"), str):
                msg = (data.get("error") if isinstance(data, dict) else None) or "no text came back"
                raise ServiceError(422, "pdf_unavailable", f"Hermes could not read that PDF: {str(msg)[:200]}")
            chunk = strip_line_numbers(data["content"])
            parts.append(chunk)
            total = data.get("total_lines")
            offset += PDF_PAGE_LINES
            if not isinstance(total, int) or offset > total or not chunk.strip():
                break
        text = "\n".join(parts).strip()
        if not text:
            raise ServiceError(422, "pdf_no_text", "That PDF has no text layer (scanned?). Paste the text instead.")
        return text
    return reader


# ------------------------------------------------------------------ service builders

_svc_lock = threading.Lock()
_svc_cache: dict[str, CodeCoachService] = {}


def build_service_for_ctx(ctx: Any, *, store: Store | None = None) -> CodeCoachService:
    """Plugin-host child: tools and the command. No event emitter (events come from the gateway)."""
    with _svc_lock:
        key = "ctx"
        if key not in _svc_cache:
            cfg = config_from_ctx(ctx)
            _svc_cache[key] = CodeCoachService(store or open_store(), CtxLLM(ctx, default_timeout=cfg.llm_timeout_s),
                                            cfg, path_reader=make_ctx_reader(ctx))
        return _svc_cache[key]


# ------------------------------------------------------------------ read aloud: Hermes TTS, in process

class HermesTTS:
    """Speech from the learner's Hermes TTS setting (``tts.provider`` and its voice), in the gateway process.

    Goes through Hermes's own tool registry (``text_to_speech``, the same entry the agent uses), so provider
    choice, voice, long-text chunking and API keys are all Hermes's: nothing is spawned and no setting is
    copied. The passage text is passed as is; no model is involved."""

    def __init__(self, scratch: Path | None = None):
        self._scratch = scratch

    def voice(self) -> str:
        try:
            from hermes_cli.config import load_config
            tts = (load_config() or {}).get("tts") or {}
        except Exception:
            tts = {}
        provider = str(tts.get("provider") or "edge")
        sub = tts.get(provider)
        sub = sub if isinstance(sub, dict) else {}
        voice = sub.get("voice") or sub.get("voice_id") or sub.get("model") or "default"
        return f"{provider}/{voice}"

    def synthesize(self, text: str) -> tuple[bytes, str]:
        try:
            # Public surface only (catalog rule 9): tools.registry. Built-in tools register on discovery;
            # the gateway has usually discovered them already, so this is a no-op there.
            from tools.registry import discover_builtin_tools, registry
            if registry.get_entry("text_to_speech") is None:
                discover_builtin_tools()
            if registry.get_entry("text_to_speech") is None:
                raise TTSUnavailable("Hermes has no text_to_speech tool in this process")
        except TTSUnavailable:
            raise
        except Exception as e:
            raise TTSUnavailable(f"Hermes TTS is not available here ({type(e).__name__})") from None
        import tempfile
        scratch = self._scratch or Path(tempfile.gettempdir())
        scratch.mkdir(parents=True, exist_ok=True)
        out = scratch / f"tts-{threading.get_ident()}-{time_ns()}.mp3"
        try:
            raw = registry.dispatch("text_to_speech", {"text": text, "output_path": str(out)})
            res = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as e:
            raise TTSUnavailable(f"{type(e).__name__}: {str(e)[:200]}") from None
        if not isinstance(res, dict) or res.get("success") is False or res.get("error"):
            msg = (res.get("error") if isinstance(res, dict) else None) or "no result"
            raise TTSUnavailable(str(msg)[:300])
        paths = [Path(p) for p in (res.get("file_paths") or [res.get("file_path")]) if p]
        try:
            data = b"".join(p.read_bytes() for p in paths)
            ext = paths[0].suffix.lstrip(".") if paths else "mp3"
        except OSError as e:
            raise TTSUnavailable(f"could not read the audio file ({type(e).__name__})") from None
        finally:
            for p in paths:
                try:
                    p.unlink()
                except OSError:
                    pass
        return data, ext or "mp3"


def audio_cache() -> AudioCache:
    from plugins.plugin_storage import plugin_data_dir
    root = Path(plugin_data_dir(PLUGIN_ID)) / "audio"
    return AudioCache(root, HermesTTS(scratch=root / "_tmp"))


def exports_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir
    return Path(plugin_data_dir(PLUGIN_ID)) / "exports"


def build_service_for_gateway(*, store: Store | None = None) -> CodeCoachService:
    """Gateway process: REST routes. PluginLlm door; progress events through broadcast_plugin_event."""
    with _svc_lock:
        key = "gateway"
        if key not in _svc_cache:
            def emit(event: str, payload: dict) -> None:
                from hermes_cli.plugin_events import broadcast_plugin_event
                broadcast_plugin_event(PLUGIN_ID, event, payload)
            cfg = config_from_hermes()
            _svc_cache[key] = CodeCoachService(store or open_store(), PluginLLM(default_timeout=cfg.llm_timeout_s),
                                            cfg, emit=emit, audio=audio_cache(), exports_dir=exports_dir())
        return _svc_cache[key]


def reset_for_tests() -> None:
    with _svc_lock:
        _svc_cache.clear()
    with _stores_lock:
        _stores.clear()


def register_aux_task(ctx: Any) -> None:
    ctx.register_auxiliary_task(AUX_TASK, display_name=AUX_DISPLAY, description=AUX_DESCRIPTION)
