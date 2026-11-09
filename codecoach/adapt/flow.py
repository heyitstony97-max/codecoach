"""Recording an answer: the one door that writes an Attempt, advances the FSRS card and feeds placement."""

import random
from dataclasses import dataclass
from datetime import datetime

from codecoach.core.models import Attempt, Item, ReviewState
from codecoach.core.registry import CheckResult, check
from codecoach.core.store import Store

from . import fsrs, placement
from .mastery import mastery_map


@dataclass(frozen=True)
class AnswerOutcome:
    recorded: bool
    attempt: Attempt | None
    correct: bool | None        # None for self-rated explain items
    score: float
    review: ReviewState | None
    interval_days: float | None
    mastery: float | None       # objective mastery 0..1 after this answer
    state: str | None           # new | shaky | known
    placement_done: bool = False


def record_attempt(store: Store, item: Item, response, result: CheckResult, *, now: datetime,
                   hints_used: int = 0, schedule: bool = True) -> AnswerOutcome:
    """Store the attempt, advance the item's FSRS card (unless schedule=False), return the new mastery.
    An `undecided` check result is never recorded (it is not a wrong answer)."""
    if result.undecided:
        return AnswerOutcome(False, None, None, result.score, None, None, None, None)
    correct = None if item.format == "explain" else bool(result.ok)
    att = store.add_attempt(Attempt(item_id=item.id, response=response, correct=correct,
                                    score=result.score, graded_by=item.checker, hints_used=hints_used, at=now))
    card = None
    if schedule:
        card = fsrs.review(store.get_review_state(item.id), item.id,
                           fsrs.grade_for(item.format, result.score, hints_used), now)
        store.put_review_state(card)
    cid = store.course_of_objective(item.objective_id)
    m = mastery_map(store, cid).get(item.objective_id) if cid else None
    return AnswerOutcome(True, att, correct, result.score, card,
                         (card.due - now).total_seconds() / 86400 if card else None,
                         m.p if m else None, m.state if m else None)


def submit_answer(store: Store, item_id: str, response, *, now: datetime, hints_used: int = 0,
                  result: CheckResult | None = None, rng: random.Random | None = None) -> AnswerOutcome:
    """Grade (via the checker registry unless `result` is given), record, and advance a running placement."""
    item = store.get_item(item_id)
    if item is None:
        raise KeyError(item_id)
    res = result or check(item, response)
    cid = store.course_of_objective(item.objective_id)
    # a placement miss is not a review: the learner has not been taught this yet
    in_placement = bool(cid) and placement.is_current(store, cid, item.id)
    missed = in_placement and res.score * _credit(hints_used) < placement.PARTIAL_AT
    out = record_attempt(store, item, response, res, now=now, hints_used=hints_used, schedule=not missed)
    if out.recorded:
        if cid:
            st = placement.record_result(store, cid, item, res.score * _credit(hints_used), now, rng)
            if st is not None:
                return AnswerOutcome(**{**out.__dict__, "placement_done": st["status"] == "done"})
    return out


def _credit(hints_used: int) -> float:
    from .mastery import hint_credit
    return hint_credit(hints_used)
