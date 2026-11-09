"""Math (SymPy in child processes) and numeric/unit checkers."""

import os
import time

import pytest

from codecoach.check import math_sympy as ms
from codecoach.check import numeric as nm
from codecoach.core.models import MathPayload, NumericPayload
from codecoach.core.registry import check
from codecoach.core.samples import sample_item


def _m(op, expr, given=None, var="x", subs=None):
    return sample_item("math", payload=MathPayload(op=op, expr=expr, given_answer=given, var=var, subs=subs))


def test_worker_sees_sympy_when_the_interpreter_has_none_of_its_own(monkeypatch):
    """Live bug: Hermes runs a bare interpreter and adds its package environment to sys.path at startup.
    The worker (sys.executable + clean env) then failed with "No module named 'sympy'" and every generated
    math item was dropped. Reproduce it with this venv's base interpreter, which has no SymPy."""
    import sys
    base = getattr(sys, "_base_executable", sys.executable)
    if base == sys.executable:
        pytest.skip("not running in a venv, so there is no bare interpreter to reproduce with")
    import subprocess
    has = subprocess.run([base, "-I", "-c", "import importlib.util as u; print(bool(u.find_spec('sympy')))"],
                         capture_output=True, text=True).stdout.strip()
    if has != "False":
        pytest.skip("the base interpreter has SymPy of its own")
    monkeypatch.setattr(sys, "executable", base)
    it = _m("diff", "x**3 + 2*x")
    assert check(it, "3*x^2 + 2").ok
    assert not check(it, "3*x^2").ok


def test_diff_equivalent_forms():
    it = _m("diff", "x**3 + 2*x")
    assert check(it, "3*x^2 + 2").ok
    assert check(it, "2 + 3x^2").ok                    # implicit multiplication, ^ as power
    assert not check(it, "3*x^2").ok


def test_integrate_up_to_constant():
    it = _m("integrate", "2*x")
    assert check(it, "x^2").ok and check(it, "x^2 + 7").ok
    assert not check(it, "x^2 + x").ok


def test_solve_set_equality():
    it = _m("solve", "x**2 - 4 = 0")
    assert check(it, "2, -2").ok and check(it, "{-2, 2}").ok
    assert not check(it, "2").ok and not check(it, "2, -2, 3").ok


def test_simplify_and_evaluate():
    assert check(_m("simplify", "(x**2 - 1)/(x - 1)"), "x + 1").ok
    assert not check(_m("simplify", "(x**2 - 1)/(x - 1)"), "x - 1").ok
    ev = _m("evaluate", "x**2 + 1", subs={"x": "3"})
    assert check(ev, "10").ok and not check(ev, "9").ok


def test_unreadable_and_hostile_input_never_executes(tmp_path):
    marker = tmp_path / "pwned"
    it = _m("diff", "x**2")
    for evil in (f"__import__('os').system('touch {marker}')", f"open('{marker}','w')", "().__class__.__mro__",
                 "x; import os", "lambda: 1"):
        r = check(it, evil)
        assert not r.ok
    assert not marker.exists()


def test_long_input_rejected():
    assert not check(_m("diff", "x"), "x+" * 400 + "1").ok


def test_timeout_falls_back_to_numeric(monkeypatch):
    monkeypatch.setattr(ms, "SYMBOLIC_TIMEOUT_S", 0.01)         # symbolic stage cannot finish
    monkeypatch.setattr(ms, "NUMERIC_TIMEOUT_S", 20.0)
    r = check(_m("diff", "x**3 + 2*x"), "3*x^2 + 2")
    assert r.ok and "same value" in r.detail
    assert not check(_m("diff", "x**3 + 2*x"), "3*x^2 + 3").ok


def test_undecided_when_everything_times_out(monkeypatch):
    monkeypatch.setattr(ms, "SYMBOLIC_TIMEOUT_S", 0.001)
    monkeypatch.setattr(ms, "NUMERIC_TIMEOUT_S", 0.001)
    r = check(_m("diff", "x**3"), "3*x^2")
    assert r.undecided and not r.ok


def test_verify_key_gate():
    assert ms.verify_key(MathPayload(op="diff", expr="sin(x)", given_answer="cos(x)")).ok
    assert not ms.verify_key(MathPayload(op="diff", expr="sin(x)", given_answer="-cos(x)")).ok
    assert not ms.verify_key(MathPayload(op="diff", expr="sin(x)")).ok
    assert ms.truth(MathPayload(op="diff", expr="sin(x)")) in ("cos(x)",)


def test_plot_points():
    pts = ms.plot_points("1/x", "x", -1, 1, 21)
    assert len(pts) == 21 and pts[0] == (-1.0, -1.0)
    assert any(y is None for _, y in pts) or all(abs(x) > 1e-9 for x, _ in pts)
    with pytest.raises(ValueError):
        ms.plot_points("__import__('os')", "x", 0, 1, 5)
    with pytest.raises(ValueError):
        ms.plot_points("x", "x", 2, 1, 5)


def test_no_sympy_import_in_parent_for_math():
    import sys
    assert "codecoach.check.math_sympy" in sys.modules
    # the math module itself must not have pulled sympy in; numeric may (lazily, in function scope)
    src = open(ms.__file__).read()
    assert "import sympy" not in src


# ----------------------------------------------------------------------------- numeric

def _n(value, unit, rel=0.01):
    return sample_item("numeric", payload=NumericPayload(answer_value=value, unit=unit, rel_tol=rel))


def test_parse_quantity_forms():
    assert nm.parse_quantity("6.5 N") == (6.5, "N")
    assert nm.parse_quantity("3e8 m/s") == (3e8, "m/s")
    assert nm.parse_quantity("1,5 kg") == (1.5, "kg")
    v, u = nm.parse_quantity("1.2 x 10^3 kg")
    assert v == pytest.approx(1200) and u == "kg"
    assert nm.parse_quantity("-4")[0] == -4
    with pytest.raises(nm.UnitError):
        nm.parse_quantity("abc")


def test_convert():
    assert nm.convert(1, "km", "m") == pytest.approx(1000)
    assert nm.convert(1, "N", "kg*m/s^2") == pytest.approx(1)
    assert nm.convert(60, "mph", "m/s") == pytest.approx(26.8224, rel=1e-4)
    assert nm.convert(2, "h", "min") == pytest.approx(120)
    with pytest.raises(nm.UnitError):
        nm.convert(1, "m", "s")
    with pytest.raises(nm.UnitError):
        nm.parse_unit("__import__('os')")
    with pytest.raises(nm.UnitError):
        nm.parse_unit("m^99")


def test_numeric_equivalent_units_and_tolerance():
    it = _n(1500.0, "m")
    assert check(it, "1500 m").ok and check(it, "1.5 km").ok and check(it, "1495 m").ok
    assert not check(it, "1400 m").ok
    assert check(_n(10.0, "N"), "10 kg*m/s^2").ok
    assert check(_n(5.0, "m/s"), "18 km/h").ok


def test_right_number_wrong_unit_feedback():
    r = check(_n(1500.0, "m"), "1500 s")
    assert not r.ok and "number is right" in r.detail and "unit" in r.detail
    r = check(_n(1500.0, "m"), "1500 km")
    assert not r.ok and "number is right" in r.detail
    r = check(_n(1500.0, "m"), "1500")
    assert not r.ok and "unit is missing" in r.detail
    r = check(_n(1500.0, "m"), "7 s")
    assert not r.ok and "number is right" not in r.detail


def test_numeric_bad_inputs_and_dimensionless():
    assert not check(_n(2.0, "m"), "banana").ok
    assert not check(_n(2.0, "m"), "2 flurbs").ok
    assert not check(_n(2.0, "m"), None).ok
    assert check(_n(0.5, ""), "0.5").ok
    assert check(_n(2.0, "m"), 2.0).ok is False        # bare number: unit missing
    assert check(_n(0.0, "m"), "0 m").ok
    assert check(_n(2.0, "wibble"), "2 m").undecided   # broken item is not the learner's fault


# ----------------------------------------------------------------------------- science notation a student really types

@pytest.mark.parametrize("value,unit,resp", [
    (9.8, "m/s^2", "9.8 m s^-2"),
    (9.8, "m/s^2", "9.8 m\u00b7s\u207b\u00b2"),
    (9.8, "m/s^2", "9.8 meters per second squared"),
    (6.022e23, "", "6.022 \u00d7 10\u00b2\u00b3"),
    (6.022e23, "", "6.022 \u00d7 10^{23}"),
    (6.022e23, "1/mol", "6.022 \u00d7 10\u00b2\u00b3 mol\u207b\u00b9"),
    (6.022e23, "1/mol", "6.022e23 per mole"),
    (18.015, "g/mol", "18.015 g mol^-1"),
    (18.015, "g/mol", "18.02 grams per mole"),
    (2.0, "mol", "2.0 moles"),
    (298.15, "K", "298.15 kelvin"),
    (3.00e8, "m/s", "3.00 \u00d7 10\u2078 m/s"),
    (1.6e-19, "C", "1.6 \u00d7 10\u207b\u00b9\u2079 C"),
    (1.6e-19, "C", "1.6e-19 coulombs"),
    (4.18, "J/(g*K)", "4.18 J g\u207b\u00b9 K\u207b\u00b9"),
    (12.0, "g", "12 g."),
    (12.0, "g", "about 12 g"),
    (12.0, "g", "= 12 g"),
])
def test_numeric_accepts_science_notation(value, unit, resp):
    r = check(_n(value, unit), resp)
    assert r.ok, r.detail


@pytest.mark.parametrize("value,unit,resp", [
    (9.8, "m/s^2", "9.8 m s^-1"),               # right number, wrong unit: still wrong
    (6.022e23, "", "6.022 \u00d7 10\u00b2\u00b2"),   # wrong exponent
    (6.022e23, "1/mol", "6.022 \u00d7 10\u00b2\u00b3 mol"),
    (18.015, "g/mol", "18.015 g"),
    (2.0, "mol", "2.0 moles per liter"),
])
def test_numeric_science_notation_wrong_stays_wrong(value, unit, resp):
    assert not check(_n(value, unit), resp).ok


def test_numeric_huge_power_of_ten_is_a_friendly_wrong_not_a_crash():
    """Break-it B5: '1 x 10^99999 N' raised OverflowError out of the checker (a 500 for the student)."""
    it = sample_item("numeric", payload=NumericPayload(answer_value=6.0, unit="N"))
    for resp in ("1 x 10^99999 N", "2 x 10^400 N", "1e308 x 10^308 N"):
        r = check(it, resp)
        assert r.ok is False and "too large" in r.detail
