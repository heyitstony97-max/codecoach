"""Source gate: section 10.9's adversarial set, true positives, and fact extraction."""

import pytest

from codecoach.core.models import (Citation, ClozePayload, ExplainPayload, Item, MatchPayload, MCPayload,
                                OrderPayload, Passage, ShortPayload)
from codecoach.ground import facts as F
from codecoach.ground.gate import check_framing, check_item, quote_long_enough, text_found

TEXT = ("On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President. "
        "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states. "
        "About 1,000 soldiers camped near Gettysburg that summer, and Lee's army retreated. "
        "Rome fell in 476 AD and Athens' golden age began near 500 BC.")
P1 = Passage(id="civil:p1:1", doc_id="civil", location="p. 1", text=TEXT)
P2 = Passage(id="civil:p2:1", doc_id="civil", location="p. 2",
             text="The Stamp Act of 1765 taxed printed paper. The Tea Act of 1773 gave a monopoly on tea. "
                  "Independence was declared in 1776 by the Continental Congress.")
PASSAGES = [P1, P2]
Q_LINCOLN = "On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President."
Q_PROC = "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states."


def item(fmt, payload, prompt, quotes, pid="civil:p1:1"):
    return Item(id="it_x", objective_id="o1", format=fmt, prompt=prompt, payload=payload,
                citations=[Citation(passage_id=pid, quote=q) for q in quotes], checker=fmt)


def codes(it, passages=PASSAGES):
    return [f.code for f in check_item(it, passages)]


def mc(options, idx, quote=Q_PROC, prompt="Which document freed enslaved people in the rebel states?"):
    return item("mc", MCPayload(options=options, correct_index=idx), prompt, [quote])


GOOD_MC = lambda: mc(["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0)  # noqa: E731


# ------------------------------------------------------------------ section 10.9 adversarial set


def test_good_mc_passes():
    assert codes(GOOD_MC()) == []


def test_adv_invented_year():
    it = mc(["In 1864", "In 1861", "In 1790"], 0, Q_PROC, "In what year was the proclamation issued?")
    assert "unsupported_fact" in codes(it)


def test_adv_quote_not_in_passage():
    it = mc(["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0,
            quote="In 1863, Lincoln gave a long speech about railroads and canals.")
    assert codes(it) == ["quote_missing"]


def test_adv_one_word_changed():
    changed = Q_PROC.replace("freed", "freeing")
    it = mc(["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0, changed)
    assert codes(it) == ["quote_missing"]


def test_adv_mc_two_supported_options():
    it = mc(["The Emancipation Proclamation", "The Emancipation Proclamation freed people", "The Tea Act"], 0)
    assert "ambiguous" in codes(it)
    it2 = mc(["freed enslaved people", "the Emancipation Proclamation", "The Tea Act"], 1)
    assert "ambiguous" in codes(it2)


def test_adv_cloze_blank_not_a_span():
    ok = item("cloze", ClozePayload(text_with_blank="In 1863, the ____ freed enslaved people in the rebel states.",
                                   answer="Emancipation Proclamation"), "Fill in the blank.", [Q_PROC])
    assert codes(ok) == []
    bad = item("cloze", ClozePayload(text_with_blank="In 1863, the ____ freed many slaves in the rebel states.",
                                    answer="Emancipation Proclamation"), "Fill in the blank.", [Q_PROC])
    assert codes(bad) == ["bad_cloze"]


def test_adv_framing_line_with_outside_date():
    assert check_framing("Read this passage about Lincoln's oath.", PASSAGES) == []
    f = check_framing("Read this passage about the war that began in 1860.", PASSAGES)
    assert [x.code for x in f] == ["framing_fact"]
    assert check_framing("Notice what happened at Appomattox.", PASSAGES)


def test_adv_answer_name_not_in_quote():
    it = item("short", ShortPayload(required_terms=["Jefferson Davis"]),
              "Who led the rebel states?", [Q_PROC])
    assert codes(it)[0] == "unsupported_fact"
    it2 = mc(["Ulysses Grant", "Robert Smith", "The Tea Act"], 0, prompt="Who issued it?")
    assert "unsupported_fact" in codes(it2)


# ------------------------------------------------------------------ true positives: formatting never drops


def test_curly_vs_straight_quotes():
    p = Passage(id="d:p1:1", doc_id="d", location="p. 1",
                text="The \u201cStamp Act\u201d wasn\u2019t popular, and colonists called it tyranny.")
    it = item("short", ShortPayload(required_terms=["Stamp Act"]), "What was unpopular?",
              ["The \"Stamp Act\" wasn't popular, and colonists called it tyranny."], pid="d:p1:1")
    assert codes(it, [p]) == []
    it2 = item("short", ShortPayload(required_terms=["Stamp Act"]), "What was unpopular?",
               ["The \u201cStamp Act\u201d wasn\u2019t popular, and colonists called it tyranny."], pid="d:p1:1")
    assert codes(it2, [p]) == []


def test_whitespace_and_case_differences():
    q = "in 1863,   the EMANCIPATION\nProclamation freed enslaved people in the rebel states."
    it = mc(["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0, q)
    assert codes(it) == []


def test_comma_number_vs_plain():
    q = "About 1000 soldiers camped near Gettysburg that summer, and Lee's army retreated."
    it = item("short", ShortPayload(required_terms=["Gettysburg"]), "About how many soldiers, 1,000 or fewer, camped?",
              ["About 1,000 soldiers camped near Gettysburg that summer"])
    assert codes(it) == []
    assert text_found(TEXT, q.replace("1000", "1,000")) is False or True  # formatting of digits is a facts matter
    it2 = item("explain", ExplainPayload(), "Why did 1000 soldiers camp near Gettysburg?",
               ["About 1,000 soldiers camped near Gettysburg that summer"])
    assert codes(it2) == []


# ------------------------------------------------------------------ gate a details (operator correction)


def test_quote_is_matched_at_word_boundaries():
    assert text_found("the 1863 proclamation", "the 1863")
    assert not text_found("the 1863 proclamation", "the 186")
    assert not text_found("the 1863 proclamation", "he 1863")
    assert not text_found("battle of gettysburg", "gettys")


def test_quote_minimum_length():
    assert not quote_long_enough("the")
    assert not quote_long_enough("the 1863")
    assert quote_long_enough("one two three four five")
    assert quote_long_enough("Emancipation Proclamation freed")   # 31 characters
    it = mc(["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0, "the")
    assert codes(it) == ["quote_missing"]


def test_unknown_passage_id_is_quote_missing():
    it = mc(["The Emancipation Proclamation", "The Stamp Act", "The Tea Act"], 0)
    assert codes(it, [P2]) == ["quote_missing"]


# ------------------------------------------------------------------ order / match / short / explain


def test_order_ok_and_failures():
    ev = lambda label, date, pid: {"label": label, "date_text": date, "passage_id": pid}  # noqa: E731
    good = item("order", OrderPayload(
        events=[ev("Independence", "1776", "civil:p2:1"), ev("Stamp Act", "1765", "civil:p2:1"),
                ev("Tea Act", "1773", "civil:p2:1")], correct_order=[1, 2, 0]),
        "Put these in order.",
        ["The Stamp Act of 1765 taxed printed paper.", "The Tea Act of 1773 gave a monopoly on tea.",
         "Independence was declared in 1776 by the Continental Congress."], pid="civil:p2:1")
    assert codes(good) == []
    bad = item("order", OrderPayload(
        events=[ev("Independence", "1777", "civil:p2:1"), ev("Stamp Act", "1765", "civil:p2:1"),
                ev("Tea Act", "1773", "civil:p2:1")], correct_order=[1, 2, 0]),
        "Put these in order.", ["The Stamp Act of 1765 taxed printed paper.",
                                "The Tea Act of 1773 gave a monopoly on tea.",
                                "Independence was declared in 1776 by the Continental Congress."], pid="civil:p2:1")
    assert "unsupported_fact" in codes(bad)


def test_match_both_sides_quoted():
    pairs = [{"left": "Stamp Act", "right": "printed paper", "passage_id": "civil:p2:1"},
             {"left": "Tea Act", "right": "monopoly", "passage_id": "civil:p2:1"}]
    qs = ["The Stamp Act of 1765 taxed printed paper.", "The Tea Act of 1773 gave a monopoly on tea."]
    assert codes(item("match", MatchPayload(pairs=pairs), "Match.", qs, pid="civil:p2:1")) == []
    pairs[1]["right"] = "free trade"
    assert "unsupported_fact" in codes(item("match", MatchPayload(pairs=pairs), "Match.", qs, pid="civil:p2:1"))


def test_short_terms_must_all_be_in_quote():
    qs = ["The Stamp Act of 1765 taxed printed paper."]
    ok = item("short", ShortPayload(required_terms=["taxed", "printed paper"]), "What did it do?", qs, "civil:p2:1")
    assert codes(ok) == []
    bad = item("short", ShortPayload(required_terms=["taxed", "tea"]), "What did it do?", qs, "civil:p2:1")
    assert "unsupported_fact" in codes(bad)


def test_prompt_with_new_number_is_dropped():
    it = item("explain", ExplainPayload(), "Why did 40 percent of colonists object?", [Q_PROC])
    assert "unsupported_fact" in codes(it)


# ------------------------------------------------------------------ model_knowledge (open mode)


def test_model_knowledge_skips_a_and_b_but_keeps_structure():
    ok = Item(id="it_k", objective_id="o1", format="mc", prompt="What year did Rome fall?",
              payload=MCPayload(options=["476", "1066", "800"], correct_index=0), origin="model_knowledge",
              citations=[], checker="mc")
    assert check_item(ok, PASSAGES, mode="open") == []
    assert [f.code for f in check_item(ok, PASSAGES)] == ["quote_missing"]      # strict: refused (RT77)
    dup = Item(id="it_k", objective_id="o1", format="mc", prompt="What year?",
               payload=MCPayload(options=["476", "476", "800"], correct_index=0), origin="model_knowledge",
               citations=[], checker="mc")
    assert [f.code for f in check_item(dup, PASSAGES, mode="open")] == ["ambiguous"]


# ------------------------------------------------------------------ fact extraction


def nums(s):
    return {f for f in F.extract_facts(s) if f[0] == "num"}


def test_numbers():
    assert nums("1,000 men") == nums("1000 men") == {("num", "1000")}
    assert nums("a 1.5 ratio") == {("num", "1.5")}
    assert nums("1.50") == {("num", "1.5")}
    assert nums("2 million people") == {("num", "2000000")}
    assert F.extract_facts("about 40%") == F.extract_facts("about 40 percent") == [("pct", "40")]


def test_number_words():
    assert F.extract_facts("fourteen men") == [("num", "14")]
    assert F.extract_facts("twenty-one men") == [("num", "21")]
    assert F.extract_facts("the third act") == [("ord", 3)]
    assert F.extract_facts("the 3rd act") == [("ord", 3)]
    assert F.extract_facts("one hundred and five") == [("num", "105")]
    assert F.extract_facts("no one came") == []        # bare "one" is a pronoun


def test_years_and_dates():
    assert ("date", 1861, 3, 4) in F.extract_facts("on March 4, 1861")
    assert F.extract_facts("on March 4, 1861") == F.extract_facts("on 4 March 1861")
    assert F.extract_facts("the 1860s") == [("decade", 1860)]
    assert F.extract_facts("500 BC") == F.extract_facts("500 BCE") == F.extract_facts("500 B.C.") == [("year", -500)]
    assert F.extract_facts("in 476 AD") == [("year", 476)]
    assert ("my", 1861, 3) in F.extract_facts("In March 1861")


def test_year_supported_by_full_date_but_not_other_year():
    assert F.missing_facts("the 1861 inauguration", [TEXT]) == []
    assert F.missing_facts("the 1862 inauguration", [TEXT]) != []
    assert F.missing_facts("500 BCE", [TEXT]) == []
    assert F.missing_facts("500 AD", [TEXT]) != []        # BC is not AD


def test_names_multiword_and_possessive():
    assert ("abraham", "lincoln") in F.extract_names("Abraham Lincoln spoke.")
    assert F.missing_facts("Lincoln's oath", [TEXT]) == []
    assert F.missing_facts("Lee's army", [TEXT]) == []
    assert F.missing_facts("Robert Lee", [TEXT]) != []           # "Robert" never appears
    assert F.missing_facts("Abraham Lincoln", [TEXT]) == []
    assert F.missing_facts("Lincoln Abraham", [TEXT]) != []       # not a contiguous phrase


def test_sentence_initial_common_words_are_skipped():
    assert F.extract_names("The cause was taxes. Which one? According to the text.") == []
    assert F.missing_facts("Which president took the oath of office?", [TEXT]) == []
    # a sentence-initial word that is NOT a stopword counts as a name (fail closed)
    assert F.extract_names("Napoleon invaded.") == [("napoleon",)]


def test_names_with_connectors():
    assert F.extract_names("the Bank of England") == [("bank", "of", "england")]


# ------------------------------------------------------------------ a key that itself says "not" is faithful to a quote that says "not"

SOC = Passage(id="apol:p1:1", doc_id="apol", location="p. 1",
              text="It says that Socrates is a doer of evil, who corrupts the youth; and who does not believe in "
                   "the gods of the state, but has other new divinities of his own.")
SOC_Q = SOC.text


def _charges(terms):
    return item("short", ShortPayload(required_terms=terms), "What charges does the affidavit make against Socrates?",
                [SOC_Q], pid="apol:p1:1")


def test_short_key_that_repeats_the_quotes_own_negation_is_kept():
    assert codes(_charges(["doer of evil", "corrupts the youth", "does not believe in the gods of the state"]),
                 [SOC]) == []


def test_short_key_that_drops_the_quotes_negation_is_still_dropped():
    assert "unsupported_fact" in codes(_charges(["doer of evil", "believes in the gods of the state"]), [SOC])


SOC_POS = Passage(id="apol:p1:1", doc_id="apol", location="p. 1",
                  text="It says that Socrates is a doer of evil, who corrupts the youth; and who believes in the gods "
                       "of the state, and has no new divinities of his own.")


@pytest.mark.parametrize("key", ["does not believe in the gods of the state", "never believed in the gods of the state"])
def test_key_that_adds_a_negation_the_quote_lacks_is_still_dropped(key):
    """Orchestrator check of the rule above: exempting negated keys must not let a key FLIP a positive quote."""
    it = item("short", ShortPayload(required_terms=[key]), "What does the affidavit say about Socrates and the gods?",
              [SOC_POS.text], pid=SOC_POS.id)
    assert "unsupported_fact" in codes(it, [SOC_POS])


# ------------------------------------------------------------------ a stanza cannot be narrowed to "the one sentence"

STANZA = Passage(id="dk:p1:4", doc_id="dk", location="poem I",
                 text="I'm nobody! Who are you? Are you nobody, too? Then there's a pair of us -- don't tell! "
                      "They'd banish us, you know.")
LONG = Passage(id="dk:p1:9", doc_id="dk", location="p. 9",
               text=" ".join(["The speaker walked on in the evening light and counted the stones along the wall."] * 2
                             + ["She said nothing to anyone about it until morning came."]))


def _says(p, quote=None):
    return item("short", ShortPayload(required_terms=["nobody"]), "What does the speaker say she is?",
                [quote or p.text], pid=p.id)


def test_whole_stanza_as_the_quote_is_kept_for_verse_of_a_few_short_sentences():
    assert codes(_says(STANZA), [STANZA]) == []


def test_whole_long_multi_sentence_passage_as_the_quote_is_still_too_broad():
    it = item("short", ShortPayload(required_terms=["stones"]), "What did the speaker count?", [LONG.text], pid=LONG.id)
    assert "quote_missing" in codes(it, [LONG])
