"""Checker and generator registries. Lanes add files and register at import; core never imports them.

Checker signature (every checker must accept `mode`):
    fn(item: Item, response: Any, *, mode: str = "submit") -> CheckResult
    mode "visible": run only visible tests (POST /items/{id}/run). mode "submit": the full grade.

Generator signature (keyword-only, a2a owns the real implementations):
    fn(*, objective: Objective, passages: list[Passage], llm: LLMPort, mode: str,
       rng: random.Random, now: datetime) -> GenResult
"""

from typing import Any, Callable

from pydantic import Field

from .models import Drop, Item, PerTestResult, _M


class NotRegistered(LookupError):
    pass


class CheckResult(_M):
    ok: bool
    score: float = Field(ge=0, le=1)
    detail: str = ""
    per_test: list[PerTestResult] | None = None
    undecided: bool = False       # checker could not decide (e.g. SymPy timeout); never counts as wrong


class GenResult(_M):
    items: list[Item] = Field(default_factory=list)
    drops: list[Drop] = Field(default_factory=list)


Checker = Callable[..., CheckResult]
Generator = Callable[..., GenResult]

_checkers: dict[str, Checker] = {}
_generators: dict[str, Generator] = {}


def register_checker(kind: str, fn: Checker) -> None:
    """Last registration for a kind wins (so reloads are harmless)."""
    _checkers[kind] = fn


def get_checker(kind: str) -> Checker:
    try:
        return _checkers[kind]
    except KeyError:
        raise NotRegistered(f"no checker registered for kind {kind!r}") from None


def register_generator(format: str, fn: Generator) -> None:
    _generators[format] = fn


def get_generator(format: str) -> Generator:
    try:
        return _generators[format]
    except KeyError:
        raise NotRegistered(f"no generator registered for format {format!r}") from None


def registered_checkers() -> list[str]:
    return sorted(_checkers)


def registered_generators() -> list[str]:
    return sorted(_generators)


def check(item: Item, response: Any, *, mode: str = "submit") -> CheckResult:
    """Convenience: dispatch on item.checker."""
    return get_checker(item.checker)(item, response, mode=mode)


def _clear_for_tests() -> None:
    _checkers.clear()
    _generators.clear()
