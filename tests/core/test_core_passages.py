import pytest

from codecoach.core.passages import split_document

PDF = ("Chapter 2\n\nThe road to revolution began in 1763.\nIt was long.\n\n"
       "Taxes followed.\n"
       "\f"
       "Page fourteen starts here.\n\nSecond paragraph of fourteen.\n"
       "--- page 20 ---\n"
       "Twenty opens.\n\n\n\nTwenty again.\n"
       "\f"
       "Twenty-one.\n")


def ids(ps):
    return [p.id for p in ps]


def test_page_markers_formfeed_and_explicit():
    ps = split_document("book", PDF, "pdf_text")
    assert ids(ps) == ["book:p1:1", "book:p1:2", "book:p1:3", "book:p2:1", "book:p2:2",
                       "book:p20:1", "book:p20:2", "book:p21:1"]
    assert ps[1].text == "The road to revolution began in 1763. It was long."   # wrapped lines joined
    assert ps[0].location == "Ch. 2, p. 1, para 1"
    assert ps[3].location == "Ch. 2, p. 2, para 1"
    assert ps[5].location == "Ch. 2, p. 20, para 1"
    assert all("---" not in p.text and "\f" not in p.text for p in ps)


def test_ids_stable_across_runs_and_crlf():
    a = split_document("book", PDF, "pdf_text")
    b = split_document("book", PDF, "pdf_text")
    c = split_document("book", PDF.replace("\n", "\r\n"), "pdf_text")
    assert a == b == c


def test_no_markers_is_page_one_without_page_label():
    ps = split_document("notes", "First.\n\nSecond.\n\nThird.", "text")
    assert ids(ps) == ["notes:p1:1", "notes:p1:2", "notes:p1:3"]
    assert ps[2].location == "para 3"


def test_repeated_page_number_never_collides():
    ps = split_document("d", "A\n\n--- page 5 ---\nB\n--- PAGE 5 ---\nC\n", "text")
    assert len(set(ids(ps))) == 3 == len(ps)
    assert ids(ps) == ["d:p1:1", "d:p5:1", "d:p5:2"]


def test_marker_forms_tolerated():
    ps = split_document("d", "x\n  --- page 7 ---  \ny\n- - page 8 - -\n", "text")
    assert ids(ps)[:2] == ["d:p1:1", "d:p7:1"]       # "- - page 8" is not a marker
    ps2 = split_document("d", "x\n--page 3--\ny", "text")
    assert ids(ps2) == ["d:p1:1", "d:p3:1"]


MD = """Preface line before headings.

# The Stamp Act

Parliament passed it in 1765.
Second line of same paragraph.

Another paragraph.

## Reaction

Colonists protested.

```
# not a heading
code line

still code
```

# The Stamp Act

Duplicate heading.
"""


def test_markdown_sections():
    ps = split_document("hist", MD, "markdown")
    assert ids(ps) == ["hist:intro:1", "hist:the-stamp-act:1", "hist:the-stamp-act:2",
                       "hist:reaction:1", "hist:reaction:2", "hist:the-stamp-act-2:1"]
    assert ps[1].text == "Parliament passed it in 1765. Second line of same paragraph."
    assert ps[0].location == "Introduction, para 1"
    assert ps[1].location == "The Stamp Act, para 1"
    assert ps[4].text.startswith("```") and "# not a heading" in ps[4].text and "\n\nstill code" in ps[4].text


def test_markdown_with_pages_labels():
    ps = split_document("m", "# H\n\none\n\f\ntwo\n", "markdown")
    assert [p.location for p in ps] == ["H, p. 1, para 1", "H, p. 2, para 2"]
    assert ids(ps) == ["m:h:1", "m:h:2"]


def test_unterminated_fence_keeps_text():
    ps = split_document("m", "# H\n```\ncode\n", "markdown")
    assert len(ps) == 1 and "code" in ps[0].text


def test_chapter_tracking_roman_and_abbrev():
    ps = split_document("b", "CHAPTER IV\n\ntext\n\nCh. 5\n\nmore\n", "text")
    assert [p.location for p in ps] == ["Ch. IV, para 1", "Ch. IV, para 2", "Ch. 5, para 3", "Ch. 5, para 4"]


def test_empty_and_whitespace_documents():
    assert split_document("d", "", "text") == []
    assert split_document("d", " \n\n\f\n  ", "text") == []


@pytest.mark.parametrize("bad", ["Has:Colon", "UPPER", "", "-lead"])
def test_bad_doc_ids_rejected(bad):
    with pytest.raises(ValueError):
        split_document(bad, "x", "text")


def test_bad_kind_rejected():
    with pytest.raises(ValueError):
        split_document("d", "x", "docx")
