"""The five fixed warm-up tasks of the model fit check (V11-CONTRACT section 5).

The same five for every language. Each has the text the model is given and three stdin -> stdout tests whose
keys are written out here by hand as data. tests/fit/test_fit_tasks.py recomputes every key with a small
Python reference and fails on any difference, so a typo in a key cannot silently fail a good model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WarmupTest:
    stdin: str
    expected_stdout: str


@dataclass(frozen=True)
class Warmup:
    id: str
    title: str
    text: str                       # what the model is given (no language words: the prompt adds those)
    tests: tuple[WarmupTest, ...]


def _t(stdin: str, out: str) -> WarmupTest:
    return WarmupTest(stdin, out)


WARMUPS: tuple[Warmup, ...] = (
    Warmup(
        "sum2", "Add two numbers",
        "Read one line holding two integers separated by a space. Print their sum on one line. "
        "The numbers may be negative.",
        (_t("3 4\n", "7\n"),
         _t("-10 4\n", "-6\n"),
         _t("1000000 2345678\n", "3345678\n"))),
    Warmup(
        "count_vowels", "Count the vowels",
        "Read one line of text. Print how many of its letters are vowels (a, e, i, o, u), counting upper and "
        "lower case alike. Print the count on one line.",
        (_t("Hello World\n", "3\n"),
         _t("AEIOU aeiou xyz\n", "10\n"),
         _t("rhythm\n", "0\n"))),
    Warmup(
        "fizzbuzz", "FizzBuzz",
        "Read one integer n. For each number from 1 to n, one per line: print Fizz when it is a multiple of 3, "
        "Buzz when it is a multiple of 5, FizzBuzz when it is a multiple of both, and the number itself otherwise.",
        (_t("5\n", "1\n2\nFizz\n4\nBuzz\n"),
         _t("1\n", "1\n"),
         _t("15\n", "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz\n"))),
    Warmup(
        "reverse_words", "Reverse the words",
        "Read one line of text. The words are separated by one or more spaces. Print the same words in reverse "
        "order on one line, separated by single spaces.",
        (_t("the quick brown fox\n", "fox brown quick the\n"),
         _t("hello\n", "hello\n"),
         _t("one  two   three\n", "three two one\n"))),
    Warmup(
        "max_of_list", "Largest in a list",
        "Read an integer n on the first line. The second line holds n integers separated by spaces. Print the "
        "largest of them on one line. The numbers may be negative.",
        (_t("5\n3 9 -2 7 1\n", "9\n"),
         _t("3\n-5 -2 -9\n", "-2\n"),
         _t("1\n42\n", "42\n"))),
)

BY_ID: dict[str, Warmup] = {w.id: w for w in WARMUPS}
