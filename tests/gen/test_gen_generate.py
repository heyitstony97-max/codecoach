"""Generation, self-checks, bank building, lessons. Fake LLM and FAKE checkers only: codecoach/check is never imported."""

import json
import random
import threading
from collections import Counter

import pytest

from codecoach.core import registry
from codecoach.core.llm import FakeLLM, LLMUnavailable
from codecoach.core.models import (Course, CourseMap, Doc, Lesson, Objective, Passage, PerTestResult, Unit)
from codecoach.core.registry import CheckResult, register_checker
from codecoach.core.store import Store
from codecoach.gen import build_bank, fill_objective, generate_one, make_lesson, top_up
from codecoach.gen.bank import BankReport

TEXT = ("On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President. "
        "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states. "
        "About 1,000 soldiers camped near Gettysburg that summer, and Lee's army retreated.")
P1 = Passage(id="civil:p1:1", doc_id="civil", location="p. 1", text=TEXT)
Q = "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states."
Q2 = "On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President."
OBJ = Objective(id="o1", unit="u1", lesson="l1", verb="identify",
                statement="Identify the document that freed enslaved people in the rebel states.",
                passage_ids=["civil:p1:1"])
NOT_COVERED = Objective(id="o2", unit="u1", lesson="l1", verb="identify",
                        statement="Identify the causes of the Peloponnesian War.", coverage="not_covered")
RNG = lambda: random.Random(7)  # noqa: E731


@pytest.fixture(autouse=True)
def clean_registry():
    registry._clear_for_tests()
    yield
    registry._clear_for_tests()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "codecoach.db")
    s.put_course(Course(id="civil", title="Civil War"))
    s.put_doc(Doc(id="civil", course_id="civil", name="Civil War"))
    s.put_passages([P1])
    s.save_map(CourseMap(course_id="civil", units=[Unit(id="u1", title="U", lessons=[
        Lesson(id="l1", title="L", objectives=[OBJ, NOT_COVERED])])]))
    return s


def mc_draft(n=0, **kw):
    d = {"prompt": "Which document freed enslaved people in the rebel states?",
         "options": ["The Emancipation Proclamation", f"The Stamp Act {'x' * n}".strip(), "The Tea Act"],
         "correct_index": 0, "citations": [{"passage_id": "civil:p1:1", "quote": Q}]}
    d.update(kw)
    return d


def gen(fmt, draft, *, obj=OBJ, passages=(P1,), mode="strict", rng=None):
    llm = FakeLLM(lambda purpose, text: draft)
    r = generate_one(fmt=fmt, objective=obj, passages=list(passages), llm=llm, mode=mode, rng=rng or RNG())
    return r, llm


# ------------------------------------------------------------------ the operator's corrections


def test_checker_equals_format_for_every_kept_item():
    r, _ = gen("mc", mc_draft())
    assert r.items and r.items[0].checker == "mc"
    r, _ = gen("cloze", {"prompt": "Fill in the blank.", "text_with_blank": "In 1863, the ____ freed enslaved "
                         "people in the rebel states.", "answer": "Emancipation Proclamation",
                         "citations": [{"passage_id": "civil:p1:1", "quote": Q}]})
    assert r.items[0].checker == "cloze"


def test_mc_correct_index_is_shuffled():
    seen = Counter()
    for seed in range(50):
        r, _ = gen("mc", mc_draft(), rng=random.Random(seed))
        it = r.items[0]
        assert it.payload.options[it.payload.correct_index] == "The Emancipation Proclamation"
        seen[it.payload.correct_index] += 1
    assert len(seen) > 1, seen


def test_order_events_are_never_stored_chronologically():
    q = ["The Stamp Act of 1765 taxed printed paper.", "The Tea Act of 1773 gave a monopoly on tea.",
         "Independence was declared in 1776 by the Continental Congress."]
    p2 = Passage(id="civil:p2:1", doc_id="civil", location="p. 2", text=" ".join(q))
    o = Objective(id="o9", unit="u1", lesson="l1", verb="order", statement="Order the events.",
                  passage_ids=[p2.id])
    ev = [{"label": "Stamp Act", "date_text": "1765", "passage_id": p2.id},
          {"label": "Tea Act", "date_text": "1773", "passage_id": p2.id},
          {"label": "Independence", "date_text": "1776", "passage_id": p2.id}]
    draft = {"prompt": "Put these events in order.", "events": ev,
             "citations": [{"passage_id": p2.id, "quote": x} for x in q]}
    for seed in range(40):
        r, _ = gen("order", draft, obj=o, passages=[p2], rng=random.Random(seed))
        assert r.items, r.drops
        pl = r.items[0].payload
        assert pl.correct_order != [0, 1, 2]
        by_year = [pl.events[i].date_text for i in pl.correct_order]
        assert by_year == ["1765", "1773", "1776"]


def test_strict_mode_never_creates_model_knowledge_items():
    r, _ = gen("mc", mc_draft(origin="model_knowledge"))
    assert r.items and r.items[0].origin == "materials"
    r, _ = gen("mc", mc_draft(origin="model_knowledge", citations=[]))
    assert not r.items and r.drops[0].gate_code == "schema_invalid"


def test_strict_model_sees_only_objective_and_passages():
    _, llm = gen("mc", mc_draft())
    call = llm.structured_calls[0]
    assert OBJ.statement in call.text and "civil:p1:1" in call.text and "Lincoln" in call.text
    assert "knowledge" not in call.instructions.lower() or "Do not use any outside" in call.instructions


def test_not_covered_gets_zero_items_and_zero_calls(store):
    llm = FakeLLM(lambda p, t: mc_draft())
    r = generate_one(fmt="mc", objective=NOT_COVERED, passages=[P1], llm=llm, mode="strict")
    assert r.items == [] and r.drops == [] and llm.calls == []
    rep = fill_objective(store, llm, NOT_COVERED, [P1], mode="open", wanted=3)
    assert rep.kept == 0 and llm.calls == [] and rep.not_covered == ["o2"]


def test_open_mode_labels_model_knowledge_and_skips_gates_a_b():
    d = {"prompt": "In what year did Rome fall?", "options": ["476", "1066", "800"], "correct_index": 0,
         "origin": "model_knowledge"}
    r, _ = gen("mc", d, mode="open")
    assert r.items and r.items[0].origin == "model_knowledge" and r.items[0].citations == []
    # structural checks still run
    r, _ = gen("mc", {**d, "options": ["476", "476", "800"]}, mode="open")
    assert not r.items and r.drops[0].gate_code == "ambiguous"
    # materials origin in open mode is still gated
    r, _ = gen("mc", mc_draft(citations=[{"passage_id": "civil:p1:1", "quote": "invented words about nothing here"}]),
               mode="open")
    assert r.drops[0].gate_code == "quote_missing"


# ------------------------------------------------------------------ drop reasons


def test_drop_reasons_for_reading_drafts():
    cases = {
        "unsupported_fact": mc_draft(prompt="In what year was it issued, 1864?"),
        "quote_missing": mc_draft(citations=[{"passage_id": "civil:p1:1", "quote": "Lincoln spoke about the railroads"}]),
        "ambiguous": mc_draft(options=["The Emancipation Proclamation", "The Emancipation Proclamation freed people",
                                       "The Tea Act"]),
    }
    for code, d in cases.items():
        r, _ = gen("mc", d)
        assert not r.items and r.drops[0].gate_code == code, (code, r.drops)
        assert r.drops[0].draft_hash and r.drops[0].objective_id == "o1" and r.drops[0].format == "mc"


def test_bad_json_is_schema_invalid():
    llm = FakeLLM(lambda p, t: {"nonsense": True})
    r = generate_one(fmt="mc", objective=OBJ, passages=[P1], llm=llm, rng=RNG())
    assert r.drops[0].gate_code == "schema_invalid" and len(llm.structured_calls) == 3


def test_llm_unavailable_propagates():
    llm = FakeLLM(lambda p, t: LLMUnavailable("down"))
    with pytest.raises(LLMUnavailable):
        generate_one(fmt="mc", objective=OBJ, passages=[P1], llm=llm)


# ------------------------------------------------------------------ self-checks with FAKE checkers

CODE_REF = "print(sum(map(int, input().split())))"
CODE_START = "pass"


def code_draft(**kw):
    d = {"prompt": "Write a program that prints the sum of two numbers.", "language": "python",
         "starter": CODE_START, "reference_solution": CODE_REF,
         "visible_tests": [{"name": "v1", "stdin": "1 2\n", "expected_stdout": "3\n"}],
         "hidden_tests": [{"name": "h1", "stdin": "5 5\n", "expected_stdout": "10\n"}]}
    d.update(kw)
    return d


def fake_code_checker(starter_passes=False, ref_passes=True):
    def fn(item, response, *, mode="submit"):
        if response == item.payload.reference_solution:
            return CheckResult(ok=ref_passes, score=1.0 if ref_passes else 0.0, detail="ref")
        pt = [PerTestResult(name="v1", passed=starter_passes, visible=True),
              PerTestResult(name="h1", passed=starter_passes, visible=False)]
        return CheckResult(ok=starter_passes, score=0.0, per_test=pt)
    return fn


def test_code_item_kept_when_reference_passes_and_starter_fails():
    register_checker("code", fake_code_checker())
    r, _ = gen("code", code_draft(), obj=Objective(id="oc", unit="u1", lesson="l1", verb="write",
                                                    statement="Write a sum program.", kind="code"), passages=[])
    assert r.items and r.items[0].checker == "code", r.drops


def test_code_weak_tests_when_starter_passes_hidden_tests():
    register_checker("code", fake_code_checker(starter_passes=True))
    r, _ = gen("code", code_draft(), passages=[])
    assert r.drops[0].gate_code == "weak_tests" and "starter" in r.drops[0].detail


def test_code_weak_tests_when_reference_fails():
    register_checker("code", fake_code_checker(ref_passes=False))
    r, _ = gen("code", code_draft(), passages=[])
    assert r.drops[0].gate_code == "weak_tests"


def test_no_registered_checker_fails_closed():
    r, _ = gen("code", code_draft(), passages=[])
    assert not r.items and r.drops[0].gate_code == "self_check_failed"


def test_checker_exception_or_undecided_fails_closed():
    def boom(item, response, *, mode="submit"):
        raise RuntimeError("sandbox exploded")
    register_checker("code", boom)
    r, _ = gen("code", code_draft(), passages=[])
    assert r.drops[0].gate_code == "self_check_failed"
    register_checker("code", lambda item, response, *, mode="submit": CheckResult(ok=False, score=0, undecided=True))
    r, _ = gen("code", code_draft(), passages=[])
    assert r.drops[0].gate_code == "self_check_failed"


def math_draft(**kw):
    d = {"prompt": "Differentiate the expression.", "op": "diff", "expr": "x**2", "var": "x", "given_answer": "2*x"}
    d.update(kw)
    return d


def test_math_key_must_match_computed_answer():
    register_checker("math", lambda item, resp, *, mode="submit": CheckResult(ok=resp.replace(" ", "") == "2*x", score=1.0))
    r, _ = gen("math", math_draft(), passages=[])
    assert r.items and r.items[0].checker == "math"
    r, _ = gen("math", math_draft(given_answer="3*x"), passages=[])
    assert not r.items and r.drops[0].gate_code == "key_mismatch"


def test_numeric_value_within_tolerance():
    def fn(item, resp, *, mode="submit"):
        value = float(resp.split()[0])
        return CheckResult(ok=abs(value - 6.0) <= 0.06, score=1.0)
    register_checker("numeric", fn)
    d = {"prompt": "A 2 kg mass accelerates at 3 m/s^2. Find the force.", "quantity": "force",
         "given": [{"name": "m", "value": 2.0, "unit": "kg"}, {"name": "a", "value": 3.0, "unit": "m/s^2"}],
         "answer_value": 6.0, "unit": "N", "rel_tol": 0.01}
    r, _ = gen("numeric", d, passages=[])
    assert r.items
    r, _ = gen("numeric", {**d, "answer_value": 9.0}, passages=[])
    assert r.drops[0].gate_code == "key_mismatch"


# ------------------------------------------------------------------ the scripted 30-draft run


def _cloze(prompt, blank, answer, quote=Q):
    return {"prompt": prompt, "text_with_blank": blank, "answer": answer,
            "citations": [{"passage_id": "civil:p1:1", "quote": quote}]}


def _short(prompt, terms, quote=Q):
    return {"prompt": prompt, "required_terms": terms, "citations": [{"passage_id": "civil:p1:1", "quote": quote}]}


def scripted_drafts():
    """30 drafts, 10 per format. The schema_invalid ones are repeated 3x because structured_call retries."""
    bad_cite = [{"passage_id": "civil:p1:1", "quote": "Lincoln spoke at length about railroads"}]
    one_word = [{"passage_id": "civil:p1:1", "quote": Q.replace("freed", "freeing")}]
    mc = [mc_draft(n) for n in range(1, 6)] + [
        mc_draft(prompt="In what year was it issued, 1864?"),                                   # invented year
        mc_draft(citations=bad_cite),                                                           # quote not in passage
        mc_draft(citations=one_word),                                                           # one word changed
        mc_draft(options=["The Emancipation Proclamation", "freed enslaved people", "The Tea Act"]),  # two supported
        {"prompt": "Which document?", "options": ["a"], "correct_index": 0, "n": 1}]                    # schema invalid
    cloze_blank = "In 1863, the ____ freed enslaved people in the rebel states."
    cloze = [_cloze(f, cloze_blank, "Emancipation Proclamation") for f in
             ("Fill in the blank.", "Complete the sentence.", "Supply the missing words.",
              "Finish the sentence.", "Restore the missing words.")] + [
        _cloze("Fill in the blank.", "In 1863, the ____ freed many slaves in the rebel states.",
               "Emancipation Proclamation"),                                                    # blank not a span
        _cloze("Fill in the blank.", "On March 4, 1861, ____ took the oath of office as the sixteenth President.",
               "Jefferson Davis", Q2),                                                          # answer name not in quote
        _cloze("Fill in the blank.", cloze_blank, "Emancipation Proclamation", "x y z q r"),    # quote not found
        _cloze("Fill in the blank.", "In 1863, the ____ freed enslaved people in the rebel states, said the text.",
               "Emancipation Proclamation"),                                                    # blank not a span (tail)
        {"prompt": "Fill in the blank."}]                                                       # schema invalid
    short = [_short(f, ["Emancipation Proclamation"]) for f in
             ("What freed enslaved people?", "Name the document that freed people.", "Which proclamation was it?",
              "What was issued?", "Identify the act that freed people.")] + [
        _short("Who led the rebel states?", ["Jefferson Davis"]),                               # name not in quote
        _short("What freed them?", ["Emancipation Proclamation", "Congress"]),                  # term not in quote
        _short("What freed them?", ["Emancipation Proclamation"], quote=Q.replace("freed", "freeing")),  # word changed
        _short("Why did 40 percent of people object?", ["Emancipation Proclamation"]),          # fact in the prompt
        {"prompt": "What freed them?"}]                                                         # schema invalid
    out = {"mc": [], "cloze": [], "short": []}
    for name, drafts in (("mc", mc), ("cloze", cloze), ("short", short)):
        for d in drafts:
            out[name] += [d] * (3 if "citations" not in d else 1)
    return out


def run_scripted(store):
    queues = {k: list(v) for k, v in scripted_drafts().items()}
    llm = FakeLLM(lambda purpose, text: queues[purpose.rsplit(".", 1)[1]].pop(0))
    rep = fill_objective(store, llm, OBJ, [P1], mode="strict", wanted=100, max_attempts=30,
                         formats=("mc", "cloze", "short"), rng=RNG(), report=BankReport())
    return rep


def test_scripted_30_draft_run_has_keeps_and_drops(store):
    rep = run_scripted(store)
    table = dict(sorted(rep.drops_by_code.items()))
    print("\nDROP_TABLE", json.dumps({"attempts": rep.attempts, "kept": rep.kept, "dropped": rep.dropped,
                                      "by_reason": table}))
    assert rep.attempts == 30 and rep.kept + rep.dropped + rep.duplicates == 30
    # the scripted MCs repeat one prompt+answer with new wrong options: the near-duplicate rule keeps only some
    assert rep.kept >= 8 and rep.dropped > 0 and rep.duplicates > 0
    assert set(table) >= {"quote_missing", "unsupported_fact", "ambiguous", "bad_cloze", "schema_invalid"}
    assert store.count_items(objective_id="o1", status="active") == rep.kept
    assert sum(store.count_drops_by_gate("civil").values()) == rep.dropped
    for it in store.list_items(objective_id="o1"):
        assert it.checker == it.format and it.origin == "materials" and it.citations


# ------------------------------------------------------------------ bank


def test_build_bank_fills_covered_only_and_reports_progress(store):
    llm = FakeLLM(lambda purpose, text: mc_draft() if purpose.endswith(".mc") else
                  _cloze("Fill in the blank.", "In 1863, the ____ freed enslaved people in the rebel states.",
                         "Emancipation Proclamation") if purpose.endswith(".cloze") else
                  _short("What freed people?", ["Emancipation Proclamation"]))
    marks = []
    rep = build_bank(store, llm, "civil", per_objective=2, progress=lambda pct, msg: marks.append(pct), rng=RNG())
    assert rep.kept == 2 and store.count_items(objective_id="o1", status="active") == 2
    assert store.count_items(objective_id="o2", status="active") == 0
    assert rep.not_covered == ["o2"]
    assert marks[0] == 0.0 and marks[-1] == 100.0 and marks == sorted(marks)
    before = len(llm.calls)
    build_bank(store, llm, "civil", per_objective=2, rng=RNG())          # already full: no LLM calls
    assert len(llm.calls) == before


def test_build_bank_stops_when_llm_is_down(store):
    rep = build_bank(store, FakeLLM([]), "civil", per_objective=3, rng=RNG())
    assert rep.llm_unavailable and rep.kept == 0


def test_build_bank_does_not_loop_forever_on_bad_drafts(store):
    llm = FakeLLM(lambda p, t: mc_draft(citations=[{"passage_id": "civil:p1:1", "quote": "nothing like the text at all"}]))
    rep = build_bank(store, llm, "civil", per_objective=3, rng=RNG())
    assert rep.kept == 0 and rep.attempts == 9 and rep.drops_by_code["quote_missing"] > 0


def test_top_up_refills_in_background(store):
    llm = FakeLLM(lambda purpose, text: _short("What freed people?", ["Emancipation Proclamation"])
                  if purpose.endswith(".short") else
                  _cloze("Fill in the blank.", "In 1863, the ____ freed enslaved people in the rebel states.",
                         "Emancipation Proclamation") if purpose.endswith(".cloze") else mc_draft())
    t = top_up(store, llm, "civil", 1, background=True, rng=RNG())
    assert isinstance(t, threading.Thread)
    t.join(10)
    assert not t.is_alive() and store.count_items(objective_id="o1", status="active") == 1


# ------------------------------------------------------------------ lessons


def test_strict_lesson_is_passage_location_and_gated_framing():
    llm = FakeLLM(lambda p, t: {"framing": "Read this, then notice what Lincoln did."})
    lv = make_lesson(OBJ, [P1], llm, unit_title="U", lesson_title="L")
    assert lv.framing == "Read this, then notice what Lincoln did."
    assert lv.passages[0].text == TEXT and lv.passages[0].location == "p. 1"


def test_lesson_framing_with_outside_date_is_removed_and_logged(store):
    llm = FakeLLM(lambda p, t: {"framing": "Read this passage about the war that began in 1860."})
    lv = make_lesson(OBJ, [P1], llm, store=store)
    assert lv.framing is None and lv.passages
    assert store.count_drops_by_gate("civil").get("framing_fact") == 1


def test_lesson_survives_llm_failure():
    lv = make_lesson(OBJ, [P1], FakeLLM([]))
    assert lv.framing is None and lv.passages


def test_lesson_none_for_not_covered_and_for_strict_without_passages():
    assert make_lesson(NOT_COVERED, [P1], FakeLLM([])) is None
    assert make_lesson(OBJ, [], FakeLLM([]), mode="strict") is None


# ------------------------------------------------------------------ a model that draws the blank longer than four underscores


def _cloze_draft(blank_text, answer):
    return {"prompt": "Fill in the blank.", "text_with_blank": blank_text, "answer": answer,
            "citations": [{"passage_id": "civil:p1:1", "quote": Q}]}


def test_cloze_blank_drawn_with_five_underscores_is_kept_and_normalised():
    r, _ = gen("cloze", _cloze_draft("In 1863, the _____ freed enslaved people in the rebel states.",
                                     "Emancipation Proclamation"))
    assert r.items, [d.detail for d in r.drops]
    assert r.items[0].payload.text_with_blank == "In 1863, the ____ freed enslaved people in the rebel states."


def test_cloze_blank_with_wrong_answer_is_still_dropped_after_normalising():
    r, _ = gen("cloze", _cloze_draft("In 1863, the _____ freed enslaved people in the rebel states.",
                                     "Louisiana Purchase"))
    assert not r.items and r.drops


# ------------------------------------------------------------------ F2: duplicates and phrasing


def test_reworded_duplicate_items_are_not_stored_but_other_formats_are(store):
    drafts = iter([
        mc_draft(prompt="Which document freed enslaved people in the rebel states?"),
        mc_draft(prompt="Which document freed the enslaved people in the rebel states in 1863?"),
        mc_draft(prompt="Which document freed enslaved people in rebel states?"),
    ])
    llm = FakeLLM(lambda purpose, text: next(drafts))
    rep = fill_objective(store, llm, OBJ, [P1], mode="strict", wanted=3, formats=["mc"], rng=RNG(), max_attempts=3)
    assert rep.kept == 1 and rep.duplicates == 2
    assert len(store.list_items(objective_id=OBJ.id)) == 1
    cloze = {"prompt": "Fill in the blank.", "text_with_blank": "In 1863, the ____ freed enslaved people in the "
             "rebel states.", "answer": "Emancipation Proclamation", "citations": [{"passage_id": "civil:p1:1", "quote": Q}]}
    rep2 = fill_objective(store, FakeLLM(lambda p, t: cloze), OBJ, [P1], mode="strict", wanted=1, formats=["cloze"],
                          rng=RNG(), max_attempts=1)
    assert rep2.kept == 1


def test_generation_prompt_forbids_the_according_to_opener():
    r, llm = gen("mc", mc_draft())
    ins = llm.structured_calls[0].instructions
    assert "Do NOT begin the prompt with" in ins and "According to the passage" in ins and "AUTHOR'S OWN claim" in ins
