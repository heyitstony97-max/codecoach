"""Red team, gate a: can a quote that is not word-for-word in the cited passage get through, or can a
real quote be used to prove something it does not say? Records are described in redteam_kit.py."""

import pytest
from redteam_kit import (BOTH, P1, P2, Q_OATH, Q_PROC, Q_STAMP, assert_dropped, assert_passes, build,
                         cites, explain, mc, passage)

from codecoach.core.models import Citation
from pydantic import ValidationError

OPTS_PROC = ["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"]
PROMPT_PROC = "Which document freed enslaved people in the rebel states?"


def proc_item(quote, pid=P1.id):
    return mc(PROMPT_PROC, OPTS_PROC, 0, cites(pid, quote))


def test_rt01_cyrillic_homoglyph_in_quote():
    """RT01: a Cyrillic letter swapped into a quote that otherwise matches
    severity: high
    fix: none needed, NFKC does not fold homoglyphs so the quote no longer matches
    kind: fixable
    """
    assert_passes(proc_item(Q_PROC))
    fake = Q_PROC.replace("a", "\u0430", 1)
    assert fake != Q_PROC
    assert_dropped(proc_item(fake), code="quote_missing")


def test_rt02_invisible_characters_used_as_spaces():
    """RT02: zero-width / invisible characters standing in for the spaces of a quote
    severity: high
    fix: none needed, invisibles are stripped, so the words glue together and no longer match
    kind: fixable
    """
    for ch in ("\u200b", "\u2060", "\ufeff", "\u00ad", "\u2063", "\u200d"):
        fake = Q_PROC.replace("freed enslaved", f"freed{ch}enslaved")
        assert_dropped(proc_item(fake), code="quote_missing")


def test_rt03_padding_a_short_quote_to_the_minimum_length():
    """RT03: "the" padded with zero-width characters or spaces to pass the length minimum
    severity: medium
    fix: none needed, the length test runs after normalizing
    kind: fixable
    """
    for pad in ("\u200b ", " ", "\u2060", "\ufeff\u200b "):
        assert_dropped(proc_item("the" + pad * 40), code="quote_missing")


def test_rt04_quote_stitched_across_two_passages():
    """RT04: one quote string made of the end of passage 1 and the start of passage 2
    severity: high
    fix: none needed, the quote must be a substring of the single cited passage
    kind: fixable
    """
    stitched = "and Lee's army retreated. " + Q_STAMP
    assert_dropped(proc_item(stitched, P1.id), code="quote_missing")
    assert_dropped(proc_item(stitched, P2.id), code="quote_missing")


def test_rt05_real_quote_cited_to_the_wrong_passage():
    """RT05: a true quote whose text lives in passage 2 but is cited as passage 1
    severity: high
    fix: none needed, the quote is searched only in the cited passage
    kind: fixable
    """
    ok = mc("Which law taxed printed paper?", ["The Stamp Act", "The Tea Act", "The Quartering Act"], 0,
            cites(P2.id, Q_STAMP))
    assert_passes(ok)
    wrong = mc("Which law taxed printed paper?", ["The Stamp Act", "The Tea Act", "The Quartering Act"], 0,
               cites(P1.id, Q_STAMP))
    assert_dropped(wrong, code="quote_missing")


def test_rt06_passage_id_spelled_differently():
    """RT06: the cited passage id differs by case, whitespace or a missing passage
    severity: high
    fix: none needed, ids are matched exactly and an unknown id is quote_missing
    kind: fixable
    """
    for pid in ("HIST:P1:1", "hist:p1:1 ", " hist:p1:1", "hist:p1:2", "other:p1:1"):
        assert_dropped(proc_item(Q_PROC, pid), code="quote_missing")


def test_rt07_elided_or_edited_quote():
    """RT07: a quote with "..." joining two sentences, or a [bracketed] word inserted
    severity: high
    fix: none needed, any edit breaks the substring test
    kind: fixable
    """
    for fake in ("In 1863, the Emancipation Proclamation ... in the rebel states.",
                 "In 1863, the Emancipation Proclamation freed [all] enslaved people in the rebel states.",
                 "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states, "
                 "About 1,000 soldiers camped near Gettysburg"):
        assert_dropped(proc_item(fake), code="quote_missing")


def test_rt08_empty_and_blank_quotes():
    """RT08: an empty quote, a whitespace-only quote, and an empty quote that skips pydantic validation
    severity: high
    fix: none needed, an empty quote fails at construction and again in the gate
    kind: fixable
    """
    with pytest.raises(ValidationError):
        Citation(passage_id=P1.id, quote="")
    assert_dropped(proc_item("   \n\t  "), code="quote_missing")
    assert_dropped(proc_item("\u200b" * 30), code="quote_missing")
    bypass = Citation.model_construct(passage_id=P1.id, quote="")
    assert_dropped(build("mc", proc_item(Q_PROC).payload, PROMPT_PROC, [bypass]), code="quote_missing")


def test_rt09_trivially_short_quotes():
    """RT09: quotes of "the", a bare year, or a short phrase
    severity: medium
    fix: none needed, under 5 words and 25 characters is rejected
    kind: fixable
    """
    for q in ("the", "1863", "In 1863", "the rebel states", "freed enslaved"):
        assert_dropped(proc_item(q), code="quote_missing")


def test_rt10_answer_taken_from_a_different_sentence():
    """RT10: the question is about sentence 2, the cited quote is sentence 1, which contains the key
    severity: high
    fix: require the prompt's content words to appear in the cited quote(s), not just somewhere in the passage
    kind: fixable
    """
    seward = passage("On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President. "
                     "In 1863, the Emancipation Proclamation, drafted by William Seward, freed enslaved "
                     "people in the rebel states. About 1,000 soldiers camped near Gettysburg that summer.",
                     "hist:p3:1")
    q_draft = ("In 1863, the Emancipation Proclamation, drafted by William Seward, freed enslaved people "
               "in the rebel states.")
    prompt = "Who drafted the Emancipation Proclamation?"
    options = ["Abraham Lincoln", "Ulysses Grant", "Robert Smith"]
    # control: the sentence that really answers the question does not support the key
    assert_dropped(mc(prompt, options, 0, cites(seward.id, q_draft)), [seward], why="control")
    # the false key survives when it is "proved" by the oath sentence
    assert_dropped(mc(prompt, options, 0, cites(seward.id, Q_OATH)), [seward])


def test_rt11_quote_is_the_whole_passage():
    """RT11: the cited quote is the entire passage, so any key found anywhere in it passes
    severity: high
    fix: cap quote length (one sentence, or at most about 40 percent of the passage) so a quote must pin the claim
    kind: fixable
    """
    seward_text = ("On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President. "
                   "In 1863, the Emancipation Proclamation, drafted by William Seward, freed enslaved people "
                   "in the rebel states. About 1,000 soldiers camped near Gettysburg that summer.")
    seward = passage(seward_text, "hist:p3:1")
    prompt = "Who drafted the Emancipation Proclamation?"
    options = ["Abraham Lincoln", "Ulysses Grant", "Robert Smith"]
    q_draft = ("In 1863, the Emancipation Proclamation, drafted by William Seward, freed enslaved people "
               "in the rebel states.")
    assert_dropped(mc(prompt, options, 0, cites(seward.id, q_draft)), [seward], why="control")
    assert_dropped(mc(prompt, options, 0, cites(seward.id, seward_text)), [seward])


def test_rt12_quote_starts_inside_a_comma_grouped_number():
    """RT12: passage says 1,863 soldiers, the quote starts at "863 soldiers", the key is 863
    severity: high
    fix: treat "," "." "-" and "'" between two alphanumerics as part of the word in text_found's boundary test
    kind: fixable
    """
    garrison = passage("The garrison at Fort Wayne numbered 1,863 soldiers in the winter of 1862.", "hist:p5:1")
    q = "863 soldiers in the winter of 1862"
    item = mc("How many soldiers garrisoned the fort in the winter of 1862?", ["863", "1,200", "400"], 0,
              cites(garrison.id, q))
    assert_dropped(item, [garrison], code="quote_missing")


def test_rt13_quote_starts_after_a_decimal_point():
    """RT13: passage says 1.5 million people, the quote starts at "5 million people", the key is 5 million
    severity: high
    fix: same boundary fix as RT12
    kind: fixable
    """
    colony = passage("By 1760 the colony held about 1.5 million people, mostly farmers.", "hist:p6:1")
    q = "5 million people, mostly farmers"
    item = mc("About how many people did the colony hold by 1760?", ["5 million", "2 million", "800 thousand"], 0,
              cites(colony.id, q))
    assert_dropped(item, [colony], code="quote_missing")


def test_rt14_quote_drops_a_hyphenated_prefix():
    """RT14: passage says "non-Confederate sympathizers", the quote starts at "Confederate sympathizers"
    severity: high
    fix: same boundary fix as RT12 (a hyphen between alphanumerics glues the word)
    kind: fixable
    """
    mo = passage("Officials arrested several non-Confederate sympathizers in Missouri during 1862.", "hist:p7:1")
    q = "Confederate sympathizers in Missouri during 1862"
    item = mc("Whom did officials arrest in Missouri during 1862?",
              ["Confederate sympathizers", "Union soldiers", "Mexican traders"], 0, cites(mo.id, q))
    assert_dropped(item, [mo], code="quote_missing")


def test_rt15_quote_ends_inside_a_decimal_number():
    """RT15: passage says 4.75 inches, the quote ends at "measured 4", the key is 4 inches
    severity: high
    fix: same boundary fix as RT12, applied to the right edge too
    kind: fixable
    """
    rain = passage("Rainfall that spring measured 4.75 inches near the fort.", "hist:p8:1")
    q = "Rainfall that spring measured 4"
    item = mc("How many inches of rain fell that spring?", ["4 inches", "9 inches", "12 inches"], 0,
              cites(rain.id, q))
    assert_dropped(item, [rain], code="quote_missing")


def test_rt16_content_free_quote():
    """RT16: the cited "quote" is a row of asterisks that happens to sit in the passage
    severity: low
    fix: require a quote to contain at least 3 alphabetic words
    kind: fixable
    """
    sep = passage("* * * * * * * * * * * * *\nThe Tea Act of 1773 gave a monopoly on tea.", "hist:p9:1")
    item = explain("Why did the Tea Act of 1773 give a monopoly on tea?", cites(sep.id, "* * * * * * * * * * * * *"))
    assert_dropped(item, [sep], code="quote_missing")


def test_rt17_superscript_exponent_flattened():
    """RT17: passage says 10 to the sixth (10⁶), the key says 106
    severity: high
    fix: normalize superscripts and subscripts to ^6 / _6 before NFKC, in core.text and ground.facts
    kind: fixable
    """
    det = passage("The detector covered an area of 10\u2076 square meters near the coast.", "hist:p10:1")
    q = "an area of 10\u2076 square meters near the coast"
    item = mc("What area did the detector cover?", ["106 square meters", "40 square meters", "3 square meters"], 0,
              cites(det.id, q))
    assert_dropped(item, [det])
