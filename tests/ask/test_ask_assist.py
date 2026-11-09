"""Ask assists: why-questions, writing help, other languages, premise note, mashup split, location labels.
Every case also proves the gate is unchanged: a motive the text does not state is still dropped. FakeLLM only."""

import sys
from pathlib import Path

import pytest

from codecoach.ask import assist as A
from codecoach.ask import retrieve as R
from codecoach.core.models import Course, Doc
from codecoach.core.passages import split_document

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "surface"))
from surface_helpers import Clock, FakeLLM, new_store  # noqa: E402

from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

CID = "c"
STRUNK = (ROOT / "tests" / "fixtures" / "elements-of-style-1918-rules.md").read_text()
APOLOGY = (
    "# Apology\n\n"
    "Socrates said: Men of Athens, I will obey the god rather than you, and while I have breath I shall not cease from "
    "philosophy. The god commanded me to do this, by oracles and dreams, in every way in which divine power "
    "ever commanded man to do anything.\n\n"
    "Wealth does not bring goodness, but goodness brings wealth and every other good of man.\n"
)
EARTH = (
    "# Plates\n\n"
    "Divergent boundaries are where two plates move apart and new crust forms between them.\n\n"
    "Convergent boundaries are where two plates move toward each other and one sinks below the other.\n\n"
    "Transform boundaries are where two plates slide past each other and crust is neither made nor destroyed.\n"
)
PHOTO = (
    "# Photosynthesis\n\n"
    "Plants do not release carbon dioxide during photosynthesis; they release oxygen. The light reactions make ATP.\n"
)


def build(text, llm, kind="markdown"):
    store = new_store()
    store.put_course(Course(id=CID, title="T", mode="strict"))
    store.put_doc(Doc(id="d", course_id=CID, name="d.md", kind=kind))
    store.put_passages(split_document("d", text, kind))
    return CodeCoachService(store, llm, ServiceConfig(), now_fn=Clock(), background=False, auto_bank=False), store


def ask(svc, q):
    out = svc.ask_start(CID, q)
    return svc.ask_job(CID, out["job_id"])["result"]


def pid(store, needle):
    return next(p.id for p in store.list_passages(course_id=CID) if needle in p.text)


def rep(*sentences):
    return {"answerable": True, "sentences": [
        {"text": t, "cites": [{"passage_id": p, "quote": q} for p, q in c]} for t, c in sentences]}


# ------------------------------------------------------------------ 1. why-questions

def test_why_question_clears_retrieval_on_one_shared_word():
    svc, store = build(APOLOGY, FakeLLM([]))
    idx = R.AskIndex(store.list_passages(course_id=CID))
    _, cleared = R.retrieve(idx, "Why does Socrates refuse to stop philosophizing?")
    assert cleared
    _, cleared = R.retrieve(idx, "Who was Socrates's horse called?")
    assert not cleared                      # a non-why question still needs two shared words


def test_why_answer_quoting_the_stated_reason_is_kept():
    q = "The god commanded me to do this, by oracles and dreams"
    svc, store = build(APOLOGY, FakeLLM(lambda p, t: rep(("The god commanded this.", [(pid(store, "commanded"), q)]))))
    res = ask(svc, "Why does Socrates refuse to stop philosophizing?")
    assert res["status"] == "answered" and res["removed"] == []
    assert res["sentences"][0]["cites"][0]["quote"] == q


def test_why_answer_with_an_invented_motive_is_still_dropped():
    svc, store = build(APOLOGY, FakeLLM(lambda p, t: rep(
        ("Socrates refuses because he fears poverty.", [(pid(store, "commanded"), "I shall not cease from philosophy")]))))
    res = ask(svc, "Why does Socrates refuse to stop philosophizing?")
    assert res["status"] == "not_covered" and res["sentences"] == []
    assert res["removed"] and "poverty" in res["removed"][0]["text"]


def test_why_prompt_tells_the_model_to_use_only_a_stated_reason():
    from codecoach.ask.engine import INSTRUCTIONS
    assert "never supply a motive yourself" in INSTRUCTIONS


def test_long_passage_is_shown_around_the_question_words_not_just_its_start():
    from codecoach.ask.engine import _window
    text = "Filler sentence number one is here. " * 80 + "Victor rushed out of the room in horror. " + "More filler goes here. " * 80
    shown = _window(text, {"victor", "rush"}, 600)
    assert "Victor rushed out" in shown and len(shown) < 700


# ------------------------------------------------------------------ 2. writing help

@pytest.mark.parametrize("q", [
    "Is this sentence correct according to the guide: Its a nice day, and the dog wagged it's tail",
    "How do I fix this sentence: The boy, who was tired went home",
    "Rewrite my sentence: Being a student, the book was heavy",
])
def test_pasted_sentence_gets_the_fixed_line_and_only_verbatim_guide_quotes(q):
    llm = FakeLLM([])
    svc, store = build(STRUNK, llm)
    res = ask(svc, q)
    assert res["status"] == "answered" and res["kind"] == "writing_help"
    assert res["notes"] == [A.WRITING_LINE] and res["model_calls"] == 0
    assert llm.calls == [] if hasattr(llm, "calls") else True
    assert res["sentences"], "no rule passage found"
    for s in res["sentences"]:
        c = s["cites"][0]
        assert store.get_passage(c["passage_id"]).text[c["quote_start"]:c["quote_end"]] == c["quote"] == s["text"] or c["quote"] in s["text"]
    blob = " ".join(s["text"] for s in res["sentences"]).lower()
    assert "your sentence is" not in blob and "correct:" not in blob


def test_writing_help_never_repeats_the_students_sentence_as_a_claim():
    svc, store = build(STRUNK, FakeLLM([]))
    res = ask(svc, "Rewrite my sentence: Being a student, the book was heavy")
    assert all("the book was heavy" not in s["text"].lower() for s in res["sentences"])


def test_ordinary_question_is_not_taken_for_a_pasted_sentence():
    assert A.pasted_text("What does the guide say about the possessive singular?") == ""
    assert A.pasted_text("Why did Victor flee from his creature?") == ""


# ------------------------------------------------------------------ 3. language note

def test_spanish_question_over_english_quotes_gets_one_note():
    svc, store = build(EARTH, FakeLLM(lambda p, t: rep(
        ("Transform boundaries are where two plates slide past each other.",
         [(pid(store, "Transform"), "Transform boundaries are where two plates slide past each other")])))
    )
    res = ask(svc, "¿Qué son los transform boundaries entre las plates?")
    assert res["status"] == "answered"
    assert A.LANGUAGE_NOTE in res["notes"]


def test_english_question_gets_no_language_note():
    svc, store = build(EARTH, FakeLLM(lambda p, t: rep(
        ("Transform boundaries are where two plates slide past each other.",
         [(pid(store, "Transform"), "Transform boundaries are where two plates slide past each other")])))
    )
    res = ask(svc, "What are transform boundaries between the plates?")
    assert A.LANGUAGE_NOTE not in res.get("notes", [])


def test_detect_language():
    assert A.detect_language("¿Cómo se enseñaba el español hace años?") == "es"
    assert A.detect_language("What is the theory of plate tectonics?") == "en"
    assert A.detect_language("光合作用是什么") == "cjk"


# ------------------------------------------------------------------ 4. premise note

def test_premise_note_when_a_kept_quote_denies_the_question_s_own_words():
    q = "Plants do not release carbon dioxide during photosynthesis"
    svc, store = build(PHOTO, FakeLLM(lambda p, t: rep(
        ("Plants do not release carbon dioxide during photosynthesis.", [(pid(store, "Plants"), q)]))))
    res = ask(svc, "Why do plants release carbon dioxide during photosynthesis?")
    assert res["status"] == "answered"
    assert A.PREMISE_NOTE in res["notes"]
    assert res["sentences"][0]["cites"][0]["quote"] == q          # the note is backed by a quote that says the opposite


def test_premise_note_when_gate_removed_a_sentence_that_agreed_with_the_question():
    q = "Plants do not release carbon dioxide during photosynthesis"
    svc, store = build(PHOTO, FakeLLM(lambda p, t: rep(
        ("Plants release carbon dioxide during photosynthesis.", [(pid(store, "Plants"), q)]),
        ("They release oxygen.", [(pid(store, "Plants"), "during photosynthesis; they release oxygen")]))))
    res = ask(svc, "Do plants release carbon dioxide during photosynthesis?")
    assert res["removed"], res
    assert A.PREMISE_NOTE in res.get("notes", [])


def test_no_premise_note_on_a_plain_correct_question():
    svc, store = build(PHOTO, FakeLLM(lambda p, t: rep(
        ("Plants release oxygen during photosynthesis.", [(pid(store, "Plants"), "they release oxygen")]))))
    res = ask(svc, "What do plants release during photosynthesis?")
    assert A.PREMISE_NOTE not in res.get("notes", [])


# ------------------------------------------------------------------ 5. mashup

def test_sentence_stitched_from_three_passages_becomes_one_sentence_per_quote():
    qs = {k: f"{k} boundaries are where two plates " + v for k, v in
          {"Divergent": "move apart", "Convergent": "move toward each other", "Transform": "slide past each other"}.items()}
    mash = "Divergent boundaries move apart, convergent boundaries move toward each other, and transform boundaries slide past each other."
    svc, store = build(EARTH, FakeLLM(lambda p, t: rep((mash, [(pid(store, k), q) for k, q in qs.items()]))))
    res = ask(svc, "What are the three types of plate boundaries?")
    assert res["status"] == "answered" and res["removed"] == []
    assert len(res["sentences"]) == 3
    for s in res["sentences"]:
        assert len(s["cites"]) == 1
        assert s["cites"][0]["quote"] in s["text"] or s["text"].lower().split()[0] in s["cites"][0]["quote"].lower()


# ------------------------------------------------------------------ 6. location labels

def test_copied_chapter_label_is_stripped_from_a_quote_before_the_check():
    text = "Transform boundaries are where two plates slide past each other and crust is neither made nor destroyed."
    svc, store = build(EARTH, FakeLLM(lambda p, t: rep(
        ("Transform boundaries are where two plates slide past each other.",
         [(pid(store, "Transform"), "(Ch. 5, para 56) Transform boundaries are where two plates slide past each other")]))))
    res = ask(svc, "What are transform boundaries?")
    assert res["status"] == "answered" and res["removed"] == []
    assert "para 56" not in res["sentences"][0]["cites"][0]["quote"]


def test_a_label_inside_real_source_text_is_left_alone():
    p = split_document("d", "He wrote (Ch. 5, para 56) in the margin of his book every night.", "text")[0]
    assert A.strip_location_label("wrote (Ch. 5, para 56) in the margin", p) == "wrote (Ch. 5, para 56) in the margin"


# ------------------------------------------------------------------ 2b. a fact check is not writing help
# Orchestrator probe 2026-11-06: "Is this correct: <claim>" on a history course was routed to writing help (or refused)
# without asking the model, so the student never got the answer or the premise note.

@pytest.mark.parametrize("q", [
    "Is this correct: the golden spike was made of silver",
    "Is this right: the golden spike was driven in 1869",
    "Can you check this: Leland Stanford swung the maul",
    "Check my answer: the spike was driven at Promontory Summit",
    "Is this true: plants release carbon dioxide during photosynthesis",
])
def test_fact_check_question_goes_to_the_normal_ask_path(q):
    assert A.pasted_text(q) == ""


@pytest.mark.parametrize("q", [
    "Is this sentence correct according to the guide: Its a nice day, and the dog wagged it's tail",
    "How do I fix this sentence: The boy, who was tired went home",
    "Rewrite my sentence: Being a student, the book was heavy",
    "Proofread my paragraph: Their going to the store, its very far",
    'Is "Its a nice day for a walk" grammatical?',
])
def test_writing_question_is_still_taken_for_a_pasted_sentence(q):
    assert A.pasted_text(q) != ""


def test_students_own_words_never_route_a_question_to_writing_help():
    # "fix" and "sentence" here are the student's claim, not a request about writing
    assert A.pasted_text("Is this correct: the sentence was fixed by the judge in 1790") == ""
