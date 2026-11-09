"""Fair fill-in-the-blank grading (codecoach/check/fair.py). Anthony: "if they are wrong it should be outright wrong,
but this makes you do word for word". Right answers in another fair form pass; wrong answers stay wrong."""

import pytest

from codecoach.check.fair import Corpus, fair_cloze
from codecoach.check.reading import check_cloze
from codecoach.core.models import ClozePayload

TEXT = [
    "Central Pacific President Leland Stanford and Union Pacific Vice-President Thomas Durant met at Promontory "
    "Summit, the junction where the two companies joined their tracks.",
    "The Central Pacific Railroad (CPRR) and later the Union Pacific Railroad (UPRR) built the line.",
    "Chinese-led CPRR crews entered Utah Territory in March, 1869.",
    "Using $400 of his own gold, he had the William T. Garratt Foundry of San Francisco cast a golden spike.",
    "The spike is on display with the Golden Spike in the Stanford University museum.",
    "The ceremony was emceed by wealthy Sacramento banker, Edgar Mills. General Grenville Dodge spoke.",
    "Durant had a headache, most likely a hangover from the previous night's party in Ogden.",
    "Governor Stanford took a mighty swing at the spike, and struck the tie instead.",
    "Workers brought the laurel wood tie from Stanford's coach. Most workers were Chinese immigrants.",
    "The spike was made of 17.6 carat gold and finished on May 10, 1869.",
]
CORPUS = Corpus.build(TEXT)


class It:
    def __init__(self, answer, aliases=()):
        self.payload = ClozePayload(text_with_blank="x ____ y", answer=answer, aliases=list(aliases))


def graded(answer, response):
    if check_cloze(It(answer), response).ok:
        return True
    r = fair_cloze([answer], response, CORPUS)
    return bool(r and r.ok)


@pytest.mark.parametrize("answer,response", [
    ("Promontory Summit", "promontory summit"),
    ("Promontory Summit", "PROMONTORY SUMMIT"),
    ("Promontory Summit", "at Promontory Summit"),
    ("Promontory Summit", "Promontory Sumit"),                    # one typo in a long word
    ("Union Pacific", "union pacific railroad"),                   # the materials say Union Pacific Railroad
    ("Union Pacific", "the Union Pacific Railroad"),
    ("William T. Garratt Foundry of San Francisco", "William T. Garratt Foundry"),
    ("William T. Garratt Foundry of San Francisco", "william t garratt foundry of san francisco"),
    ("William T. Garratt Foundry of San Francisco", "Garratt Foundry"),
    ("William T. Garratt Foundry of San Francisco", "the Garratt foundry"),
    ("Leland Stanford", "Stanford"),                               # "Stanford University" is not a rival Stanford
    ("Leland Stanford", "Leland Standford"),
    ("Thomas Durant", "Durant"),
    ("Grenville Dodge", "Dodge"),
    ("Edgar Mills", "Mills"),
    ("Edgar Mills", "Edgar Mill"),
    ("May 10, 1869", "May 10 1869"),
    ("Chinese immigrants", "Chinese immigrant"),
    ("gcc", "GCC"),
    ("the transcontinental railroad", "Transcontinental Railroad"),
    ("laurel wood tie", "laurelwood tie"),
    ("laurel wood tie", "laurel-wood tie"),
    ("$400", "400"),
])
def test_a_right_answer_in_another_fair_form_passes(answer, response):
    assert graded(answer, response)


@pytest.mark.parametrize("answer,response", [
    ("Promontory Summit", "Promontory"),                           # half a place name
    ("Promontory Summit", "Ogden"),
    ("Promontory Summit", "Promontory Summit or Ogden"),           # a guess list
    ("Promontory Summit", "not Promontory Summit"),
    ("Promontory Summit", "Promontory Summit Ogden"),              # Ogden is a different place in the text
    ("Promontory Summit", "Promontory Summit, Utah"),              # the text never puts Utah with it
    ("Promontory Summit", "Promontory Summit in 1869"),            # an added number is a claim
    ("Union Pacific", "Pacific"),                                  # the text also says Central Pacific
    ("Union Pacific", "Central Pacific"),
    ("Central Pacific", "Pacific"),
    ("William T. Garratt Foundry of San Francisco", "a foundry in San Francisco"),   # no name
    ("William T. Garratt Foundry of San Francisco", "450"),
    ("Leland Stanford", "Leland"),                                 # a first name is not the person
    ("Leland Stanford", "Thomas Durant"),
    ("Leland Stanford", "stanford leland"),
    ("Leland Stanford", "Governor Stanford"),                      # adds a word the answer does not have
    ("May 10, 1869", "May 11, 1869"),                              # numbers never bend
    ("May 10, 1869", "1869"),
    ("Chinese immigrants", "chinese"),
    ("Chinese immigrants", "immigrants"),                         # the dropped word is the point
    ("Chinese immigrants", "immigrants from China"),
    ("17.6 carat gold", "17 carat gold"),
    ("17.6 carat gold", "17.6 carat"),
    ("telegraph", "telegram"),
    ("silver", "silver and gold"),
    ("está", "esta"),                                              # an accent is not a typo
])
def test_a_wrong_or_incomplete_answer_stays_wrong(answer, response):
    assert not graded(answer, response)


def test_the_notes_say_what_was_accepted_and_why():
    assert fair_cloze(["Promontory Summit"], "Promontory Sumit", CORPUS).detail == \
        "Correct. Check the spelling: Promontory Summit."
    assert fair_cloze(["Leland Stanford"], "Stanford", CORPUS).detail == "Correct. The full answer: Leland Stanford."
    close = fair_cloze(["Promontory Summit"], "Promontory Summit, Utah", CORPUS)
    assert (close.ok, close.score) == (False, 0.5)
    assert "\u201cUtah\u201d" in close.detail and "Promontory Summit" in close.detail


def test_extra_words_the_materials_never_use_pass_with_a_note():
    r = fair_cloze(["Promontory Summit"], "Promontory Summit, Nevada", CORPUS)
    assert r.ok and "Nevada" in r.detail and "wasn't checked" in r.detail
    r = fair_cloze(["May 10, 1869"], "May 10, 1869 at noon", CORPUS)       # the text never says noon
    assert r.ok and "noon" in r.detail and "wasn't checked" in r.detail


def test_without_materials_only_the_same_words_pass():
    assert fair_cloze(["Leland Stanford"], "leland  stanford.", None).ok
    assert fair_cloze(["Leland Stanford"], "Stanford", None) is None
    assert fair_cloze(["Promontory Summit"], "at Promontory Summit, Utah", None) is None


def test_a_typo_that_is_another_word_in_the_materials_is_not_a_typo():
    corpus = Corpus.build(["Durant met Durand at the station."])
    assert fair_cloze(["Durant"], "Durand", corpus) is None
    assert fair_cloze(["Promontory"], "Promontary", corpus).ok


def test_the_capital_a_of_a_name_still_matters():
    r = fair_cloze(["A minor"], "minor", CORPUS)
    assert r is None or not r.ok                                           # never "Correct"
    assert fair_cloze(["A minor"], "a  minor", CORPUS).ok


def test_a_shortened_description_or_ambiguous_name_is_almost_not_correct():
    for answer, response in [("Chinese immigrants", "immigrants"), ("Union Pacific", "Pacific")]:
        r = fair_cloze([answer], response, CORPUS)
        assert (r.ok, r.score) == (False, 0.5) and answer in r.detail and r.detail.startswith("Almost")
