import pytest

from codecoach.core.text import normalize, quote_in, slugify, stable_hash


@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("   ", ""),
    ("Hello   World", "hello world"),
    ("  tabs\tand\nnewlines\r\n here ", "tabs and newlines here"),
    ("\u201cQuoted\u201d and \u2018single\u2019", "\"quoted\" and 'single'"),
    ("It\u2019s", "it's"),
    ("1765\u20131770", "1765-1770"),
    ("wait\u2014what", "wait-what"),
    ("minus \u2212 sign", "minus - sign"),
    ("zero\u200bwidth\u200c\u200djoin\ufeffed", "zerowidthjoined"),
    ("soft\u00adhyphen", "softhyphen"),
    ("\ufb01nal", "final"),                       # fi ligature via NFKC
    ("caf\u00e9", "caf\u00e9"),
    ("cafe\u0301", "caf\u00e9"),                  # combining accent composed
    ("Stra\u00dfe", "strasse"),                   # casefold, not lower
    ("\uff21\uff22\uff23 \uff11\uff12", "abc 12"),  # fullwidth
    ("a\u00a0b", "a b"),                          # nbsp
    ("wait\u2026", "wait..."),
    ("\u00abquoted\u00bb", "\"quoted\""),
    ("end\u200b ", "end"),
])
def test_normalize_cases(raw, expected):
    assert normalize(raw) == expected


def test_normalize_idempotent():
    samples = ["\u201cA\u201d \u2014 B\u200b  C", "Stra\u00dfe\ufb01", "x\u2019y\u2013z", "\u0130stanbul"]
    for s in samples:
        assert normalize(normalize(s)) == normalize(s)


def test_normalize_makes_variants_equal():
    a = "The colonists\u2019 \u201cno taxation\u201d slogan \u2014 1765."
    b = 'the  colonists\' "no taxation"\nslogan - 1765.'
    assert normalize(a) == normalize(b)


def test_quote_in():
    passage = "Parliament passed the\nStamp Act on March 22,\u00a01765 \u2014 a first."
    assert quote_in(passage, "stamp act on march 22, 1765")
    assert quote_in(passage, "\u201cStamp Act\u201d".replace("\u201c", "").replace("\u201d", ""))
    assert not quote_in(passage, "Stamp Act on March 23, 1765")
    assert not quote_in(passage, "")
    assert not quote_in(passage, " \u200b ")
    assert quote_in(passage, "1765 - a first")


def test_stable_hash_and_slug():
    assert stable_hash("a", 1) == stable_hash("a", 1) != stable_hash("a", 2)
    assert len(stable_hash("x", length=8)) == 8
    assert slugify("The Stamp Act (1765)!") == "the-stamp-act-1765"
    assert slugify("\u00c9cole d\u2019\u00e9t\u00e9") == "ecole-dete"
    assert slugify("???") == "section" and ":" not in slugify("a:b")
