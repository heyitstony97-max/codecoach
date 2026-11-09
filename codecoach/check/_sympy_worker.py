"""Child-process worker for math_sympy. Reads ONE JSON request on stdin, writes ONE JSON reply on stdout.

Run as `python -P _sympy_worker.py` so this directory is not put on sys.path. All SymPy work happens here,
so the parent can kill it on a timeout. Learner strings are parsed with parse_expr + a restricted
local_dict + a fixed set of transformations; they are never sympified or eval'd directly.
"""

import json
import random
import re
import sys

_ALLOWED = re.compile(r"^[0-9A-Za-z_+\-*/^().,\s=!]*$")
MAX_LEN = 300


class BadInput(Exception):
    """The string cannot be parsed or is not allowed."""


def _env(var):
    import sympy as sp
    from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application,
                                            standard_transformations)
    funcs = {n: getattr(sp, n) for n in (
        "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "sinh", "cosh", "tanh",
        "exp", "log", "sqrt", "Abs", "pi", "E", "oo", "I")}
    funcs.update({"ln": sp.log, "abs": sp.Abs, "arcsin": sp.asin, "arccos": sp.acos, "arctan": sp.atan})
    if var != "e":
        funcs["e"] = sp.E
    local = dict(funcs)
    local[var] = sp.Symbol(var)
    glob = {"Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational, "Symbol": sp.Symbol,
            "Function": sp.Function, "__builtins__": {}}
    trans = standard_transformations + (implicit_multiplication_application, convert_xor)
    return sp, local, glob, trans


_VULGAR = {"\u00bd": "1/2", "\u2153": "1/3", "\u2154": "2/3", "\u00bc": "1/4", "\u00be": "3/4", "\u2155": "1/5",
           "\u215b": "1/8"}
_SUP = {c: str(i) for i, c in enumerate("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079")}


def _normalize(s):
    """Turn what a student types on a phone or copies from the book into plain ASCII the parser reads:
    vulgar fractions, superscript powers, the root sign, percent, a currency sign and thousands separators."""
    s = s.strip().replace("\u2212", "-").replace("\u00d7", "*").replace("\u00b7", "*").replace("\u22c5", "*")
    s = s.replace("\u03c0", "pi").lstrip("$")
    for ch, v in _VULGAR.items():
        s = re.sub(r"(\d)" + ch, r"(\1+" + v + ")", s).replace(ch, "(" + v + ")")
    s = re.sub("(\u207b?)([" + "".join(_SUP) + "]+)",
               lambda m: "**(" + ("-" if m.group(1) else "") + "".join(_SUP[c] for c in m.group(2)) + ")", s)
    s = re.sub(r"\u221a\s*(\d+(?:\.\d+)?|[A-Za-z]\w*)", r"sqrt(\1)", s).replace("\u221a", "sqrt")
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    s = re.sub(r"(?<![\d.,])(\d{1,3})((?:[, ]\d{3})+)(?![\d,])", lambda m: m.group(1) + re.sub(r"[, ]", "", m.group(2)), s)
    return s


def parse(s, var, allow_eq=False):
    sp, local, glob, trans = _env(var)
    if not isinstance(s, str):
        raise BadInput("not text")
    s = _normalize(s)
    if not s:
        raise BadInput("empty")
    if len(s) > MAX_LEN:
        raise BadInput("too long")
    if "__" in s or not _ALLOWED.match(s) or "," in s:
        raise BadInput("contains characters that are not allowed")
    if "=" in s and not allow_eq:
        raise BadInput("unexpected '='")
    try:
        if "=" in s:
            if s.count("=") != 1 or "==" in s:
                raise BadInput("bad equation")
            lhs, rhs = s.split("=")
            return sp.Eq(parse_expr(lhs, local, glob, trans), parse_expr(rhs, local, glob, trans))
        return parse_expr(s, local, glob, trans)
    except BadInput:
        raise
    except Exception as e:  # SyntaxError, TokenError, TypeError, ...
        raise BadInput("check the brackets and operators") from None


def parse_expr(s, local, glob, trans):
    from sympy.core.function import AppliedUndef
    from sympy.parsing.sympy_parser import parse_expr as pe
    e = pe(s, local_dict=dict(local), global_dict=dict(glob), transformations=trans, evaluate=True)
    if hasattr(e, "atoms") and e.atoms(AppliedUndef):
        raise BadInput("unknown function")
    if getattr(e, "is_Relational", False):
        raise BadInput("unexpected comparison")
    return e


# ------------------------------------------------------------------ numeric helpers

def _close(a, b):
    return abs(a - b) <= 1e-6 * max(abs(a), abs(b)) + 1e-9


def _to_float(v):
    try:
        c = complex(v)
    except Exception:
        return None
    if c != c or abs(c) == float("inf"):
        return None
    if abs(c.imag) > 1e-9 * max(1.0, abs(c.real)):
        return None
    return c.real


def _sample_points(syms, count=8, seed=12345):
    rng = random.Random(seed)
    for attempt in range(80):
        lo, hi = (0.1, 4.0) if attempt < 40 else (-4.0, 4.0)
        yield {s: rng.uniform(lo, hi) for s in syms}


def _lam(sp, syms, expr):
    return sp.lambdify(syms, expr, modules=["mpmath"])


def numeric_equal(sp, a, b, var):
    """True/False/None (None = fewer than 5 valid points). a, b are sympy expressions."""
    syms = sorted((a.free_symbols | b.free_symbols), key=lambda s: s.name)
    if not syms:
        syms = [sp.Symbol(var)]
    fa, fb = _lam(sp, syms, a), _lam(sp, syms, b)
    good = 0
    for pt in _sample_points(syms):
        args = [pt[s] for s in syms]
        try:
            va, vb = _to_float(fa(*args)), _to_float(fb(*args))
        except Exception:
            continue
        if va is None or vb is None:
            continue
        if not _close(va, vb):
            return False
        good += 1
        if good >= 8:
            break
    return True if good >= 5 else None


def numeric_derivative_equal(sp, resp, target, var):
    """Compare d(resp)/dvar (numerically, via mpmath) with target."""
    import mpmath
    x = sp.Symbol(var)
    syms = sorted((resp.free_symbols | target.free_symbols | {x}), key=lambda s: s.name)
    fr, ft = _lam(sp, syms, resp), _lam(sp, syms, target)
    good = 0
    for pt in _sample_points(syms):
        others = [pt[s] for s in syms]
        idx = syms.index(x)
        try:
            def g(t, others=others):
                a = list(others)
                a[idx] = t
                return fr(*a)
            d = _to_float(mpmath.diff(g, pt[x]))
            t = _to_float(ft(*others))
        except Exception:
            continue
        if d is None or t is None:
            continue
        if not _close(d, t):
            return False
        good += 1
        if good >= 8:
            break
    return True if good >= 5 else None


# ------------------------------------------------------------------ symbolic equivalence

def equivalent(sp, a, b, var):
    d = sp.simplify(a - b)
    if d == 0:
        return True
    n = numeric_equal(sp, a, b, var)
    if n is None:
        return False if d.is_zero is False else None
    return n


def _ops(sp, e):
    return sp.count_ops(e)


# ------------------------------------------------------------------ solve helpers

def _split_solutions(text):
    t = text.strip()
    low = t.lower()
    if low in ("none", "empty", "\u2205", "{}", "no answer") or re.fullmatch(r"no (real )?solutions?\.?", low):
        return []
    t = t.strip("{}[]() \t") if t[:1] in "{[" else t
    parts = re.split(r",|;|\bor\b|\band\b", t)
    return [p.strip() for p in parts if p.strip()]


def _strip_var(part, var):
    m = re.match(r"^\s*" + re.escape(var) + r"\s*=\s*(.+)$", part)
    return m.group(1) if m else part


def _roots(sp, expr, var):
    x = sp.Symbol(var)
    eq = expr.lhs - expr.rhs if getattr(expr, "is_Equality", False) else expr
    sol = sp.solve(eq, x)
    if isinstance(sol, dict):
        sol = [sol[x]] if x in sol else []
    out = []
    for s in sol:
        if isinstance(s, dict):
            if x in s:
                out.append(s[x])
        elif isinstance(s, tuple):
            out.append(s[0])
        else:
            out.append(s)
    return eq, out


# ------------------------------------------------------------------ operations

def do_check(req):
    sp = __import__("sympy")
    op, var = req["op"], req.get("var") or "x"
    numeric = req.get("mode") == "numeric"
    x = sp.Symbol(var)
    try:
        expr = parse(req["expr"], var, allow_eq=(op == "solve"))
    except BadInput as e:
        return {"verdict": "bad_item", "feedback": f"Problem expression: {e}"}
    resp_text = req.get("response")
    if resp_text is None:
        # truth only
        return {"verdict": "truth", "truth": _truth(sp, op, expr, var, req)}
    if op == "solve":
        return _check_solve(sp, expr, var, x, resp_text, numeric)
    try:
        resp = parse(resp_text, var)
    except BadInput as e:
        return {"verdict": "unparseable", "feedback": f"I could not read your answer: {e}."}
    if getattr(resp, "has", None) and resp.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        return {"verdict": "unparseable", "feedback": "I could not read your answer: that value is undefined."}
    if op == "diff":
        if numeric:
            ok = numeric_derivative_equal(sp, sp.Integer(0) + expr, resp, var)  # d(expr)/dx vs resp
        else:
            ok = equivalent(sp, sp.diff(expr, x), resp, var)
        return _verdict(ok, "That is not the derivative.", via_numeric=numeric)
    if op == "integrate":
        if numeric:
            ok = numeric_derivative_equal(sp, resp, expr, var)
        else:
            ok = equivalent(sp, sp.diff(resp, x), expr, var)
        return _verdict(ok, "Differentiate your answer: it should give back the original expression.",
                        via_numeric=numeric)
    if op == "simplify":
        ok = numeric_equal(sp, expr, resp, var) if numeric else equivalent(sp, expr, resp, var)
        if ok:
            truth = expr if numeric else sp.simplify(expr)
            if (not numeric and _ops(sp, resp) >= _ops(sp, expr) > _ops(sp, truth)):
                return {"verdict": "wrong", "feedback": "That is equal to the original, but not simplified."}
        return _verdict(ok, "That is not equal to the original expression.", via_numeric=numeric)
    if op == "evaluate":
        return _check_evaluate(sp, expr, resp, resp_text, req, var)
    return {"verdict": "bad_item", "feedback": f"Unknown op {op}"}


def _verdict(ok, wrong_msg, via_numeric=False):
    if ok is None:
        return {"verdict": "undecided", "feedback": "Could not decide."}
    out = {"verdict": "correct" if ok else "wrong", "feedback": "" if ok else wrong_msg}
    if via_numeric:
        out["numeric"] = True
    return out


def _truth(sp, op, expr, var, req):
    x = sp.Symbol(var)
    if op == "diff":
        return str(sp.simplify(sp.diff(expr, x)))
    if op == "integrate":
        r = sp.integrate(expr, x)
        if r.has(sp.Integral):
            raise BadInput("no closed form")
        return str(r)
    if op == "simplify":
        return str(sp.simplify(expr))
    if op == "solve":
        _, roots = _roots(sp, expr, var)
        return "{" + ", ".join(sorted(str(sp.simplify(r)) for r in roots)) + "}"
    if op == "evaluate":
        return str(_evaluate_truth(sp, expr, req, var))
    raise BadInput("unknown op")


def _evaluate_truth(sp, expr, req, var):
    subs = {}
    for k, v in (req.get("subs") or {}).items():
        subs[sp.Symbol(k)] = parse(v, var)
    val = expr.subs(subs)
    val = sp.simplify(val)
    if val.has(sp.zoo, sp.nan) or val in (sp.oo, -sp.oo):
        raise BadInput("undefined value")
    return val


_DECIMAL = re.compile(r"^\s*[+-]?(\d+\.(\d*)|\.(\d+)|\d+)\s*$")


def _check_evaluate(sp, expr, resp, resp_text, req, var):
    try:
        val = _evaluate_truth(sp, expr, req, var)
    except BadInput as e:
        return {"verdict": "bad_item", "feedback": f"Problem expression: {e}"}
    if resp.free_symbols:
        return {"verdict": "wrong", "feedback": "Your answer should be a number."}
    t = _to_float(sp.N(val, 30))
    r = _to_float(sp.N(resp, 30))
    if t is None or r is None:
        return {"verdict": "wrong", "feedback": "That is not equal to the value."}
    m = _DECIMAL.match(resp_text)
    if m:
        decimals = len(m.group(2) if m.group(2) is not None else (m.group(3) or ""))
        places = req.get("places")
        if places and decimals < int(places):
            return {"verdict": "wrong", "truth": str(val),
                    "feedback": f"Give your answer to {int(places)} decimal place{'s' if int(places) != 1 else ''}."}
        half = 0.5 * 10 ** (-decimals)
        ok = abs(r - t) <= half * 1.0001 + 1e-12 * max(1.0, abs(t))
        if ok and abs(abs(r - t) - half) <= 1e-9 * half and r < t:
            ok = False  # an exact tie rounds up: 3.5 is "4", never "3"
    else:
        ok = _close(r, t) and abs(r - t) <= 1e-9 * max(1.0, abs(t))
    return {"verdict": "correct" if ok else "wrong", "feedback": "" if ok else "That is not the value.",
            "truth": str(val)}


def _check_solve(sp, expr, var, x, resp_text, numeric):
    eq = expr.lhs - expr.rhs if getattr(expr, "is_Equality", False) else expr
    parts = _split_solutions(resp_text)
    vals = []
    for p in parts:
        try:
            vals.append(parse(_strip_var(p, var), var))
        except BadInput as e:
            return {"verdict": "unparseable", "feedback": f"I could not read your answer: {e}."}
    # Every value the learner gave must satisfy the equation.
    bad = []
    for v in vals:
        if v.has(x):
            return {"verdict": "wrong", "feedback": f"Give values, not expressions in {var}."}
        if numeric:
            f = _lam(sp, [x], eq)
            try:
                r = abs(complex(f(complex(sp.N(v)))))
            except Exception:
                r = 1.0
            if r > 1e-6 * max(1.0, abs(complex(sp.N(v)))) ** 3:
                bad.append(v)
        else:
            res = sp.simplify(eq.subs(x, v))
            if res != 0 and not (res.is_zero or _numzero(sp, res)):
                bad.append(v)
    if bad:
        return {"verdict": "wrong", "feedback": f"{var} = {bad[0]} does not satisfy the equation."}
    if numeric:
        if not vals:
            return {"verdict": "undecided", "feedback": "Could not decide."}
        return {"verdict": "undecided", "feedback": "Could not confirm all solutions were found."}
    _, roots = _roots(sp, expr, var)
    if not vals:  # "no real solution": complex roots (x^2 + 1 = 0) are not an answer a student can miss
        roots = [r for r in roots if r.is_real is not False]
    unmatched = list(roots)
    for v in vals:
        hit = None
        for r in unmatched:
            if equivalent(sp, r, v, var) is True:
                hit = r
                break
        if hit is None:
            return {"verdict": "wrong", "feedback": f"{var} = {v} does not satisfy the equation."}
        unmatched.remove(hit)
    if unmatched:
        return {"verdict": "wrong", "feedback": f"You have {len(roots) - len(unmatched)} of {len(roots)} solutions."}
    return {"verdict": "correct", "feedback": ""}


def _numzero(sp, e):
    try:
        v = complex(sp.N(e))
        return abs(v) < 1e-9
    except Exception:
        return False


def do_plot(req):
    sp = __import__("sympy")
    var = req.get("var") or "x"
    try:
        expr = parse(req["expr"], var)
    except BadInput as e:
        return {"verdict": "bad_item", "feedback": str(e)}
    x = sp.Symbol(var)
    others = expr.free_symbols - {x}
    if others:
        return {"verdict": "bad_item", "feedback": "Plot expression has free symbols besides " + var}
    f = _lam(sp, [x], expr)
    lo, hi, n = float(req["lo"]), float(req["hi"]), int(req["n"])
    pts = []
    for i in range(n):
        xv = lo + (hi - lo) * i / (n - 1)
        try:
            yv = _to_float(f(xv))
        except Exception:
            yv = None
        if yv is not None and abs(yv) > 1e9:
            yv = None
        pts.append([xv, yv])
    return {"verdict": "plot", "points": pts}


def main():
    req = json.loads(sys.stdin.read())
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    except Exception:
        pass
    try:
        out = do_plot(req) if req["op"] == "plot" else do_check(req)
    except BadInput as e:
        out = {"verdict": "bad_item", "feedback": str(e)}
    except Exception as e:
        out = {"verdict": "error", "feedback": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
