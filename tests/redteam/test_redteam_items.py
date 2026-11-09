"""Red team, gates b-d per format plus the prompt check: order dates, match pairs, cloze aliases,
multiple-choice distractors, required terms, pooled citations, prompts, and the escape hatches.
Records are described in redteam_kit.py."""

import pytest
from redteam_kit import (P1, P2, Q_INDEP, Q_OATH, Q_PROC, Q_STAMP, Q_TEA, assert_dropped, assert_passes,
                         cites, cloze, codes, ev, explain, match, mc, numeric_key_verified, order, passage, short)

from codecoach.core.models import Citation, Item, MCPayload
from codecoach.core.public import public_view

PID = P2.id
ALL3 = cites(PID, Q_STAMP, Q_TEA, Q_INDEP)
PROMPT_ORDER = "Put these events in order."


def good_order():
    return order(PROMPT_ORDER, [ev("Independence", "1776", PID), ev("Stamp Act", "1765", PID),
                                ev("Tea Act", "1773", PID)], [1, 2, 0], ALL3)


# ------------------------------------------------------------------ order (gate b for dates)


def test_rt50_order_date_that_is_not_in_the_cited_quotes():
    """RT50: an event dated 1776 when only the Stamp Act and Tea Act sentences are cited
    severity: high
    fix: none needed, every event date must be a span of a quote cited for that passage
    kind: fixable
    """
    assert_passes(good_order(), [P2])
    two = cites(PID, Q_STAMP, Q_TEA)
    bad = order(PROMPT_ORDER, [ev("Independence", "1776", PID), ev("Stamp Act", "1765", PID),
                               ev("Tea Act", "1773", PID)], [1, 2, 0], two)
    assert_dropped(bad, [P2], code="unsupported_fact")


def test_rt51_order_date_borrowed_from_another_passage():
    """RT51: the event cites passage 1 but its date comes from passage 2
    severity: high
    fix: none needed, dates are searched in the quotes of the cited passage only
    kind: fixable
    """
    cs = cites(P1.id, Q_PROC) + cites(PID, Q_STAMP, Q_TEA)
    bad = order(PROMPT_ORDER, [ev("Emancipation Proclamation", "1776", P1.id), ev("Stamp Act", "1765", PID),
                               ev("Tea Act", "1773", PID)], [1, 2, 0], cs)
    assert_dropped(bad, [P1, P2], code="unsupported_fact")


def test_rt52_order_labels_and_dates_swapped():
    """RT52: Stamp Act is given 1773 and Tea Act 1765; both dates are quoted, so the wrong chronology passes
    severity: high
    fix: require an event's label and date_text to occur in the same sentence of one cited quote
    kind: fixable
    """
    swapped = order(PROMPT_ORDER, [ev("Stamp Act", "1773", PID), ev("Tea Act", "1765", PID),
                                   ev("Independence", "1776", PID)], [1, 0, 2], ALL3)
    assert_dropped(swapped, [P2])


def test_rt53_order_date_without_its_era():
    """RT53: the quote says 500 BC, the event date_text is "500", so Athens sorts after Rome (476 AD)
    severity: high
    fix: derive the sort key from the quote span (with its era), not from free text; reject a bare number next to BC
    kind: fixable
    """
    text = "Athens' golden age began near 500 BC. Rome fell in 476 AD. Alexander the Great died in 323 BC."
    p = passage(text, "hist:p30:1")
    qs = cites(p.id, "Athens' golden age began near 500 BC.", "Rome fell in 476 AD.",
               "Alexander the Great died in 323 BC.")
    honest = order(PROMPT_ORDER, [ev("Athens' golden age", "500 BC", p.id), ev("Rome fell", "476 AD", p.id),
                                  ev("Alexander the Great died", "323 BC", p.id)], [0, 2, 1], qs)
    assert_passes(honest, [p])
    forged = order(PROMPT_ORDER, [ev("Athens' golden age", "500", p.id), ev("Rome fell", "476 AD", p.id),
                                  ev("Alexander the Great died", "323 BC", p.id)], [2, 1, 0], qs)
    assert_dropped(forged, [p])


def test_rt54_year_only_event_ordered_before_a_dated_event():
    """RT54: "1861" (no month) is put before "March 4, 1861", which the text does not establish
    severity: high
    fix: when two events share a year but differ in precision, treat their order as ambiguous and drop
    kind: fixable
    """
    text = "Lincoln took the oath on March 4, 1861. Fort Sumter fell in 1861. The war ended in 1865."
    p = passage(text, "hist:p31:1")
    qs = cites(p.id, "Lincoln took the oath on March 4, 1861.", "Fort Sumter fell in 1861.",
               "The war ended in 1865.")
    bad = order(PROMPT_ORDER, [ev("Lincoln took the oath", "March 4, 1861", p.id),
                               ev("The war ended", "1865", p.id), ev("Fort Sumter fell", "1861", p.id)],
                [2, 0, 1], qs)
    assert_dropped(bad, [p])


def test_rt55_decade_ordered_before_a_year_inside_it():
    """RT55: "the 1860s" is put before 1861, though 1861 is inside the 1860s
    severity: high
    fix: treat overlapping periods (a decade and a year within it) as ambiguous and drop
    kind: fixable
    """
    text = "Railroads boomed in the 1860s. The telegraph reached the coast in 1861. The war ended in 1865."
    p = passage(text, "hist:p32:1")
    qs = cites(p.id, "Railroads boomed in the 1860s.", "The telegraph reached the coast in 1861.",
               "The war ended in 1865.")
    bad = order(PROMPT_ORDER, [ev("The telegraph reached the coast", "1861", p.id), ev("The war ended", "1865", p.id),
                               ev("Railroads boomed", "the 1860s", p.id)], [2, 0, 1], qs)
    assert_dropped(bad, [p])


def test_rt56_order_date_with_a_qualifier():
    """RT56: "before 1800" is used as the date 1800, which puts it after 1777
    severity: high
    fix: reject a date_text preceded in the quote by before, after, by, until, circa, c., around, about, early, late
    kind: fixable
    """
    text = ("Several states abolished slavery before 1800. Vermont wrote its constitution in 1777. "
            "Congress banned the slave trade in 1808.")
    p = passage(text, "hist:p33:1")
    qs = cites(p.id, "Several states abolished slavery before 1800.", "Vermont wrote its constitution in 1777.",
               "Congress banned the slave trade in 1808.")
    bad = order(PROMPT_ORDER, [ev("Congress banned the slave trade", "1808", p.id),
                               ev("Several states abolished slavery", "1800", p.id),
                               ev("Vermont wrote its constitution", "1777", p.id)], [2, 1, 0], qs)
    assert_dropped(bad, [p])


def test_rt57_order_event_label_invented():
    """RT57: the label "the transcontinental railroad opened" is attached to the Tea Act's date
    severity: high
    fix: for each event, require the label's content words to appear in the same quote sentence as its date
    kind: fixable
    """
    bad = order(PROMPT_ORDER, [ev("the transcontinental railroad opened", "1773", PID), ev("Stamp Act", "1765", PID),
                               ev("Independence", "1776", PID)], [1, 0, 2], ALL3)
    assert_dropped(bad, [P2])


def test_rt58_order_two_events_with_the_same_date():
    """RT58: two events dated 1776 cannot be ordered
    severity: high
    fix: none needed, ties are dropped as ambiguous
    kind: fixable
    """
    text = "The Continental Congress voted in 1776. Independence was declared in 1776. The Stamp Act came in 1765."
    p = passage(text, "hist:p34:1")
    qs = cites(p.id, "The Continental Congress voted in 1776.", "Independence was declared in 1776.",
               "The Stamp Act came in 1765.")
    tie = order(PROMPT_ORDER, [ev("Independence", "1776", p.id), ev("Continental Congress", "1776", p.id),
                               ev("Stamp Act", "1765", p.id)], [2, 0, 1], qs)
    assert_dropped(tie, [p], code="ambiguous")


def test_rt59_order_key_that_disagrees_with_the_dates():
    """RT59: correct_order puts 1776 before 1765
    severity: high
    fix: none needed, correct_order is recomputed from the dates
    kind: fixable
    """
    bad = order(PROMPT_ORDER, [ev("Independence", "1776", PID), ev("Stamp Act", "1765", PID),
                               ev("Tea Act", "1773", PID)], [0, 2, 1], ALL3)
    assert_dropped(bad, [P2], code="self_check_failed")


def test_rt60_order_date_written_in_another_format():
    """RT60: the date_text "4 March 1861" for a quote that says "March 4, 1861" is dropped (over-strict)
    severity: low
    fix: optional: accept a date_text that parses to the same date as a quoted span
    kind: honest-limit
    """
    text = "Lincoln took the oath on March 4, 1861. Fort Sumter fell in April 1861. The war ended in 1865."
    p = passage(text, "hist:p35:1")
    qs = cites(p.id, "Lincoln took the oath on March 4, 1861.", "Fort Sumter fell in April 1861.",
               "The war ended in 1865.")
    honest = order(PROMPT_ORDER, [ev("The war ended", "1865", p.id), ev("Lincoln took the oath", "March 4, 1861", p.id),
                                  ev("Fort Sumter fell", "April 1861", p.id)], [1, 2, 0], qs)
    assert_passes(honest, [p])
    shifted = order(PROMPT_ORDER, [ev("The war ended", "1865", p.id), ev("Lincoln took the oath", "4 March 1861", p.id),
                                   ev("Fort Sumter fell", "April 1861", p.id)], [1, 2, 0], qs)
    assert_dropped(shifted, [p], code="unsupported_fact")


# ------------------------------------------------------------------ match


def test_rt61_match_pairs_crossed():
    """RT61: Stamp Act is matched to "monopoly" and Tea Act to "printed paper"; every word is quoted
    severity: high
    fix: require both sides of a pair to occur in the same sentence of one cited quote
    kind: fixable
    """
    qs = cites(PID, Q_STAMP, Q_TEA)
    right = match("Match each act to what it did.",
                  [{"left": "Stamp Act", "right": "printed paper", "passage_id": PID},
                   {"left": "Tea Act", "right": "monopoly", "passage_id": PID}], qs)
    assert_passes(right, [P2])
    crossed = match("Match each act to what it did.",
                    [{"left": "Stamp Act", "right": "monopoly", "passage_id": PID},
                     {"left": "Tea Act", "right": "printed paper", "passage_id": PID}], qs)
    assert_dropped(crossed, [P2])


def test_rt62_match_right_side_from_another_passage():
    """RT62: the pair cites passage 1 but its right side only appears in passage 2
    severity: high
    fix: none needed, both sides are searched in the quotes of the pair's own passage
    kind: fixable
    """
    cs = cites(P1.id, Q_OATH) + cites(PID, Q_TEA)
    bad = match("Match.", [{"left": "Abraham Lincoln", "right": "monopoly", "passage_id": P1.id},
                           {"left": "Tea Act", "right": "tea", "passage_id": PID}], cs)
    assert_dropped(bad, [P1, P2], code="unsupported_fact")


# ------------------------------------------------------------------ cloze


def test_rt63_cloze_alias_that_does_not_fit_the_blank():
    """RT63: aliases "enslaved people" and "1863" are graded correct for "the ____ freed enslaved people"
    severity: medium
    fix: require the sentence with the blank filled by each alias to be an exact span of the quote
    kind: fixable
    """
    text = "In 1863, the ____ freed enslaved people in the rebel states."
    plain = cloze("Fill in the blank.", text, "Emancipation Proclamation", cites(P1.id, Q_PROC))
    assert_passes(plain, [P1])
    wrong = cloze("Fill in the blank.", text, "Emancipation Proclamation", cites(P1.id, Q_PROC),
                  aliases=["enslaved people", "1863"])
    assert_dropped(wrong, [P1])


def test_rt64_cloze_with_a_word_inserted_or_removed():
    """RT64: "did not" inserted, or "enslaved" removed, so the sentence is no longer a span of the quote
    severity: high
    fix: none needed, the filled sentence must be an exact span of the quote
    kind: fixable
    """
    for text in ("In 1863, the ____ did not free enslaved people in the rebel states.",
                 "In 1863, the ____ freed people in the rebel states.",
                 "In 1863, the ____ freed enslaved people in the Union states."):
        bad = cloze("Fill in the blank.", text, "Emancipation Proclamation", cites(P1.id, Q_PROC))
        assert_dropped(bad, [P1], code="bad_cloze")


def test_rt65_cloze_answer_repeated_outside_the_blank():
    """RT65: the answer "Stamp Act" also appears elsewhere in the sentence, so the blank gives itself away
    severity: low
    fix: none needed, a repeated answer is rejected, also in a different case
    kind: fixable
    """
    text = "The Stamp Act of 1765 taxed printed paper, and the Stamp Act angered colonists across the land."
    p = passage(text, "hist:p36:1")
    for blank in ("The Stamp Act of 1765 taxed printed paper, and the ____ angered colonists across the land.",
                  "The ____ of 1765 taxed printed paper, and the Stamp Act angered colonists across the land.",
                  "The Stamp Act of 1765 taxed printed paper, and the ____ angered colonists across the land."
                  .replace("The Stamp", "The stamp")):
        bad = cloze("Fill in the blank.", blank, "Stamp Act", cites(p.id, text))
        assert_dropped(bad, [p])


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="HOLE: a clause cut from a sentence that rejects it is a valid cloze span")
def test_rt66_cloze_from_a_claim_the_sentence_rejects():
    """RT66: the quote says historians once claimed 1860 but the archives show 1861; the cloze teaches 1860
    severity: high
    fix: only partly fixable: require the span to be the whole clause and drop sentences with hedges (claimed, but, myth, once believed)
    kind: honest-limit
    """
    text = "Historians once claimed that the war began in 1860, but the archives show it began in 1861."
    p = passage(text, "hist:p37:1")
    bad = cloze("Fill in the blank.", "the war began in ____", "1860", cites(p.id, text))
    assert_dropped(bad, [p])


# ------------------------------------------------------------------ multiple choice distractors


def test_rt67_distractor_written_in_another_number_format():
    """RT67: a second option that is the same year as "1,863", "in 1863" or "1863."
    severity: medium
    fix: none needed, options whose facts are all in the quote are ambiguous
    kind: fixable
    """
    prompt = "In what year did the Emancipation Proclamation free enslaved people in the rebel states?"
    for twin in ("1,863", "in 1863", "1863.", "1863"):
        item = mc(prompt, ["1863", twin, "1790"], 0, cites(P1.id, Q_PROC))
        assert_dropped(item, [P1], code="ambiguous")


def test_rt68_distractor_that_is_the_answer_plus_one_word():
    """RT68: "The famous Emancipation Proclamation" next to "The Emancipation Proclamation"
    severity: medium
    fix: also flag a distractor that contains all the facts and names of the correct option
    kind: fixable
    """
    prompt = "Which document freed enslaved people in the rebel states?"
    for twin in ("The famous Emancipation Proclamation", "Emancipation Proclamation itself"):
        item = mc(prompt, ["The Emancipation Proclamation", twin, "The Stamp Act"], 0, cites(P1.id, Q_PROC))
        assert_dropped(item, [P1], code="ambiguous")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="HOLE: a synonym of the right answer is judged unsupported")
def test_rt69_distractor_that_is_a_synonym_of_the_answer():
    """RT69: "It freed them" and "It liberated them" are both true, the second shares no word with the quote
    severity: medium
    fix: cannot be closed by token checks, it needs paraphrase detection
    kind: honest-limit
    """
    text = "The Emancipation Proclamation freed enslaved people in the rebel states in 1863."
    p = passage(text, "hist:p38:1")
    item = mc("What did the Emancipation Proclamation do for enslaved people in 1863?",
              ["It freed them", "It liberated them", "It taxed them"], 0, cites(p.id, text))
    assert_dropped(item, [p], code="ambiguous")


def test_rt70_distractor_that_is_true_in_another_passage():
    """RT70: the question has two right answers, one backed by a passage that was not cited
    severity: medium
    fix: check each distractor against every passage of the objective, not only the cited quotes
    kind: fixable
    """
    pa = passage("Abraham Lincoln took the oath of office in 1861 as the sixteenth President.", "hist:p39:1")
    pb = passage("Jefferson Davis took the oath of office in 1861 as president of the Confederacy.", "hist:p39:2")
    item = mc("Who took the oath of office in 1861?", ["Abraham Lincoln", "Jefferson Davis", "Ulysses Grant"], 0,
              cites(pa.id, "Abraham Lincoln took the oath of office in 1861 as the sixteenth President."))
    assert_dropped(item, [pa, pb], code="ambiguous")


def test_rt91_none_or_all_of_the_above_as_the_key():
    """RT91: the key is "None of the above" or "All of the above", which no quote can support
    severity: medium
    fix: none needed, the key must share a content word with the quote
    kind: fixable
    """
    prompt = "Which document freed enslaved people in the rebel states?"
    for key in ("None of the above", "All of the above"):
        item = mc(prompt, [key, "The Stamp Act", "The Tea Act"], 0, cites(P1.id, Q_PROC))
        assert_dropped(item, [P1], code="unsupported_fact")


def test_rt92_malformed_multiple_choice():
    """RT92: two options only, a repeated option, the key repeated in another case
    severity: medium
    fix: none needed, the structural check runs before any quote is read
    kind: fixable
    """
    prompt = "Which document freed enslaved people in the rebel states?"
    two = mc(prompt, ["The Emancipation Proclamation", "The Stamp Act"], 0, cites(P1.id, Q_PROC))
    assert_dropped(two, [P1], code="self_check_failed")
    repeated = mc(prompt, ["The Emancipation Proclamation", "The Stamp Act", "The Stamp Act"], 0,
                  cites(P1.id, Q_PROC))
    assert_dropped(repeated, [P1], code="ambiguous")
    cased = mc(prompt, ["The Emancipation Proclamation", "the emancipation proclamation", "The Tea Act"], 0,
               cites(P1.id, Q_PROC))
    assert_dropped(cased, [P1], code="ambiguous")


def test_rt93_item_with_no_citations_at_all():
    """RT93: a materials item whose citations were emptied after validation (model_construct)
    severity: high
    fix: none needed, no citation is quote_missing
    kind: fixable
    """
    sound = mc("Which document freed enslaved people in the rebel states?",
               ["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0, cites(P1.id, Q_PROC))
    hollow = Item.model_construct(**{**dict(sound), "citations": []})
    assert_dropped(hollow, [P1], code="quote_missing")


# ------------------------------------------------------------------ short answer, pooled citations


def test_rt71_required_term_is_a_stop_word():
    """RT71: the only required term is "the", so every answer that contains "the" is graded correct
    severity: medium
    fix: require each required term to contain a content word of at least 3 letters (or a number or name)
    kind: fixable
    """
    for terms in (["the"], ["in"], ["of", "the"]):
        item = short("What did the Emancipation Proclamation do?", terms, cites(P1.id, Q_PROC))
        assert_dropped(item, [P1], code="unsupported_fact")


def test_rt72_key_from_one_quote_question_from_another():
    """RT72: the question is about the oath (passage 1), the key is Jefferson Davis from a second quote
    severity: high
    fix: require the key to be supported by one quote that also overlaps the prompt, not by the pool of all quotes
    kind: fixable
    """
    davis = passage("Jefferson Davis presided over the government of the rebel states in Richmond.", "hist:p40:1")
    q_davis = "Jefferson Davis presided over the government of the rebel states in Richmond."
    prompt = "Who took the oath of office on March 4, 1861?"
    options = ["Jefferson Davis", "Ulysses Grant", "Robert Smith"]
    assert_dropped(mc(prompt, options, 0, cites(davis.id, q_davis)), [P1, davis], why="control: one citation")
    pooled = mc(prompt, options, 0, cites(P1.id, Q_OATH) + cites(davis.id, q_davis))
    assert_dropped(pooled, [P1, davis])


# ------------------------------------------------------------------ prompts


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="HOLE: a prompt with a false premise built from supported words passes")
def test_rt73_false_premise_in_the_prompt():
    """RT73: "Why did the Emancipation Proclamation fail to free anyone in 1863?" uses only supported names and years
    severity: high
    fix: cannot be closed by token checks, it needs a model of what the premise claims
    kind: honest-limit
    """
    item = explain("Why did the Emancipation Proclamation fail to free anyone in 1863?", cites(P1.id, Q_PROC))
    assert_dropped(item, [P1])


def test_rt74_ordinal_premise_in_the_prompt():
    """RT74: the quote says Lee retreated after the third battle, the prompt says the fifth
    severity: high
    fix: ignore small number words in prompts only when they are cardinals after an imperative (name, list, give); check ordinals
    kind: fixable
    """
    text = "Lee's army fought three battles that summer, and Lee's army retreated after the third."
    p = passage(text, "hist:p41:1")
    honest = explain("Why did Lee's army retreat after the third battle?", cites(p.id, text))
    assert_passes(honest, [p])
    bad = explain("Why did Lee's army retreat after the fifth battle?", cites(p.id, text))
    assert_dropped(bad, [p], code="unsupported_fact")


def test_rt75_prompt_premise_taken_from_a_false_objective():
    """RT75: the course-map objective says Lincoln signed the Stamp Act in 1765, so the prompt may say it too
    severity: high
    fix: check prompts against the passages only, and gate objective statements against the passages at import
    kind: fixable
    """
    objective = "Explain why Lincoln signed the Stamp Act in 1765."
    item = explain("Why did Lincoln sign the Stamp Act in 1765?", cites(P1.id, Q_OATH))
    assert_dropped(item, [P1], why="control: without the objective as context")
    assert_dropped(item, [P1], context=[objective])


def test_rt76_prompt_that_contains_the_answer():
    """RT76: the question text states the answer (MC option, cloze answer, required term), all of it quoted so it passes
    severity: low
    fix: structural check that no answer, alias or required term appears verbatim in the prompt
    kind: fixable
    """
    leaks = []
    q = cites(P1.id, Q_PROC)
    items = {
        "mc": mc("The Emancipation Proclamation freed enslaved people in the rebel states. Which document is this?",
                 ["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0, q),
        "cloze": cloze("Fill in the blank: the Emancipation Proclamation freed enslaved people.",
                       "In 1863, the ____ freed enslaved people in the rebel states.", "Emancipation Proclamation", q),
        "short": short("Name the Emancipation Proclamation.", ["Emancipation Proclamation"], q),
    }
    for fmt, item in items.items():
        if not codes(item, [P1]):
            leaks.append(fmt)
    assert not leaks, f"prompts that give away the answer got through: {leaks}"


# ------------------------------------------------------------------ escape hatches


def test_rt77_model_knowledge_item_in_a_strict_course():
    """RT77: an item that labels itself model_knowledge skips gates a-d; the gate cannot know the course is strict
    severity: medium
    fix: give check_item a mode argument and refuse origin='model_knowledge' items when it is 'strict'
    kind: fixable
    """
    item = Item(id="it_mk", objective_id="o1", format="mc", prompt="Who won the Battle of Waterloo?",
                payload=MCPayload(options=["Napoleon", "Wellington", "Blucher"], correct_index=0),
                origin="model_knowledge", citations=[], checker="mc")
    assert_dropped(item, [P1])


def test_rt78_numeric_item_prompt_with_a_false_fact():
    """RT78: a cited numeric item whose prompt says Newton published in 1812 passes verify() because only the key is computed
    severity: high
    fix: run the prompt fact check (names, numbers, dates) on every cited item, not only the reading formats
    kind: fixable
    """
    from codecoach.core.models import NumericPayload, Objective, Quantity
    from codecoach.gen.generate import verify

    p = passage("Newton's second law states that force equals mass times acceleration, as Newton showed in 1687.",
                "phys:p1:1")
    item = Item(id="it_num", objective_id="o1", format="numeric",
                prompt="Newton published his second law in 1812. A 2 kg cart accelerates at 3 m/s^2. "
                       "What is the net force?",
                payload=NumericPayload(given=[Quantity(name="m", value=2.0, unit="kg"),
                                              Quantity(name="a", value=3.0, unit="m/s^2")],
                                       quantity="the net force", answer_value=6.0, unit="N"),
                citations=[Citation(passage_id=p.id,
                                    quote="Newton's second law states that force equals mass times acceleration")],
                checker="numeric")
    obj = Objective(id="o1", unit="u", lesson="l", verb="compute", statement="Compute force from mass and acceleration.",
                    passage_ids=[p.id])
    with numeric_key_verified():
        failures = verify(item, [p], obj)
    assert failures, "the gate let this item through"


def test_rt79_order_already_in_the_right_order():
    """RT79: correct_order is 0,1,2, so the presented list IS the answer
    severity: low
    fix: structural check: reject correct_order == identity (the generator already reshuffles, the gate should insist)
    kind: fixable
    """
    sorted_item = order(PROMPT_ORDER, [ev("Stamp Act", "1765", PID), ev("Tea Act", "1773", PID),
                                       ev("Independence", "1776", PID)], [0, 1, 2], ALL3)
    view = public_view(sorted_item)
    assert [e["label"] for e in view.payload["events"]] == ["Stamp Act", "Tea Act", "Independence"]   # the leak is real
    assert_dropped(sorted_item, [P2])


def test_rt80_order_labels_that_contain_their_dates():
    """RT80: labels "Stamp Act of 1765" etc. are shown before answering and give the order away
    severity: low
    fix: structural check: no event label may contain a date or year that appears in any event's date_text
    kind: fixable
    """
    labelled = order(PROMPT_ORDER, [ev("Tea Act of 1773", "1773", PID), ev("Independence declared in 1776", "1776", PID),
                                    ev("Stamp Act of 1765", "1765", PID)], [2, 0, 1], ALL3)
    shown = [e["label"] for e in public_view(labelled).payload["events"]]
    assert any("1765" in s for s in shown)                                             # the leak is real
    assert_dropped(labelled, [P2])
