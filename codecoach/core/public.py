"""The only door items leave the server through before an answer.

public_view(item) -> PublicItem : no answer keys, no hidden tests, no reference solution, no
citations. reveal(item, locate) -> citations, called only AFTER an answer or report.
Built as an allow-list per format: a new payload field is private until added here.
"""

import hashlib
import re
from typing import Callable

from .models import (FROM_MATERIALS_LABEL, NOT_FROM_MATERIALS_LABEL, ClozePayload, CodePayload,
                     ExplainPayload, Item, MathPayload, MatchPayload, MCPayload, NumericPayload,
                     OrderPayload, PublicItem, RevealedCitation, ShortPayload)


def _shuffled(values: list[str], salt: str) -> list[str]:
    """Deterministic shuffle keyed by item id, so the right-hand column never mirrors the left."""
    out = sorted(values, key=lambda v: hashlib.sha256(f"{salt}|{v}".encode()).hexdigest())
    if len(out) > 1 and len(set(values)) > 1 and out == values:
        out = out[1:] + out[:1]
    return out


def _payload_view(item: Item) -> dict:
    p = item.payload
    if isinstance(p, MCPayload):
        return {"options": list(p.options)}
    if isinstance(p, ClozePayload):
        return {"text_with_blank": p.text_with_blank}
    if isinstance(p, OrderPayload):
        return {"events": [{"label": e.label} for e in p.events]}
    if isinstance(p, MatchPayload):
        return {"lefts": [m.left for m in p.pairs],
                "rights": _shuffled([m.right for m in p.pairs], item.id)}
    if isinstance(p, ShortPayload):
        return {"terms_expected": len(p.required_terms) if p.min_terms is None else p.min_terms}
    if isinstance(p, ExplainPayload):
        return {}
    if isinstance(p, CodePayload):
        return {
            "language": p.language,
            "starter": p.starter,
            "visible_tests": [t.model_dump(exclude_none=True) for t in p.visible_tests],
            "hidden_test_count": len(p.hidden_tests),
            "time_limit_s": p.time_limit_s,
            "mem_mb": p.mem_mb,
        }
    if isinstance(p, MathPayload):
        return {"op": p.op, "expr": p.expr, "var": p.var, "subs": p.subs, "origin": p.origin, "places": p.places,
                "units_ok": _units_ok(p, item.prompt)}
    if isinstance(p, NumericPayload):
        return {"given": [q.model_dump() for q in p.given], "quantity": p.quantity, "unit": p.unit}
    raise ValueError(f"no public view for payload {type(p).__name__}")  # pragma: no cover


def _units_ok(p: MathPayload, prompt: str) -> bool:
    """A word problem with a bare-number key: the box takes a trailing unit or "percent" and says so."""
    return p.op == "evaluate" and len(prompt.split()) >= 8 and "=" not in prompt and bool(
        re.search(r"\d\s*(?:%|[A-Za-z\u00b0]{2,}|[mglsh]\b)|percent|per cent", prompt, re.I))


def source_label(item: Item) -> str:
    return FROM_MATERIALS_LABEL if item.origin == "materials" else NOT_FROM_MATERIALS_LABEL


def public_view(item: Item) -> PublicItem:
    return PublicItem(
        id=item.id,
        objective_id=item.objective_id,
        format=item.format,
        prompt=item.prompt,
        payload=_payload_view(item),
        origin=item.origin,
        source_label=source_label(item),
        status=item.status,
    )


def reveal(item: Item, locate: Callable[[str], str | None] | None = None) -> list[RevealedCitation]:
    """Citations with quote + human location. `locate(passage_id) -> location label` is supplied by
    the caller (e.g. Store.get_passage). Call only after the learner has answered."""
    return [RevealedCitation(passage_id=c.passage_id, quote=c.quote,
                             location=locate(c.passage_id) if locate else None)
            for c in item.citations]
