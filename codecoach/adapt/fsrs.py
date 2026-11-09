"""FSRS-5 spaced repetition with the default parameters, pure Python, one card per item.

Deterministic: the clock is passed in, there is no fuzz. Grades: 1 Again, 2 Hard, 3 Good, 4 Easy.
A card is the core `ReviewState` (due, stability, difficulty, reps, lapses, last_review).
"""

import math
from datetime import datetime, timedelta

from codecoach.core.models import ReviewState

W = (0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
     1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621)
DECAY = -0.5
FACTOR = 19 / 81
REQUEST_RETENTION = 0.9
MAX_INTERVAL_DAYS = 36500
AGAIN_DELAY = timedelta(minutes=10)
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def init_stability(g: int) -> float:
    return max(W[g - 1], 0.1)


def init_difficulty(g: int) -> float:
    return _clamp_d(W[4] - math.exp(W[5] * (g - 1)) + 1)


def retrievability(elapsed_days: float, stability: float) -> float:
    return (1 + FACTOR * max(elapsed_days, 0.0) / stability) ** DECAY


def interval_days(stability: float, retention: float = REQUEST_RETENTION) -> float:
    return stability / FACTOR * (retention ** (1 / DECAY) - 1)


def next_difficulty(d: float, g: int) -> float:
    d1 = d + (-W[6] * (g - 3)) * (10 - d) / 9      # linear damping (FSRS-5)
    return _clamp_d(W[7] * init_difficulty(4) + (1 - W[7]) * d1)


def recall_stability(d: float, s: float, r: float, g: int) -> float:
    hard = W[15] if g == HARD else 1.0
    easy = W[16] if g == EASY else 1.0
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** -W[9] * (math.exp((1 - r) * W[10]) - 1) * hard * easy)


def forget_stability(d: float, s: float, r: float) -> float:
    new = W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14])
    return min(new, s)


def short_term_stability(s: float, g: int) -> float:
    new = s * math.exp(W[17] * (g - 3 + W[18]))
    return max(new, s) if g >= GOOD else new


def review(card: ReviewState | None, item_id: str, grade: int, now: datetime) -> ReviewState:
    """The next card state after reviewing at `now` with `grade`."""
    if grade not in (1, 2, 3, 4):
        raise ValueError(f"grade must be 1..4, got {grade}")
    if card is None or card.last_review is None:
        s, d, reps, lapses = init_stability(grade), init_difficulty(grade), 1, 0
    else:
        t = (now - card.last_review).total_seconds() / 86400
        r = retrievability(t, card.stability)
        if grade == AGAIN:
            s, lapses = forget_stability(card.difficulty, card.stability, r), card.lapses + 1
        elif t < 1:
            s, lapses = short_term_stability(card.stability, grade), card.lapses
        else:
            s, lapses = recall_stability(card.difficulty, card.stability, r, grade), card.lapses
        d, reps = next_difficulty(card.difficulty, grade), card.reps + 1
    s = max(s, 0.01)
    if grade == AGAIN:
        due = now + AGAIN_DELAY
    else:
        days = min(max(1, round(interval_days(s))), MAX_INTERVAL_DAYS)
        due = now + timedelta(days=days)
    return ReviewState(item_id=item_id, due=due, stability=s, difficulty=d, reps=reps, lapses=lapses,
                       last_review=now)


def grade_for(fmt: str, score: float, hints_used: int = 0) -> int:
    """Map an attempt to an FSRS grade. Self-rated explain items use the rating directly; others are
    Good for a clean pass, Hard for a partial score or heavy hint use, Again for a miss."""
    if fmt == "explain":
        return AGAIN if score < 0.4 else HARD if score < 0.7 else GOOD if score < 0.95 else EASY
    if score < 0.5:
        return AGAIN
    if score < 0.85 or hints_used >= 2:
        return HARD
    return GOOD
