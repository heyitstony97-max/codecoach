"""Lessons. Strict mode: the passage(s), their location, and at most one gate-checked framing line."""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, ConfigDict

from ..core.llm import LLMError, LLMPort, structured_call
from ..core.models import (NOT_FROM_MATERIALS_LABEL, Drop, LessonPassage, LessonView, Objective, Passage)
from ..core.store import Store
from ..core.text import stable_hash
from ..ground.gate import FRAMING_MAX_CHARS, check_framing

MAX_LESSON_PASSAGES = 3


class _Framing(BaseModel):
    model_config = ConfigDict(extra="ignore")
    framing: str = ""


_INSTR = ("Write ONE short sentence (under 25 words) that tells the learner what to look for in the passage "
          "below, like 'Read this, then answer: why did the colonies object?'. Do NOT add any number, date or "
          "name that is not in the passage. Reply with only a JSON object {\"framing\": \"...\"}.")


def framing_key(objective_id: str) -> str:
    """Store meta key for an objective's cached, gate-checked framing line ({"framing": str | None})."""
    return f"codecoach:framing:{objective_id}"


def make_lesson(objective: Objective, passages: Sequence[Passage], llm: LLMPort | None = None, *,
                mode: str = "strict", unit_title: str = "", lesson_title: str = "",
                store: Store | None = None, cached_framing: str | None = None) -> LessonView | None:
    """-> LessonView, or None when there is nothing honest to teach (not_covered, or strict with no passages).

    The framing line is optional: if the model fails or the framing gate rejects it, the lesson is
    still served as the bare passage and the rejection is logged as a framing_fact drop. With no
    llm, `cached_framing` (a line that already passed the gate) is used as-is.
    """
    if objective.coverage == "not_covered":
        return None
    shown = list(passages)[:MAX_LESSON_PASSAGES]
    if not shown and mode == "strict":
        return None
    framing: str | None = cached_framing if llm is None else None
    if llm is not None:
        body = "\n\n".join(p.text for p in shown)
        try:
            raw = structured_call(llm, _Framing, instructions=_INSTR,
                                  text=f"OBJECTIVE: {objective.statement}\n\nPASSAGE:\n{body}",
                                  purpose="codecoach.gen.framing", retries=2).framing.strip()
        except LLMError:
            raw = ""
        if raw:
            fails = check_framing(raw, shown) if shown else \
                ([] if len(raw) <= FRAMING_MAX_CHARS and "\n" not in raw else check_framing(raw, shown))
            if fails and store is not None:
                store.log_drop(Drop(objective_id=objective.id, format="lesson", gate_code="framing_fact",
                                    detail=fails[0].detail[:300], draft_hash=stable_hash(raw)))
            framing = None if fails else raw
    return LessonView(
        objective_id=objective.id, unit_title=unit_title, lesson_title=lesson_title,
        statement=objective.statement, framing=framing,
        passages=[LessonPassage(passage_id=p.id, location=p.location, text=p.text) for p in shown],
        **({} if shown else {"source_label": NOT_FROM_MATERIALS_LABEL}))
