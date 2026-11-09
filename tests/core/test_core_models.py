import pytest
from pydantic import ValidationError

from codecoach.core.models import (Attempt, CodePayload, CodeTest, Course, CourseMap, Drop, Hint, Item,
                                MCPayload, ClozePayload, OrderPayload, MatchPayload, ShortPayload,
                                ExplainPayload, MathPayload, NumericPayload, Passage, ImportRequest,
                                NextResponse, Span, GATE_CODES, GATE_LABELS)
from codecoach.core.samples import SAMPLE_PAYLOADS, sample_item


def omap(oid, unit="u1", lesson="l1", prereqs=(), passages=("d:p1:1",), **kw):
    return {"id": oid, "unit": unit, "lesson": lesson, "verb": "explain", "statement": f"Explain {oid}",
            "prereqs": list(prereqs), "passage_ids": list(passages), **kw}


def cmap(*objs, edges=None, course="c1"):
    d = {"course_id": course, "units": [{"id": "u1", "title": "U", "lessons": [
        {"id": "l1", "title": "L", "objectives": list(objs)}]}]}
    if edges is not None:
        d["edges"] = edges
    return d


def test_map_valid_and_edges_derived():
    m = CourseMap.model_validate(cmap(omap("a"), omap("b", prereqs=["a"]), omap("c", prereqs=["a", "b"])))
    assert [(e.prereq, e.objective) for e in m.edges] == [("a", "b"), ("a", "c"), ("b", "c")]
    assert len(m.objectives()) == 3 and m.objective("b").prereqs == ["a"]


def test_map_rejects_cycle():
    with pytest.raises(ValidationError, match="cycle"):
        CourseMap.model_validate(cmap(omap("a", prereqs=["c"]), omap("b", prereqs=["a"]), omap("c", prereqs=["b"])))


def test_map_rejects_cycle_via_explicit_edges():
    with pytest.raises(ValidationError, match="cycle"):
        CourseMap.model_validate(cmap(omap("a"), omap("b"), edges=[
            {"prereq": "a", "objective": "b"}, {"prereq": "b", "objective": "a"}]))


def test_map_rejects_self_prereq():
    with pytest.raises(ValidationError, match="own prerequisite|itself"):
        CourseMap.model_validate(cmap(omap("a", prereqs=["a"])))


def test_map_rejects_dangling_prereq():
    with pytest.raises(ValidationError, match="dangling"):
        CourseMap.model_validate(cmap(omap("a", prereqs=["ghost"])))


def test_map_rejects_dangling_edge():
    with pytest.raises(ValidationError, match="dangling"):
        CourseMap.model_validate(cmap(omap("a"), edges=[{"prereq": "a", "objective": "nope"}]))


def test_map_rejects_duplicates_and_misplaced_objective():
    with pytest.raises(ValidationError, match="duplicate objective"):
        CourseMap.model_validate(cmap(omap("a"), omap("a")))
    with pytest.raises(ValidationError, match="sits under"):
        CourseMap.model_validate(cmap(omap("a", unit="u9")))


def test_objective_coverage_rules():
    with pytest.raises(ValidationError, match="cites no passages"):
        CourseMap.model_validate(cmap(omap("a", passages=())))
    with pytest.raises(ValidationError, match="not_covered"):
        CourseMap.model_validate(cmap(omap("a", coverage="not_covered")))
    ok = CourseMap.model_validate(cmap(omap("a", passages=(), coverage="not_covered")))
    assert ok.objective("a").coverage == "not_covered"
    # code objectives have no passages and are still covered
    CourseMap.model_validate(cmap(omap("a", passages=(), kind="code")))


@pytest.mark.parametrize("fmt", sorted(SAMPLE_PAYLOADS))
def test_payload_discrimination_roundtrip(fmt):
    item = sample_item(fmt)
    again = Item.model_validate_json(item.model_dump_json())
    assert again == item
    assert type(again.payload).__name__.lower().startswith(fmt)


def test_payload_class_by_format():
    expect = {"mc": MCPayload, "cloze": ClozePayload, "order": OrderPayload, "match": MatchPayload,
              "short": ShortPayload, "explain": ExplainPayload, "code": CodePayload,
              "math": MathPayload, "numeric": NumericPayload}
    assert set(expect) == set(SAMPLE_PAYLOADS)
    for fmt, cls in expect.items():
        assert type(sample_item(fmt).payload) is cls


def test_item_format_must_match_payload():
    d = sample_item("mc").model_dump()
    d["format"] = "cloze"
    with pytest.raises(ValidationError, match="payload format"):
        Item.model_validate(d)


def test_unknown_payload_format_rejected():
    d = sample_item("mc").model_dump()
    d["payload"]["format"] = "essay"
    with pytest.raises(ValidationError):
        Item.model_validate(d)


def test_materials_item_needs_citation():
    d = sample_item("mc").model_dump()
    d["citations"] = []
    with pytest.raises(ValidationError, match="citation"):
        Item.model_validate(d)
    d["origin"] = "model_knowledge"
    Item.model_validate(d)


def test_payload_validators():
    with pytest.raises(ValidationError):
        MCPayload(options=["a", "b"], correct_index=2)
    with pytest.raises(ValidationError):
        ClozePayload(text_with_blank="no blank here", answer="x")
    with pytest.raises(ValidationError):
        ClozePayload(text_with_blank="____ and ____", answer="x")
    with pytest.raises(ValidationError):
        OrderPayload(events=[{"label": "a", "date_text": "1", "passage_id": "d:p1:1"},
                             {"label": "b", "date_text": "2", "passage_id": "d:p1:1"}], correct_order=[0, 0])
    with pytest.raises(ValidationError):
        MatchPayload(pairs=[{"left": "a", "right": "x"}, {"left": "a", "right": "y"}])
    with pytest.raises(ValidationError):
        NumericPayload(answer_value=1.0, unit="m", rel_tol=0)


def test_code_test_needs_exactly_one_form():
    with pytest.raises(ValidationError):
        CodeTest(name="t")
    with pytest.raises(ValidationError):
        CodeTest(name="t", expected_stdout="1\n", harness="assert True")
    assert CodeTest(name="t", stdin="1", expected_stdout="1\n").harness is None
    assert CodeTest(name="t", harness="assert True").expected_stdout is None


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Course(id="c1", title="T", mood="strict")


def test_ids_and_naive_datetimes():
    with pytest.raises(ValidationError):
        Course(id="Bad Id", title="T")
    with pytest.raises(ValidationError):
        Passage(id="d:p1", doc_id="d", location="x", text="y")
    with pytest.raises(ValidationError):
        Passage(id="other:p1:1", doc_id="d", location="x", text="y")
    from datetime import datetime
    a = Attempt(item_id="i", score=1, graded_by="mc", at=datetime(2026, 1, 1))
    assert a.at.tzinfo is not None


def test_ranges():
    with pytest.raises(ValidationError):
        Attempt(item_id="i", score=1.5, graded_by="mc")
    with pytest.raises(ValidationError):
        Hint(level=4, text="x")
    with pytest.raises(ValidationError):
        Span(start=5, end=5)
    with pytest.raises(ValidationError):
        Drop(objective_id="o", format="mc", gate_code="made_up")


def test_gate_codes_complete():
    assert set(GATE_CODES) == set(GATE_LABELS) == {
        "quote_missing", "unsupported_fact", "ambiguous", "bad_cloze", "framing_fact",
        "key_mismatch", "weak_tests", "self_check_failed", "schema_invalid"}


def test_request_and_next_validators():
    with pytest.raises(ValidationError):
        ImportRequest(title="t")
    with pytest.raises(ValidationError):
        ImportRequest(title="t", text="a", path="b")
    assert ImportRequest(title="t", text="a").mode == "strict"
    with pytest.raises(ValidationError):
        NextResponse(kind="item")
    assert NextResponse(kind="done", reason="none", message="all caught up").item is None
