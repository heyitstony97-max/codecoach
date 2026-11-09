"""Cloze: numeric US dates and centuries are accepted in their other written form; a different date stays wrong;
the capital A of a name ("A minor") is not an article to drop."""

import pytest

from codecoach.check import register_all
from codecoach.core.models import ClozePayload
from codecoach.core.registry import check
from codecoach.core.samples import sample_item

register_all()


def _cloze(answer):
    return sample_item("cloze", payload=ClozePayload(text_with_blank="x ____ y", answer=answer, aliases=[]))


@pytest.mark.parametrize("answer,resp", [
    ("September 3, 1783", "9/3/1783"),
    ("September 3, 1783", "09/03/1783."),
    ("9/3/1783", "September 3, 1783"),
    ("July 4, 1776", "7/4/1776"),
    ("the 18th century", "1700s"),
    ("1700s", "18th century"),
    ("the 21st century", "2000s"),
    ("A minor", "A minor"),
    ("A minor", "a minor."),
    ("A major", "the A major"),
    ("the Constitution", "Constitution"),
    ("a mitochondrion", "mitochondrion"),
])
def test_accepted(answer, resp):
    assert check(_cloze(answer), resp).ok, (answer, resp)


@pytest.mark.parametrize("answer,resp", [
    ("September 3, 1783", "3/9/1783"),          # that is March 9 in US order
    ("September 3, 1783", "9/4/1783"),
    ("September 3, 1783", "9/3/1784"),
    ("the 18th century", "1800s"),
    ("the 18th century", "1600s"),
    ("5th century BCE", "400s"),
    ("A minor", "minor"),
    ("A major", "major"),
    ("A minor", "A major"),
])
def test_rejected(answer, resp):
    assert not check(_cloze(answer), resp).ok, (answer, resp)


def test_numeric_date_feedback_states_us_order():
    r = check(_cloze("September 3, 1783"), "3/9/1783")
    assert "month/day/year" in r.detail and "US" in r.detail
