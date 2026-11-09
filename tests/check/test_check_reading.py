"""Reading-format checkers + registration of every format."""

from codecoach.check import CHECKERS, register_all
from codecoach.core import registry
from codecoach.core.models import (ClozePayload, ExplainPayload, MatchPair, MatchPayload, MCPayload, OrderEvent,
                                OrderPayload, ShortPayload)
from codecoach.core.public import public_view
from codecoach.core.registry import check
from codecoach.core.samples import sample_item

FORMATS = ["mc", "cloze", "order", "match", "short", "explain", "code", "math", "numeric"]


def test_every_format_registered_under_its_own_name():
    registry._clear_for_tests()
    register_all()
    register_all()                                  # idempotent
    for fmt in FORMATS:
        assert fmt in CHECKERS
        assert registry.get_checker(fmt) is not None


def test_mc():
    it = sample_item("mc", payload=MCPayload(options=["a", "b", "c"], correct_index=1))
    assert check(it, 1).ok and check(it, "1").ok
    assert not check(it, 0).ok and not check(it, 7).ok and not check(it, None).ok and not check(it, True).ok


def test_cloze_normalizes_and_aliases():
    it = sample_item("cloze", payload=ClozePayload(text_with_blank="The ____ is red.", answer="Mars",
                                                   aliases=["the red planet"]))
    assert check(it, "  MARS ").ok and check(it, "The Red Planet").ok
    assert not check(it, "Venus").ok and not check(it, "").ok and not check(it, 3).ok


def _order():
    ev = [OrderEvent(label=l, date_text=d, passage_id="d:p1:1") for l, d in [("c", "3"), ("a", "1"), ("b", "2")]]
    return sample_item("order", payload=OrderPayload(events=ev, correct_order=[1, 2, 0]))


def test_order_exact_and_partial():
    it = _order()
    assert check(it, [1, 2, 0]).ok
    part = check(it, [1, 0, 2])
    assert not part.ok and 0 < part.score < 1
    assert check(it, [0, 2, 1]).score == 0.5 or check(it, [0, 2, 1]).score < 1
    assert not check(it, [0, 1]).ok and not check(it, [0, 0, 1]).ok and not check(it, "x").ok


def _match():
    pairs = [MatchPair(left=l, right=r) for l, r in [("H", "hydrogen"), ("O", "oxygen"), ("C", "carbon"), ("N", "nitrogen")]]
    return sample_item("match", payload=MatchPayload(pairs=pairs))


def test_match_uses_the_public_rights_order():
    it = _match()
    pub = public_view(it)
    data = pub.model_dump() if hasattr(pub, "model_dump") else dict(pub)
    pl = data["payload"]
    rights, lefts = pl["rights"], pl["lefts"]
    truth = {"H": "hydrogen", "O": "oxygen", "C": "carbon", "N": "nitrogen"}
    good = [[i, rights.index(truth[l])] for i, l in enumerate(lefts)]
    assert check(it, good).ok
    bad = [[i, (rights.index(truth[l]) + 1) % 4] for i, l in enumerate(lefts)]
    r = check(it, bad)
    assert not r.ok
    swapped = [list(p) for p in good]
    swapped[0][1], swapped[1][1] = swapped[1][1], swapped[0][1]
    r = check(it, swapped)
    assert not r.ok and r.score == 0.5
    assert check(it, {"H": "hydrogen", "O": "oxygen", "C": "carbon", "N": "nitrogen"}).ok
    assert not check(it, good[:2]).ok                           # incomplete
    assert not check(it, [[0, 0], [0, 1], [2, 2], [3, 3]]).ok   # left used twice
    assert not check(it, [[0, 9]]).ok


def test_short_terms_with_word_boundaries():
    it = sample_item("short", payload=ShortPayload(required_terms=["photosynthesis", "light"]))
    assert check(it, "It is Photosynthesis, driven by light.").ok
    assert not check(it, "photosynthesis only").ok
    assert not check(it, "highlighted photosynthesis").ok          # 'light' inside 'highlighted'
    some = sample_item("short", payload=ShortPayload(required_terms=["a1", "b2", "c3"], min_terms=2))
    assert check(some, "a1 and c3").ok and not check(some, "a1").ok


def test_explain_returns_the_rating_and_no_half_weight():
    it = sample_item("explain", payload=ExplainPayload())
    r = check(it, 0.8)
    assert r.score == 0.8 and r.ok
    assert check(it, 0.2).score == 0.2 and not check(it, 0.2).ok
    assert check(it, 7).undecided and check(it, None).undecided and check(it, "abc").undecided


# ----------------------------------------------------------------------------- short answers: textbook wording variants

import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("terms,resp", [
    (["chloroplast"], "It happens in the chloroplasts"),
    (["thylakoid membrane"], "in the thylakoid membranes"),
    (["chloroplasts"], "inside a chloroplast"),
    (["thylakoid membranes"], "the thylakoid membrane"),
    (["H2O"], "water splits: H\u2082O"),
    (["Calvin cycle"], "the CALVIN CYCLE"),
])
def test_short_accepts_plural_singular_and_notation(terms, resp):
    assert check(sample_item("short", payload=ShortPayload(required_terms=terms)), resp).ok


@_pytest.mark.parametrize("terms,resp", [
    (["chloroplast"], "chloroplastic"),
    (["photosynthesis"], "photosynthesi"),
    (["thylakoid membrane"], "membrane of the thylakoid"),
    (["cell"], "cellular respiration"),
    (["ion"], "region"),
])
def test_short_plural_tolerance_does_not_loosen_matching(terms, resp):
    assert not check(sample_item("short", payload=ShortPayload(required_terms=terms)), resp).ok
