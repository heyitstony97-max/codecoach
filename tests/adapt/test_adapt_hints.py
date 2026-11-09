import pytest
from adapt_helpers import T0, build_course, new_store, oid

from codecoach.adapt import answer_keys, get_hint, leaks
from codecoach.core.models import (Attempt, Citation, MathPayload, NumericPayload, Quantity)
from codecoach.core.samples import SAMPLE_PAYLOADS, sample_item
from codecoach.core.text import normalize

FORMATS = list(SAMPLE_PAYLOADS)
PASSAGE = ("Intro sentence stays out of it. Parliament passed the SECRET Stamp Act in 1765. "
           "SECRET quote text. The right answer is stated here with representation, SECRET term and 6.0123456. "
           "A last sentence follows.")


def setup(fmt, extra=()):
    store = new_store()
    build_course(store, {1: []}, items_per=0)
    from codecoach.core.models import Passage
    store.put_passages([Passage(id="fx:s01:1", doc_id="fx", location="Section 1, para 1", text=PASSAGE)])
    item = sample_item(fmt, objective_id=oid(1), citations=[Citation(passage_id="fx:s01:1", quote="SECRET quote text")])
    store.add_item(item)
    # a second item for the same objective with an identical payload: its worked example would leak
    store.add_item(sample_item(fmt, id=f"item-{fmt}-2", objective_id=oid(1),
                               citations=[Citation(passage_id="fx:s01:1", quote="SECRET quote text")]))
    for e in extra:
        store.add_item(e)
    return store, item


def attempts(store, item, n, response="my guess"):
    for k in range(n):
        store.add_attempt(Attempt(item_id=item.id, response=response, correct=False, score=0.0, graded_by="x",
                                  at=T0.replace(minute=k)))


def blob(h):
    return " | ".join(str(x) for x in (h.hint.text, h.hint.passage_id, h.location, h.passage_text) if x)


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("n_attempts", [0, 1])
def test_hints_never_contain_the_answer_key_before_two_attempts(fmt, n_attempts):
    store, item = setup(fmt)
    attempts(store, item, n_attempts)
    keys = answer_keys(item)
    for level in (1, 2, 3):
        h = get_hint(store, item.id, level)
        text = normalize(blob(h))
        assert h.hint.level == level and h.hint.text
        for k in keys:
            assert normalize(k) not in text, (fmt, level, k, text)
        assert "secret" not in text
        assert not leaks(blob(h), keys)


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_blank_response_is_not_a_real_attempt(fmt):
    store, item = setup(fmt)
    attempts(store, item, 3, response="")
    h = get_hint(store, item.id, 3)
    assert "SECRET" not in blob(h) and h.passage_text is None


def test_reading_item_ladder_section_paragraph_then_highlight():
    store, item = setup("cloze")
    h1, h2, h3 = (get_hint(store, item.id, n) for n in (1, 2, 3))
    assert "Section 1" in h1.hint.text and "para" not in h1.hint.text and h1.hint.passage_id is None
    assert h2.location == "Section 1, para 1" and h2.hint.passage_id == "fx:s01:1"
    assert h3.locked and h3.passage_text is None and h3.hint.highlight is None      # no attempts yet
    attempts(store, item, 2)
    h3 = get_hint(store, item.id, 3)
    assert not h3.locked and h3.passage_text == PASSAGE and h3.hint.passage_id == "fx:s01:1"
    span = h3.hint.highlight
    assert PASSAGE[span.start:span.end].strip() == "SECRET quote text."      # the key sentence only


def test_highlight_span_expands_to_the_whole_sentence():
    store, item = setup("mc")
    item2 = sample_item("mc", id="mc-b", objective_id=oid(1),
                        citations=[Citation(passage_id="fx:s01:1", quote="Stamp Act in 1765")])
    store.add_item(item2)
    attempts(store, item2, 2)
    h = get_hint(store, "mc-b", 3)
    assert PASSAGE[h.hint.highlight.start:h.hint.highlight.end].strip() == \
        "Parliament passed the SECRET Stamp Act in 1765."


def test_level_three_for_non_reading_items_is_a_worked_example_of_a_different_item():
    other = sample_item("math", id="math-other", objective_id=oid(1), prompt="Differentiate x**3.", origin="model_knowledge",
                        payload=MathPayload(op="diff", expr="x**3", var="x", given_answer="3*x**2"), citations=[])
    store, item = setup("math", extra=[other])
    h = get_hint(store, item.id, 3)
    assert "Differentiate x**3." in h.hint.text and "3*x**2" in h.hint.text
    assert "different question" in h.hint.text
    assert "SECRET" not in h.hint.text and h.passage_text is None and not h.locked


def test_worked_example_skips_a_different_item_that_would_leak():
    store, item = setup("numeric")      # the only other item has the same answer: no example can be shown
    h = get_hint(store, item.id, 3)
    assert "6.0123456" not in h.hint.text and "no other worked example" in h.hint.text.lower()
    other = sample_item("numeric", id="num-other", objective_id=oid(1), prompt="Find the force on 1 kg at 2 m/s^2.", origin="model_knowledge",
                        payload=NumericPayload(given=[Quantity(name="m", value=1.0, unit="kg")],
                                               quantity="force", answer_value=2.0, unit="N"), citations=[])
    store.add_item(other)
    h = get_hint(store, item.id, 3)
    assert "Find the force on 1 kg" in h.hint.text and "2 N" in h.hint.text


def test_leak_guard_replaces_a_nudge_that_would_name_the_answer():
    store, item = setup("mc")
    # an objective statement equal to the answer: the nudge must fall back to a plainer sentence
    from codecoach.core.models import CourseMap, Lesson, Objective, Unit
    store.save_map(CourseMap(course_id="fx", units=[Unit(id="u1", title="U", lessons=[Lesson(id="l1", title="L", objectives=[
        Objective(id=oid(1), unit="u1", lesson="l1", verb="explain", statement="right answer",
                  passage_ids=["fx:s01:1"])])])]))
    h = get_hint(store, item.id, 1)
    assert "right answer" not in normalize(blob(h))


def test_bad_level_and_unknown_item():
    store, item = setup("mc")
    with pytest.raises(ValueError):
        get_hint(store, item.id, 4)
    with pytest.raises(KeyError):
        get_hint(store, "nope", 1)
