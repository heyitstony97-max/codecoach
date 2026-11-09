"""Checkers for the reading formats: mc, cloze, order, match, short, explain. Pure stdlib, no LLM.

Response shapes (SPEC section 5):
    mc      int index into the options (a digit string is accepted)
    cloze   string
    order   list of event indexes in the learner's order (a permutation of range(n))
    match   [[left_index, right_index], ...] against the PUBLIC view (rights shuffled by
            core.public._shuffled(rights, item.id)); a {left_text: right_text} dict is also accepted
    short   string
    explain self-rating 0..1 (a2b returns score=rating; `ok` is only rating >= 0.5 and the service must
            report correct=None for this format; a3 applies the half weight)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from codecoach.core.models import (ClozePayload, ExplainPayload, MatchPayload, MCPayload, OrderPayload,
                                ShortPayload)
from codecoach.core.public import _shuffled
from codecoach.core.registry import CheckResult
from codecoach.core.text import normalize


def _no(detail: str, score: float = 0.0) -> CheckResult:
    return CheckResult(ok=False, score=score, detail=detail)


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+\s*", v):
        return int(v)
    return None


def check_mc(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    p = item.payload
    if not isinstance(p, MCPayload):
        return _no("Not a multiple-choice item.")
    idx = _int(response)
    if idx is None or not 0 <= idx < len(p.options):
        return _no("Choose one of the options.")
    return CheckResult(ok=True, score=1.0, detail="Correct") if idx == p.correct_index else _no("Not correct.")


def check_cloze(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    p = item.payload
    if not isinstance(p, ClozePayload):
        return _no("Not a fill-in-the-blank item.")
    if not isinstance(response, str) or not normalize(response):
        return _no("Fill in the blank first.")
    got = normalize(response)
    keys = [a for a in [p.answer, *p.aliases] if normalize(a)]
    if got in {normalize(a) for a in keys}:
        return CheckResult(ok=True, score=1.0, detail="Correct")
    # Same words in another valid form: a full stop or question marks around the answer, a thousands
    # comma (1,776), an English article dropped or added. Accents are never folded: esta is not est\u00e1.
    loose = _loose(response)
    if loose and any(_loose(response, keep_a=_is_name_a(a)) == _loose(a, keep_a=_is_name_a(a)) for a in keys):
        return CheckResult(ok=True, score=1.0, detail="Correct")
    if loose and _fold_accents(loose) in {_fold_accents(_loose(a)) for a in keys}:
        return _no("Not correct, but close: check your accents. In Spanish and French an accent can change the word.")
    if _NUMERIC_DATE.search(normalize(response)):
        return _no("Not correct. A date with slashes is read month/day/year (US order): 3/9/1783 means March 9, 1783.")
    return _no("Not correct.")


_EDGE = re.compile(r"^[^\w$%\u00b0]+|[^\w$%\u00b0]+$")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_ARTICLE = re.compile(r"^(?:the|a|an) (?=\S)")
_ARTICLE_NO_A = re.compile(r"^(?:the|an) (?=\S)")
# "A minor", "A major", "A flat": the capital A is part of the name, not an article to drop.
_NAME_A = re.compile(r"^\s*A (?:major|minor|flat|sharp|natural)\b")
_NUMERIC_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{3,4})(?![\d/])")
_CENTURY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th) century\b(?!\s*(?:bc|b\.c\.|bce|b\.c\.e\.))")


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MONTH_ALIAS = {m[:3]: m for m in _MONTHS} | {"sept": "september"}
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(_MONTH_ALIAS, key=len, reverse=True)) + r")\b\.?")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b(?!\s+(?:century|millennium))")
_DAY_FIRST = re.compile(r"\b(\d{1,2}) (" + "|".join(_MONTHS) + r") (\d{3,4})\b")
_DECADE = re.compile(r"\b(\d{3}0)'s\b")
_ERA = {"bc": "bce", "b.c.": "bce", "b.c.e.": "bce", "ad": "ce", "a.d.": "ce", "c.e.": "ce"}
_CIRCA = re.compile(r"\b(?:circa|ca\.?|c\.)\s*(?=\d)")
_UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_NUM_WORD = re.compile(r"\b(?:(one hundred)|(" + "|".join(_TENS) + r")(?:[- ](one|two|three|four|five|six|seven|eight|nine))?|(" +
                       "|".join(_UNITS) + r"))\b")


def _num_word(m: re.Match) -> str:
    if m.group(1):
        return "100"
    if m.group(4):
        return str(_UNITS.index(m.group(4)))
    return str(_TENS[m.group(2)] + (_UNITS.index(m.group(3)) if m.group(3) else 0))


def _is_name_a(key: str) -> bool:
    return bool(_NAME_A.match(key))


def _us_date(m: re.Match) -> str:
    mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)                                  # not a real month/day: leave it, it will not match
    return f"{_MONTHS[mo - 1]} {d} {int(y)}"


def _loose(s: str, keep_a: bool = False) -> str:
    """normalize() plus the harmless variations: edge punctuation off, 1,776 -> 1776, no leading the/a/an,
    dates in any common order or abbreviation (Sept. 3 1783 = 3 September 1783 = September 3rd, 1783),
    1860's = 1860s, BC = BCE, circa dropped, number words = digits (twenty-seven = 27), 9/3/1783 = September 3,
    1783 (month/day/year, US order), 18th century = 1700s. keep_a: the "a" of a name like "A minor" stays."""
    t = _EDGE.sub("", normalize(s))
    t = _NUMERIC_DATE.sub(_us_date, t)
    t = _CENTURY.sub(lambda m: f"{(int(m.group(1)) - 1) * 100}s", t)
    t = _THOUSANDS.sub("", t)
    t = _DECADE.sub(r"\1s", t)
    t = _MONTH_RE.sub(lambda m: _MONTH_ALIAS[m.group(1)], t)
    t = _ORDINAL.sub(r"\1", t)
    t = _CIRCA.sub("", t)
    t = re.sub(r"\s*,\s*", " ", t)                       # commas between words/dates carry no meaning here
    t = " ".join(_ERA.get(w, w) for w in t.split())
    t = _DAY_FIRST.sub(lambda m: f"{m.group(2)} {m.group(1)} {m.group(3)}", t)
    t = _NUM_WORD.sub(_num_word, t)
    return (_ARTICLE_NO_A if keep_a else _ARTICLE).sub("", t)


def _fold_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def check_order(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    p = item.payload
    if not isinstance(p, OrderPayload):
        return _no("Not an ordering item.")
    n = len(p.events)
    if not isinstance(response, (list, tuple)):
        return _no("Put the events in order first.")
    seq = [_int(v) for v in response]
    if None in seq or sorted(seq) != list(range(n)):          # type: ignore[type-var]
        return _no("Place every event exactly once.")
    if seq == list(p.correct_order):
        return CheckResult(ok=True, score=1.0, detail="Correct")
    rank = {ev: pos for pos, ev in enumerate(p.correct_order)}
    pairs = n - 1
    good = sum(1 for a, b in zip(seq, seq[1:]) if rank[a] < rank[b])      # type: ignore[index]
    score = good / pairs if pairs else 0.0
    return _no(f"{good} of {pairs} neighbouring pairs are in the right order.", score=min(score, 0.999))


def check_match(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    p = item.payload
    if not isinstance(p, MatchPayload):
        return _no("Not a matching item.")
    lefts = [m.left for m in p.pairs]
    truth = {m.left: m.right for m in p.pairs}
    rights = _shuffled([m.right for m in p.pairs], item.id)       # exactly what public_view shows
    chosen: dict[str, str] = {}
    if isinstance(response, dict):
        for k, v in response.items():
            if isinstance(k, str) and isinstance(v, str):
                chosen[k] = v
    elif isinstance(response, (list, tuple)):
        for pair in response:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                return _no("Match each item with one choice.")
            li, ri = _int(pair[0]), _int(pair[1])
            if li is None or ri is None or not 0 <= li < len(lefts) or not 0 <= ri < len(rights):
                return _no("Match each item with one choice.")
            if lefts[li] in chosen:
                return _no("Each item can only be matched once.")
            chosen[lefts[li]] = rights[ri]
    else:
        return _no("Match the items first.")
    if len(chosen) < len(lefts):
        return _no("Match every item before checking.")
    right = sum(1 for left in lefts if normalize(chosen.get(left, "")) == normalize(truth[left]))
    if right == len(lefts):
        return CheckResult(ok=True, score=1.0, detail="Correct")
    return _no(f"{right} of {len(lefts)} matches are right.", score=min(right / len(lefts), 0.999))


def _number_tolerant(term: str) -> str:
    """Regex for a term whose last word may be typed singular or plural ("chloroplast" ~ "chloroplasts")."""
    base = term[:-1] if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "is", "us")) else term
    if not base[-1:].isalpha():
        return re.escape(term)
    return re.escape(base) + r"(?:e?s)?"


def check_short(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    p = item.payload
    if not isinstance(p, ShortPayload):
        return _no("Not a short-answer item.")
    if not isinstance(response, str) or not normalize(response):
        return _no("Write your answer first.")
    text = normalize(response)
    terms = [normalize(t) for t in p.required_terms if normalize(t)]
    need = len(terms) if p.min_terms is None else min(p.min_terms, len(terms))
    found = sum(1 for t in terms if re.search(r"(?<!\w)" + _number_tolerant(t) + r"(?!\w)", text))
    if need <= 0:
        return _no("This item has no scoring terms.")
    if found >= need:
        return CheckResult(ok=True, score=1.0, detail="Correct")
    return _no(f"Your answer covers {found} of the {need} key ideas needed.", score=min(found / need, 0.999))


def check_explain(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    if not isinstance(item.payload, ExplainPayload):
        return _no("Not an explain item.")
    if isinstance(response, bool) or response is None:
        return CheckResult(ok=False, score=0.0, undecided=True, detail="Rate your answer first.")
    try:
        r = float(response)
    except (TypeError, ValueError):
        return CheckResult(ok=False, score=0.0, undecided=True, detail="Rate your answer first.")
    if r != r or not 0.0 <= r <= 1.0:
        return CheckResult(ok=False, score=0.0, undecided=True, detail="Rate your answer between 0 and 1.")
    return CheckResult(ok=r >= 0.5, score=r, detail="Self-rated")
