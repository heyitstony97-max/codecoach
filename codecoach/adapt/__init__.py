"""Adaptive engine: mastery (BKT), FSRS scheduling, placement, next_item, hint ladder.

Pure functions over core.Store; the clock (`now`) and RNG are always passed in.
"""

from .flow import AnswerOutcome, record_attempt, submit_answer
from .fsrs import grade_for
from .fsrs import review as fsrs_review
from .hints import HintResult, answer_keys, get_hint, leaks
from .mastery import (EXPLAIN_WEIGHT, HINT_CREDIT, KNOWN_AT, NEW_BELOW, BKTParams, Mastery, bkt_update,
                      hint_credit, mastery_map, mean_mastery, objective_mastery, state_of)
from .placement import (MAX_ITEMS, MAX_SECONDS, current_item as placement_item, is_active as placement_active,
                        placement_result, start_placement, trace_lines as placement_trace_lines)
from .selector import Pick, mark_lesson_seen, next_item

__all__ = [
    "AnswerOutcome", "record_attempt", "submit_answer", "grade_for", "fsrs_review", "HintResult",
    "answer_keys", "get_hint", "leaks", "EXPLAIN_WEIGHT", "HINT_CREDIT", "KNOWN_AT", "NEW_BELOW",
    "BKTParams", "Mastery", "bkt_update", "hint_credit", "mastery_map", "mean_mastery",
    "objective_mastery", "state_of", "MAX_ITEMS", "MAX_SECONDS", "placement_item", "placement_active",
    "placement_result", "start_placement", "placement_trace_lines", "Pick", "mark_lesson_seen", "next_item",
]
