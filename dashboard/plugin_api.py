"""CodeCoach REST routes, mounted by Hermes at /api/plugins/codecoach/. Runs in the gateway process.

Thin: every route calls CodeCoachService (codecoach/service.py). LLM access is PluginLlm through
codecoach.hermes_glue. Handlers are plain `def` so FastAPI runs them in worker threads and never
blocks the gateway's event loop.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Callable

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from fastapi import APIRouter, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from codecoach.core.models import (AudioPayload, AnswerRequest, HintRequest, ImportRequest, ImportResponse,  # noqa: E402
                                ReportRequest, RunRequest, SubjectPatch)
from codecoach.service import CodeCoachService, ServiceError  # noqa: E402

MAX_BODY_BYTES = 2_500_000   # a 2M-character import plus JSON overhead

class _CodedRoute(APIRoute):
    """Every failure leaves as {"detail", "code"}: oversize bodies (413) and bad shapes (422) included."""

    def get_route_handler(self):
        inner = super().get_route_handler()

        async def handler(request: Request):
            try:
                n = int(request.headers.get("content-length") or 0)
            except ValueError:
                n = 0
            if n > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={"detail": "That request is too large.", "code": "too_large"})
            try:
                return await inner(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "That request was not understood.",
                                                              "code": "bad_request"})
        return handler


router = APIRouter(route_class=_CodedRoute)
_service: CodeCoachService | None = None
_factory: Callable[[], CodeCoachService] | None = None


def set_service(svc: CodeCoachService | None, factory: Callable[[], CodeCoachService] | None = None) -> None:
    """Tests (and the glue) install the service here."""
    global _service, _factory
    _service, _factory = svc, factory


def get_service() -> CodeCoachService:
    global _service
    if _service is None:
        if _factory is not None:
            _service = _factory()
        else:
            from codecoach import hermes_glue
            _service = hermes_glue.build_service_for_gateway()
    return _service


def _fail(e: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=e.status, content=e.as_dict())


def _call(fn: Callable[[CodeCoachService], Any]):
    try:
        out = fn(get_service())
    except ServiceError as e:
        return _fail(e)
    except Exception:
        return JSONResponse(status_code=500, content={"detail": "CodeCoach hit an unexpected problem.", "code": "internal"})
    return JSONResponse(content=_jsonable(out))


def _jsonable(model: Any) -> Any:
    if isinstance(model, list):
        return [_jsonable(m) for m in model]
    dump = getattr(model, "model_dump", None)
    return dump(mode="json") if dump else model


def _too_big(request: Request) -> JSONResponse | None:
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        n = 0
    if n > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "That request is too large.", "code": "too_large"})
    return None


@router.get("/courses")
def list_courses():
    return _call(lambda s: s.courses())


@router.post("/courses/import")
def import_course(body: ImportRequest, request: Request):
    big = _too_big(request)
    if big:
        return big
    return _call(lambda s: ImportResponse(job_id=s.start_import(title=body.title, text=body.text, path=body.path,
                                                                mode=body.mode, outline=body.outline,
                                                                syllabus_text=body.syllabus_text,
                                                                syllabus_path=body.syllabus_path,
                                                                subject=body.subject)))


@router.patch("/courses/{course_id}")
def patch_course(course_id: str, body: SubjectPatch):
    return _call(lambda s: s.set_subject(course_id, body.subject))


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return _call(lambda s: s.job(job_id))


@router.get("/courses/{course_id}/map")
def course_map(course_id: str):
    return _call(lambda s: s.map(course_id))


@router.get("/courses/{course_id}/next")
def course_next(course_id: str, objective_id: str | None = None, mistakes_upto: int | None = None):
    return _call(lambda s: s.next(course_id, objective_id, mistakes_upto))


@router.get("/courses/{course_id}/mistakes")
def course_mistakes(course_id: str):
    return _call(lambda s: s.mistakes(course_id))


@router.post("/courses/{course_id}/export")
def course_export(course_id: str):
    return _call(lambda s: s.export_study_guide(course_id))


@router.post("/items/{item_id}/answer")
def item_answer(item_id: str, body: AnswerRequest, request: Request):
    big = _too_big(request)
    if big:
        return big
    return _call(lambda s: s.answer(item_id, body.response))


@router.post("/items/{item_id}/run")
def item_run(item_id: str, body: RunRequest, request: Request):
    big = _too_big(request)
    if big:
        return big
    return _call(lambda s: s.run(item_id, body.code))


@router.post("/items/{item_id}/hint")
def item_hint(item_id: str, body: HintRequest):
    return _call(lambda s: s.hint(item_id, body.level))


@router.post("/items/{item_id}/report")
def item_report(item_id: str, body: ReportRequest):
    return _call(lambda s: s.report(item_id, body.reason))


@router.get("/courses/{course_id}/progress")
def course_progress(course_id: str):
    return _call(lambda s: s.progress(course_id))


@router.get("/courses/{course_id}/drops")
def course_drops(course_id: str):
    return _call(lambda s: s.drops(course_id))


@router.get("/courses/{course_id}/timeline")
def course_timeline(course_id: str):
    return _call(lambda s: s.timeline(course_id))


@router.get("/items/{item_id}/plot")
def item_plot(item_id: str):
    return _call(lambda s: s.plot(item_id))


@router.get("/courses/{course_id}/materials")
def course_materials(course_id: str):
    return _call(lambda s: s.materials(course_id))


@router.get("/courses/{course_id}/sources")
def course_sources(course_id: str):
    return _call(lambda s: s.sources(course_id))


@router.get("/courses/{course_id}/sources/{doc_id}")
def course_source(course_id: str, doc_id: str):
    return _call(lambda s: s.source(course_id, doc_id))


@router.get("/courses/{course_id}/sources/{doc_id}/guide")
def course_source_guide(course_id: str, doc_id: str):
    return _call(lambda s: s.source_guide(course_id, doc_id))


@router.post("/items/{item_id}/release")
def item_release(item_id: str):
    return _call(lambda s: s.release(item_id))


@router.get("/courses/{course_id}/passages/{passage_id:path}/audio")
def passage_audio(course_id: str, passage_id: str, encoding: str | None = None):
    """The passage as audio. Default: the audio bytes. ?encoding=base64: JSON the desktop can read, because
    Hermes Desktop's ctx.rest decodes every body as UTF-8 JSON and cannot carry binary."""
    try:
        clip = get_service().passage_audio(course_id, passage_id)
    except ServiceError as e:
        return _fail(e)
    except Exception:
        return JSONResponse(status_code=500, content={"detail": "CodeCoach hit an unexpected problem.", "code": "internal"})
    if encoding == "base64":
        try:
            raw = clip.path.read_bytes()
        except OSError:
            return JSONResponse(status_code=503, content={"detail": "Hermes could not read this aloud.",
                                                          "code": "tts_unavailable"})
        return JSONResponse(content=AudioPayload(passage_id=passage_id, mime=clip.mime, voice=clip.voice, cached=clip.cached,
                                                 size=clip.size, data_base64=base64.b64encode(raw).decode("ascii")
                                                 ).model_dump(mode="json"))
    return FileResponse(clip.path, media_type=clip.mime, headers={"X-CodeCoach-Voice": clip.voice,
                                                                  "X-CodeCoach-Cached": "1" if clip.cached else "0"})


@router.get("/passages/{passage_id:path}")
def get_passage(passage_id: str):
    return _call(lambda s: s.passage(passage_id))


# ------------------------------------------------------------------ Ask your materials (codecoach/ask; S2a)

from codecoach.ask import AskRequest  # noqa: E402


@router.post("/courses/{course_id}/ask")
def course_ask(course_id: str, body: AskRequest):
    return _call(lambda s: s.ask_start(course_id, body.question, body.open_item_id))


@router.get("/courses/{course_id}/ask/{job_id}")
def course_ask_job(course_id: str, job_id: str):
    return _call(lambda s: s.ask_job(course_id, job_id))


@router.get("/courses/{course_id}/asks")
def course_asks(course_id: str):
    return _call(lambda s: s.asks(course_id))


# ------------------------------------------------------------------ Learn mode (codecoach/learn.py; LEARN-CONTRACT.md)

from codecoach.learn import LearnCheckRequest, LearnStepRequest  # noqa: E402


@router.get("/courses/{course_id}/learn")
def course_learn(course_id: str):
    return _call(lambda s: s.learn_overview(course_id))


@router.post("/courses/{course_id}/learn/{objective_id}/check")
def course_learn_check(course_id: str, objective_id: str, body: LearnCheckRequest, request: Request):
    big = _too_big(request)
    if big:
        return big
    return _call(lambda s: s.learn_check(course_id, objective_id, body.item_id, body.response))


@router.post("/courses/{course_id}/learn/{objective_id}/step")
def course_learn_step(course_id: str, objective_id: str, body: LearnStepRequest):
    return _call(lambda s: s.learn_step(course_id, objective_id, body.index))


@router.get("/courses/{course_id}/learn/{objective_id}")
def course_learn_topic(course_id: str, objective_id: str):
    return _call(lambda s: s.learn_topic(course_id, objective_id))


# ------------------------------------------------------------------ Project mode (codecoach/project.py; PROJECT-CONTRACT.md)

from codecoach.project import ProjectCodeRequest, ProjectCreateRequest  # noqa: E402


@router.get("/projects")
def projects_list():
    return _call(lambda s: s.project_list())


@router.post("/projects")
def projects_create(body: ProjectCreateRequest):
    return _call(lambda s: s.project_create(body.idea, body.language))


@router.get("/projects/{project_id}")
def projects_get(project_id: str):
    return _call(lambda s: s.project_get(project_id))


@router.delete("/projects/{project_id}")
def projects_delete(project_id: str, confirm: bool = False):
    if not confirm:
        return JSONResponse(status_code=422, content={"detail": "Add ?confirm=true to delete a project.",
                                                      "code": "confirm_required"})
    return _call(lambda s: s.project_delete(project_id))


@router.get("/projects/{project_id}/steps/{index}")
def projects_step(project_id: str, index: int):
    return _call(lambda s: s.project_step(project_id, index))


@router.post("/projects/{project_id}/steps/{index}/run")
def projects_step_run(project_id: str, index: int, body: ProjectCodeRequest, request: Request):
    big = _too_big(request)
    if big:
        return big
    return _call(lambda s: s.project_run(project_id, index, body.code))


@router.post("/projects/{project_id}/steps/{index}/check")
def projects_step_check(project_id: str, index: int, body: ProjectCodeRequest, request: Request):
    big = _too_big(request)
    if big:
        return big
    return _call(lambda s: s.project_check(project_id, index, body.code))


@router.post("/projects/{project_id}/steps/{index}/hint")
def projects_step_hint(project_id: str, index: int):
    return _call(lambda s: s.project_hint(project_id, index))


@router.post("/projects/{project_id}/steps/{index}/solution")
def projects_step_solution(project_id: str, index: int):
    return _call(lambda s: s.project_solution(project_id, index))


@router.post("/projects/{project_id}/steps/{index}/retry")
def projects_step_retry(project_id: str, index: int):
    return _call(lambda s: s.project_retry(project_id, index))


# ------------------------------------------------------------------ v1.1: languages, model fit, practice, tests
# Each package adds its own routes and answers errors as {"detail", "code"} like _call above.

from codecoach import __version__ as _VERSION  # noqa: E402
from codecoach.fit import api as _fit_api  # noqa: E402
from codecoach.lang import api as _lang_api  # noqa: E402
from codecoach.practice import api as _practice_api  # noqa: E402

_lang_api.register(router, get_service)
_fit_api.register(router, get_service)
_practice_api.register(router, get_service)


@router.get("/meta")
def meta():
    """What the screen may show. `notes` is the hidden v1.0 course-from-your-notes mode (setting show_notes)."""
    return _call(lambda s: {"version": _VERSION, "features": {"notes": bool(s.cfg.show_notes)}})
