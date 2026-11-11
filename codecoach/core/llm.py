"""LLM access port. The only way core/gen/ground code talks to a model.

hermes_glue (a4a) adapts ctx.llm / PluginLlm to LLMPort. Tests use FakeLLM. No Hermes imports here.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    """Base class."""


class LLMUnavailable(LLMError):
    """Provider error, timeout, or nothing left to say. Not retried."""


class LLMBadOutput(LLMError):
    """The model kept returning output that failed validation."""

    def __init__(self, message: str, *, purpose: str = "", attempts: int = 0, last_error: str = ""):
        super().__init__(message)
        self.purpose = purpose
        self.attempts = attempts
        self.last_error = last_error


@runtime_checkable
class LLMPort(Protocol):
    def complete_structured(self, *, instructions: str, text: str, schema: dict,
                            purpose: str, max_tokens: int | None = None,
                            timeout: float | None = None) -> dict | None:
        """Return the parsed JSON object, or None when the model's reply was not valid JSON.
        Raise LLMUnavailable on provider failure or timeout. Validation against `schema`
        is the CALLER's job (structured_call does it with pydantic)."""
        ...

    def complete(self, *, messages: list[dict], purpose: str, max_tokens: int | None = None,
                 timeout: float | None = None) -> str:
        """Plain text completion. messages = [{"role": "system"|"user"|"assistant", "content": str}]."""
        ...

    def last_route(self) -> dict | None:
        """{"provider": str, "model": str} of the model that answered the most recent call, or None when
        nothing has answered yet or the host did not say."""
        ...


M = TypeVar("M", bound=BaseModel)


def grammar_safe(schema: dict) -> dict:
    """The reply schema with every list and text field required (a copy; the input is not changed).

    A local model served by llama.cpp turns the JSON schema into a grammar, and a grammar lets the model close
    the object as soon as the required fields are written. Pydantic marks a field with a default as optional,
    so local Qwen answered every Learn step with {"answerable": true} and no sentences (orchestrator probe on the
    live store, Nov 7). A field whose default is empty ("", [], {} or a default factory) is content the model
    must write, so it becomes required; a field with a real default ("materials", True, None) stays optional."""
    import copy

    def walk(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                req = list(node.get("required") or [])
                for k, v in props.items():
                    if k not in req and isinstance(v, dict) and ("default" not in v or v["default"] in ("", [], {})):
                        req.append(k)
                node["required"] = req
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        return node

    return walk(copy.deepcopy(schema))


def structured_call(port: LLMPort, ModelCls: type[M], *, instructions: str, text: str,
                    purpose: str, retries: int = 3, max_tokens: int | None = None,
                    timeout: float | None = None) -> M:
    """Ask for one ModelCls. `retries` is the TOTAL number of attempts (3 = first try + 2 repairs).

    A reply that is None, not a dict, or fails ModelCls.model_validate counts as bad: the next
    attempt's `text` gets the validation error appended. After `retries` bad replies raise
    LLMBadOutput. LLMUnavailable from the port propagates immediately; any other exception from
    the port becomes LLMUnavailable (ValueError is treated as bad output instead).
    """
    schema = grammar_safe(ModelCls.model_json_schema())
    prompt = text
    last_error = "no valid JSON object was returned"
    attempts = max(1, retries)
    for n in range(1, attempts + 1):
        try:
            raw = port.complete_structured(instructions=instructions, text=prompt, schema=schema,
                                           purpose=purpose, max_tokens=max_tokens, timeout=timeout)
        except LLMUnavailable:
            raise
        except ValueError as e:
            raw, last_error = None, f"ValueError: {e}"
        except Exception as e:  # provider errors and timeouts
            raise LLMUnavailable(f"{purpose}: {type(e).__name__}: {e}") from e
        if raw is None:
            pass
        elif not isinstance(raw, dict):
            last_error = f"expected a JSON object, got {type(raw).__name__}"
        else:
            try:
                return ModelCls.model_validate(raw)
            except ValidationError as e:
                last_error = _short_errors(e)
        prompt = (f"{text}\n\nYour previous reply was rejected: {last_error}\n"
                  f"Reply again with only a JSON object that matches the schema.")
    raise LLMBadOutput(f"{purpose}: no valid output after {attempts} attempts: {last_error}",
                       purpose=purpose, attempts=attempts, last_error=last_error)


def _short_errors(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:6]:
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


# ---------------------------------------------------------------- test double


@dataclass
class FakeCall:
    kind: str                     # "structured" | "text"
    purpose: str
    text: str = ""                # structured: the text; text: the last message content
    instructions: str = ""
    messages: list[dict] = field(default_factory=list)
    schema: dict | None = None


class FakeLLM:
    """Scripted LLMPort. Records every call in `.calls`.

    script / text_script is either
      - an iterable consumed in order (items: dict | None | Exception instance | callable), or
      - a function (purpose, text) -> dict | None | str | Exception
    An Exception instance (or returned exception) is raised. A pydantic model is dumped to a dict.
    When a queue is exhausted the call raises LLMUnavailable("FakeLLM script exhausted").
    """

    def __init__(self, script: Iterable[Any] | Callable[[str, str], Any] | None = None,
                 text_script: Iterable[Any] | Callable[[str, str], Any] | None = None):
        self._script = script if callable(script) else iter(list(script or []))
        self._text = text_script if callable(text_script) else iter(list(text_script or []))
        self._script_callable = callable(script)
        self._text_callable = callable(text_script)
        self.calls: list[FakeCall] = []
        self.route: dict | None = None      # tests set this (or call set_route) to change the reported model

    def set_route(self, provider: str | None, model: str | None = None) -> None:
        self.route = None if provider is None else {"provider": provider, "model": model or ""}

    def last_route(self) -> dict | None:
        return dict(self.route) if self.route else None

    # -- helpers
    @property
    def structured_calls(self) -> list[FakeCall]:
        return [c for c in self.calls if c.kind == "structured"]

    @property
    def text_calls(self) -> list[FakeCall]:
        return [c for c in self.calls if c.kind == "text"]

    def purposes(self) -> list[str]:
        return [c.purpose for c in self.calls]

    def _next(self, src: Any, is_fn: bool, purpose: str, text: str) -> Any:
        if is_fn:
            out = src(purpose, text)
        else:
            try:
                out = next(src)
            except StopIteration:
                raise LLMUnavailable("FakeLLM script exhausted") from None
            if callable(out):
                out = out(purpose, text)
        if isinstance(out, BaseException):
            raise out
        return out

    # -- LLMPort
    def complete_structured(self, *, instructions: str, text: str, schema: dict, purpose: str,
                            max_tokens: int | None = None, timeout: float | None = None) -> dict | None:
        self.calls.append(FakeCall("structured", purpose, text=text, instructions=instructions, schema=schema))
        out = self._next(self._script, self._script_callable, purpose, text)
        if isinstance(out, BaseModel):
            return out.model_dump(mode="json")
        if isinstance(out, str):          # convenience: a JSON string
            try:
                out = json.loads(out)
            except ValueError:
                return None
        return out

    def complete(self, *, messages: list[dict], purpose: str, max_tokens: int | None = None,
                 timeout: float | None = None) -> str:
        last = messages[-1]["content"] if messages else ""
        self.calls.append(FakeCall("text", purpose, text=last, messages=list(messages)))
        out = self._next(self._text, self._text_callable, purpose, last)
        return out if isinstance(out, str) else json.dumps(out)
