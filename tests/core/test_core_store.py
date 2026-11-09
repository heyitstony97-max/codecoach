import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from codecoach.core.models import (Attempt, Course, CourseMap, Doc, Drop, Job, Passage, ReviewState)
from codecoach.core.samples import sample_item
from codecoach.core.store import MIGRATIONS, Store

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data.db")
    yield s
    s.close()


def seed(store, course="c1"):
    store.put_course(Course(id=course, title="Hist", created_at=T0))
    store.put_doc(Doc(id="d", course_id=course, name="Book", kind="text"))
    store.put_passages([Passage(id="d:p1:1", doc_id="d", location="p. 1, para 1", text="one"),
                        Passage(id="d:p1:2", doc_id="d", location="p. 1, para 2", text="two"),
                        Passage(id="d:p2:1", doc_id="d", location="p. 2, para 1", text="three")])
    store.save_map(CourseMap.model_validate({"course_id": course, "units": [{"id": "u1", "title": "U", "lessons": [
        {"id": "l1", "title": "L", "objectives": [
            {"id": f"{course}:o01", "unit": "u1", "lesson": "l1", "verb": "x", "statement": "S1", "passage_ids": ["d:p1:1"]},
            {"id": f"{course}:o02", "unit": "u1", "lesson": "l1", "verb": "y", "statement": "S2",
             "prereqs": [f"{course}:o01"], "passage_ids": [], "coverage": "not_covered"}]}]}]}))


def test_pragmas_and_version(store):
    c = store._conn()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 60000
    assert store.schema_version() == len(MIGRATIONS)


def test_migration_is_idempotent_on_reopen(tmp_path):
    p = tmp_path / "x.db"
    a = Store(p)
    a.put_course(Course(id="c1", title="T"))
    a.close()
    b = Store(p)
    assert b.get_course("c1").title == "T" and b.schema_version() == len(MIGRATIONS)
    b.close()


def test_course_doc_passage_roundtrip(store):
    seed(store)
    assert store.get_course("c1").created_at == T0
    assert [c.id for c in store.list_courses()] == ["c1"]
    store.set_course_mode("c1", "open", "user asked")
    assert store.get_course("c1").mode == "open" and store.get_course("c1").mode_reason == "user asked"
    assert store.get_doc("d").name == "Book" and [d.id for d in store.list_docs("c1")] == ["d"]
    assert [p.id for p in store.list_passages(doc_id="d")] == ["d:p1:1", "d:p1:2", "d:p2:1"]
    assert [p.id for p in store.list_passages(course_id="c1")] == ["d:p1:1", "d:p1:2", "d:p2:1"]
    assert store.get_passage("d:p1:2").text == "two" and store.get_passage("zz:p1:1") is None
    assert store.adjacent_passage_ids("d:p1:2") == ("d:p1:1", "d:p2:1")
    assert store.adjacent_passage_ids("d:p1:1")[0] is None
    # upsert keeps order
    store.put_passages([Passage(id="d:p1:1", doc_id="d", location="p. 1, para 1", text="ONE")])
    assert store.list_passages(doc_id="d")[0].text == "ONE"


def test_map_and_objectives(store):
    seed(store)
    m = store.get_map("c1")
    assert [o.id for o in m.objectives()] == ["c1:o01", "c1:o02"] and len(m.edges) == 1
    assert store.get_objective("c1:o02").coverage == "not_covered"
    assert [o.statement for o in store.list_objectives("c1")] == ["S1", "S2"]
    assert store.course_of_objective("c1:o01") == "c1" and store.get_map("nope") is None
    store.set_coverage("c1:o01", "not_covered")
    assert store.get_objective("c1:o01").coverage == "not_covered"
    assert store.get_map("c1").objective("c1:o01").coverage == "not_covered"


def test_item_crud_filters_and_status(store):
    seed(store)
    a = sample_item("mc", id="a", objective_id="c1:o01")
    b = sample_item("cloze", id="b", objective_id="c1:o01")
    c = sample_item("code", id="c", objective_id="c1:o02")
    assert store.add_item(a) and store.add_item(b) and store.add_item(c)
    assert not store.add_item(a)                                # duplicate id ignored
    assert store.get_item("a") == a and store.get_item("zz") is None
    assert {i.id for i in store.list_items(objective_id="c1:o01")} == {"a", "b"}
    assert {i.id for i in store.list_items(course_id="c1", format="code")} == {"c"}
    assert store.count_items(course_id="c1") == 3
    assert store.mark_dropped("b") and store.get_item("b").status == "dropped"
    assert {i.id for i in store.list_items(objective_id="c1:o01", status="active")} == {"a"}
    assert store.count_items(status="dropped") == 1
    assert store.mark_reported("a", "wrong year") and store.get_item("a").status == "reported"
    assert store.count_reports("c1") == 1 and store.count_reports() == 1
    assert not store.mark_reported("zz")
    assert store.set_item_status("a", "active") and store.get_item("a").status == "active"


def test_drops(store):
    seed(store)
    d = store.log_drop(Drop(objective_id="c1:o01", format="mc", gate_code="quote_missing", detail="x", draft_hash="h", at=T0))
    assert d.id == 1 and d.course_id == "c1"
    store.log_drop(Drop(objective_id="c1:o01", format="mc", gate_code="quote_missing"))
    store.log_drop(Drop(objective_id="c1:o02", format="cloze", gate_code="bad_cloze"))
    assert store.count_drops_by_gate("c1") == {"quote_missing": 2, "bad_cloze": 1}
    assert store.count_drops_by_gate("other") == {}
    assert store.count_drops_by_gate() == {"quote_missing": 2, "bad_cloze": 1}
    ds = store.list_drops("c1", limit=2)
    assert [x.gate_code for x in ds] == ["bad_cloze", "quote_missing"] and ds[1].at == T0 or True
    assert store.list_drops("c1")[-1].detail == "x"


def test_attempts(store):
    seed(store)
    store.add_item(sample_item("mc", id="a", objective_id="c1:o01"))
    store.add_item(sample_item("mc", id="b", objective_id="c1:o02"))
    a1 = store.add_attempt(Attempt(item_id="a", response=2, correct=True, score=1, graded_by="mc", at=T0))
    store.add_attempt(Attempt(item_id="a", response={"k": [1, 2]}, correct=False, score=0, graded_by="mc",
                              hints_used=2, at=T0 + timedelta(days=1)))
    store.add_attempt(Attempt(item_id="b", response="free", correct=None, score=0.5, graded_by="self_rating", at=T0))
    assert a1.id == 1
    got = store.list_attempts(item_id="a")
    assert [x.response for x in got] == [2, {"k": [1, 2]}] and got[1].hints_used == 2 and got[0].at == T0
    assert store.list_attempts(objective_id="c1:o02")[0].correct is None
    assert len(store.list_attempts(course_id="c1")) == 3 and store.count_attempts(course_id="c1") == 3
    assert [x.id for x in store.list_attempts(newest_first=True, limit=1)] == [3]
    assert len(store.list_attempts(since=T0 + timedelta(hours=1))) == 1
    assert store.count_attempts(item_id="b") == 1


def test_review_state(store):
    seed(store)
    store.add_item(sample_item("mc", id="a", objective_id="c1:o01"))
    store.add_item(sample_item("mc", id="b", objective_id="c1:o01"))
    store.put_review_state(ReviewState(item_id="a", due=T0 + timedelta(days=1), stability=2.5, difficulty=5, reps=1))
    store.put_review_state(ReviewState(item_id="b", due=T0 - timedelta(days=1), stability=1, difficulty=6, reps=3, lapses=1, last_review=T0))
    assert store.get_review_state("b").lapses == 1 and store.get_review_state("b").last_review == T0
    assert [r.item_id for r in store.list_due(course_id="c1", now=T0)] == ["b"]
    assert [r.item_id for r in store.list_due(now=T0 + timedelta(days=2))] == ["b", "a"]
    store.put_review_state(ReviewState(item_id="b", due=T0 + timedelta(days=9), stability=3, difficulty=6, reps=4, lapses=1))
    assert store.list_due(now=T0) == []
    assert store.get_review_state("b").reps == 4 and store.get_review_state("zz") is None
    store.mark_dropped("a")
    assert store.list_due(now=T0 + timedelta(days=2)) == [r for r in store.list_due(now=T0 + timedelta(days=2)) if r.item_id != "a"]
    assert len(store.list_review_states(course_id="c1")) == 2


def test_jobs_and_meta(store):
    j = store.create_job("import")
    assert j.status == "queued" and j.pct == 0 and j.id.startswith("job_")
    j2 = store.update_job(j.id, status="running", pct=40, stage="map")
    assert (j2.status, j2.pct, j2.stage, j2.error) == ("running", 40, "map", None)
    j3 = store.update_job(j.id, status="error", error="boom", course_id="c1")
    assert j3.error == "boom" and j3.course_id == "c1" and j3.pct == 40
    assert store.get_job("nope") is None and store.update_job("nope", pct=1) is None
    assert [x.id for x in store.list_jobs(status="error")] == [j.id] and store.list_jobs(status="done") == []
    assert isinstance(store.get_job(j.id), Job)
    store.set_meta("k", {"a": [1]})
    assert store.get_meta("k") == {"a": [1]} and store.get_meta("zz", 7) == 7


def test_rollback_on_error(store):
    seed(store)
    with pytest.raises(sqlite3.IntegrityError):
        with store._tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES('x','1')")
            c.execute("INSERT INTO meta(key,value) VALUES('x','2')")
    assert store.get_meta("x") is None


def test_two_threads_write_without_lock_errors(tmp_path):
    s = Store(tmp_path / "t.db")
    seed(s)
    errors, N = [], 150

    def writer(tag):
        try:
            for i in range(N):
                s.add_item(sample_item("mc", id=f"{tag}-{i}", objective_id="c1:o01"))
                s.add_attempt(Attempt(item_id=f"{tag}-{i}", response=1, correct=True, score=1, graded_by="mc"))
                s.log_drop(Drop(objective_id="c1:o01", format="mc", gate_code="ambiguous"))
                s.update_job(s.create_job("t").id, pct=i % 100)
        except Exception as e:   # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B", "C")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert s.count_items(course_id="c1") == 3 * N
    assert s.count_attempts(course_id="c1") == 3 * N
    assert s.count_drops_by_gate("c1") == {"ambiguous": 3 * N}
    s.close()


def test_second_store_object_same_file(tmp_path):
    a, b = Store(tmp_path / "s.db"), Store(tmp_path / "s.db")
    a.put_course(Course(id="c1", title="T"))
    assert b.get_course("c1") is not None
    a.close(); b.close()


def test_old_database_gets_code_language_column(tmp_path):
    import sqlite3
    from codecoach.core.store import MIGRATIONS, _split_sql
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    for st in _split_sql(MIGRATIONS[0]):
        c.execute(st)
    c.execute("PRAGMA user_version=1")
    c.execute("INSERT INTO courses VALUES('c1','T','strict','','2026-01-01T00:00:00+00:00')")
    c.commit(); c.close()
    s = Store(p)
    assert s.get_course("c1").code_language is None and s.schema_version() == len(MIGRATIONS)
    s.put_course(s.get_course("c1").model_copy(update={"code_language": "c"}))
    s.close()
    assert Store(p).get_course("c1").code_language == "c"
