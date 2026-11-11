"""The warm-up keys are hand-written data; a small Python reference recomputes every one of them."""

import re

from codecoach.fit.tasks import BY_ID, WARMUPS


def _sum2(s):
    a, b = s.split()
    return f"{int(a) + int(b)}\n"


def _count_vowels(s):
    return f"{sum(1 for ch in s.rstrip(chr(10)) if ch.lower() in 'aeiou')}\n"


def _fizzbuzz(s):
    out = []
    for i in range(1, int(s) + 1):
        out.append("FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i))
    return "".join(x + "\n" for x in out)


def _reverse_words(s):
    return " ".join(reversed(s.split())) + "\n"


def _max_of_list(s):
    first, second = s.split("\n")[:2]
    nums = [int(x) for x in second.split()]
    assert len(nums) == int(first)
    return f"{max(nums)}\n"


REFERENCE = {"sum2": _sum2, "count_vowels": _count_vowels, "fizzbuzz": _fizzbuzz,
             "reverse_words": _reverse_words, "max_of_list": _max_of_list}


def test_the_five_warmups_in_contract_order():
    assert [w.id for w in WARMUPS] == ["sum2", "count_vowels", "fizzbuzz", "reverse_words", "max_of_list"]
    assert set(REFERENCE) == set(BY_ID)


def test_every_warmup_has_title_text_and_three_tests():
    for w in WARMUPS:
        assert w.title.strip() and w.text.strip()
        assert len(w.tests) == 3
        assert len({t.stdin for t in w.tests}) == 3


def test_every_key_equals_the_python_reference():
    for w in WARMUPS:
        for t in w.tests:
            assert REFERENCE[w.id](t.stdin) == t.expected_stdout, (w.id, t.stdin)


def test_task_texts_name_no_language_and_no_emoji():
    for w in WARMUPS:
        assert not re.search(r"\b(python|c\+\+|java|rust|ruby|bash|go)\b", w.text, re.I)
        assert all(ord(ch) < 0x2000 for ch in w.text + w.title)
