import pytest

from codecoach.core.passages import is_quotable, split_document


@pytest.mark.parametrize("text", [
    "I.", "II.", "Chapter 3", "CHAPTER XII", "Poem VIII", "2.5", "THE ELEMENTS OF STYLE", "VIII. TRIUMPHANT",
    "Life", "Part One", "2.5 Measures of the Center", "Section 4.1: Rates", "see page 3", "",
])
def test_headings_numerals_and_short_lines_are_not_quotable(text):
    assert not is_quotable(text)


@pytest.mark.parametrize("text", [
    "Hope is the thing with feathers.",
    "I'm nobody! Who are you?",
    "The Stamp Act of 1765 required stamps on printed paper.",
    "Form the possessive singular of nouns by adding 's",
    "Dear Sir, you are the very best of men",
    "Chapter 3 begins with a storm over the lake.",
])
def test_real_sentences_stay_quotable(text):
    assert is_quotable(text)


def test_not_quotable_passages_keep_their_ids_and_text():
    text = "I.\n\nHope is the thing with feathers that perches in the soul.\n\nII.\n\nThe bee is not afraid of me."
    ps = split_document("d", text, "text")
    assert [p.id for p in ps] == ["d:p1:1", "d:p1:2", "d:p1:3", "d:p1:4"]
    assert [is_quotable(p.text) for p in ps] == [False, True, False, True]
