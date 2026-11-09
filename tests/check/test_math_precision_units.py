"""Fair precision and units for evaluate items, and honest math hints."""

import pytest

from codecoach.adapt.hints import math_rule
from codecoach.check import math_sympy as ms  # noqa: F401  (registers the checker)
from codecoach.core.models import MathPayload
from codecoach.core.public import _units_ok
from codecoach.core.registry import check
from codecoach.core.samples import sample_item
from codecoach.gen.precision import needs_places, with_precision


def _ev(expr, prompt="Evaluate the expression.", places=None):
    return sample_item("math", prompt=prompt, payload=MathPayload(op="evaluate", expr=expr, given_answer=None, places=places))


@pytest.mark.parametrize("key,need", [("5", False), ("0.5", False), ("1/2", False), ("3/8", False), ("1/3", True),
                                      ("10/3", True), ("sqrt(2)", True), ("pi", True), ("0.0625", True),
                                      ("2.25", False), ("1/7", True), ("-4", False)])
def test_needs_places(key, need):
    assert needs_places(key) is need


def test_prompt_states_the_precision_once():
    assert with_precision("Evaluate sqrt(2)", 2).endswith("Evaluate sqrt(2). Give your answer to 2 decimal places.")
    p = "Round to 2 decimal places: sqrt(2)"
    assert with_precision(p, 2) == p


@pytest.mark.parametrize("resp,ok", [("1", False), ("1.4", False), ("1.41", True), ("1.414", True), ("1.4142", True),
                                     ("1.42", False), ("sqrt(2)", True), ("\u221a2", True), ("1.40", False)])
def test_places_enforced(resp, ok):
    assert check(_ev("sqrt(2)", places=2), resp).ok is ok


def test_places_feedback_says_what_is_wanted():
    assert "2 decimal places" in check(_ev("sqrt(2)", places=2), "1").detail


def test_without_places_nothing_changes():
    assert check(_ev("sqrt(2)"), "1.414").ok


@pytest.mark.parametrize("resp", ["25", "25 percent", "25 %", "25%", "25 apples"])
def test_percent_and_words_on_bare_key(resp):
    item = _ev("25", prompt="A shop marks a 40 dollar coat down by 25 percent of its price. What is the discount in percent?")
    assert check(item, resp).ok


@pytest.mark.parametrize("resp,ok", [("5", True), ("5 m", True), ("5 meters", True), ("5m", True), ("5 km", False),
                                     ("5000 m", False), ("6 m", False)])
def test_different_unit_is_not_accepted(resp, ok):
    item = _ev("5", prompt="A rope is cut into pieces and one piece is 5 m long. How long is that piece, in metres?")
    assert check(item, resp).ok is ok


def test_half_percent_still_means_hundredths():
    assert check(_ev("1/2"), "50%").ok


def test_units_ok_only_for_word_problems():
    p = MathPayload(op="evaluate", expr="25", given_answer=None)
    assert _units_ok(p, "A rope is cut and one piece is 5 m long. How long is that piece in total?")
    assert not _units_ok(p, "Evaluate 3x + 2 at x = 4.")
    assert not _units_ok(MathPayload(op="diff", expr="x", given_answer=None), "A rope is 5 m long and so on and on.")


@pytest.mark.parametrize("op,expr,words", [
    ("diff", "3*x**2 + 5*x", "power rule on each term"),
    ("diff", "x*sin(x)", "product rule"),
    ("diff", "sin(x**2)", "chain rule"),
    ("diff", "(x+1)/(x-1)", "quotient rule"),
    ("integrate", "2*x + 1", "power rule for integrals"),
    ("solve", "x**2 - 4 = 0", "factor"),
    ("solve", "(x+1)/(2*x-1) = 2", "common denominator"),
    ("solve", "2*x + 3 = 7", "alone"),
])
def test_math_hint_level_one_names_the_rule(op, expr, words):
    first, second = math_rule(MathPayload(op=op, expr=expr, given_answer=None))
    assert words in first
    assert "Name the rule" not in first and second
