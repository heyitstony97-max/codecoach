"""Practice routes. `register(router, get_service)` adds the four drill routes of docs/V11-CONTRACT.md section 6.

Every route answers from stored state; a build runs in the background and a route never waits on the model.
"""

from __future__ import annotations

from typing import Any, Callable

from codecoach.practice.checkpoint import CheckpointAnswerRequest, CheckpointCreateRequest, checkpoint_engine_for
from codecoach.practice.engine import PracticeAnswerRequest, PracticeNextRequest, engine_for


def register(router, get_service: Callable[[], Any]) -> None:
    from fastapi.responses import JSONResponse

    from codecoach.service import ServiceError

    def call(fn: Callable[[Any], Any]):
        try:
            out = fn(engine_for(get_service()))
        except ServiceError as e:
            return JSONResponse(status_code=e.status, content=e.as_dict())
        except Exception:
            return JSONResponse(status_code=500, content={"detail": "CodeCoach hit an unexpected problem.",
                                                          "code": "internal"})
        return JSONResponse(content=out)

    @router.get("/practice")
    def practice_topics(language: str | None = None):
        return call(lambda e: e.topics(language))

    @router.post("/practice/next")
    def practice_next(body: PracticeNextRequest):
        return call(lambda e: e.next(body.language, body.topic))

    @router.get("/practice/drills/{drill_id}")
    def practice_drill(drill_id: str):
        return call(lambda e: e.view(drill_id))

    @router.post("/practice/drills/{drill_id}/answer")
    def practice_answer(drill_id: str, body: PracticeAnswerRequest):
        return call(lambda e: e.answer(drill_id, body.prediction))

    # ---- Test (checkpoint): section 6, second half

    def tcall(fn: Callable[[Any], Any], status: int = 200):
        try:
            out = fn(checkpoint_engine_for(get_service()))
        except ServiceError as e:
            return JSONResponse(status_code=e.status, content=e.as_dict())
        except Exception:
            return JSONResponse(status_code=500, content={"detail": "CodeCoach hit an unexpected problem.",
                                                          "code": "internal"})
        return JSONResponse(status_code=status, content=out)

    @router.post("/tests")
    def tests_create(body: CheckpointCreateRequest):
        return tcall(lambda e: e.create(body.language, body.size))

    @router.get("/tests")
    def tests_list():
        return tcall(lambda e: e.list())

    @router.get("/tests/{test_id}")
    def tests_get(test_id: str):
        return tcall(lambda e: e.get(test_id))

    @router.post("/tests/{test_id}/answer")
    def tests_answer(test_id: str, body: CheckpointAnswerRequest):
        return tcall(lambda e: e.answer(test_id, body.n, body.prediction))

    @router.post("/tests/{test_id}/finish")
    def tests_finish(test_id: str):
        return tcall(lambda e: e.finish(test_id))
