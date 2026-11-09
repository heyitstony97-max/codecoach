"""Break-it (world): fair grading of fill-in-the-blank answers in other languages and with real-world dates/numbers.

A learner who types the right words with a full stop, a thousands comma, a leading "the" or a dropped one must
not be marked wrong. Accents stay meaningful (esta is not est\u00e1), but the feedback says so.
"""

import pytest

from codecoach.check import register_all
from codecoach.core.models import ClozePayload
from codecoach.core.registry import check
from codecoach.core.samples import sample_item

register_all()


def _cloze(answer, aliases=()):
    return sample_item("cloze", payload=ClozePayload(text_with_blank="x ____ y", answer=answer, aliases=list(aliases)))


@pytest.mark.parametrize("answer,resp", [
    ("est\u00e1", "est\u00e1."),                  # a full stop after the answer
    ("D\u00f3nde", "\u00bfD\u00f3nde?"),          # Spanish question marks around a single word
    ("1776", "1,776"),                            # thousands separator
    ("2700", "2,700"),
    ("the Constitution", "Constitution"),         # article dropped
    ("Bill of Rights", "the Bill of Rights"),     # article added
    ("the 1860s", "1860s"),
    ("July 4, 1776", "July 4, 1776."),
])
def test_right_answer_in_a_valid_form_is_accepted(answer, resp):
    assert check(_cloze(answer), resp).ok, (answer, resp)


@pytest.mark.parametrize("answer,resp", [
    ("1776", "1777"),
    ("2700", "2,70"),
    ("the Constitution", "the Declaration"),
    ("\u00e9l", "el"),                            # \u00e9l (he) and el (the) are different words
    ("a", "b"),
    ("the", "a"),
    ("27", "2,7"),
    ("est\u00e1", "esta"),                         # accents still count
])
def test_wrong_answers_still_wrong(answer, resp):
    assert not check(_cloze(answer), resp).ok, (answer, resp)


def test_missing_accent_is_wrong_but_the_feedback_says_why():
    r = check(_cloze("est\u00e1"), "esta")
    assert not r.ok
    assert "accent" in r.detail.lower()
    r2 = check(_cloze("a\u00f1o"), "ano")
    assert not r2.ok and "accent" in r2.detail.lower()


def test_plain_wrong_answer_does_not_mention_accents():
    assert "accent" not in check(_cloze("est\u00e1"), "ser").detail.lower()


@pytest.mark.parametrize("answer,resp", [
    ("September 3, 1783", "september 3 1783"),
    ("September 3, 1783", "Sept. 3, 1783"),
    ("September 3, 1783", "3 September 1783"),
    ("September 3, 1783", "September 3rd, 1783"),
    ("1860s", "1860's"),
    ("c. 1500 BCE", "1500 BC"),
    ("c. 1500 BCE", "circa 1500 BCE"),
    ("twenty-seven", "27"),
    ("27", "twenty seven"),
    ("one hundred", "100"),
    ("John Adams, Benjamin Franklin, and John Jay", "John Adams, Benjamin Franklin and John Jay"),
])
def test_dates_and_numbers_in_any_common_format(answer, resp):
    assert check(_cloze(answer), resp).ok, (answer, resp)


@pytest.mark.parametrize("answer,resp", [
    ("September 3, 1783", "September 4, 1783"),
    ("September 3, 1783", "3 October 1783"),
    ("c. 1500 BCE", "1500 CE"),
    ("1860s", "1870s"),
    ("twenty-seven", "28"),
    ("the 18th century", "the 19th century"),
])
def test_different_dates_and_numbers_stay_wrong(answer, resp):
    assert not check(_cloze(answer), resp).ok, (answer, resp)
