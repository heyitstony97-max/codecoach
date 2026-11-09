"""Break-it (world): running headers, footers, page numbers and bell characters from real handbooks
(the USCIS 100 questions has "-N- www.uscis.gov" on every page) must not become passages or questions."""

import pytest

from codecoach import pdf
from codecoach.core.passages import split_document
from tests.core.test_pdf_reader import make_pdf

pytestmark = pytest.mark.skipif(not pdf.available(), reason="pdftotext not installed")

BODY = [
    "When you approach a stop sign you must come to a full stop before the limit line.",
    "Keep a safe distance of at least three seconds behind the vehicle ahead of you.",
    "Yield to pedestrians who are crossing the road in a marked or unmarked crosswalk.",
    "Use your turn signal at least one hundred feet before you turn or change lanes.",
    "Never drive in a bicycle lane except to park, enter or leave the road, or turn.",
]


def _doc(tmp_path, n=5, footnote=True):
    pages = []
    for i in range(n):
        paras = ["STATE DRIVER MANUAL 2026", BODY[i % len(BODY)]]
        if footnote:
            paras.append("* \u0007If you are 65 years old or older and have been a resident for 20 years, you may study less.")
        paras += [f"-{i + 1}-", "www.example.gov"]
        pages.append(paras)
    p = tmp_path / "manual.pdf"
    p.write_bytes(make_pdf(pages))
    return pdf.read_pdf(str(p))


def test_running_header_and_footer_lines_are_removed(tmp_path):
    text = _doc(tmp_path).text
    assert "STATE DRIVER MANUAL" not in text
    assert "www.example.gov" not in text
    assert "-3-" not in text
    for b in BODY:
        assert b in text.replace("\n", " ")             # the real content stays whole


def test_repeated_footnote_is_kept_once(tmp_path):
    text = _doc(tmp_path).text
    assert text.count("you may study less") == 1


def test_bell_characters_are_gone(tmp_path):
    assert "\u0007" not in _doc(tmp_path).text


def test_no_passage_is_page_chrome(tmp_path):
    ps = split_document("manual-pdf", _doc(tmp_path).text)
    blob = " ".join(p.text for p in ps)
    assert "STATE DRIVER" not in blob and "example.gov" not in blob and "-4-" not in blob


def test_short_document_is_left_alone(tmp_path):
    p = tmp_path / "two.pdf"
    p.write_bytes(make_pdf([["Title line", BODY[0], "-1-"], ["Title line", BODY[1], "-2-"]]))
    t = pdf.read_pdf(str(p)).text
    assert "Title line" in t                              # two pages: too few to call anything running chrome


def test_a_sentence_that_repeats_only_in_the_body_is_not_removed(tmp_path):
    mid = "The speed limit in a school zone is twenty five miles per hour."
    pages = [[f"Chapter {i}", BODY[i], BODY[i - 1], mid, BODY[i - 2], BODY[i - 3], BODY[i - 4], f"Page {i}"] for i in range(5)]
    p = tmp_path / "body.pdf"
    p.write_bytes(make_pdf(pages))
    t = pdf.read_pdf(str(p)).text
    assert t.count("school zone") == 5
