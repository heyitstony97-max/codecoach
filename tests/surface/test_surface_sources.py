"""Sources: the two routes, old (reconstructed) and new (stored) courses, ownership, and doc_id on every quote."""

import importlib.util
from pathlib import Path

import pytest
from surface_helpers import FakeLLM, make_service, new_store, oid

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.core.models import Doc, doc_of  # noqa: E402
from codecoach.core.passages import normalize_newlines  # noqa: E402
from codecoach.importer import import_materials  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HISTORY_MD = (ROOT / "demo" / "materials" / "history.md").read_text(encoding="utf-8")
P = "/api/plugins/codecoach"


def client_for(svc):
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_src", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix=P)
    return TestClient(app)


def imported_service():
    """A course really imported (so it carries stored text and spans) next to the fixture course."""
    sys_path = ROOT / "tests" / "importer"
    import sys
    if str(sys_path) not in sys.path:
        sys.path.insert(0, str(sys_path))
    from importer_helpers import scripted
    store = new_store()
    cid = import_materials(store, scripted(), title="History", text=HISTORY_MD.replace("\n", "\r\n"), doc_name="history.md")
    svc = CodeCoachService(store, FakeLLM(lambda p, t: None), ServiceConfig(), background=False, auto_bank=False)
    return svc, store, cid


def test_stored_text_is_served_verbatim_with_spans():
    svc, store, cid = imported_service()
    docs = svc.sources(cid)
    assert len(docs) == 1 and docs[0].has_text and docs[0].chars == len(normalize_newlines(HISTORY_MD))
    v = svc.source(cid, docs[0].doc_id)
    assert v.reconstructed is False and v.text == normalize_newlines(HISTORY_MD)
    assert len(v.passages) == docs[0].passages
    for sp in v.passages:
        p = store.get_passage(sp.id)
        assert " ".join(v.text[sp.start:sp.end].split()) == " ".join(p.text.split())
        assert sp.location == p.location


def test_old_course_is_rebuilt_from_its_passages():
    svc, store, cid = make_service()          # fixture course: passages without text or spans
    d = svc.sources(cid)[0]
    assert d.has_text is False and d.passages >= 1 and d.chars > 0
    v = svc.source(cid, d.doc_id)
    assert v.reconstructed is True and len(v.text) == d.chars
    last = 0
    for sp in v.passages:
        p = store.get_passage(sp.id)
        assert v.text[sp.start:sp.end] == p.text and sp.start >= last
        last = sp.end
    assert "\n\n" in v.text and v.passages[0].location.rsplit(", para", 1)[0] in v.text


def test_partial_spans_count_as_old():
    svc, store, cid = imported_service()
    doc = svc.sources(cid)[0]
    with store._tx() as c:
        c.execute("UPDATE passages SET start=NULL WHERE seq=(SELECT MIN(seq) FROM passages)")
    v = svc.source(cid, doc.doc_id)
    assert v.reconstructed is True and svc.sources(cid)[0].has_text is False


def test_routes_shapes_and_errors():
    svc, store, cid = imported_service()
    c = client_for(svc)
    r = c.get(f"{P}/courses/{cid}/sources")
    assert r.status_code == 200
    row = r.json()[0]
    assert set(row) == {"doc_id", "name", "kind", "chars", "passages", "has_text", "note"}
    assert row["note"] is None
    r = c.get(f"{P}/courses/{cid}/sources/{row['doc_id']}")
    body = r.json()
    assert set(body) == {"doc_id", "name", "kind", "text", "reconstructed", "passages"}
    assert set(body["passages"][0]) == {"id", "location", "start", "end"}
    # a doc of another course, an unknown doc, an unknown course: the existing ServiceError shape
    store.put_doc(Doc(id="elsewhere", course_id="not-this-one", name="x", kind="text"))
    for url in (f"{P}/courses/{cid}/sources/elsewhere", f"{P}/courses/{cid}/sources/nope",
                f"{P}/courses/zz/sources/{row['doc_id']}"):
        r = c.get(url)
        assert r.status_code == 404 and set(r.json()) == {"detail", "code"}
    assert c.get(f"{P}/courses/{cid}/sources/elsewhere").json()["code"] == "unknown_doc"
    assert c.get(f"{P}/courses/zz/sources").status_code == 404


def test_every_quote_bearing_payload_names_its_doc():
    svc, store, cid = make_service()
    # practice feedback
    n = next(x for x in (svc.next(cid) for _ in range(40)) if x.kind == "item")
    item = store.get_item(n.item.id)
    a = svc.answer(item.id, item.payload.correct_index if item.format == "mc" else 1)
    assert a.citations and all(c.doc_id == doc_of(c.passage_id) for c in a.citations)
    # lesson
    lv = svc._lesson(svc._course(cid), oid(1))
    assert lv.passages and all(p.doc_id == doc_of(p.passage_id) for p in lv.passages)
    # materials
    m = svc.materials(cid)
    assert m.sections and all(p.doc_id == doc_of(p.id) for s in m.sections for p in s.passages)
    # topic panel (map objectives)
    objs = [o for u in svc.map(cid).units for l in u.lessons for o in l.objectives if o.passage_ids]
    assert objs and all([s.passage_id for s in o.sources] == list(dict.fromkeys(o.passage_ids)) and
                        all(s.doc_id == doc_of(s.passage_id) for s in o.sources) for o in objs)
    # passage
    pr = svc.passage(objs[0].passage_ids[0])
    assert pr.passage.doc_id == doc_of(pr.passage.id)
    # hint
    h = svc.hint(item.id, 2)
    assert h.hint.passage_id is None or h.hint.doc_id == doc_of(h.hint.passage_id)
