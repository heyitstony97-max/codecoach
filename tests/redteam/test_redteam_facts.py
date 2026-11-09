"""Red team, gate b: can a number, date, name or claim that the quote does not say be marked as the
answer key (or sit in a prompt)? Every test builds an item whose KEY is false, or an equivalent true
control, and asserts the gate drops the false one. Records are described in redteam_kit.py."""

import pytest
from redteam_kit import assert_dropped, assert_passes, cites, mc, passage, short


def run(text, prompt, options, quote=None, pid="hist:p20:1"):
    """An MC item whose attacked key is options[0]; the quote defaults to the whole sentence passage."""
    p = passage(text, pid)
    return mc(prompt, options, 0, cites(pid, quote or text)), [p]


def drop(text, prompt, options, code=None, quote=None):
    item, ps = run(text, prompt, options, quote)
    assert_dropped(item, ps, code=code)


def ok(text, prompt, options, quote=None):
    item, ps = run(text, prompt, options, quote)
    assert_passes(item, ps)


LINC = "On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President."
LINC_PROMPT = "When did Abraham Lincoln take the oath of office as the sixteenth President?"
WHEN_OPTS = ["June 1, 1790", "July 4, 1776"]


def test_rt20_cyrillic_homoglyph_in_an_answer_name():
    """RT20: the key is "Abraham Linсoln" with a Cyrillic "с", the quote has the Latin name
    severity: high
    fix: none needed, the name tokens differ so the name is reported as unsupported
    kind: fixable
    """
    prompt = "Who took the oath of office as the sixteenth President?"
    ok(LINC, prompt, ["Abraham Lincoln", "George Washington", "John Adams"])
    drop(LINC, prompt, ["Abraham Lin\u0441oln", "George Washington", "John Adams"], code="unsupported_fact")


def test_rt21_invisible_character_between_digit_groups():
    """RT21: the key "18<U+2061>64" displays as 1864 but is read as the two supported numbers 18 and 64
    severity: high
    fix: make ground.facts.prep strip the same invisible set as core.text.normalize (all Cf characters)
    kind: fixable
    """
    text = "The fort held 18 cannons and 64 muskets in the spring of that year."
    prompt = "In what year was the fort built?"
    drop(text, prompt, ["1864", "1790", "1920"], code="unsupported_fact")          # control: the plain forgery
    for ch in ("\u2061", "\u2062", "\u202e", "\u2066"):
        drop(text, prompt, [f"18{ch}64", "1790", "1920"])


def test_rt22_number_words_and_digits_that_do_not_match():
    """RT22: key "21 men" when the quote says twelve, and "1005" when it says one hundred and five
    severity: high
    fix: none needed, number words are converted to numbers before comparing
    kind: fixable
    """
    text = "The expedition lost twelve men on the river in 1862, and one hundred and five more fell ill."
    prompt = "How many men did the expedition lose on the river in 1862?"
    ok(text, prompt, ["12 men", "7 men", "40 men"])
    for wrong in ("21 men", "twenty-one men", "1,200 men", "twelve hundred men"):
        drop(text, prompt, [wrong, "7 men", "40 men"], code="unsupported_fact")
    prompt2 = "How many more men fell ill on the river in 1862?"
    ok(text, prompt2, ["105 more", "7 more", "40 more"])
    drop(text, prompt2, ["1005 more", "7 more", "40 more"], code="unsupported_fact")
    drop(text, prompt2, ["one hundred and fifty more", "7 more", "40 more"], code="unsupported_fact")


def test_rt23_dozen_and_other_quantity_words():
    """RT23: the quote says three men, the key says "a dozen men"
    severity: high
    fix: add dozen, score, gross, couple, pair, hundreds, thousands and millions to the number-word list
    kind: fixable
    """
    text = "The expedition lost three men on the river in 1862."
    prompt = "How many men did the expedition lose on the river in 1862?"
    ok(text, prompt, ["three men", "none survived", "most of the crew"])
    drop(text, prompt, ["a dozen men", "none survived", "most of the crew"], code="unsupported_fact")


def test_rt24_spoken_year_built_from_two_supported_numbers():
    """RT24: key "eighteen sixty-four" is read as 18 and 64, which the quote has as separate counts
    severity: high
    fix: parse a teen followed by a tens-word as one year-like number, and require that number to be supported
    kind: fixable
    """
    text = "The fort held 18 cannons and 64 muskets in the spring of that year."
    prompt = "In what year was the fort built?"
    drop(text, prompt, ["eighteen sixty-four", "1790", "1920"], code="unsupported_fact")


def test_rt25_roman_numeral_mix_ups():
    """RT25: Louis XVI for XIV, Pius XI for IX, "Henry 8" for "Henry VIII"
    severity: high
    fix: none needed, a multi-letter numeral is part of the name and must match, digits are not Roman letters
    kind: fixable
    """
    louis = "Louis XIV ruled France for 72 years, and his great-grandson Louis XV followed him."
    prompt = "Which king ruled France for 72 years?"
    ok(louis, prompt, ["Louis XIV", "Henry IV", "Charles IX"])
    drop(louis, prompt, ["Louis XVI", "Henry IV", "Charles IX"], code="unsupported_fact")
    pius = "Pope Pius IX opened the council in 1869 and lost the Papal States a year later."
    drop(pius, "Which pope opened the council in 1869?", ["Pius XI", "Leo XIII", "John Paul"], code="unsupported_fact")
    henry = "Henry VIII broke with Rome in 1534 and took control of the English church."
    drop(henry, "Who broke with Rome in 1534?", ["Henry 8", "Edward III", "John Smith"], code="unsupported_fact")


def test_rt26_napoleon_i_for_napoleon_iii():
    """RT26: the quote says Napoleon III, the key says Napoleon I
    severity: high
    fix: do not treat "I" as the pronoun when it directly follows a capitalized word; keep it as a numeral token
    kind: fixable
    """
    text = "Napoleon III became Emperor of the French in 1852 and fell from power in 1870."
    prompt = "Who became Emperor of the French in 1852?"
    ok(text, prompt, ["Napoleon III", "Louis XVI", "Charles X"])
    drop(text, prompt, ["Napoleon I", "Louis XVI", "Charles X"], code="unsupported_fact")


def test_rt27_world_war_i_for_world_war_ii():
    """RT27: the quote says World War II, the key says World War I
    severity: high
    fix: same as RT26
    kind: fixable
    """
    text = "World War II ended in 1945 after six years of fighting across Europe and Asia."
    prompt = "Which war ended in 1945?"
    ok(text, prompt, ["World War II", "The Crimean War", "The Boer War"])
    drop(text, prompt, ["World War I", "The Crimean War", "The Boer War"], code="unsupported_fact")


def test_rt28_bc_and_ad_swapped():
    """RT28: the quote says 500 BC, the key says 500 AD (BC, BCE and B.C. are equal; AD is not)
    severity: high
    fix: none needed, the era is part of the year fact
    kind: fixable
    """
    text = "Athens' golden age began near 500 BC and Rome fell in 476 AD."
    prompt = "When did Athens' golden age begin?"
    for good in ("500 BC", "500 BCE", "500 B.C."):
        ok(text, prompt, [good, "44 AD", "1066 AD"])
    for bad in ("500 AD", "500 CE", "AD 500"):
        drop(text, prompt, [bad, "44 AD", "1066 AD"], code="unsupported_fact")


def test_rt29_era_dropped_from_the_key():
    """RT29: the quote says 500 BC, the key is the bare "500"
    severity: high
    fix: stop expanding ("year", -500) into a bare ("num", "500") in ground.facts.expand
    kind: fixable
    """
    text = "Athens' golden age began near 500 BC and Rome fell in 476 AD."
    drop(text, "In what year did Athens' golden age begin?", ["500", "44", "1066"], code="unsupported_fact")


def test_rt30_date_format_shifts():
    """RT30: the quote says March 4, 1861; keys with the month and day swapped or the year moved
    severity: high
    fix: none needed, "4 March 1861" and "March 4, 1861" are one fact and swapped parts are not
    kind: fixable
    """
    for good in ("4 March 1861", "March 4, 1861", "the 4th of March, 1861"):
        ok(LINC, LINC_PROMPT, [good, *WHEN_OPTS])
    for bad in ("April 3, 1861", "3 April 1861", "March 4, 1862", "4 March 1862", "March 14, 1861",
                "the 4th of March, 1860", "March 1860", "April 4, 1861", "March 3, 1861"):
        drop(LINC, LINC_PROMPT, [bad, *WHEN_OPTS], code="unsupported_fact")


def test_rt31_slash_date_transposed():
    """RT31: the quote says 3/4/1861, the key says 4/3/1861 (a different day)
    severity: high
    fix: parse d/m/y and m/d/y as ordered dates, and drop a key whose order differs or is ambiguous
    kind: fixable
    """
    text = "The oath was administered on 3/4/1861 in the Capitol."
    drop(text, "When was the oath administered?", ["4/3/1861", "7/4/1776", "1/1/1900"], code="unsupported_fact")


def test_rt32_iso_date_transposed():
    """RT32: the quote says 1861-03-04, the key says 1861-04-03
    severity: high
    fix: same as RT31 (parse yyyy-mm-dd as an ordered date)
    kind: fixable
    """
    text = "The treaty took effect on 1861-03-04 under the new rules."
    drop(text, "When did the treaty take effect?", ["1861-04-03", "1861-12-25", "1790-01-01"],
         code="unsupported_fact")


def test_rt33_decade_versus_year():
    """RT33: "the 1860s" and a single year are not interchangeable in either direction
    severity: high
    fix: none needed, a decade is its own fact
    kind: fixable
    """
    decade = "Railroad building boomed in the 1860s across the northern states."
    prompt = "When did railroad building boom across the northern states?"
    ok(decade, prompt, ["the 1860s", "the 1790s", "the 1920s"])
    for bad in ("1865", "1860", "the 1870s", "the 1850s"):
        drop(decade, prompt, [bad, "the 1790s", "the 1920s"], code="unsupported_fact")
    year = "Railroad building began in 1860 across the northern states."
    for bad in ("the 1860s", "the 1860's", "1865"):
        drop(year, "When did railroad building begin across the northern states?",
             [bad, "the 1790s", "the 1920s"], code="unsupported_fact")


def test_rt34_diacritics_in_names():
    """RT34: "Jose" for "José" and "José" for "Jose" are dropped (over-strict: a true spelling variant)
    severity: medium
    fix: optional: fold diacritics for names when both spellings differ only by accents
    kind: honest-limit
    """
    accent = "José Rizal wrote his novel in 1887 while living in Berlin."
    prompt = "Who wrote the novel in 1887?"
    ok(accent, prompt, ["Jos\u00e9 Rizal", "Mario Lopez", "Pedro Cruz"])
    ok(accent, prompt, ["Jose\u0301 Rizal", "Mario Lopez", "Pedro Cruz"])
    drop(accent, prompt, ["Jose Rizal", "Mario Lopez", "Pedro Cruz"], code="unsupported_fact")
    plain = "Jose Rizal wrote his novel in 1887 while living in Berlin."
    drop(plain, prompt, ["Jos\u00e9 Rizal", "Mario Lopez", "Pedro Cruz"], code="unsupported_fact")


def test_rt35_possessive_names():
    """RT35: "Lees army" for "Lee's army"; a possessive of a different name
    severity: high
    fix: none needed, only a trailing 's is stripped, so Lees is a different name
    kind: fixable
    """
    text = "Lee's army retreated after Gettysburg in 1863, and Grant's army advanced."
    prompt = "Whose army retreated after Gettysburg in 1863?"
    ok(text, prompt, ["Lee's army", "Custer's cavalry", "Porter's fleet"])
    ok(text, prompt, ["Lee\u2019s army", "Custer's cavalry", "Porter's fleet"])
    drop(text, prompt, ["Lees army", "Custer's cavalry", "Porter's fleet"], code="unsupported_fact")
    drop(text, prompt, ["Meade's army", "Custer's cavalry", "Porter's fleet"], code="unsupported_fact")


def test_rt36_partial_and_extended_names():
    """RT36: "Abraham Lincoln" when the quote only says Lincoln; "John Adams" for "John Quincy Adams"
    severity: high
    fix: none needed, a multi-word name must be a contiguous phrase of the quote
    kind: fixable
    """
    text = "Lincoln spoke at Gettysburg in 1863 before a crowd of thousands."
    prompt = "Who spoke at Gettysburg in 1863?"
    ok(text, prompt, ["Lincoln", "Grant", "Sherman"])
    drop(text, prompt, ["Abraham Lincoln", "Grant", "Sherman"], code="unsupported_fact")
    adams = "John Quincy Adams served as president from 1825 to 1829 after a close election."
    prompt = "Who served as president from 1825 to 1829?"
    ok(adams, prompt, ["John Quincy Adams", "Henry Clay", "Andrew Jackson"])
    drop(adams, prompt, ["John Adams", "Henry Clay", "Andrew Jackson"], code="unsupported_fact")


def test_rt37_number_inside_a_longer_number():
    """RT37: 186, 863 or 18 as the key when the quote says 1863
    severity: high
    fix: none needed, numbers are compared whole
    kind: fixable
    """
    text = "The Emancipation Proclamation took effect in 1863 in the rebel states."
    prompt = "In what year did the Emancipation Proclamation take effect?"
    ok(text, prompt, ["1863", "1790", "1920"])
    ok(text, prompt, ["1,863", "1790", "1920"])
    for bad in ("186", "863", "18", "18630", "1.863"):
        drop(text, prompt, [bad, "1790", "1920"], code="unsupported_fact")


def test_rt90_ratio_written_backwards():
    """RT90: the quote says 3:1, the key says 1:3; facts are compared as a set, so order is lost
    severity: high
    fix: keep numbers joined by ":" "/" or "to" as an ordered pair fact, as for RT31 and RT32
    kind: fixable
    """
    text = "In the war's final year the Union had soldiers at a ratio of 3:1 against the Confederacy."
    prompt = "What was the Union's ratio of soldiers to the Confederacy in the war's final year?"
    ok(text, prompt, ["3:1", "4:5", "7:2"])
    drop(text, prompt, ["1:3", "4:5", "7:2"], code="unsupported_fact")


def test_rt38_lowercase_invented_name():
    """RT38: key "rebel jefferson davis" in lowercase shares the word "rebel" with the quote
    severity: high
    fix: for the correct option, require every content word to appear in the quote (not only one shared token)
    kind: fixable
    """
    text = "The rebel states formed a new government in 1861 and chose a leader."
    prompt = "Who led the rebel states in 1861?"
    drop(text, prompt, ["rebel jefferson davis", "union general grant", "northern admiral porter"],
         code="unsupported_fact")


def test_rt39_lowercase_month_swapped():
    """RT39: key "april 4, 1861" in lowercase; its numbers 4 and 1861 are both in the quote
    severity: high
    fix: recognize month names case-insensitively in ground.facts.scan
    kind: fixable
    """
    drop(LINC, LINC_PROMPT, ["april 4, 1861", "june 1, 1790", "july 4, 1776"], code="unsupported_fact")


def test_rt40_negation_flip_in_multiple_choice():
    """RT40: the quote says the Senate did NOT ratify the treaty, the key says the Senate did
    severity: high
    fix: when the quote clause containing the key has not/never/no/n't/without/failed to/refused, drop unless the prompt carries it too
    kind: fixable
    """
    text = "The Senate did not ratify the treaty in 1920, and the treaty never took effect in America."
    prompt = "Which body ratified the treaty in 1920?"
    ok("The Senate ratified the treaty in 1920, and the treaty took effect in America.", prompt,
       ["The Senate", "The House", "The Court"])
    drop(text, prompt, ["The Senate", "The House", "The Court"], quote="The Senate did not ratify the treaty in 1920")


def test_rt41_negation_flip_in_short_answer():
    """RT41: required term "ratify" is graded correct for a quote that says the Senate did NOT ratify
    severity: high
    fix: same as RT40 for required terms
    kind: fixable
    """
    text = "The Senate did not ratify the treaty in 1920, and the treaty never took effect in America."
    p = passage(text, "hist:p21:1")
    item = short("What did the Senate do with the treaty in 1920?", ["ratify"],
                 cites(p.id, "The Senate did not ratify the treaty in 1920"))
    assert_dropped(item, [p])


def test_rt42_percentage_turned_into_a_count():
    """RT42: the quote says about 40% of the colonists, the key says 40 colonists
    severity: high
    fix: stop expanding ("pct", "40") into a bare ("num", "40") in ground.facts.expand
    kind: fixable
    """
    text = "About 40% of the colonists supported the Revolution by 1776, historians estimate."
    drop(text, "How many colonists supported the Revolution by 1776?",
         ["40 colonists", "10 colonists", "90 colonists"], code="unsupported_fact")


def test_rt43_negative_number_loses_its_sign():
    """RT43: the quote says -40 degrees (also with the U+2212 minus), the key says 40 degrees
    severity: high
    fix: capture a leading minus (hyphen, U+2212) that is not a range hyphen as part of the number fact
    kind: fixable
    """
    for minus in ("-", "\u2212"):
        text = f"Overnight the temperature in the valley dropped to {minus}40 degrees on the coldest night."
        drop(text, "How cold did the valley get on the coldest night?",
             ["40 degrees", "10 degrees", "25 degrees"], code="unsupported_fact")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="HOLE: a count written 1,863 supports the same digits used as a year")
def test_rt44_count_read_as_a_year():
    """RT44: the quote says 1,863 soldiers, the key is the year 1863
    severity: high
    fix: only partly fixable: keep comma-grouped numbers as quantities, but "1863 soldiers" still reads as a year
    kind: honest-limit
    """
    text = "About 1,863 soldiers camped near Gettysburg that summer, and Lee's army retreated."
    drop(text, "In what year did Lee's army retreat from Gettysburg?", ["1863", "1790", "1920"],
         code="unsupported_fact")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="HOLE: the key can be the wrong fact from the right sentence")
def test_rt45_role_swap_inside_one_sentence():
    """RT45: the quote gives the start and the end year, the question asks for the end, the key is the start
    severity: high
    fix: cannot be closed by token checks, it needs a model of which fact answers the question
    kind: honest-limit
    """
    text = "The war began in 1861 and ended in 1865, after four years of fighting."
    drop(text, "When did the war end?", ["1861", "1790", "1920"], code="unsupported_fact")


def test_rt46_quantifier_swapped_from_some_to_all():
    """RT46: the quote says SOME colonists objected, the key says ALL colonists
    severity: high
    fix: compare a small quantifier lexicon (all, every, none, no, only, always, never, some, most) between key and quote
    kind: fixable
    """
    text = "Some colonists objected to the Stamp Act of 1765 in public meetings."
    ok(text, "Which colonists objected to the Stamp Act of 1765?",
       ["Some colonists", "Only merchants", "Mostly soldiers"])
    drop(text, "Which colonists objected to the Stamp Act of 1765?",
         ["All colonists", "Only merchants", "Mostly soldiers"], code="unsupported_fact")


def test_rt47_first_name_that_is_also_a_stop_word():
    """RT47: the quote says Rogers, the key says "Will Rogers"; "Will" is skipped as a sentence-initial stop word
    severity: high
    fix: do not skip a stop word that is directly followed by another capitalized word; treat the pair as a name
    kind: fixable
    """
    text = "Rogers founded the colony of New Haven in 1638 after leaving Boston."
    prompt = "Who founded the colony in 1638?"
    ok(text, prompt, ["Rogers", "Anne Hutchinson", "Roger Williams"])
    drop(text, prompt, ["Will Rogers", "Anne Hutchinson", "Roger Williams"], code="unsupported_fact")


def test_rt48_key_with_invented_content_words():
    """RT48: key "Enslaved people everywhere in the nation" shares "enslaved people" with a quote about the rebel states
    severity: high
    fix: for the correct option, require every content word to appear in the quote (stop words aside)
    kind: fixable
    """
    text = "The Emancipation Proclamation freed enslaved people in the rebel states in 1863."
    prompt = "Whom did the Emancipation Proclamation free in 1863?"
    ok(text, prompt, ["Enslaved people in the rebel states", "Soldiers in the Union army",
                      "Merchants in the northern ports"])
    drop(text, prompt, ["Enslaved people everywhere in the nation", "Soldiers in the Union army",
                        "Merchants in the northern ports"], code="unsupported_fact")

