import json

import pytest

from codecoach.core.models import Citation
from codecoach.core.public import public_view, reveal
from codecoach.core.registry import (CheckResult, GenResult, NotRegistered, _clear_for_tests, check,
                                  get_checker, get_generator, register_checker, register_generator,
                                  registered_checkers)
from codecoach.core.samples import SAMPLE_PAYLOADS, SECRET, sample_item


@pytest.fixture(autouse=True)
def clean():
    _clear_for_tests()
    yield
    _clear_for_tests()


@pytest.mark.parametrize("fmt", sorted(SAMPLE_PAYLOADS))
def test_public_view_leaks_nothing(fmt):
    item = sample_item(fmt)
    pv = public_view(item)
    blob = pv.model_dump_json()
    assert SECRET not in blob, f"{fmt}: leaked a secret: {blob}"
    assert "quote text" not in blob and "citations" not in blob
    d = json.loads(blob)
    forbidden = {"correct_index", "answer", "aliases", "hidden_tests", "reference_solution",
                 "given_answer", "answer_value", "correct_order", "citations", "required_terms", "harness"}
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert forbidden.isdisjoint(set(keys(d))), set(keys(d)) & forbidden


def test_public_view_specifics():
    assert public_view(sample_item("code")).payload["hidden_test_count"] == 2
    assert [t["name"] for t in public_view(sample_item("code")).payload["visible_tests"]] == ["v1"]
    m = public_view(sample_item("match")).payload
    assert sorted(m["rights"]) == sorted(m["rights"]) and len(m["lefts"]) == len(m["rights"]) == 2
    assert public_view(sample_item("match")).payload == m   # deterministic
    assert "date_text" not in json.dumps(public_view(sample_item("order")).payload)
    assert public_view(sample_item("numeric")).payload["unit"] == "N"
    assert "6.01" not in json.dumps(public_view(sample_item("numeric")).payload)


def test_source_label():
    assert public_view(sample_item("mc")).source_label == "From your materials"
    it = sample_item("mc", origin="model_knowledge")
    assert public_view(it).source_label == "Not from your materials"


def test_reveal():
    it = sample_item("mc", citations=[Citation(passage_id="d:p1:1", quote="q1"),
                                      Citation(passage_id="d:p2:1", quote="q2")])
    out = reveal(it, lambda pid: {"d:p1:1": "p. 1, para 1"}.get(pid))
    assert [(c.passage_id, c.quote, c.location) for c in out] == [("d:p1:1", "q1", "p. 1, para 1"),
                                                                 ("d:p2:1", "q2", None)]
    assert reveal(it)[0].location is None


def test_unknown_kind_raises_not_at_import():
    with pytest.raises(NotRegistered):
        get_checker("nope")
    with pytest.raises(NotRegistered):
        get_generator("nope")
    assert issubclass(NotRegistered, LookupError)


def test_register_and_dispatch():
    def mc(item, response, *, mode="submit"):
        ok = response == item.payload.correct_index
        return CheckResult(ok=ok, score=1.0 if ok else 0.0, detail="d")
    register_checker("mc", mc)
    assert get_checker("mc") is mc and registered_checkers() == ["mc"]
    assert check(sample_item("mc"), 2).ok and not check(sample_item("mc"), 0).ok
    g = lambda **kw: GenResult()
    register_generator("mc", g)
    assert get_generator("mc") is g
    register_checker("mc", lambda item, response, *, mode="submit": CheckResult(ok=False, score=0))
    assert not check(sample_item("mc"), 2).ok     # last registration wins


def test_check_result_shape():
    r = CheckResult(ok=True, score=1)
    assert r.per_test is None and r.undecided is False
    with pytest.raises(Exception):
        CheckResult(ok=True, score=2)
