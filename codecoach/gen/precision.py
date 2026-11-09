"""Evaluate items with an inexact answer say how many decimal places to give. No LLM, no network."""

from __future__ import annotations

import re
from fractions import Fraction

_PLAIN = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?$")
SHORT = 3          # a terminating decimal of up to this many places is "exact enough" to ask for as it is


def needs_places(key: str | None) -> bool:
    """True when the exact answer is not an integer or a short terminating decimal (sqrt(2), pi, 10/3, 1/7)."""
    k = re.sub(r"\s+", "", (key or "").replace("\u2212", "-"))
    if not k or not _PLAIN.match(k):
        return True                       # sqrt(2), pi, 3*sqrt(5)... anything symbolic is inexact
    try:
        f = Fraction(k)
    except (ValueError, ZeroDivisionError):
        return True
    d = f.denominator
    places = 0
    while d % 10 == 0:
        d //= 10
        places += 1
    # reduce to the digits needed for a terminating decimal
    two = five = 0
    while d % 2 == 0:
        d //= 2
        two += 1
    while d % 5 == 0:
        d //= 5
        five += 1
    if d != 1:
        return True                       # repeating decimal
    return max(two, five) > SHORT


def with_precision(prompt: str, places: int) -> str:
    if re.search(r"decimal place|significant figure|nearest (?:tenth|hundredth|thousandth|whole)", prompt, re.I):
        return prompt
    tail = "" if prompt.rstrip().endswith((".", "?", "!")) else "."
    return f"{prompt.rstrip()}{tail} Give your answer to {places} decimal places."
