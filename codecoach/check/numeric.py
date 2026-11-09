"""Numeric answers with units (science). Units come from sympy.physics.units (imported lazily).

Unit strings are parsed by a tiny recursive-descent parser over an ALLOW-LIST of unit names; nothing is
eval'd or sympified. Grammar:  unit := term (('*'|'.'|'/'|space) term)* ;  term := atom ('^' int | '**' int)? ;
atom := name | '(' unit ')' | '1'.

Public API:
    check_numeric(item, response, *, mode="submit") -> CheckResult     # kind "numeric"
    parse_quantity(text) -> (value: float, unit_text: str)
    convert(value, from_unit, to_unit) -> float
    UnitError
"""

from __future__ import annotations

import functools
import math
import re
from typing import Any

from codecoach.core.models import NumericPayload
from codecoach.core.registry import CheckResult


class UnitError(ValueError):
    pass


_NUM = re.compile(
    r"^\s*([+\-\u2212]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+\-]?\d+)?)"
    r"(?:\s*(?:[x\u00d7*]|\u22c5)\s*10\s*(?:\^|\*\*)\s*\(?([+\-\u2212]?\d+)\)?)?\s*(.*?)\s*$", re.S)

# name -> (SI-prefixable?, attribute on sympy.physics.units or a scale/expression spec)
_BASE = {
    "m": "meter", "g": "gram", "s": "second", "A": "ampere", "K": "kelvin", "mol": "mole", "cd": "candela",
    "N": "newton", "J": "joule", "W": "watt", "Pa": "pascal", "Hz": "hertz", "V": "volt", "ohm": "ohm",
    "C": "coulomb", "F": "farad", "H": "henry", "T": "tesla", "Wb": "weber", "L": "liter", "l": "liter",
}
_PREFIX = {"n": -9, "u": -6, "\u00b5": -6, "\u03bc": -6, "m": -3, "c": -2, "d": -1, "h": 2, "k": 3, "M": 6,
           "G": 9, "T_": 12}
_FIXED = {
    "min": ("minute", 1), "hr": ("hour", 1), "h": ("hour", 1), "day": ("day", 1), "atm": ("atmosphere", 1),
    "bar": ("bar", 1), "eV": ("electronvolt", 1), "cal": ("calorie", 1), "lb": ("pound", 1),
    "in": ("inch", 1), "ft": ("foot", 1), "mi": ("mile", 1), "mph": None, "u": None,
    "ohm": ("ohm", 1), "\u03a9": ("ohm", 1), "deg": None,
}
_ALIASES = {"\u03a9": "ohm", "\u00b0C": "degC", "meter": "m", "metre": "m", "meters": "m", "metres": "m",
            "gram": "g", "grams": "g", "second": "s", "seconds": "s", "sec": "s", "newton": "N", "newtons": "N",
            "joule": "J", "joules": "J", "watt": "W", "watts": "W", "pascal": "Pa", "hertz": "Hz",
            "volt": "V", "volts": "V", "amp": "A", "amps": "A", "ampere": "A", "kilogram": "kg",
            "kilograms": "kg", "liter": "L", "litre": "L", "hour": "h", "hours": "h", "minutes": "min",
            "minute": "min", "kilometer": "km", "kilometers": "km", "days": "day",
            "mole": "mol", "moles": "mol", "kelvin": "K", "kelvins": "K", "coulomb": "C", "coulombs": "C",
            "farad": "F", "farads": "F", "henry": "H", "tesla": "T", "weber": "Wb", "ohms": "ohm"}


@functools.lru_cache(maxsize=1)
def _u():
    from sympy.physics import units as u
    return u


def _lookup(name: str):
    """Return a sympy unit expression for an allow-listed name, else raise UnitError."""
    u = _u()
    import sympy as sp
    name = _ALIASES.get(name, name)
    if name in ("1", ""):
        return sp.Integer(1)
    if name == "mph":
        return u.mile / u.hour
    if name == "degC" or name == "degF":
        raise UnitError("temperature in \u00b0C/\u00b0F is not supported: use K")
    if name in _FIXED and _FIXED[name]:
        return getattr(u, _FIXED[name][0])
    if name in _BASE:
        return getattr(u, _BASE[name])
    # prefix + base (kg, mm, kPa, ...). "T" as prefix is not supported (tesla only).
    if len(name) >= 2 and name[0] in _PREFIX and name[0] != "T":
        rest = name[1:]
        if rest in _BASE:
            return sp.Integer(10) ** _PREFIX[name[0]] * getattr(u, _BASE[rest])
    if name.startswith("da") and name[2:] in _BASE:
        return 10 * getattr(u, _BASE[name[2:]])
    raise UnitError(f"unknown unit '{name}'")


_TOK = re.compile(r"\s*(?:([A-Za-z\u00b5\u03bc\u03a9\u00b0]+)|(\d+)|(\*\*|[*/^().\u00b7\u22c5-]))")

_SUP_RUN = re.compile(r"[\u207a\u207b]?[\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+")
_SUP_MAP = str.maketrans("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b", "0123456789+-")
_BRACED_EXP = re.compile(r"\^\s*\{\s*([+\-\u2212]?\d+)\s*\}")
_LEAD_IN = re.compile(r"^\s*(?:(?:about|approximately|approx\.?|roughly|around)\b\s*|[=\u2248~]\s*)+", re.I)


def _normalise_notation(text: str) -> str:
    """Superscript runs (10\u00b2\u00b3, s\u207b\u00b2) -> ^23, ^-2; 10^{23} -> 10^23."""
    text = _SUP_RUN.sub(lambda m: "^" + m.group().translate(_SUP_MAP).lstrip("+"), text)
    return _BRACED_EXP.sub(lambda m: "^" + m.group(1), text)


def parse_unit(text: str):
    """Unit string -> sympy expression (value 1 of that unit). Raises UnitError."""
    s = _normalise_notation((text or "").strip()).replace("\u2212", "-")
    s = re.sub(r"\bper\b", "/", s, flags=re.I)
    s = re.sub(r"\bsquared\b", "^2", s, flags=re.I)
    s = re.sub(r"\bcubed\b", "^3", s, flags=re.I)
    s = s.replace("\u00b7", "*").replace("\u22c5", "*")
    if s.startswith("/"):
        s = "1" + s                    # "per mole", "/mol"
    if len(s) > 60:
        raise UnitError("unit text too long")
    toks: list[tuple[str, str]] = []
    pos = 0
    while pos < len(s):
        if s[pos:].strip() == "":
            break
        m = _TOK.match(s, pos)
        if not m:
            raise UnitError(f"cannot read unit near '{s[pos:pos + 6]}'")
        if m.group(1):
            toks.append(("name", m.group(1)))
        elif m.group(2):
            toks.append(("int", m.group(2)))
        else:
            toks.append(("op", m.group(3)))
        pos = m.end()
    if not toks:
        import sympy as sp
        return sp.Integer(1)
    idx = 0

    def peek():
        return toks[idx] if idx < len(toks) else (None, None)

    def atom():
        nonlocal idx
        k, v = peek()
        if k == "name":
            idx += 1
            return _lookup(v)
        if k == "int" and v == "1":
            idx += 1
            import sympy as sp
            return sp.Integer(1)
        if (k, v) == ("op", "("):
            idx += 1
            e = expr()
            if peek() != ("op", ")"):
                raise UnitError("missing ')'")
            idx += 1
            return e
        raise UnitError("unit expected")

    def term():
        nonlocal idx
        base = atom()
        if peek() in (("op", "^"), ("op", "**")):
            idx += 1
            neg = False
            if peek() == ("op", "-"):
                neg = True
                idx += 1
            k, v = peek()
            if k != "int":
                raise UnitError("exponent must be an integer")
            idx += 1
            n = int(v)
            if n > 6:
                raise UnitError("exponent too large")
            base = base ** (-n if neg else n)
        return base

    def expr():
        nonlocal idx
        e = term()
        while True:
            k, v = peek()
            if (k, v) in (("op", "*"), ("op", ".")):
                idx += 1
                e = e * term()
            elif (k, v) == ("op", "/"):
                idx += 1
                e = e / term()
            elif k in ("name",) or (k, v) == ("op", "("):
                e = e * term()          # juxtaposition: "N m"
            else:
                return e

    out = expr()
    if idx != len(toks):
        raise UnitError("could not read the whole unit")
    return out


def _si(expr):
    """Express in SI base units: (numeric factor, dimension-signature)."""
    import sympy as sp
    u = _u()
    base = [u.meter, u.kilogram, u.second, u.ampere, u.kelvin, u.mole, u.candela]
    c = u.convert_to(expr, base)
    factor, rest = c.as_coeff_Mul()
    sig = {}
    for f in sp.Mul.make_args(rest):
        b, e = f.as_base_exp()
        if f != 1:
            sig[str(b)] = e
    # as_coeff_Mul leaves symbolic coefficients (e.g. 1000) in `factor`; all good
    return float(sp.N(factor)), tuple(sorted(sig.items()))


_DIM_NAMES = {
    (("kilogram", 1), ("meter", 1), ("second", -2)): "force", (("meter", 1), ("second", -1)): "speed",
    (("meter", 1),): "length", (("kilogram", 1),): "mass", (("second", 1),): "time",
    (("kilogram", 1), ("meter", 2), ("second", -2)): "energy",
    (("kilogram", 1), ("meter", 2), ("second", -3)): "power",
    (("kilogram", 1), ("meter", -1), ("second", -2)): "pressure",
    (("meter", 1), ("second", -2)): "acceleration", (("kelvin", 1),): "temperature", (): "a pure number",
}


def _kind(sig) -> str:
    return _DIM_NAMES.get(sig, "a different kind of quantity")


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert `value` from one unit string to another; UnitError if the dimensions differ."""
    f1, s1 = _si(parse_unit(from_unit))
    f2, s2 = _si(parse_unit(to_unit))
    if s1 != s2:
        raise UnitError(f"'{from_unit}' ({_kind(s1)}) cannot be converted to '{to_unit}' ({_kind(s2)})")
    return value * f1 / f2


def parse_quantity(text: str) -> tuple[float, str]:
    """'6.5 N' -> (6.5, 'N'); '3e8 m/s'; '1.2 x 10^3 kg'. Raises UnitError when no number is found."""
    text = _LEAD_IN.sub("", _normalise_notation(text or ""))
    m = _NUM.match(text)
    if not m:
        raise UnitError("I could not find a number in your answer")
    num = m.group(1).replace("\u2212", "-").replace(",", ".")
    value = float(num)
    if m.group(2):
        try:
            value *= 10.0 ** int(m.group(2).replace("\u2212", "-"))
        except OverflowError:
            raise UnitError("that number is too large") from None
    if not math.isfinite(value):
        raise UnitError("that number is too large")
    return value, m.group(3).strip().rstrip(".").strip()


def _close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= rel * abs(b) + (1e-12 if b == 0 else 0.0)


def _sentence(s: str) -> str:
    """Capitalise and end with exactly one full stop (unit errors arrive as bare lower-case fragments)."""
    s = s.strip().rstrip(".!? ")
    return (s[:1].upper() + s[1:] + ".") if s else ""


def check_numeric(item: Any, response: Any, *, mode: str = "submit") -> CheckResult:
    """response = string that may carry a unit ('6 N'); a bare number is accepted (int/float/str)."""
    p = getattr(item, "payload", None)
    if not isinstance(p, NumericPayload):
        return CheckResult(ok=False, score=0.0, detail="Not a numeric item.")
    if isinstance(response, bool) or response is None:
        return CheckResult(ok=False, score=0.0, detail="Type your answer first.")
    text = repr(response) if isinstance(response, (int, float)) else response
    if not isinstance(text, str) or not text.strip():
        return CheckResult(ok=False, score=0.0, detail="Type your answer first.")
    if len(text) > 120:
        return CheckResult(ok=False, score=0.0, detail="That answer is too long.")
    try:
        value, unit_text = parse_quantity(text)
    except UnitError as e:
        return CheckResult(ok=False, score=0.0, detail=_sentence(str(e)))
    want_unit = p.unit.strip()
    rel = p.rel_tol
    try:
        target_f, target_sig = _si(parse_unit(want_unit))
    except UnitError as e:                      # the item itself is broken: not the learner's fault
        return CheckResult(ok=False, score=0.0, undecided=True, detail=f"This item's unit could not be read ({e}).")
    dimensionless = target_sig == ()
    if unit_text == "" and not dimensionless:
        if _close(value, p.answer_value, rel):
            return CheckResult(ok=False, score=0.0,
                               detail=f"The number is right but the unit is missing. Add the unit ({want_unit}).")
        return CheckResult(ok=False, score=0.0, detail=f"Not correct, and the unit ({want_unit}) is missing.")
    try:
        given_f, given_sig = _si(parse_unit(unit_text))
    except UnitError as e:
        return CheckResult(ok=False, score=0.0, detail=_sentence(f"I could not read the unit '{unit_text}': {e}"))
    if given_sig != target_sig:
        if _close(value, p.answer_value, rel):
            return CheckResult(ok=False, score=0.0, detail=(
                f"The number is right but the unit is wrong: '{unit_text}' measures {_kind(given_sig)}, "
                f"and this answer is {_kind(target_sig)} (expected {want_unit})."))
        return CheckResult(ok=False, score=0.0, detail=(
            f"Not correct: '{unit_text}' measures {_kind(given_sig)}, but this needs {want_unit}."))
    converted = value * given_f / target_f
    if _close(converted, p.answer_value, rel):
        return CheckResult(ok=True, score=1.0, detail="Correct")
    if given_f != target_f and _close(value, p.answer_value, rel):
        return CheckResult(ok=False, score=0.0, detail=(
            f"The number is right but the unit is wrong: {text.strip()} is not the same amount as "
            f"{p.answer_value:g} {want_unit}. Check the unit prefix."))
    return CheckResult(ok=False, score=0.0, detail=f"Not correct. Check your calculation (answer in {want_unit}).")
