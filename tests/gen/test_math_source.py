"""Math items are honest about where the problem came from: in the quote -> sourced; CodeCoach's own -> practice
(only when the citation teaches the method); trivial or label-number items are dropped."""
import random

import pytest

from codecoach.core import registry
from codecoach.core.llm import FakeLLM
from codecoach.core.models import Objective, Passage
from codecoach.core.public import public_view
from codecoach.core.registry import CheckResult, register_checker
from codecoach.gen import generate_one

TEACH = ("The power rule is the basic tool for finding a derivative: to differentiate a power function, "
         "bring the exponent down as a coefficient and reduce the exponent by one.")
Q_TEACH = "to differentiate a power function, bring the exponent down as a coefficient"
SRC = "Example: we find the derivative of f(x) = 3x^2 + 5x by applying the power rule to each term of the sum."
Q_SRC = "we find the derivative of f(x) = 3x^2 + 5x by applying the power rule"
HEADING = "2.2 Linear Equations in One Variable Learning Objectives"
LABEL = "EXAMPLE 3.17 Applying the Constant Rule Find the derivative of Solution"
GAP = "In this case the derivative of the function  is found by the power rule, as we show here."

P = {
    "calc:p1:1": Passage(id="calc:p1:1", doc_id="calc", location="p. 1", text=TEACH),
    "calc:p1:2": Passage(id="calc:p1:2", doc_id="calc", location="p. 1", text=SRC),
    "calc:p1:3": Passage(id="calc:p1:3", doc_id="calc", location="p. 1", text=HEADING),
    "calc:p1:4": Passage(id="calc:p1:4", doc_id="calc", location="p. 1", text=LABEL),
    "calc:p1:5": Passage(id="calc:p1:5", doc_id="calc", location="p. 1", text=GAP, formula_missing=True),
}
OBJ = Objective(id="o1", unit="u1", lesson="l1", verb="compute",
                statement="Compute the derivative of a power function.", passage_ids=list(P))


@pytest.fixture(autouse=True)
def fake_checker():
    registry._clear_for_tests()
    register_checker("math", lambda item, resp, *, mode="submit": CheckResult(ok=True, score=1.0))
    yield
    registry._clear_for_tests()


def run(pid, quote, **kw):
    d = {"prompt": "Differentiate the expression.", "op": "diff", "expr": "x**2", "var": "x", "given_answer": "2*x",
         "citations": [{"passage_id": pid, "quote": quote}]}
    d.update(kw)
    llm = FakeLLM(lambda purpose, text: d)
    return generate_one(fmt="math", objective=OBJ, passages=list(P.values()), llm=llm, rng=random.Random(1))


def test_in_source_item_is_kept_as_sourced():
    r = run("calc:p1:2", Q_SRC, expr="3*x**2 + 5*x", given_answer="6*x + 5")
    assert r.items and r.items[0].payload.origin == "source"
    assert public_view(r.items[0]).payload["origin"] == "source"


def test_invented_item_citing_a_teaching_passage_is_kept_as_practice():
    r = run("calc:p1:1", Q_TEACH)          # x**2 is not in that quote
    assert r.items and r.items[0].payload.origin == "practice"
    assert public_view(r.items[0]).payload["origin"] == "practice"


def test_invented_item_citing_a_heading_is_dropped():
    r = run("calc:p1:3", "Linear Equations in One Variable Learning Objectives")
    assert not r.items and r.drops[0].gate_code == "quote_missing"


def test_invented_item_citing_a_formula_gap_passage_is_dropped():
    r = run("calc:p1:5", "the derivative of the function  is found by the power rule")
    assert not r.items and r.drops[0].gate_code == "quote_missing"


def test_example_label_number_is_dropped():
    r = run("calc:p1:4", LABEL, expr="3.17", given_answer="0")
    assert not r.items


@pytest.mark.parametrize("op,expr,ans", [("diff", "7", "0"), ("integrate", "5", "5*x"), ("solve", "3 = 3", "0")])
def test_no_variable_items_are_dropped(op, expr, ans):
    r = run("calc:p1:1", Q_TEACH, op=op, expr=expr, given_answer=ans)
    assert not r.items and r.drops[0].gate_code == "self_check_failed"


def test_practice_needs_the_topic_in_the_passage():
    # a teaching-shaped sentence about something else
    P["calc:p1:6"] = Passage(id="calc:p1:6", doc_id="calc", location="p. 1",
                             text="The committee met on Tuesday to discuss the budget for the coming year.")
    r = run("calc:p1:6", "The committee met on Tuesday to discuss the budget")
    assert not r.items
    del P["calc:p1:6"]
