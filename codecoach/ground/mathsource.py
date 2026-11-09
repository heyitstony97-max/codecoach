"""Where does a math item's problem come from?  No LLM, no network.

A generated math item is either
  * "source":   its expression is written in the quote it cites (compared after normalize, ignoring spaces and
                multiplication/power symbols), or
  * "practice": the expression is CodeCoach's own. It may only exist when it cites a passage that TEACHES the
                method (names the op or topic in a real sentence); the UI says so.
Trivial items (no variable to work with, a constant that is only an "Example 3.17" label) are dropped either way.
"""

from __future__ import annotations

import re
from typing import Sequence

from codecoach.core.models import MathPayload, Passage
from codecoach.core.text import normalize

_SYMBOLS = re.compile(r"[\s*·×⋅()\[\]{}$\\]|\bcdot\b")
_LABEL_BEFORE = re.compile(
    r"(?:example|figure|fig\.?|section|sec\.?|table|chapter|ch\.?|exercise|problem|theorem|lemma|definition|"
    r"equation|eq\.?|step|page|pp?\.|part|rule|checkpoint|exhibit|listing|appendix)\s*$", re.I)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w]|\.\d)")
_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "exp", "log", "ln", "sqrt", "abs", "asin", "acos", "atan",
          "sinh", "cosh", "tanh", "pi", "e", "oo", "Abs", "E", "I"}

OP_WORDS = {
    "diff": ("derivative", "differentiat", "slope", "rate of change", "tangent", "power rule", "product rule",
             "quotient rule", "chain rule"),
    "integrate": ("integral", "integrat", "antiderivative", "area under"),
    "solve": ("solve", "solving", "solution", "equation", "root", "zero", "factor", "quadratic", "isolate"),
    "simplify": ("simplif", "expression", "factor", "expand", "combine", "reduce"),
    "evaluate": ("evaluate", "value", "compute", "calculat", "substitut", "plug"),
}


def _squash(s: str) -> str:
    s = normalize(s).replace("**", "^")
    return _SYMBOLS.sub("", s)


def expr_in_quote(payload: MathPayload, quote: str) -> bool:
    """True when the item's expression is written in the quote (symbol-insensitive)."""
    q = _squash(quote)
    e = _squash(payload.expr)
    if len(e) < 3:
        return False
    cands = {e}
    if payload.op == "solve" and "=" not in e:
        cands.add(e + "=0")
    if payload.op == "solve" and e.endswith("=0"):
        cands.add(e[:-2])
    return any(c in q for c in cands)


def _idents(expr: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z_]\w*", expr) if t not in _FUNCS}


def has_variable(payload: MathPayload) -> bool:
    expr = payload.expr
    var = payload.var or "x"
    if re.search(rf"(?<![A-Za-z]){re.escape(var)}(?![A-Za-z(])", expr):
        return True
    return bool(_idents(expr))      # another letter standing in as the variable


def _label_only(literal: str, quote: str) -> bool:
    """The number occurs in the quote, and every time it does it directly follows a label word."""
    hits = [m for m in re.finditer(rf"(?<![\d.]){re.escape(literal)}(?!\d|\.\d)", quote)]
    return bool(hits) and all(_LABEL_BEFORE.search(quote[:m.start()]) for m in hits)


def trivial(payload: MathPayload, quote: str) -> str | None:
    """A reason this item is not worth asking, or None."""
    if payload.op in ("diff", "integrate", "solve") and not has_variable(payload):
        return f"{payload.op} of an expression with no variable: {payload.expr[:40]!r}"
    stripped = re.sub(r"[\s()+\-]", "", payload.expr)
    constant = bool(re.fullmatch(r"\d+(?:\.\d+)?", stripped))
    for lit in _NUMBER.findall(payload.expr):
        if (constant or "." in lit) and _label_only(lit, quote):
            return f"the number {lit} is only a label in the quote (Example/Figure/Section {lit})"
    return None


def teaches(passage_text: str, payload: MathPayload, topic: str = "") -> bool:
    """The cited passage is real teaching text: at least one sentence of 6+ words, and it names the op or topic.
    A heading, an "Example 3.17 ... Solution" run-in, or a passage with a picture-formula gap does not count."""
    low = passage_text.lower()
    sentences = [s for s in re.split(r"(?<=[.!?:])\s+", passage_text.strip()) if len(s.split()) >= 6]
    if not any(s.rstrip()[-1:] in ".!?:" for s in sentences):
        return False
    words = list(OP_WORDS.get(payload.op, ()))
    words += re.findall(r"[a-z]{6,}", topic.lower())
    return any(w in low for w in words)


def math_origin(payload: MathPayload, quote_texts: Sequence[str]) -> str:
    return "source" if any(expr_in_quote(payload, q) for q in quote_texts) else "practice"


def check_math_source(payload: MathPayload, quotes: Sequence[str], cited: Sequence[Passage],
                      topic: str = "") -> list[tuple[str, str]]:
    """[(gate code, detail)] for a math item that cites passages. Empty = fine."""
    bad = trivial(payload, " ".join(quotes))
    if bad:
        return [("self_check_failed", bad)]
    in_quote = any(expr_in_quote(payload, q) for q in quotes)
    if payload.origin == "source" and not in_quote:
        return [("quote_missing", "the expression is not in the cited quote (a practice problem must be labelled)")]
    if payload.origin == "practice" and not in_quote:
        if not any(not p.formula_missing and teaches(p.text, payload, topic) for p in cited):
            return [("quote_missing", "an invented practice problem must cite a passage that teaches the method "
                                      "(not a heading or a passage with a missing formula)")]
    return []
