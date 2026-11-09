"""Subjects: migration backfill, inference order, fail-safe import, PATCH, command, tool arg."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from surface_helpers import make_service

ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / "tests" / "importer"
if str(IMPORTER) not in sys.path:
    sys.path.insert(0, str(IMPORTER))

from importer_helpers import HISTORY, OUTLINE, new_store, scripted  # noqa: E402

from codecoach import tools  # noqa: E402
from codecoach.core.llm import FakeLLM, LLMUnavailable  # noqa: E402
from codecoach.core.models import Course  # noqa: E402
from codecoach.core.store import MIGRATIONS, Store, _split_sql  # noqa: E402
from codecoach.importer import SUBJECTS, import_materials, import_outline, import_with_syllabus, infer_subject  # noqa: E402
from codecoach.service import ServiceError  # noqa: E402


# ------------------------------------------------------------------ migration

def _v2_db(path):
    c = sqlite3.connect(path)
    for i in range(2):          # the schema as it was before subjects
        for st in _split_sql(MIGRATIONS[i]):
            c.execute(st)
    c.execute("PRAGMA user_version=2")
    c.execute("INSERT INTO courses(id,title,mode,mode_reason,created_at,code_language) "
              "VALUES('c-basics','C Basics','strict','','2026-01-01T00:00:00+00:00','c')")
    c.execute("INSERT INTO courses(id,title,mode,mode_reason,created_at) "
              "VALUES('golden-spike','Golden Spike','strict','','2026-01-02T00:00:00+00:00')")
    c.commit()
    c.close()


def test_migration_backfills_programming_and_is_idempotent(tmp_path):
    p = tmp_path / "old.db"
    _v2_db(p)
    s = Store(p)
    assert s.schema_version() == len(MIGRATIONS)
    assert s.get_course("c-basics").subject == "Programming"
    assert s.get_course("golden-spike").subject is None
    # a hand-set subject survives re-opening: the migration does not run twice
    s.set_course_subject("golden-spike", "History")
    s.set_course_subject("c-basics", "Other")
    s.close()
    s2 = Store(p)
    assert s2.schema_version() == len(MIGRATIONS)
    assert s2.get_course("golden-spike").subject == "History"
    assert s2.get_course("c-basics").subject == "Other"
    s2.close()


def test_new_columns_round_trip_and_old_rows_load_as_none(tmp_path):
    s = Store(tmp_path / "n.db")
    s.put_course(Course(id="a", title="A"))
    assert s.get_course("a").subject is None
    s.put_course(Course(id="b", title="B", subject="Math"))
    assert s.get_course("b").subject == "Math"


def test_set_course_subject_trims_caps_and_clears(tmp_path):
    s = Store(tmp_path / "n.db")
    s.put_course(Course(id="a", title="A"))
    assert s.set_course_subject("a", "  Medieval   Europe ") == "Medieval Europe"
    assert s.get_course("a").subject == "Medieval Europe"
    assert s.set_course_subject("a", "x" * 60) == "x" * 40
    assert len(s.get_course("a").subject) == 40
    assert s.set_course_subject("a", "   ") is None and s.get_course("a").subject is None
    assert s.set_course_subject("a", None) is None


# ------------------------------------------------------------------ inference order

def _pick(answer):
    return FakeLLM(lambda purpose, text: {"subject": answer})


def test_explicit_beats_code_language_beats_llm_beats_none():
    llm = _pick("History")
    assert infer_subject(llm, title="t", text="x", explicit="Cooking", code_language="c") == "Cooking"
    assert llm.calls == []
    assert infer_subject(llm, title="t", text="x", code_language="c") == "Programming"
    assert llm.calls == []
    assert infer_subject(llm, title="t", text="x") == "History"
    assert len(llm.calls) == 1 and llm.calls[0].purpose == "codecoach.subject"
    assert infer_subject(None, title="t", text="x") is None


def test_llm_sees_title_and_only_first_1500_chars():
    llm = _pick("Science")
    infer_subject(llm, title="Rocks", text="A" * 1500 + "TAIL")
    t = llm.calls[0].text
    assert "Rocks" in t and "TAIL" not in t
    assert llm.calls[0].schema is not None


@pytest.mark.parametrize("answer", ["Cooking", "history is fun", "", "Math, Science", 5])
def test_off_list_answer_gives_none(answer):
    assert infer_subject(_pick(answer), title="t", text="x") is None


def test_on_list_answer_is_normalised():
    assert infer_subject(_pick(" history. "), title="t", text="x") == "History"
    assert infer_subject(_pick("language & writing"), title="t", text="x") == "Language & Writing"
    assert set(SUBJECTS) == {"History", "Programming", "Math", "Science", "Language & Writing", "Business", "Other"}


@pytest.mark.parametrize("failure", [LLMUnavailable("down"), TimeoutError("slow"), RuntimeError("boom"), None])
def test_llm_failure_gives_none(failure):
    llm = FakeLLM(lambda purpose, text: failure)
    assert infer_subject(llm, title="t", text="x") is None


# ------------------------------------------------------------------ imports stay fail-safe

def _llm_with_subject(answer):
    base = scripted()._script

    def fn(purpose, text):
        if purpose == "codecoach.subject":
            if isinstance(answer, BaseException):
                raise answer
            return {"subject": answer}
        return base(purpose, text)
    return FakeLLM(fn)


def test_import_materials_uses_the_llm_pick():
    store = new_store()
    cid = import_materials(store, _llm_with_subject("History"), title="Kingdom", text=HISTORY, doc_name="h")
    assert store.get_course(cid).subject == "History"


def test_import_succeeds_when_subject_call_errors():
    store = new_store()
    cid = import_materials(store, _llm_with_subject(LLMUnavailable("down")), title="Kingdom", text=HISTORY, doc_name="h")
    assert store.get_course(cid) is not None and store.get_course(cid).subject is None
    assert store.list_objectives(cid)


def test_import_off_list_answer_is_none():
    store = new_store()
    cid = import_materials(store, _llm_with_subject("Underwater basket weaving"), title="K", text=HISTORY, doc_name="h")
    assert store.get_course(cid).subject is None


def test_explicit_subject_threads_through_all_three_importers():
    llm = _llm_with_subject("History")
    store = new_store()
    a = import_materials(store, llm, title="One", text=HISTORY, doc_name="h", subject="  Local History ")
    b = import_outline(store, llm, title="Two", text=OUTLINE, subject="Cooking")
    c = import_with_syllabus(store, llm, title="Three", materials_text=HISTORY, doc_name="h3",
                             syllabus_text="Describe the kingdom", subject="Geography")
    assert [store.get_course(x).subject for x in (a, b, c)] == ["Local History", "Cooking", "Geography"]
    assert not any(c.purpose == "codecoach.subject" for c in llm.calls)


def test_code_language_course_is_programming_without_a_llm_call():
    llm = _llm_with_subject("History")
    store = new_store()
    cid = import_outline(store, llm, title="C Basics", text=OUTLINE, code_language="c")
    assert store.get_course(cid).subject == "Programming"
    assert not any(c.purpose == "codecoach.subject" for c in llm.calls)


def test_service_import_threads_subject_and_too_long_is_422():
    svc, store, _ = make_service(llm=_llm_with_subject("Math"))
    job = svc.start_import(title="Kingdom", text=HISTORY, subject="Local History")
    assert store.get_course(svc.job(job).course_id).subject == "Local History"
    job = svc.start_import(title="Kingdom2", text=HISTORY)
    assert store.get_course(svc.job(job).course_id).subject == "Math"
    with pytest.raises(ServiceError) as e:
        svc.start_import(title="Kingdom3", text=HISTORY, subject="x" * 41)
    assert e.value.status == 422


# ------------------------------------------------------------------ PATCH /courses/{id}

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

P = "/api/plugins/codecoach"


@pytest.fixture()
def env():
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_subjects", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    svc, store, cid = make_service()
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix=P)
    return TestClient(app), svc, store, cid


def test_patch_200_returns_the_updated_course_and_lists_show_it(env):
    c, svc, store, cid = env
    r = c.patch(f"{P}/courses/{cid}", json={"subject": "  History "})
    assert r.status_code == 200
    assert r.json()["id"] == cid and r.json()["subject"] == "History"
    assert store.get_course(cid).subject == "History"
    listed = c.get(f"{P}/courses").json()["courses"]
    assert [x["subject"] for x in listed if x["id"] == cid] == ["History"]
    r = c.patch(f"{P}/courses/{cid}", json={"subject": ""})
    assert r.status_code == 200 and r.json()["subject"] is None


def test_patch_404_and_422(env):
    c, svc, store, cid = env
    assert c.patch(f"{P}/courses/nope", json={"subject": "Math"}).status_code == 404
    r = c.patch(f"{P}/courses/{cid}", json={"subject": "x" * 41})
    assert r.status_code == 422 and store.get_course(cid).subject is None
    assert c.patch(f"{P}/courses/{cid}", json={"subject": "x" * 40}).status_code == 200
    assert c.patch(f"{P}/courses/{cid}", json={"junk": 1}).status_code == 422


def test_rest_import_accepts_subject(env):
    c, svc, store, cid = env
    svc.llm = _llm_with_subject("Math")
    r = c.post(f"{P}/courses/import", json={"title": "Kingdom", "text": HISTORY, "subject": "Local History"})
    assert r.status_code == 200
    assert store.get_course(svc.job(r.json()["job_id"]).course_id).subject == "Local History"
    assert c.post(f"{P}/courses/import", json={"title": "K", "text": HISTORY, "subject": "x" * 41}).status_code == 422


# ------------------------------------------------------------------ command and tool

def test_subject_command():
    svc, store, cid = make_service()
    cmd = tools.make_command(lambda: svc)
    out = cmd(f"subject {cid} Local   History")
    assert "Local History" in out and store.get_course(cid).subject == "Local History"
    assert "Uncategorized" in cmd(f"subject {cid}") and store.get_course(cid).subject is None
    assert "Usage" in cmd("subject")
    assert "No course" in cmd("subject nope Math")
    assert "at most 40" in cmd(f"subject {cid} {'x' * 41}")
    assert "/codecoach subject" in tools.HELP


def test_import_command_and_tool_pass_subject():
    svc, store, cid = make_service(llm=_llm_with_subject("Math"))
    cmd = tools.make_command(lambda: svc)
    out = cmd('import Notes A --subject "Local History" | ' + HISTORY)
    assert "Import started" in out
    handlers = tools.make_handlers(lambda: svc)
    r = json.loads(handlers["codecoach_import"]({"title": "Tooled", "text": HISTORY, "subject": "Cooking"}))
    assert r["ok"] is True
    by_title = {c.title: c.subject for c in store.list_courses()}
    assert by_title["Tooled"] == "Cooking"
    assert "Local History" in by_title.values()
    bad = json.loads(handlers["codecoach_import"]({"title": "Bad", "text": HISTORY, "subject": "x" * 41}))
    assert bad["ok"] is False
    assert "subject" in tools.SCHEMAS["codecoach_import"]["parameters"]["properties"]


def test_courses_text_mentions_subject():
    svc, store, cid = make_service()
    store.set_course_subject(cid, "History")
    assert "History" in tools.op_courses(svc)["text"]


def test_subject_call_leaves_room_for_a_reasoning_model():
    """E2E finding: max_tokens=60 let a reasoning model (Qwen) spend the whole budget thinking, so every
    import ended Uncategorized. The budget must leave room to think and a slow local model must get time."""
    seen = {}

    class Spy(FakeLLM):
        def complete_structured(self, **kw):
            seen.update(kw)
            return super().complete_structured(**kw)

    llm = Spy([{"subject": "History"}])
    assert infer_subject(llm, title="Rome", text="The Roman Republic fell in 27 BC.") == "History"
    assert seen["max_tokens"] >= 300
    assert seen["timeout"] >= 120
