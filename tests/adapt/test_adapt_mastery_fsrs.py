from datetime import timedelta

import pytest
from adapt_helpers import T0, Clock, build_course, new_store, oid, ok

from codecoach.adapt import (EXPLAIN_WEIGHT, BKTParams, bkt_update, fsrs_review, grade_for, hint_credit,
                          mastery_map, state_of, submit_answer)
from codecoach.adapt import fsrs
from codecoach.adapt.mastery import attempt_evidence
from codecoach.core.models import ReviewState


def run(card, grades_and_gaps, item="i"):
    """Review at T0, then after each (grade, gap_days). Returns [(due_days_from_review, round(S,3), round(D,3))]."""
    out, now = [], T0
    for g, gap in grades_and_gaps:
        now = now + timedelta(days=gap)
        card = fsrs_review(card, item, g, now)
        out.append((round((card.due - now).total_seconds() / 86400, 4), round(card.stability, 3),
                    round(card.difficulty, 3)))
    return out


# -------------------------------------------------------------- golden FSRS sequences

def test_golden_first_review_intervals():
    # FSRS-5 defaults: first-review stability is w[grade-1]; interval at 90% retention equals stability
    assert run(None, [(1, 0)])[0][0] == pytest.approx(10 / 1440, abs=1e-4)  # Again: 10 minutes
    assert run(None, [(2, 0)])[0][:2] == (1.0, 1.184)                      # Hard: 1.18 -> 1 day
    assert run(None, [(3, 0)])[0][:2] == (3.0, 3.173)                      # Good: 3 days
    assert run(None, [(4, 0)])[0][:2] == (16.0, 15.691)                    # Easy: 16 days


def test_golden_good_chain():
    # (days until due, stability, difficulty); reviews on the day each card falls due or a bit later
    assert run(None, [(3, 0), (3, 3), (3, 7), (3, 15)]) == [
        (3.0, 3.173, 5.282), (11.0, 10.739, 5.273), (27.0, 26.502, 5.264), (57.0, 57.159, 5.254)]


def test_golden_lapse_sequence():
    seq = run(None, [(3, 0), (3, 3), (1, 10), (3, 0.5), (3, 1)])
    assert seq == [(3.0, 3.173, 5.282), (11.0, 10.739, 5.273), (0.0069, 2.146, 6.791),
                   (3.0, 3.022, 6.774), (5.0, 5.022, 6.758)]
    assert seq[2][1] < seq[1][1] and seq[2][2] > seq[1][2]   # lapse: stability drops, difficulty rises


def test_golden_mixed_grades():
    assert run(None, [(3, 0), (3, 3), (2, 10), (4, 12)]) == [
        (3.0, 3.173, 5.282), (11.0, 10.739, 5.273), (16.0, 15.803, 6.027), (82.0, 82.048, 5.372)]


def test_lapses_and_reps_counted():
    c = fsrs_review(None, "i", 3, T0)
    c = fsrs_review(c, "i", 1, T0 + timedelta(days=4))
    c = fsrs_review(c, "i", 3, T0 + timedelta(days=5))
    assert (c.reps, c.lapses) == (3, 1)


def test_retrievability_is_90_percent_at_stability():
    assert fsrs.retrievability(5.0, 5.0) == pytest.approx(0.9)
    assert fsrs.interval_days(5.0) == pytest.approx(5.0)


def test_bad_grade_rejected():
    with pytest.raises(ValueError):
        fsrs_review(None, "i", 5, T0)


def test_grade_mapping():
    assert grade_for("mc", 0.0) == 1 and grade_for("mc", 1.0) == 3
    assert grade_for("short", 0.6) == 2 and grade_for("mc", 1.0, hints_used=2) == 2
    assert grade_for("explain", 0.2) == 1 and grade_for("explain", 1.0) == 4


# -------------------------------------------------------------- mastery

def test_bkt_correct_raises_wrong_lowers():
    p = 0.5
    assert bkt_update(p, 1.0) > p
    assert bkt_update(p, 0.0) < p


def test_two_clean_correct_answers_reach_known_one_wrong_stays_new():
    p = bkt_update(bkt_update(0.2, 1.0), 1.0)
    assert state_of(p) == "known"
    assert state_of(bkt_update(0.2, 0.0)) == "new"


def test_explain_counts_half_weight():
    assert attempt_evidence("explain", 1.0)[1] == EXPLAIN_WEIGHT == 0.5
    assert attempt_evidence("mc", 1.0)[1] == 1.0
    no_learn = BKTParams(p_learn=0.0)
    full = bkt_update(0.3, 1.0, 1.0, no_learn) - 0.3
    half = bkt_update(0.3, 1.0, 0.5, no_learn) - 0.3
    assert half == pytest.approx(full / 2)


def test_explain_attempts_move_mastery_less_than_mc_in_the_store():
    store = new_store()
    build_course(store, {1: [], 2: []}, explain_on=(1, 2))
    clock = Clock()
    submit_answer(store, "it-01-1", 1, now=clock(), result=ok(1.0))
    submit_answer(store, "it-02-9", 1.0, now=clock(), result=ok(1.0))   # explain, self-rated 1.0
    m = mastery_map(store, "fx")
    assert m[oid(2)].p < m[oid(1)].p
    assert m[oid(2)].p > 0.2      # but it still counts


def test_hints_lower_credit():
    assert hint_credit(0) > hint_credit(1) > hint_credit(3) and hint_credit(9) == hint_credit(3)
    store = new_store()
    build_course(store, {1: [], 2: []})
    clock = Clock()
    submit_answer(store, "it-01-1", 1, now=clock(), result=ok(1.0))
    submit_answer(store, "it-02-1", 1, now=clock(), result=ok(1.0), hints_used=3)
    m = mastery_map(store, "fx")
    assert m[oid(2)].p < m[oid(1)].p


def test_mastery_replay_is_deterministic_and_covered_only():
    store = new_store()
    build_course(store, {1: [], 2: [1]}, not_covered=(2,))
    submit_answer(store, "it-01-1", 1, now=T0 + timedelta(minutes=1), result=ok(1.0))
    a, b = mastery_map(store, "fx"), mastery_map(store, "fx")
    assert a == b and set(a) == {oid(1)}


def test_submit_schedules_review_per_item():
    store = new_store()
    build_course(store, {1: []})
    out = submit_answer(store, "it-01-1", 1, now=T0, result=ok(1.0))
    assert out.review.due == T0 + timedelta(days=3) and out.interval_days == 3.0
    out2 = submit_answer(store, "it-01-2", 1, now=T0, result=ok(0.0))
    assert out2.review.due == T0 + timedelta(minutes=10)
    assert isinstance(store.get_review_state("it-01-1"), ReviewState)


def test_undecided_result_is_not_recorded():
    from codecoach.core.registry import CheckResult
    store = new_store()
    build_course(store, {1: []})
    out = submit_answer(store, "it-01-1", "x", now=T0, result=CheckResult(ok=False, score=0.0, undecided=True))
    assert not out.recorded and store.count_attempts(course_id="fx") == 0
