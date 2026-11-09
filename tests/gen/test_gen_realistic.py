"""Over-strictness check (A2c): a scripted, realistic run on demo/materials/history.md.

A fake LLM returns drafts built from REAL sentences of the demo history text. GOOD drafts are honest
questions about those sentences (mc, cloze, short, order, match). BAD drafts are the same kind of
question with an invented date or name, or a changed word. Both sets go through `generate_one`, the
same path the bank builder uses. The gate must keep at least 90 percent of the good set (false-drop
rate <= 10 percent) and drop every bad draft.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from codecoach.core import registry
from codecoach.core.llm import FakeLLM
from codecoach.core.models import Objective, Passage
from codecoach.gen import generate_one

HISTORY = Path(__file__).resolve().parents[2] / "demo" / "materials" / "history.md"
MAX_FALSE_DROP_RATE = 0.10


def _passages() -> list[Passage]:
    paras = [b.strip() for b in HISTORY.read_text(encoding="utf-8").split("\n\n")]
    paras = [b for b in paras if b and not b.startswith("#")]
    return [Passage(id=f"hist:p{i}:1", doc_id="hist", location=f"history, paragraph {i}", text=t)
            for i, t in enumerate(paras, 1)]


PASSAGES = _passages()
_SENT = re.compile(r"(?<=[.!?])[\"')\]\u201d]*\s+(?=[\"'(\[\u201c]?[A-Z0-9])")


_ABBREV = re.compile(r"(?:\b(?:Mr|Mrs|Dr|Gov|St|Co|vs)|\b[A-Z]|\bU\.S|\d)\.$")


def _split(text: str) -> list[str]:
    """Sentences of a paragraph; a period after an initial or Mr./Dr. does not end one."""
    out: list[str] = []
    for frag in _SENT.split(text):
        if out and _ABBREV.search(out[-1]):
            out[-1] += " " + frag
        else:
            out.append(frag)
    return out


def S(snippet: str) -> tuple[str, str]:
    """(passage_id, the one real sentence of history.md that contains `snippet`)."""
    hits = [(p.id, s) for p in PASSAGES for s in _split(p.text) if snippet in s]
    assert len(hits) == 1, f"{snippet!r} matches {len(hits)} sentences"
    return hits[0]


def cite(*snippets: str) -> list[dict]:
    out = []
    for sn in snippets:
        pid, sent = S(sn)
        out.append({"passage_id": pid, "quote": sent})
    return out


def OBJ(statement: str) -> Objective:
    return Objective(id="o1", unit="u", lesson="l", verb="identify", statement=statement,
                     passage_ids=[p.id for p in PASSAGES])


# ------------------------------------------------------------------ the good set
# (label, format, draft). Every sentence is copied from history.md; every distractor is wrong.

GOOD: list[tuple[str, str, dict]] = []
BAD: list[tuple[str, str, dict]] = []


def good(label, fmt, draft):
    GOOD.append((label, fmt, draft))


def bad(label, fmt, draft):
    BAD.append((label, fmt, draft))


def mcd(prompt, options, correct, *snippets):
    return {"prompt": prompt, "options": options, "correct_index": correct, "citations": cite(*snippets)}


def clz(text, answer, *snippets, aliases=()):
    return {"prompt": "Fill in the blank.", "text_with_blank": text, "answer": answer,
            "aliases": list(aliases), "citations": cite(*snippets)}


def sh(prompt, terms, *snippets, min_terms=None):
    return {"prompt": prompt, "required_terms": terms, "min_terms": min_terms, "citations": cite(*snippets)}


def od(prompt, events):
    """events: [(label, date_text, snippet)] earliest first."""
    ev, cs = [], []
    for label, date, sn in events:
        pid, sent = S(sn)
        ev.append({"label": label, "date_text": date, "passage_id": pid})
        cs.append({"passage_id": pid, "quote": sent})
    return {"prompt": prompt, "events": ev, "citations": cs}


def mt(prompt, pairs):
    """pairs: [(left, right, snippet)]"""
    ps, cs = [], []
    for left, right, sn in pairs:
        pid, sent = S(sn)
        ps.append({"left": left, "right": right, "passage_id": pid})
        cs.append({"passage_id": pid, "quote": sent})
    return {"prompt": prompt, "pairs": ps, "citations": cs}


# --- multiple choice (14)
good("mc spike carat", "mc", mcd("How many carats was the Golden Spike made of?", ["17.6", "24", "14.03", "22"], 0,
                                 "17.6 carat gold"))
good("mc spike weight", "mc", mcd("How much did the Golden Spike weigh?", ["14.03 ounces", "10 1/2 ounces", "25 ounces"], 0,
                                  "weighed 14.03 ounces"))
good("mc foundry", "mc", mcd("Which foundry cast the Golden Spike for David Hewes?",
                             ["William T. Garratt Foundry", "Dowling Blacksmith Shop", "Strahle & Hughes"], 0,
                             "Garratt Foundry of San Francisco cast"))
good("mc nevada forged", "mc", mcd("How was Nevada's silver spike made, unlike the Golden Spike?",
                                   ["It was forged", "It was molded", "It was carved"], 0, "was forged instead of cast"))
good("mc tritle", "mc", mcd("Who ordered the silver spike for Nevada?", ["F.A. Tritle", "Anson P.K. Safford", "Frederick Marriott"], 0,
                            "Mr. F.A. Tritle"))
good("mc safford", "mc", mcd("Who was the newly appointed governor of the Arizona Territory who had the spike made?",
                             ["Anson P.K. Safford", "Edgar Mills", "Thomas Durant"], 0, "Anson P.K. Safford, had the spike made"))
good("mc marriott", "mc", mcd("Who ordered a second golden spike?", ["Frederick Marriott", "David Hewes", "Leland Stanford"], 0,
                              "A second golden spike was ordered by"))
good("mc emcee", "mc", mcd("Who emceed the Golden Spike Ceremony?", ["Edgar Mills", "John Todd", "Grenville Dodge"], 0,
                           "emceed by wealthy Sacramento banker"))
good("mc invocation", "mc", mcd("Who offered the invocation at the ceremony?",
                                ["Reverend Dr. John Todd", "Dr. H.W. Harkness", "Edgar Mills"], 0, "An invocation was offered by"))
good("mc act", "mc", mcd("Which act authorized the Central Pacific Railroad and later the Union Pacific Railroad?",
                         ["The Railway Act of 1862", "The Chinese Exclusion Act", "The Stamp Act"], 0,
                         "The Railway Act of 1862 authorized"))
good("mc survey", "mc", mcd("How many potential transcontinental railroad routes did the Army survey?",
                            ["five", "three", "seven"], 0, "survey five potential transcontinental railroad routes"))
good("mc chinese workers", "mc", mcd("How many Chinese individuals were employed in the construction of the Pacific Railroad by 1868?",
                                     ["11,000", "50", "12,000", "30"], 0, "some 11,000 Chinese individuals"))
good("mc track record", "mc", mcd("How much track did the crews lay in 12 hours on April 28, 1869?",
                                  ["10 miles and 56 feet", "three miles", "31 miles"], 0, "helped lay 10 miles and 56 feet of track"))
good("mc exclusion", "mc", mcd("What law passed in 1882 caused many railroad jobs to go to other immigrant communities?",
                               ["The Chinese Exclusion Act", "The Railway Act", "The Homestead Act"], 0,
                               "after the passage of the 1882 Chinese Exclusion Act"))

# --- cloze (8)
good("cloze forged", "cloze", clz("Unlike the Golden Spike, Nevada\u2019s silver spike was ____ instead of cast.", "forged",
                                  "was forged instead of cast"))
good("cloze carat", "cloze", clz("The spike was 5 5/8 inches long, weighed 14.03 ounces and was made of ____ carat gold.", "17.6",
                                 "17.6 carat gold"))
good("cloze maul", "cloze", clz("L.W. Coe, president of San Francisco\u2019s Pacific Express Company, had tool maker Conroy & "
                                "O\u2019Connor manufacture a special spike hammer, or ____, for the Golden Spike Ceremony.", "maul",
                                "or maul, for the Golden Spike Ceremony"))
good("cloze emcee", "cloze", clz("The ceremony then commenced, emceed by wealthy Sacramento banker, ____.", "Edgar Mills",
                                 "emceed by wealthy Sacramento banker"))
good("cloze acts", "cloze", clz("The Railway Act of ____ authorized the Central Pacific Railroad (CPRR) and later the Union Pacific "
                                "Railroad (UPRR) to build the western and eastern portions, respectively, of the chosen route.",
                                "1862", "The Railway Act of 1862 authorized"))
good("cloze dugouts", "cloze", clz("Today, shallow depressions survive as traces of this housing, called ____.", "dugouts",
                                   "shallow depressions survive"))
good("cloze groups", "cloze", clz("Instead, they were organized into groups of up to ____ men, each of which had a leader "
                                  "responsible for receiving and distributing their wages.", "30", "organized into groups of up to 30"))
good("cloze stanford speech", "cloze", clz("Stanford then offered a rousing ____.", "speech", "Stanford then offered a rousing speech"))

# --- short answer (6)
good("short spike", "short", sh("Describe the Golden Spike.", ["5 5/8 inches long", "14.03 ounces", "17.6 carat gold"],
                                "weighed 14.03 ounces", min_terms=2))
good("short silver", "short", sh("How was Nevada's silver spike different from the Golden Spike?", ["forged"],
                                 "was forged instead of cast"))
good("short hewes", "short", sh("What did David Hewes do with his own gold?", ["$400", "golden spike"],
                                "Using $400 of his own gold"))
good("short laborers", "short", sh("How did the CPRR organize its Chinese workers?",
                                   ["groups of up to 30 men", "a leader"], "organized into groups of up to 30"))
good("short sources", "short", sh("What do archeologists have to work from for the Chinese workers?",
                                  ["artifacts"], "Through archeological analyses of the artifacts"))
good("short census", "short", sh("What did the 1880 Census report about Terrace?", ["54 Chinese individuals"],
                                 "the 1880 Census, which reported 54 Chinese individuals"))

# --- ordering (5)
good("order 1", "order", od("Put these events in the order they happened.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated"),
    ("The San Francisco earthquake destroyed the News Letter building", "1906",
     "the San Francisco earthquake and subsequent fire destroyed the News Letter building")]))
good("order 2", "order", od("Order these events from earliest to latest.", [
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated"),
    ("The San Francisco earthquake destroyed the News Letter building", "1906",
     "the San Francisco earthquake and subsequent fire destroyed the News Letter building")]))
good("order 3", "order", od("Order these events from earliest to latest.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("The San Francisco earthquake destroyed the News Letter building", "1906",
     "the San Francisco earthquake and subsequent fire destroyed the News Letter building")]))
good("order 4", "order", od("Order these events from earliest to latest.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated"),
    ("The San Francisco earthquake destroyed the News Letter building", "1906",
     "the San Francisco earthquake and subsequent fire destroyed the News Letter building")]))
good("order 5", "order", od("Order these events from earliest to latest.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated")]))

# --- matching (5)
good("match people", "match", mt("Match each person to the description.", [
    ("Edgar Mills", "wealthy Sacramento banker", "emceed by wealthy Sacramento banker"),
    ("Reverend Dr. John Todd", "Pittsfield, Massachusetts", "An invocation was offered by"),
    ("Frederick Marriott", "proprietor of the San Francisco News Letter", "A second golden spike was ordered by")]))
good("match roles", "match", mt("Match each person to what he did.", [
    ("L.W. Coe", "president of San Francisco\u2019s Pacific Express Company", "L.W. Coe, president of San Francisco"),
    ("Anson P.K. Safford", "newly appointed governor", "Anson P.K. Safford, had the spike made"),
    ("Frederick Marriott", "proprietor of the San Francisco News Letter", "A second golden spike was ordered by")]))
good("match spike facts", "match", mt("Match each spike to its detail.", [
    ("Nevada\u2019s silver spike", "forged instead of cast", "was forged instead of cast"),
    ("The spike", "17.6 carat gold", "17.6 carat gold")]))
good("match places", "match", mt("Match each person to the place.", [
    ("Reverend Dr. John Todd", "Pittsfield, Massachusetts", "An invocation was offered by"),
    ("Edgar Mills", "Sacramento banker", "emceed by wealthy Sacramento banker")]))
good("match acts", "match", mt("Match each law to what it did.", [
    ("The Railway Act of 1862", "authorized the Central Pacific Railroad", "The Railway Act of 1862 authorized"),
    ("1882 Chinese Exclusion Act", "many railroad jobs trickled into other immigrant communities",
     "after the passage of the 1882 Chinese Exclusion Act")]))


# ------------------------------------------------------------------ the bad set
# Same shapes, but a fact is invented or a word is changed.

# invented / changed numbers and dates
bad("mc wrong carat key", "mc", mcd("How many carats was the Golden Spike made of?", ["24", "17.6", "14.03"], 0, "17.6 carat gold"))
bad("mc invented year", "mc", mcd("In what year did Hewes donate his art collection?", ["1895", "1892", "1906"], 0, "In 1892, Hewes donated"))
bad("mc invented count", "mc", mcd("How many Chinese individuals were employed by 1868?", ["12,500", "11,000", "50"], 0,
                                   "some 11,000 Chinese individuals"))
bad("mc wrong survey", "mc", mcd("How many routes did the Army survey?", ["seven", "five", "three"], 0,
                                 "survey five potential transcontinental railroad routes"))
bad("mc false prompt year", "mc", mcd("Which act of 1865 authorized the Central Pacific Railroad?",
                                      ["The Railway Act", "The Stamp Act", "The Tea Act"], 0, "The Railway Act of 1862 authorized"))
bad("mc two answers", "mc", mcd("How much did the Golden Spike weigh?", ["14.03 ounces", "14.03 ounces exactly", "10 1/2 ounces"], 0,
                                "weighed 14.03 ounces"))
# invented / swapped names
bad("mc invented name", "mc", mcd("Who emceed the Golden Spike Ceremony?", ["Henry Clay", "Edgar Mills", "John Todd"], 0,
                                  "emceed by wealthy Sacramento banker"))
bad("mc wrong person", "mc", mcd("Who ordered the silver spike for Nevada?", ["Anson P.K. Safford", "F.A. Tritle", "Frederick Marriott"],
                                 0, "Mr. F.A. Tritle"))
bad("mc invented foundry", "mc", mcd("Which foundry cast the Golden Spike for David Hewes?",
                                     ["Carnegie Steel Works", "William T. Garratt Foundry", "Dowling Blacksmith Shop"], 0,
                                     "Garratt Foundry of San Francisco cast"))
bad("mc changed word", "mc", mcd("How was Nevada's silver spike made, unlike the Golden Spike?", ["It was cast", "It was forged", "It was molded"],
                                 0, "was forged instead of cast"))
bad("mc not in quote", "mc", mcd("Who ordered the silver spike for Nevada?", ["Thomas Durant", "F.A. Tritle", "Anson P.K. Safford"], 0,
                                 "Mr. F.A. Tritle"))
bad("mc invented prompt name", "mc", mcd("Which foundry did Cornelius Vanderbilt hire to cast the Golden Spike?",
                                         ["William T. Garratt Foundry", "Dowling Blacksmith Shop", "Strahle & Hughes"], 0,
                                         "Garratt Foundry of San Francisco cast"))
# cloze
bad("cloze changed word", "cloze", clz("Unlike the Golden Spike, Nevada\u2019s silver spike was ____ instead of cast.", "melted",
                                       "was forged instead of cast"))
bad("cloze invented year", "cloze", clz("The Railway Act of ____ authorized the Central Pacific Railroad (CPRR) and later the Union "
                                        "Pacific Railroad (UPRR) to build the western and eastern portions, respectively, of the "
                                        "chosen route.", "1865", "The Railway Act of 1862 authorized"))
bad("cloze inserted word", "cloze", clz("Stanford then offered a very rousing ____.", "speech", "Stanford then offered a rousing speech"))
bad("cloze invented name", "cloze", clz("The ceremony then commenced, emceed by wealthy Sacramento banker, ____.", "Henry Mills",
                                        "emceed by wealthy Sacramento banker"))
bad("cloze alias off", "cloze", clz("Today, shallow depressions survive as traces of this housing, called ____.", "dugouts",
                                    "shallow depressions survive", aliases=["tents"]))
bad("cloze answer repeated", "cloze", clz("Nevada\u2019s ____ spike was forged instead of cast, and the silver spike was rough.", "silver",
                                          "was forged instead of cast"))
# short
bad("short invented term", "short", sh("Describe the Golden Spike.", ["24 carat gold", "14.03 ounces"], "weighed 14.03 ounces"))
bad("short invented number", "short", sh("What did David Hewes do with his own gold?", ["$500", "golden spike"],
                                         "Using $400 of his own gold"))
bad("short invented name", "short", sh("Who did the maul go to?", ["Leland Stanford", "Henry Ford"],
                                       "L.W. Coe, president of San Francisco"))
bad("short negated", "short", sh("How did the Census treat West Lake and East Lake?", ["Chinese inhabitants", "counted"],
                                 "the Census ignored Chinese inhabitants"))
bad("short bad quote", "short", {"prompt": "Describe the spike.", "required_terms": ["solid gold"],
                                 "citations": [{"passage_id": PASSAGES[0].id,
                                                "quote": "The spike was made of solid gold and weighed 20 ounces."}]})
bad("short answer in prompt", "short", sh("The spike was forged, so what word describes how it was made?", ["forged"],
                                          "was forged instead of cast"))
# ordering
bad("order wrong dates", "order", od("Put these events in order.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1865", "The Railway Act of 1862 authorized"),
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("The San Francisco earthquake destroyed the News Letter building", "1906",
     "the San Francisco earthquake and subsequent fire destroyed the News Letter building")]))
bad("order swapped labels", "order", od("Put these events in order.", [
    ("Passage of the Chinese Exclusion Act", "1862", "The Railway Act of 1862 authorized"),
    ("The Railway Act authorized the Central Pacific Railroad", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated")]))
bad("order wrong sequence", "order", {**od("Put these events in order.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated"),
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act")])})
bad("order label leaks year", "order", od("Put these events in order.", [
    ("The 1862 Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("Passage of the Chinese Exclusion Act", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated")]))
bad("order invented event", "order", od("Put these events in order.", [
    ("The Railway Act authorized the Central Pacific Railroad", "1862", "The Railway Act of 1862 authorized"),
    ("The telegraph reached Salt Lake City", "1882", "after the passage of the 1882 Chinese Exclusion Act"),
    ("Hewes donated his art collection to the Stanford museum", "1892", "In 1892, Hewes donated")]))
# matching
bad("match swapped", "match", mt("Match each person to the description.", [
    ("Edgar Mills", "Pittsfield, Massachusetts", "emceed by wealthy Sacramento banker"),
    ("Reverend Dr. John Todd", "wealthy Sacramento banker", "An invocation was offered by")]))
bad("match invented right", "match", mt("Match each person to the description.", [
    ("Edgar Mills", "wealthy Sacramento banker", "emceed by wealthy Sacramento banker"),
    ("Frederick Marriott", "proprietor of the Chicago Tribune", "A second golden spike was ordered by")]))
bad("match invented left", "match", mt("Match each person to the description.", [
    ("Edgar Mills", "wealthy Sacramento banker", "emceed by wealthy Sacramento banker"),
    ("Henry Clay", "Pittsfield, Massachusetts", "An invocation was offered by")]))
bad("match cross sentence", "match", mt("Match each person to the description.", [
    ("L.W. Coe", "newly appointed governor", "L.W. Coe, president of San Francisco"),
    ("Anson P.K. Safford", "president of San Francisco\u2019s Pacific Express Company", "Anson P.K. Safford, had the spike made")]))
# structure and sourcing
bad("mc no citation", "mc", {"prompt": "Who emceed the ceremony?", "options": ["Edgar Mills", "John Todd", "Henry Clay"],
                             "correct_index": 0, "citations": [{"passage_id": PASSAGES[0].id,
                                                                "quote": "Edgar Mills emceed the ceremony in a top hat."}]})
bad("mc quote too short", "mc", {"prompt": "Who emceed the ceremony?", "options": ["Edgar Mills", "John Todd", "Henry Clay"],
                                 "correct_index": 0, "citations": [{"passage_id": S("emceed by wealthy Sacramento banker")[0],
                                                                    "quote": "Edgar Mills"}]})
bad("mc year as day", "mc", mcd("On what date was the Golden Spike Ceremony scheduled to finish the railroad?",
                                ["May 9, 1869", "May 10, 1869", "June 8, 1869"], 0, "which was scheduled to be finished on May 8, 1869"))
bad("mc number word swapped", "mc", mcd("How many men moved the final rail into position?", ["nine", "eight", "twelve"], 0,
                                        "eight Chinese men moved the final rail"))
bad("cloze negation flipped", "cloze", clz("With few exceptions, the CPRR ____ record names of individual Chinese workers.", "did",
                                           "the CPRR did not record names of individual Chinese workers"))


# ------------------------------------------------------------------ the run


def _run(fmt: str, draft: dict, seed: int = 11):
    """(kept?, reason code, detail)"""
    llm = FakeLLM(lambda purpose, text: draft)
    r = generate_one(fmt=fmt, objective=OBJ("Recall facts about the first transcontinental railroad."),
                     passages=PASSAGES, llm=llm, mode="strict", rng=random.Random(seed))
    if r.items:
        return True, "", ""
    d = r.drops[0]
    return False, d.gate_code, d.detail


@pytest.fixture(autouse=True)
def clean_registry():
    registry._clear_for_tests()
    yield
    registry._clear_for_tests()


def test_the_scripted_run_is_big_enough_and_uses_real_sentences():
    assert len(GOOD) >= 30 and len(BAD) >= 30
    assert {f for _, f, _ in GOOD} == {"mc", "cloze", "short", "order", "match"}
    text = re.sub(r"\s+", " ", HISTORY.read_text(encoding="utf-8"))
    for label, _, d in GOOD:
        for c in d["citations"]:
            assert re.sub(r"\s+", " ", c["quote"]) in text, f"{label}: not a real sentence"


def test_false_drop_rate_on_good_items_is_at_most_ten_percent():
    drops = []
    for label, fmt, d in GOOD:
        kept, code, detail = _run(fmt, d)
        if not kept:
            drops.append((label, code, detail))
    rate = len(drops) / len(GOOD)
    report = "; ".join(f"{lb} [{c}] {dt}" for lb, c, dt in drops)
    print(f"\nfalse_drop_rate = {len(drops)}/{len(GOOD)} = {rate:.1%}  {report}")
    assert rate <= MAX_FALSE_DROP_RATE, f"{len(drops)}/{len(GOOD)} good items dropped: {report}"


def test_every_bad_item_is_dropped():
    leaked = [label for label, fmt, d in BAD if _run(fmt, d)[0]]
    assert not leaked, f"bad drafts that got through: {leaked}"


def test_good_items_survive_any_shuffle():
    """The shuffle must not matter: a kept item stays kept under other seeds."""
    for label, fmt, d in GOOD:
        if _run(fmt, d, 11)[0]:
            for seed in (1, 2, 3):
                assert _run(fmt, d, seed)[0], f"{label} dropped under seed {seed}"
