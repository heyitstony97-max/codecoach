"""One valid sample Item per format. Handy in lane tests; also drives the public_view leak test.
Every secret (answer key, hidden test, reference solution) contains the marker SECRET."""

from .models import (Citation, ClozePayload, CodePayload, CodeTest, ExplainPayload, Item, MathPayload,
                     MatchPayload, MCPayload, NumericPayload, OrderPayload, Quantity, ShortPayload)

SECRET = "SECRET"

SAMPLE_PAYLOADS = {
    # options are visible by design; the secret is the index
    "mc": lambda: MCPayload(options=["wrong one", "wrong two", "right answer", "wrong four"],
                            correct_index=2),
    "cloze": lambda: ClozePayload(text_with_blank="Parliament passed the ____ in 1765.",
                                  answer=f"{SECRET} Stamp Act", aliases=[f"{SECRET} alias"]),
    "order": lambda: OrderPayload(
        events=[{"label": "Declaration", "date_text": f"{SECRET} 1776", "passage_id": "d:p1:2"},
                {"label": "Stamp Act", "date_text": f"{SECRET} 1765", "passage_id": "d:p1:1"},
                {"label": "Yorktown", "date_text": f"{SECRET} 1781", "passage_id": "d:p1:3"}],
        correct_order=[1, 0, 2]),
    "match": lambda: MatchPayload(pairs=[
        {"left": "Stamp Act", "right": "taxed printed paper", "passage_id": "d:p1:1"},
        {"left": "Tea Act", "right": "tea monopoly", "passage_id": "d:p1:2"}]),
    "short": lambda: ShortPayload(required_terms=[f"{SECRET}term", "representation"]),
    "explain": lambda: ExplainPayload(),
    "code": lambda: CodePayload(
        language="python", starter="def total(xs):\n    pass\n",
        visible_tests=[CodeTest(name="v1", stdin="1 2\n", expected_stdout="3\n")],
        hidden_tests=[CodeTest(name="h1", stdin="5 5\n", expected_stdout=f"{SECRET}10\n"),
                      CodeTest(name="h2", harness=f"assert True  # {SECRET}")],
        reference_solution=f"print(sum(map(int, input().split())))  # {SECRET}"),
    "math": lambda: MathPayload(op="diff", expr="x**2*sin(x)", var="x",
                                given_answer=f"{SECRET}2*x*sin(x) + x**2*cos(x)"),
    "numeric": lambda: NumericPayload(
        given=[Quantity(name="m", value=2.0, unit="kg"), Quantity(name="a", value=3.0, unit="m/s^2")],
        quantity="the net force", answer_value=6.0123456, unit="N", rel_tol=0.01),
}


def sample_item(fmt: str, **overrides) -> Item:
    payload = SAMPLE_PAYLOADS[fmt]()
    data = dict(
        id=f"item-{fmt}-1", objective_id="c1:o01", format=fmt, prompt=f"A {fmt} question?",
        payload=payload, citations=[Citation(passage_id="d:p1:1", quote=f"{SECRET} quote text")],
        origin="materials", checker=fmt)
    data.update(overrides)
    return Item(**data)
