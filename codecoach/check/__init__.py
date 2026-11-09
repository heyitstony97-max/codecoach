"""Checkers. Importing this package registers one checker per item format (kind == item.format).

Heavy parts are lazy: SymPy is only ever imported in child processes (math) or inside functions (numeric).
"""

from codecoach.core.registry import register_checker

from .code_runner import check_code, grade, run_visible, sandbox_status
from .math_sympy import check_math, plot_points, truth as math_truth, verify_key as verify_math_key
from .numeric import check_numeric
from .reading import check_cloze, check_explain, check_match, check_mc, check_order, check_short

CHECKERS = {
    "mc": check_mc, "cloze": check_cloze, "order": check_order, "match": check_match,
    "short": check_short, "explain": check_explain,
    "code": check_code, "math": check_math, "numeric": check_numeric,
}


def register_all() -> None:
    """Idempotent; call again after registry._clear_for_tests()."""
    for kind, fn in CHECKERS.items():
        register_checker(kind, fn)


register_all()

__all__ = ["CHECKERS", "register_all", "check_code", "grade", "run_visible", "sandbox_status", "check_math",
           "plot_points", "math_truth", "verify_math_key", "check_numeric"]
