"""Forms a student really types (break-it card `math`). Each was marked wrong or unreadable before."""

import pytest

from codecoach.check import math_sympy as ms  # noqa: F401  (registers the checker)
from codecoach.core.models import MathPayload
from codecoach.core.registry import check
from codecoach.core.samples import sample_item


def _m(op, expr, var="x", subs=None):
    return sample_item("math", payload=MathPayload(op=op, expr=expr, given_answer=None, var=var, subs=subs))


@pytest.mark.parametrize("resp", ["\u00bd", "1/2", "0.5", "50%", "50 %"])
def test_half_in_every_notation(resp):
    assert check(_m("evaluate", "1/2"), resp).ok


@pytest.mark.parametrize("resp", ["x\u00b2", "x\u00b2 + C", "x^2", "x**2 + c"])
def test_superscript_two_is_a_square(resp):
    assert check(_m("integrate", "2*x"), resp).ok
    assert not check(_m("integrate", "2*x"), "x\u00b3").ok


@pytest.mark.parametrize("resp", ["\u221a2", "\u221a(2)", "sqrt(2)", "\u221a 2"])
def test_square_root_sign(resp):
    assert check(_m("evaluate", "sqrt(2)"), resp).ok
    assert not check(_m("evaluate", "sqrt(2)"), "\u221a3").ok


@pytest.mark.parametrize("resp", ["1,000", "1 000", "1000", "$1,000"])
def test_thousands_separators(resp):
    assert check(_m("evaluate", "1000"), resp).ok
    assert not check(_m("evaluate", "1000"), "1,001").ok


def test_percent_means_hundredths():
    it = _m("evaluate", "1/4")
    assert check(it, "25%").ok and check(it, "0.25").ok
    assert not check(it, "2.5%").ok and not check(it, "25").ok


def test_comma_list_is_still_a_solution_list():
    it = _m("solve", "x**2 - 5*x + 6 = 0")
    assert check(it, "2, 3").ok and check(it, "x=2 or x=3").ok
    assert not check(it, "2, 4").ok


def test_exact_half_does_not_round_both_ways():
    """3.5 is not 3. A bare integer is a rounding to the units place, and 3.5 rounds up."""
    it = _m("evaluate", "7/2")
    assert check(it, "3.5").ok and check(it, "4").ok
    assert not check(it, "3").ok


@pytest.mark.parametrize("resp", ["no real solution", "no real solutions", "none", "\u2205", "{}"])
def test_no_real_solution_is_recognised(resp):
    assert check(_m("solve", "x**2 + 1 = 0"), resp).ok


def test_division_by_zero_does_not_leak_a_python_error():
    r = check(_m("diff", "x**2"), "1/0")
    assert not r.ok and "KeyError" not in r.detail and "ComplexInfinity" not in r.detail
