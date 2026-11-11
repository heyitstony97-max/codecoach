"""HTTP surface of the language runtime: `register(router, get_service)` adds two routes to a FastAPI router."""

from __future__ import annotations

from typing import Any, Callable

from codecoach import lang


def _payload(refresh: bool) -> dict:
    from codecoach.check import code_runner
    return {"sandbox": code_runner.sandbox_status(),
            "languages": [s.to_dict() for s in lang.statuses(refresh=refresh)]}


def register(router: Any, get_service: Callable[[], Any] | None = None) -> None:
    """GET /languages (cached statuses) and POST /languages/refresh (re-probe, up to ~60 s).

    `get_service` is accepted for the shared plugin signature; the language runtime needs no service."""

    @router.get("/languages")
    def list_languages() -> dict:
        return _payload(False)

    @router.post("/languages/refresh")
    def refresh_languages() -> dict:
        return _payload(True)
