"""Superscripts stay exponents: NFKC alone would turn 10^23 (written with superscript digits) into 1023."""
import pytest

from codecoach.ask.check import quote_span
from codecoach.core.text import normalize, quote_in
from codecoach.ground import gate
from codecoach.study import key_spans


@pytest.mark.parametrize("raw,expected", [
    ("10\u00b2\u00b3", "10^23"),
    ("x\u00b2", "x^2"),
    ("m\u207b\u00b9", "m^-1"),
    ("H\u2082O", "h2o"),                 # subscripts may flatten
    ("x^2", "x^2"),
    ("x^\u00b2", "x^2"),
])
def test_superscript_normalize(raw, expected):
    assert normalize(raw) == expected


def test_superscript_idempotent():
    for s in ["10\u00b2\u00b3 m\u207b\u00b9 x\u00b2+C", "x^\u00b2", "\u00b2\u00b3"]:
        assert normalize(normalize(s)) == normalize(s)


def test_exponent_matches_caret_not_flattened():
    assert gate.text_found("about 6.0 \u00d7 10\u00b2\u00b3 atoms", "10^23")
    assert gate.text_found("about 6.0 x 10^23 atoms", "10\u00b2\u00b3")
    assert quote_in("a mass of 10\u00b2\u00b3 kg", "10^23 kg")
    assert not gate.text_found("a mass of 10\u00b2\u00b3 kg", "1023")
    assert not gate.text_found("a mass of 1023 kg", "10\u00b2\u00b3")
    assert not quote_in("a mass of 10\u00b2\u00b3 kg", "1023 kg")


def test_offsets_exact_with_superscripts():
    text = "Avogadro: 6.02 \u00d7 10\u00b2\u00b3 per mole, and speed in m\u207b\u00b9 units; area x\u00b2 + 1 here."
    for quote in ["6.02 \u00d7 10\u00b2\u00b3 per mole", "10^23 per mole", "speed in m^-1 units", "area x\u00b2 + 1 here",
                  "area x^2 + 1 here"]:
        a, b = quote_span(text, quote)
        assert normalize(text[a:b]) == normalize(quote)
    assert quote_span(text, "per mole and 1023") is None
    assert quote_span(text, "6.02 \u00d7 1023 per mole") is None


def test_key_spans_offsets_with_superscripts():
    text = "Before. The constant is 6.02 \u00d7 10\u00b2\u00b3 per mole in chemistry, roughly."
    quote = "The constant is 6.02 \u00d7 10\u00b2\u00b3 per mole in chemistry"
    spans = key_spans(text, quote, ["10^23 per mole"])
    assert spans
    a, b = spans[0]
    assert normalize(text[a:b]) == "10^23 per mole"
    assert key_spans(text, quote, ["1023 per mole"]) == []
