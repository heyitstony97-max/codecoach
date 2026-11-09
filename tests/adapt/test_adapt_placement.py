import random
from datetime import timedelta

from adapt_helpers import GRAPH12, T0, Clock, build_course, new_store, oid, ok

from codecoach.adapt import (MAX_ITEMS, mastery_map, next_item, placement_active, placement_result,
                          placement_trace_lines, start_placement, submit_answer)


def place(store, truth, *, course="fx", clock=None, rng=None):
    """Run a whole placement with a simulated learner who knows exactly the objectives in `truth`."""
    clock = clock or Clock()
    start_placement(store, course, clock(), random.Random(7))
    n = 0
    while placement_active(store, course):
        pick = next_item(store, course, clock(), random.Random(7))
        assert pick.reason == "placement"
        objective = int(pick.objective_id.split(":o")[1])
        submit_answer(store, pick.item.id, "x", now=clock(), result=ok(1.0 if objective in truth else 0.0),
                      rng=random.Random(7))
        n += 1
        assert n <= 40
    return n


def trace_of(store, course="fx"):
    from codecoach.adapt.placement import get_state
    return get_state(store, course)


def test_placement_trace_on_12_objective_graph():
    store = new_store()
    build_course(store)
    truth = {1, 2, 3, 4, 5, 6, 7, 8}          # the learner knows o01-o08 and misses o09-o12
    n = place(store, truth)
    st = trace_of(store)
    asked = [(t["objective_id"], t["result"]) for t in st["trace"]]
    # starts at mid-depth (depth 2, the objective with most information: o08), then dependents / prerequisites
    assert asked == [(oid(8), "correct"), (oid(10), "wrong"), (oid(7), "correct"), (oid(6), "correct"),
                     (oid(9), "wrong")]
    assert n == 5 < MAX_ITEMS
    res = placement_result(store, "fx")
    assert {k: v for k, v in res.items() if v == "known"}.keys() == {oid(i) for i in truth}
    assert {k for k, v in res.items() if v == "new"} == {oid(i) for i in (9, 10, 11, 12)}
    # inferred, not asked: o01-o05 known from o08, o03 from o06, o11 and o12 new from the misses
    assert st["marks"][oid(1)]["via"].startswith("inferred") and st["marks"][oid(11)]["via"].startswith("inferred")
    lines = placement_trace_lines(st)
    assert lines[0].startswith("1. ask fx:o08") and lines[-1].startswith("final:")


def test_placement_all_known_stops_early_and_all_unknown_probes_down():
    store = new_store()
    build_course(store)
    place(store, set(range(1, 13)))
    t = trace_of(store)["trace"]
    assert [x["objective_id"] for x in t] == [oid(8), oid(10), oid(12)]   # each correct answer climbs to a dependent
    assert set(placement_result(store, "fx").values()) == {"known"}

    store2 = new_store()
    build_course(store2)
    place(store2, set())
    t2 = trace_of(store2)["trace"]
    assert t2[0]["objective_id"] == oid(8)
    assert t2[1]["objective_id"] in (oid(4), oid(5))                      # a wrong answer tests its prerequisites
    assert set(placement_result(store2, "fx").values()) == {"new"}


def test_placement_is_deterministic_with_same_rng_and_clock():
    a, b = new_store(), new_store()
    build_course(a)
    build_course(b)
    place(a, {1, 2, 4, 5}, clock=Clock())
    place(b, {1, 2, 4, 5}, clock=Clock())
    assert trace_of(a)["trace"] == trace_of(b)["trace"]


def test_placement_marks_shaky_on_partial_score():
    store = new_store()
    build_course(store, {1: [], 2: [1], 3: [2]})
    clock = Clock()
    start_placement(store, "fx", clock(), random.Random(1))
    pick = next_item(store, "fx", clock(), random.Random(1))
    submit_answer(store, pick.item.id, "x", now=clock(), result=ok(0.55))
    assert trace_of(store)["marks"][pick.objective_id]["status"] == "shaky"


def test_placement_caps_at_12_items():
    store = new_store()
    graph = {n: [] for n in range(1, 21)}               # 20 independent objectives: nothing can be inferred
    build_course(store, graph)
    n = place(store, set(range(1, 21)), clock=Clock(step=timedelta(seconds=5)))
    assert n == MAX_ITEMS == 12
    res = placement_result(store, "fx")
    assert len(res) == 20 and sum(v == "known" for v in res.values()) == 12
    assert sum(v == "new" for v in res.values()) == 8     # the unasked ones are new


def test_placement_caps_at_10_minutes():
    store = new_store()
    build_course(store, {n: [] for n in range(1, 11)})
    clock = Clock(step=timedelta(minutes=4))              # the third answer lands after 10+ minutes
    n = place(store, set(range(1, 11)), clock=clock)
    assert n < 12 and n <= 4
    assert placement_result(store, "fx") is not None


def test_placement_asks_covered_objectives_only():
    store = new_store()
    build_course(store, {1: [], 2: [1], 3: [2], 4: []}, not_covered=(3,))
    place(store, {1, 2, 4})
    asked = {t["objective_id"] for t in trace_of(store)["trace"]}
    assert oid(3) not in asked
    assert oid(3) not in placement_result(store, "fx")


def test_placement_writes_priors_so_mastery_matches_marks():
    store = new_store()
    build_course(store)
    place(store, {1, 2, 3, 4, 5, 6, 7, 8})
    m = mastery_map(store, "fx")
    assert m[oid(8)].state == "known" and m[oid(1)].state == "known"      # tested and inferred
    assert m[oid(10)].state == "new" and m[oid(12)].state == "new"
    # after placement the learner moves on: the frontier is a new objective whose prerequisites are known
    clock = Clock(start=T0 + timedelta(hours=1))
    pick = next_item(store, "fx", clock())
    assert (pick.kind, pick.reason) == ("lesson", "new") and pick.objective_id == oid(9)


def test_placement_restart_is_idempotent_and_resumes():
    store = new_store()
    build_course(store)
    s1 = start_placement(store, "fx", T0, random.Random(3))
    s2 = start_placement(store, "fx", T0 + timedelta(minutes=1), random.Random(99))
    assert s1["current"] == s2["current"]
