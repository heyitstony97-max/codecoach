"""Fit routes (V11-CONTRACT section 5). The orchestrator wires `register(router, get_service)` at merge."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from codecoach.fit.engine import engine_for, language_table


def register(router: APIRouter, get_service: Callable[[], Any]) -> None:
    from codecoach.service import ServiceError

    def run(fn: Callable[[Any], Any], status: int = 200):
        try:
            out = fn(engine_for(get_service()))
        except ServiceError as e:
            return JSONResponse(status_code=e.status, content=e.as_dict())
        except Exception:
            return JSONResponse(status_code=500, content={"detail": "CodeCoach hit an unexpected problem.",
                                                          "code": "internal"})
        return JSONResponse(status_code=status, content=out)

    def known(language: str) -> None:
        if language not in language_table():
            raise ServiceError(422, "bad_language", "That language is not set up on this machine yet.")

    @router.get("/fit")
    def fit_all():
        return run(lambda e: e.overview())

    @router.post("/fit/{language}")
    def fit_start(language: str):
        return run(lambda e: e.start(language), 202)

    @router.get("/fit/{language}")
    def fit_one(language: str):
        def one(e):
            known(language)
            return e.view(language)
        return run(one)
