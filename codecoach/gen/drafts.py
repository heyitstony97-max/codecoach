"""Small per-format draft schemas the model fills in, one item per call.

Flat on purpose: weak local models do better with a few top-level fields. A draft becomes an
`Item` in generate.py, where the shuffles happen and the gate runs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..core.models import (Citation, CodeTest, MathPayload, NumericPayload, OrderEvent, Quantity)


class Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt: str = Field(min_length=1, description="The question or task shown to the learner.")
    citations: list[Citation] = Field(
        default_factory=list,
        description="Exact quotes copied from the passages that support the answer (passage_id + quote).")
    origin: Literal["materials", "model_knowledge"] = Field(
        default="materials", description="Only 'materials' unless you were told open mode is on.")


class MCDraft(Draft):
    options: list[str] = Field(min_length=3, max_length=8)
    correct_index: int = Field(ge=0)


class ClozeDraft(Draft):
    text_with_blank: str = Field(description="A sentence copied from a quote with the key phrase replaced by ____")
    answer: str
    aliases: list[str] = Field(default_factory=list)


class OrderDraft(Draft):
    events: list[OrderEvent] = Field(min_length=3, description="In CHRONOLOGICAL order, earliest first.")


class MatchDraft(Draft):
    pairs: list[dict] = Field(min_length=2, description="[{left, right, passage_id}]; both sides quoted")


class ShortDraft(Draft):
    required_terms: list[str] = Field(min_length=1, description="Key words or phrases from the quote")
    min_terms: int | None = None


class ExplainDraft(Draft):
    pass


class CodeDraft(Draft):
    language: Literal["python", "c"]
    starter: str = ""
    visible_tests: list[CodeTest] = Field(min_length=1)
    hidden_tests: list[CodeTest] = Field(min_length=1)
    reference_solution: str
    time_limit_s: float = 5.0
    mem_mb: int = 256


class MathDraft(Draft):
    op: Literal["diff", "integrate", "solve", "simplify", "evaluate"]
    expr: str
    var: str = "x"
    given_answer: str
    subs: dict[str, str] | None = None


class NumericDraft(Draft):
    given: list[Quantity] = Field(default_factory=list)
    quantity: str = ""
    answer_value: float
    unit: str
    rel_tol: float = 0.01


DRAFT_MODELS: dict[str, type[Draft]] = {
    "mc": MCDraft, "cloze": ClozeDraft, "order": OrderDraft, "match": MatchDraft, "short": ShortDraft,
    "explain": ExplainDraft, "code": CodeDraft, "math": MathDraft, "numeric": NumericDraft,
}


_STRICT: dict[str, type[Draft]] = {}


_CODE_LANG: dict[str, type[Draft]] = {}


def draft_model(fmt: str, mode: str, language: str | None = None) -> type[Draft]:
    """The draft schema to ask for. Strict mode (and every reading format) lists `citations` as a
    REQUIRED field: with the optional default, real models left it out, every draft failed with
    "needs at least one citation" and was dropped (found end to end on longcat-2.5). A required field
    fails validation instead, so structured_call retries with the error shown. Open mode keeps the
    optional field because the model may legitimately answer from its own knowledge with no quote."""
    base = DRAFT_MODELS[fmt]
    if fmt == "code" and language:
        # the course teaches one language: a draft in another one fails validation (the model sees the
        # error and retries) and, if it never complies, ends as a schema_invalid drop
        if language not in _CODE_LANG:
            _CODE_LANG[language] = create_model(  # type: ignore[call-overload]
                f"CodeDraft_{language}", __base__=CodeDraft,
                language=(Literal[language], Field(description=f"Must be exactly '{language}'.")))  # type: ignore
        return _CODE_LANG[language]
    if mode == "open" or fmt in ("code", "math", "numeric"):
        return base
    if fmt not in _STRICT:
        _STRICT[fmt] = create_model(  # type: ignore[call-overload]
            base.__name__ + "Strict", __base__=base,
            citations=(list[Citation], Field(description=Draft.model_fields["citations"].description)))
    return _STRICT[fmt]
