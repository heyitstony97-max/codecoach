"""Every checker message a learner can read: no doubled punctuation, no "Correct. Correct", no developer jargon."""

import functools
import re

import pytest

from codecoach.check import CHECKERS, register_all
from codecoach.core.registry import check
from codecoach.core.samples import SAMPLE_PAYLOADS, sample_item
from codecoach.service import correct_feedback, tidy_feedback

register_all()

# Wrong, empty, odd and hostile responses, for every response shape.
RESPONSES = ["", "   ", "zzz", "42", "3 xyz", "5 m/s", "3/9/1783", "x +", "(", "2x+", "1/0", "sqrt(", "a=b=c", "x^^2",
             "5 kg", "5 N", "10 mm", "100 degC", "1e999", "foo(2)", "0", -1, 0, 1, 3.5, None, True, [], [0], [1, 0],
             [[0, 0]], [[0, 9]], {"a": "b"}, ["x"], 0.3, 7, "7"]
BAD = [
    (re.compile(r"\.\s*\.(?!\.)"), "double full stop"),
    (re.compile(r"[?!]\s*\.(?!\.)"), "stop after ? or !"),
    (re.compile(r"[:;,]\s*\."), "stop after punctuation"),
    (re.compile(r"(?i)correct\W+correct"), "Correct. Correct"),
    (re.compile(r"  "), "double space"),
    (re.compile(r"(?i)traceback|nonetype|sympy|exception|payload|\bregex\b|Error\)|\bNone\b"), "jargon"),
]


@functools.cache
def _messages():
    return list(_gen_messages())


def _gen_messages():
    for fmt in sorted(SAMPLE_PAYLOADS):
        if fmt not in CHECKERS or fmt == "code":      # code feedback is the learner's own interpreter output
            continue
        try:
            it = sample_item(fmt)
        except Exception:
            continue
        for r in RESPONSES:
            try:
                res = check(it, r)
            except Exception:
                continue
            if res.detail:
                yield fmt, r, res.detail


def test_there_are_messages_to_sweep():
    assert len({f for f, _, _ in _messages()}) >= 5


@pytest.mark.parametrize("pat,name", BAD, ids=[n for _, n in BAD])
def test_no_checker_message_has_it(pat, name):
    found = [(f, r, d) for f, r, d in _messages() if pat.search(d)]
    assert not found, found[:5]


def test_messages_survive_service_tidying_unchanged_where_clean():
    for _, _, d in _messages():
        t = tidy_feedback(d)
        assert t == d.strip() or not t.startswith("Correct"), (d, t)
        assert not re.search(r"(?i)correct\W+correct", correct_feedback(d)), d


@pytest.mark.parametrize("raw,clean", [
    ("Not correct..", "Not correct."),
    ("Not correct. .", "Not correct."),
    ("Is it x?.", "Is it x?"),
    ("Look here:.", "Look here:"),
    ("Wait... what", "Wait... what"),           # a real ellipsis is left alone
    ("Too  many   spaces.", "Too many spaces."),
    (None, ""),
])
def test_tidy_feedback(raw, clean):
    assert tidy_feedback(raw) == clean


@pytest.mark.parametrize("raw,clean", [
    ("Correct", "Correct."),
    ("Correct.", "Correct."),
    ("Correct. Correct", "Correct. Correct"),   # checkers never emit this; correct_feedback must not add a third
    ("Correct..", "Correct."),
    ("", "Correct."),
    ("Close, rounded.", "Correct. Close, rounded."),
])
def test_correct_feedback(raw, clean):
    assert correct_feedback(raw) == clean
