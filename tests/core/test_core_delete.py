"""Store.delete_course: everything of one course goes in one transaction; other courses stay untouched."""

import sqlite3
from datetime import datetime, timezone

import pytest

from codecoach.core.models import Attempt, Course, CourseMap, Doc, Drop, Passage, ReviewState
from codecoach.core.samples import sample_item
from codecoach.core.store import Store

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
TABLES = ["courses", "docs", "passages", "course_maps", "objectives", "items", "attempts", "review_state",
          "reports", "drops", "jobs", "meta"]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data.db")
    yield s
    s.close()


def seed(store, cid, doc):
    store.put_course(Course(id=cid, title=f"Course {cid}", created_at=T0))
    store.put_doc(Doc(id=doc, course_id=cid, name="Book", kind="text"))
    store.put_passages([Passage(id=f"{doc}:p1:1", doc_id=doc, location="p. 1, para 1", text="one"),
                        Passage(id=f"{doc}:p1:2", doc_id=doc, location="p. 1, para 2", text="two")])
    store.save_map(CourseMap.model_validate({"course_id": cid, "units": [{"id": f"{cid}:u1", "title": "U", "lessons": [
        {"id": f"{cid}:l1", "title": "L", "objectives": [
            {"id": f"{cid}:o01", "unit": f"{cid}:u1", "lesson": f"{cid}:l1", "verb": "x", "statement": "S1",
             "passage_ids": [f"{doc}:p1:1"]}]}]}]}))
    item = sample_item("mc", id=f"{cid}-item", objective_id=f"{cid}:o01")
    assert store.add_item(item)
    store.add_attempt(Attempt(item_id=item.id, response="a", correct=True, score=1.0, graded_by="code", at=T0))
    store.put_review_state(ReviewState(item_id=item.id, due=T0, stability=1.0, difficulty=5.0, reps=1, lapses=0,
                                       last_review=T0))
    store.mark_reported(item.id, "bad")
    store.log_drop(Drop(course_id=cid, objective_id=f"{cid}:o01", format="mc", gate_code="ambiguous", at=T0))
    store.create_job("import", course_id=cid, job_id=f"job-{cid}")
    store.set_meta(f"import:{cid}", {"status": "done"})
    store.set_meta(f"adapt:placement:{cid}", {"x": 1})
    store.set_meta(f"adapt:lessons:{cid}", {"x": 1})
    store.set_meta(f"adapt:prior:{cid}", {"x": 1})
    store.set_meta(f"codecoach:hints:{item.id}", 2)
    return item


def dump(store):
    c = store._conn()
    return {t: sorted(map(tuple, c.execute(f"SELECT * FROM {t}").fetchall())) for t in TABLES}


def test_delete_removes_every_trace_of_the_course(store):
    seed(store, "c1", "d1")
    counts = store.delete_course("c1")
    assert counts["docs"] == 1 and counts["passages"] == 2 and counts["objectives"] == 1
    assert counts["items"] == 1 and counts["attempts"] == 1 and counts["review_states"] == 1
    assert counts["drops"] == 1 and counts["jobs"] == 1
    assert all(rows == [] for rows in dump(store).values())


def test_other_courses_stay_exactly_as_they_were(store):
    seed(store, "c2", "d2")
    before = dump(store)
    seed(store, "c1", "d1")
    store.set_meta("codecoach:active_course", "c1")
    store.set_meta("codecoach:last_item", "c1-item")
    store.delete_course("c1")
    assert dump(store) == before
    assert store.get_course("c2") is not None and len(store.list_passages(course_id="c2")) == 2
    assert store.get_meta("import:c2") == {"status": "done"}


def test_active_course_and_last_item_pointers_are_cleared_only_when_they_point_at_it(store):
    seed(store, "c1", "d1")
    seed(store, "c2", "d2")
    store.set_meta("codecoach:active_course", "c2")
    store.set_meta("codecoach:last_item", "c2-item")
    store.delete_course("c1")
    assert store.get_meta("codecoach:active_course") == "c2" and store.get_meta("codecoach:last_item") == "c2-item"
    store.delete_course("c2")
    assert store.get_meta("codecoach:active_course") is None and store.get_meta("codecoach:last_item") is None


def test_unknown_course_deletes_nothing(store):
    seed(store, "c1", "d1")
    before = dump(store)
    assert store.delete_course("nope") is None
    assert dump(store) == before


def test_a_course_id_that_is_a_suffix_of_another_keeps_the_others_meta(store):
    seed(store, "c1", "d1")
    seed(store, "xc1", "d9")
    store.delete_course("c1")
    assert store.get_meta("import:xc1") == {"status": "done"} and store.get_meta("import:c1") is None


def test_it_is_one_transaction_a_failure_rolls_everything_back(store):
    seed(store, "c1", "d1")
    before = dump(store)
    store._conn().executescript(
        "CREATE TRIGGER boom BEFORE DELETE ON courses BEGIN SELECT RAISE(ABORT, 'boom'); END;")
    with pytest.raises(sqlite3.DatabaseError):
        store.delete_course("c1")
    assert dump(store) == before


def test_footprint_counts_without_changing_anything(store):
    seed(store, "c1", "d1")
    before = dump(store)
    fp = store.course_footprint("c1")
    assert fp["items"] == 1 and fp["passages"] == 2
    assert dump(store) == before
