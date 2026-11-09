from datetime import timedelta

from adapt_helpers import T0, Clock, build_course, make_item, new_store, oid, ok

from codecoach.adapt import mark_lesson_seen, next_item, submit_answer
from codecoach.adapt.mastery import set_priors
from codecoach.core.models import ReviewState


def due(store, item_id, days_ago):
    store.put_review_state(ReviewState(item_id=item_id, due=T0 - timedelta(days=days_ago), stability=3.0,
                                       difficulty=5.0, reps=1, last_review=T0 - timedelta(days=days_ago + 3)))


def attempt(store, item_id, minutes, score=1.0):
    submit_answer(store, item_id, 1, now=T0 - timedelta(minutes=minutes), result=ok(score))


def test_lesson_before_first_item_then_items_until_known_then_dependent():
    store = new_store()
    build_course(store, {1: [], 2: [1]})
    clock = Clock()
    p = next_item(store, "fx", clock())
    assert (p.kind, p.reason, p.objective_id) == ("lesson", "new", oid(1))
    mark_lesson_seen(store, "fx", oid(1), clock())
    p = next_item(store, "fx", clock())
    assert (p.kind, p.objective_id) == ("item", oid(1)) and p.item.status == "active"
    submit_answer(store, p.item.id, 1, now=clock(), result=ok())
    p = next_item(store, "fx", clock())
    assert p.objective_id == oid(1) and p.item.id != "it-01-1"        # the other, unseen item first
    submit_answer(store, p.item.id, 1, now=clock(), result=ok())      # second clean answer: known
    p = next_item(store, "fx", clock())
    assert (p.kind, p.objective_id) == ("lesson", oid(2))              # prerequisites known: next frontier


def test_frontier_waits_for_prerequisites():
    store = new_store()
    build_course(store, {1: [], 2: [1], 3: []})
    p = next_item(store, "fx", T0)
    assert p.objective_id == oid(1)
    mark_lesson_seen(store, "fx", oid(1), T0)
    p = next_item(store, "fx", T0 + timedelta(seconds=1))
    assert p.objective_id == oid(1)              # o02 is blocked by o01, o03 comes after o01 in map order


def test_not_covered_never_served_even_with_items_and_due_reviews():
    store = new_store()
    build_course(store, {1: [], 2: [1], 3: []}, not_covered=(2,))
    # force stray items and a due review onto the not_covered objective
    store.add_item(make_item(2, 1))
    due(store, "it-02-1", 2)
    served = set()
    clock = Clock()
    for _ in range(30):
        p = next_item(store, "fx", clock())
        if p.kind == "done":
            break
        served.add(p.objective_id)
        if p.kind == "lesson":
            mark_lesson_seen(store, "fx", p.objective_id, clock())
        else:
            submit_answer(store, p.item.id, 1, now=clock(), result=ok())
    assert oid(2) not in served and served == {oid(1), oid(3)}


def test_only_active_items_are_served():
    store = new_store()
    build_course(store, {1: []}, items_per=3)
    mark_lesson_seen(store, "fx", oid(1), T0)
    store.mark_dropped("it-01-1")
    store.mark_reported("it-01-2", "wrong")
    due(store, "it-01-1", 1)
    due(store, "it-01-2", 1)
    p = next_item(store, "fx", T0 + timedelta(seconds=5))
    assert p.item.id == "it-01-3"


def test_due_reviews_come_before_new_material_most_overdue_first():
    store = new_store()
    build_course(store, {1: [], 2: [], 3: []})
    due(store, "it-01-1", 1)
    due(store, "it-02-1", 5)
    p = next_item(store, "fx", T0)
    assert (p.reason, p.item.id) == ("review", "it-02-1")


def test_interleaving_never_same_objective_twice_when_alternative():
    store = new_store()
    build_course(store, {1: [], 2: []}, items_per=3)
    attempt(store, "it-01-3", 30)                    # the learner just answered an o01 item
    due(store, "it-01-1", 9)                         # o01 items are the most overdue ...
    due(store, "it-01-2", 8)
    due(store, "it-02-1", 1)                         # ... but o02 is the alternative
    p = next_item(store, "fx", T0)
    assert (p.reason, p.item.id) == ("review", "it-02-1")


def test_interleaving_alternates_across_a_run_of_due_reviews():
    store = new_store()
    build_course(store, {1: [], 2: [], 3: []}, items_per=3)
    for n in (1, 2, 3):
        mark_lesson_seen(store, "fx", oid(n), T0 - timedelta(days=30))
    for n, d in ((1, 9), (2, 6), (3, 3)):
        for k in (1, 2, 3):
            due(store, f"it-{n:02d}-{k}", d + k * 0.1)
    clock = Clock(start=T0)
    seq = []
    for _ in range(6):
        p = next_item(store, "fx", clock())
        assert p.reason == "review"
        seq.append(p.objective_id)
        submit_answer(store, p.item.id, 1, now=clock(), result=ok())
    assert all(a != b for a, b in zip(seq, seq[1:])), seq


def test_frontier_beats_a_same_objective_repeat_but_repeat_is_served_when_it_is_all_there_is():
    store = new_store()
    build_course(store, {1: [], 2: [1]}, items_per=3)
    set_priors(store, "fx", {oid(1): 0.95}, T0 - timedelta(days=40))
    attempt(store, "it-01-3", 30)
    due(store, "it-01-1", 3)
    due(store, "it-01-2", 2)
    p = next_item(store, "fx", T0)
    assert (p.kind, p.objective_id) == ("lesson", oid(2))               # alternative: new material

    store2 = new_store()
    build_course(store2, {1: []}, items_per=3)
    set_priors(store2, "fx", {oid(1): 0.95}, T0 - timedelta(days=40))   # everything known: no frontier
    attempt(store2, "it-01-3", 30)
    due(store2, "it-01-1", 3)
    due(store2, "it-01-2", 2)
    p2 = next_item(store2, "fx", T0)
    assert (p2.reason, p2.item.id) == ("review", "it-01-1")             # no alternative: serve it


def test_retrieval_gap_after_a_lesson():
    store = new_store()
    build_course(store, {1: [], 2: []}, items_per=2)
    attempt(store, "it-02-1", 600)                       # o02 already taught and practised
    mark_lesson_seen(store, "fx", oid(2), T0 - timedelta(minutes=700))
    mark_lesson_seen(store, "fx", oid(1), T0 - timedelta(minutes=1))   # lesson for o01 just read
    # make o01 the frontier by keeping o02 out of the way
    p = next_item(store, "fx", T0)
    assert p.kind == "item" and p.reason == "interleave" and p.objective_id == oid(2)
    # once the learner has answered something since the lesson the gap no longer applies
    attempt(store, p.item.id, 0.5)
    p2 = next_item(store, "fx", T0)
    assert p2.objective_id == oid(1) and p2.reason == "new"


def test_retrieval_gap_falls_back_when_there_is_no_alternative():
    store = new_store()
    build_course(store, {1: []}, items_per=2)
    mark_lesson_seen(store, "fx", oid(1), T0 - timedelta(minutes=1))
    p = next_item(store, "fx", T0)
    assert (p.kind, p.reason, p.objective_id) == ("item", "new", oid(1))


def test_retrieval_gap_ignores_items_citing_the_lesson_passage():
    store = new_store()
    build_course(store, {1: [], 2: []}, items_per=1)
    # an o02 item that also cites the o01 lesson passage is NOT a valid alternative
    store.add_item(make_item(2, 5, citations=[{"passage_id": "fx:s01:1", "quote": "Fact 1 is established here"}]))
    store.mark_dropped("it-02-1")
    attempt(store, "it-02-5", 600)
    mark_lesson_seen(store, "fx", oid(1), T0 - timedelta(minutes=1))
    p = next_item(store, "fx", T0)
    assert p.objective_id == oid(1) and p.reason == "new"


def test_done_when_everything_known_with_next_review_message():
    store = new_store()
    build_course(store, {1: [], 2: [1]})
    set_priors(store, "fx", {oid(1): 0.95, oid(2): 0.95}, T0 - timedelta(days=1))
    store.put_review_state(ReviewState(item_id="it-01-1", due=T0 + timedelta(days=4), stability=3.0,
                                       difficulty=5.0, reps=1, last_review=T0))
    p = next_item(store, "fx", T0)
    assert p.kind == "done" and p.reason == "none" and "Next review" in p.message


def test_next_item_is_deterministic_for_the_same_store_and_clock():
    store = new_store()
    build_course(store, {1: []}, items_per=4)
    mark_lesson_seen(store, "fx", oid(1), T0)
    a = next_item(store, "fx", T0 + timedelta(seconds=1))
    b = next_item(store, "fx", T0 + timedelta(seconds=1))
    assert a.item.id == b.item.id
