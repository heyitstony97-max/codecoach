import json

import pytest
from surface_helpers import T0, make_service, oid, secrets_of

from codecoach.core.llm import FakeLLM
from codecoach.core.models import (AnswerResponse, CoursesResponse, DropsResponse, MapResponse, NextResponse,
                                ProgressResponse)
from codecoach.service import CodeCoachService, ServiceConfig, ServiceError, read_local_path


def test_courses_and_map():
    svc, store, cid = make_service()
    cs = svc.courses()
    assert isinstance(cs, CoursesResponse) and cs.courses[0].id == cid
    assert cs.courses[0].items_active == 24
    m = svc.map(cid)
    assert isinstance(m, MapResponse) and m.summary.objectives == 12
    assert m.units[0].lessons[0].objectives[0].items_active == 2


def test_not_covered_objective_has_label_and_zero_mastery():
    svc, store, cid = make_service(not_covered=(3,))
    objs = {o.id: o for u in svc.map(cid).units for l in u.lessons for o in l.objectives}
    assert objs[oid(3)].status_label and objs[oid(3)].mastery == 0
    assert svc.map(cid).summary.not_covered == 1


def test_unknown_ids_raise_404():
    svc, store, cid = make_service()
    for call in (lambda: svc.map("zz"), lambda: svc.next("zz"), lambda: svc.progress("zz"),
                 lambda: svc.drops("zz"), lambda: svc.timeline("zz"), lambda: svc.job("zz"),
                 lambda: svc.answer("zz", 0), lambda: svc.hint("zz"), lambda: svc.report("zz"),
                 lambda: svc.plot("zz"), lambda: svc.run("zz", "x"), lambda: svc.passage("zz")):
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.status == 404 and e.value.code


def test_next_item_is_public_and_answer_reveals():
    svc, store, cid = make_service()
    seen = None
    for _ in range(40):
        n = svc.next(cid)
        assert isinstance(n, NextResponse)
        if n.kind == "item":
            seen = n
            break
    assert seen is not None
    raw = json.dumps(seen.model_dump(mode="json"))
    item = store.get_item(seen.item.id)
    for s in secrets_of(item):
        assert s not in raw or s in item.prompt
    assert "correct_index" not in raw and "citations" not in raw
    a = svc.answer(item.id, item.payload.correct_index if item.format == "mc" else 1)
    assert isinstance(a, AnswerResponse) and a.correct is True
    assert a.citations and a.correct_answer
    assert svc.progress(cid).attempts == 1


def test_wrong_answer_and_progress_shape():
    svc, store, cid = make_service()
    it = store.list_items(course_id=cid)[0]
    wrong = 0 if it.payload.correct_index != 0 else 2
    a = svc.answer(it.id, wrong)
    assert a.correct is False
    p = svc.progress(cid)
    assert isinstance(p, ProgressResponse) and p.accuracy == 0.0 and len(p.recent_days) == 7


def test_answer_rejects_huge_and_retired():
    svc, store, cid = make_service()
    it = store.list_items(course_id=cid)[0]
    with pytest.raises(ServiceError) as e:
        svc.answer(it.id, "x" * 200_000)
    assert e.value.status == 413
    svc.report(it.id, "bad")
    with pytest.raises(ServiceError) as e:
        svc.answer(it.id, 0)
    assert e.value.status == 409


def test_one_in_flight_per_item():
    svc, store, cid = make_service()
    it = store.list_items(course_id=cid)[0]
    with svc._exclusive(it.id):
        with pytest.raises(ServiceError) as e:
            svc.answer(it.id, 0)
        assert e.value.code == "busy"
    svc.answer(it.id, 0)


def test_hint_ladder_and_report():
    svc, store, cid = make_service()
    it = store.list_items(course_id=cid)[0]
    h1 = svc.hint(it.id)
    assert h1.hints_used == 1
    assert svc.hint(it.id).hints_used == 2
    with pytest.raises(ServiceError):
        svc.hint(it.id, 7)
    r = svc.report(it.id, "wrong")
    assert r.status == "reported" and r.reports_total == 1
    assert store.get_item(it.id).status == "reported"


def test_drops_lists_every_gate():
    svc, store, cid = make_service()
    d = svc.drops(cid)
    assert isinstance(d, DropsResponse) and d.total == 0 and len(d.by_gate) >= 5


def test_timeline_empty_before_attempts_and_plot_rejects_non_math():
    svc, store, cid = make_service()
    assert svc.timeline(cid).events == []
    it = store.list_items(course_id=cid)[0]
    with pytest.raises(ServiceError) as e:
        svc.plot(it.id)
    assert e.value.status == 422


def test_run_rejects_non_code():
    svc, store, cid = make_service()
    it = store.list_items(course_id=cid)[0]
    with pytest.raises(ServiceError) as e:
        svc.run(it.id, "print(1)")
    assert e.value.code == "not_code"


def test_import_validates_before_work(tmp_path):
    svc, store, cid = make_service()
    with pytest.raises(ServiceError) as e:
        svc.start_import(title="", text="hello")
    assert e.value.status == 422
    with pytest.raises(ServiceError):
        svc.start_import(title="t", text="a", path="b")
    with pytest.raises(ServiceError) as e:
        svc.start_import(title="t", text="   ")
    assert e.value.code == "empty"
    with pytest.raises(ServiceError) as e:
        svc.start_import(title="t", text="x" * 2_000_001)
    assert e.value.status == 413


def test_import_failure_marks_job_error():
    svc, store, cid = make_service()
    jid = svc.start_import(title="Book", text="A paragraph. " * 40)
    j = svc.job(jid)
    assert j.status == "error" and j.error
    assert svc.events and svc.events[-1][0] == "job.progress"


def test_read_local_path(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# hi\ntext")
    assert read_local_path(str(f)).startswith("# hi")
    for bad in ("", "/nope/x.md", str(tmp_path / "a.exe"), "x.pdf"):
        with pytest.raises(ServiceError):
            read_local_path(bad)


def test_config_from_mapping_clamps():
    c = ServiceConfig.from_mapping({"default_mode": "weird", "bank_per_objective": "99",
                                    "allow_unsandboxed_code": "yes"}.get)
    assert (c.default_mode, c.bank_per_objective, c.allow_unsandboxed_code) == ("strict", 10, False)
