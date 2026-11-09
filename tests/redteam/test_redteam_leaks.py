"""Red team, the two doors a learner sees: core.public_view (can an item field leak the answer before
answering?) and the lesson framing line (can the AI add a fact the passage lacks?).
Records are described in redteam_kit.py."""

import pytest
from redteam_kit import P1, numeric_key_verified, passage

from codecoach.core.models import (Citation, ClozePayload, CodePayload, CodeTest, ExplainPayload, Item, MathPayload,
                                MatchPayload, MCPayload, NumericPayload, OrderPayload, Quantity, ShortPayload)
from codecoach.core.public import public_view
from codecoach.ground.gate import check_framing


def mark(name: str) -> str:
    return f"ZZ_{name}_ZZ"


def secret_items() -> dict[str, Item]:
    """One item per format; every private field holds its own marker so a leak names its field."""
    cite = [Citation(passage_id=mark("cite_pid"), quote=mark("cite_quote"))]

    def it(fmt, payload, prompt="A question?"):
        return Item(id=f"it_{fmt}", objective_id="o1", format=fmt, prompt=prompt, payload=payload,
                    citations=cite, checker=fmt)

    return {
        "mc": it("mc", MCPayload(options=["wrong one", "wrong two", "right three"], correct_index=2)),
        "cloze": it("cloze", ClozePayload(text_with_blank="The ____ was passed.", answer=mark("cloze_answer"),
                                          aliases=[mark("cloze_alias")])),
        "order": it("order", OrderPayload(
            events=[{"label": "Second event", "date_text": mark("date_b"), "passage_id": mark("order_pid")},
                    {"label": "First event", "date_text": mark("date_a"), "passage_id": mark("order_pid")},
                    {"label": "Third event", "date_text": mark("date_c"), "passage_id": mark("order_pid")}],
            correct_order=[1, 0, 2])),
        "match": it("match", MatchPayload(pairs=[
            {"left": "L1", "right": "R1", "passage_id": mark("match_pid")},
            {"left": "L2", "right": "R2", "passage_id": mark("match_pid")}])),
        "short": it("short", ShortPayload(required_terms=[mark("term_a"), mark("term_b")], min_terms=1)),
        "explain": it("explain", ExplainPayload()),
        "code": it("code", CodePayload(
            language="python", starter="def f(x):\n    pass\n",
            visible_tests=[CodeTest(name="v1", stdin="1\n", expected_stdout="2\n")],
            hidden_tests=[CodeTest(name="h1", stdin=mark("hidden_stdin"), expected_stdout=mark("hidden_out")),
                          CodeTest(name="h2", harness=mark("hidden_harness"))],
            reference_solution=mark("reference"))),
        "math": it("math", MathPayload(op="diff", expr="x**2", var="x", given_answer=mark("math_key"))),
        "numeric": it("numeric", NumericPayload(given=[Quantity(name="m", value=2.0, unit="kg")],
                                                quantity="the force", answer_value=6.0123456, unit="N",
                                                rel_tol=0.0137)),
    }


FORBIDDEN_KEYS = {"correct_index", "citations", "answer", "aliases", "required_terms", "date_text", "correct_order",
                  "reference_solution", "hidden_tests", "given_answer", "answer_value", "rel_tol", "checker",
                  "quote", "min_terms"}
MARKERS = [mark(n) for n in ("cite_pid", "cite_quote", "cloze_answer", "cloze_alias", "date_a", "date_b", "date_c",
                             "order_pid", "match_pid", "term_a", "term_b", "hidden_stdin", "hidden_out",
                             "hidden_harness", "reference", "math_key")] + ["6.0123456", "0.0137"]


def all_keys(obj) -> set[str]:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= all_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= all_keys(v)
    return out


def test_rt81_public_view_strips_every_private_field():
    """RT81: markers planted in every answer key, alias, hidden test, reference solution, citation and tolerance; none may appear
    severity: high
    fix: none needed, public_view is an allow-list per format
    kind: fixable
    """
    for fmt, item in secret_items().items():
        view = public_view(item)
        blob = view.model_dump_json()
        for m in MARKERS:
            assert m not in blob, f"{fmt}: {m} leaked through public_view"
        leaked = all_keys(view.model_dump()) & FORBIDDEN_KEYS
        assert not leaked, f"{fmt}: private keys in the public view: {leaked}"
        assert "citations" not in view.model_dump()


def test_rt82_public_view_shares_no_mutable_state_with_the_item():
    """RT82: editing the lists and dicts of a returned view must not change the stored item
    severity: medium
    fix: none needed, the view is built from copies
    kind: fixable
    """
    items = secret_items()
    for fmt in ("mc", "match", "order", "code", "numeric"):
        item = items[fmt]
        before = item.model_dump_json()
        view = public_view(item)
        for v in view.payload.values():
            if isinstance(v, list):
                v.append("HACK")
                for el in v:
                    if isinstance(el, dict):
                        el["HACK"] = 1
        assert item.model_dump_json() == before, f"{fmt}: the view aliased the item"
        assert "HACK" not in public_view(item).model_dump_json()


def test_rt83_match_right_column_mirrors_the_left_for_some_ids():
    """RT83: with 3 pairs about 1 id in 6 gets rights in the same order as lefts, so "line them up" is the answer
    severity: low
    fix: in public._shuffled, rotate the result by one when it equals the original order
    kind: fixable
    """
    pairs = [{"left": f"L{i}", "right": f"R{i}", "passage_id": "d:p1:1"} for i in range(3)]
    mirrored = []
    for n in range(60):
        item = Item(id=f"it_mirror_{n}", objective_id="o1", format="match", prompt="Match.",
                    payload=MatchPayload(pairs=pairs), citations=[Citation(passage_id="d:p1:1", quote=mark("q"))],
                    checker="match")
        view = public_view(item).payload
        if view["rights"] == [p["right"] for p in pairs]:
            mirrored.append(item.id)
    assert not mirrored, f"{len(mirrored)} of 60 items show the right column in answer order, e.g. {mirrored[:3]}"


def test_rt84_numeric_prompt_that_states_the_answer():
    """RT84: "... so the net force is 6 N. What is the net force?" passes verify()
    severity: low
    fix: structural check for code/math/numeric: no number equal to the key (within tolerance) may appear in the prompt or the quantity text
    kind: fixable
    """
    from codecoach.core.models import Objective
    from codecoach.gen.generate import verify

    p = passage("Newton's second law states that force equals mass times acceleration, as Newton showed in 1687.",
                "phys:p2:1")
    item = Item(id="it_num2", objective_id="o1", format="numeric",
                prompt="A 2 kg cart accelerates at 3 m/s^2, so the net force is 6 N. What is the net force?",
                payload=NumericPayload(given=[Quantity(name="m", value=2.0, unit="kg"),
                                              Quantity(name="a", value=3.0, unit="m/s^2")],
                                       quantity="the net force", answer_value=6.0, unit="N"),
                citations=[Citation(passage_id=p.id,
                                    quote="Newton's second law states that force equals mass times acceleration")],
                checker="numeric")
    obj = Objective(id="o1", unit="u", lesson="l", verb="compute", statement="Compute force from mass and acceleration.",
                    passage_ids=[p.id])
    assert public_view(item).prompt.count("6 N") == 1                      # the leak is real
    with numeric_key_verified():
        failures = verify(item, [p], obj)
    assert failures, "the gate let this item through"


# ------------------------------------------------------------------ the framing line


def framing(text):
    return [f.code for f in check_framing(text, [P1])]


def test_rt85_framing_line_with_a_fact_the_passage_lacks():
    """RT85: a framing line that adds a date, a name, a spelled-out number or an ordinal
    severity: high
    fix: none needed, numbers, dates and names must all occur in the shown passage
    kind: fixable
    """
    assert framing("Read this passage about Lincoln's oath, then answer.") == []
    for bad in ("Read about the war that began in 1860.",
                "Notice what happened at Appomattox.",
                "Read why Jefferson Davis resigned.",
                "Notice the nineteenth amendment.",
                "Note that twenty soldiers camped there.",
                "Read about Lin\u0441oln's oath, then answer.",
                "Read what happened on April 12 to Lincoln."):
        assert framing(bad) == ["framing_fact"], bad


def test_rt86_framing_line_that_is_more_than_one_short_line():
    """RT86: a second sentence, a newline, or a very long line used to carry extra claims
    severity: high
    fix: none needed, framing must be one short line
    kind: fixable
    """
    assert framing("Read this, then answer: what did Lincoln do at the oath?") == []
    for bad in ("Read this. Then note that Lincoln lost.",
                "Read this!\nLincoln lost.",
                "Read this? Lincoln lost.",
                "Read this passage about Lincoln " + "and the war " * 30):
        assert framing(bad) == ["framing_fact"], bad[:40]


def test_rt87_framing_line_with_a_lowercase_false_claim():
    """RT87: "the proclamation freed every enslaved person in the country" and a lowercase invented name pass
    severity: high
    fix: none needed, every content word of the framing line must be in the passage or in a short list of instruction words
    kind: fixable
    """
    for bad in ("Notice that the proclamation freed every enslaved person in the country.",
                "Look for how napoleon reacted to the oath.",
                "Read this, then explain why lincoln lost the election of 1860 and fled."):
        assert framing(bad) != [], bad


def test_rt88_framing_line_with_a_spelled_out_decade():
    """RT88: "the eighties" for a passage about 1861 to 1863 adds a period the passage never mentions
    severity: medium
    fix: add spelled-out decades (twenties to nineties, with -ies) and centuries to the date extractor
    kind: fixable
    """
    assert framing("Read this passage about life in the eighties, then answer.") != []
    assert framing("Notice how the roaring twenties shaped this, then answer.") != []


def test_rt89_framing_year_split_into_number_words():
    """RT89: "eighteen sixty" is read as 18 and 60, which a passage with 1,000 soldiers and an 18-line map may contain
    severity: medium
    fix: same as RT24: read a teen followed by a tens-word as one year-like number
    kind: fixable
    """
    text = "The map had 18 lines and the regiment had 60 men."
    p = passage(text, "hist:p50:1")
    codes = [f.code for f in check_framing("Read what happened in eighteen sixty, then answer.", [p])]
    assert codes == ["framing_fact"]
