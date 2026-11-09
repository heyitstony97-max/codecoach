"""Learn mode: steps, the explanation gate (both directions), the engine rules, check and step. FakeLLM only."""

import importlib.util
import re
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "surface"))
from surface_helpers import Clock, FakeLLM, new_store  # noqa: E402

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.core.llm import LLMUnavailable  # noqa: E402
from codecoach.core.models import (BLANK, Citation, ClozePayload, Course, CourseMap, Doc, Item, Lesson,  # noqa: E402
                                MCPayload, Objective, Passage, ReviewState, Unit)
from codecoach.learn import MAX_STEPS, plan_steps  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig, ServiceError  # noqa: E402

CID = "rail"

A = ("The Central Pacific Railroad built east from Sacramento, California, while the Union Pacific Railroad built "
     "west from Omaha, Nebraska. The two lines met at Promontory Summit, Utah, on May 10, 1869. A golden spike was "
     "driven to mark the joining of the rails. Workers on the Central Pacific were mostly immigrants from China, "
     "and they laid the track across the high desert.")
B = ("Building through the Sierra Nevada was the hardest part of the job. Crews cut tunnels through solid granite "
     "with hand drills and black powder, and they moved only a few inches each day. The snow was so deep in the "
     "winter that camps were buried and the crews dug tunnels to reach their work, and the work went on all year.")
SHORT1 = "Congress passed the Pacific Railroad Act in 1862, which paid for the line with land and loans."
SHORT2 = "The act chose the Central Pacific and the Union Pacific to build the first coast to coast railroad."
LONG = ("The company sold bonds to pay for rails, engines and wages, and the government paid a fixed sum for every "
        "mile of track that was laid on flat ground, with more for each mile in the mountains. Progress was measured "
        "in miles and each crew was paid when its section passed an inspection by the government.")
C = ("The railroad changed how people travelled and how goods were sold, because a trip that once took months by wagon "
     "could now be made in about a week, and farms in the West could ship their crops to cities in the East.")
Q_A = "The two lines met at Promontory Summit, Utah, on May 10, 1869."
Q_A_WORKERS = "Workers on the Central Pacific were mostly immigrants from China"
Q_B = "Crews cut tunnels through solid granite with hand drills and black powder"


def pid(ch, n):
    return f"book:{ch}:{n}"


def _passages():
    ps = [Passage(id=pid("ch1", 1), doc_id="book", location="Ch 1, para 1", text=A),
          Passage(id=pid("ch1", 2), doc_id="book", location="Ch 1, para 2", text="Chapter 2"),
          Passage(id=pid("ch1", 3), doc_id="book", location="Ch 1, para 3", text=B),
          Passage(id=pid("ch1", 4), doc_id="book", location="Ch 1, para 4", text=LONG + " [equation missing]",
                  formula_missing=True),
          Passage(id=pid("ch1", 5), doc_id="book", location="Ch 1, para 5", text=SHORT1),
          Passage(id=pid("ch1", 6), doc_id="book", location="Ch 1, para 6", text=SHORT2),
          Passage(id=pid("ch2", 1), doc_id="book", location="Ch 2, para 1", text=LONG),
          Passage(id=pid("ch2", 2), doc_id="book", location="Ch 2, para 2", text=A.replace("Central", "Central ")),
          Passage(id=pid("ch3", 1), doc_id="book", location="Ch 3, para 1", text=C)]
    for n in range(1, 9):                               # a topic with eight usable passages: the 6-step cap
        ps.append(Passage(id=pid("big", n), doc_id="book", location=f"Big, para {n}",
                          text=f"Section {n} tells one separate story about the railroad. " + LONG))
    return ps


def cloze(item_id, oid, passage, quote, answer):
    return Item(id=item_id, objective_id=oid, format="cloze", prompt="Fill in the blank.",
                payload=ClozePayload(text_with_blank=quote.replace(answer, BLANK, 1), answer=answer),
                citations=[Citation(passage_id=passage, quote=quote)], checker="cloze")


def mc(item_id, oid, passage, quote, options, correct):
    return Item(id=item_id, objective_id=oid, format="mc", prompt="Which one?",
                payload=MCPayload(options=options, correct_index=correct),
                citations=[Citation(passage_id=passage, quote=quote)], checker="mc")


def mk(llm, *, background=False, items=True):
    store = new_store()
    store.put_course(Course(id=CID, title="Railroad", mode="strict"))
    store.put_doc(Doc(id="book", course_id=CID, name="book.md", kind="markdown"))
    store.put_passages(_passages())
    objs = [
        Objective(id="rail:o01", unit="u1", lesson="l1", verb="explain", statement="How the railroad was built",
                  passage_ids=[pid("ch1", n) for n in range(1, 7)]),
        Objective(id="rail:o02", unit="u1", lesson="l1", verb="explain", statement="How it was paid for",
                  passage_ids=[pid("ch2", 1), pid("ch2", 2)]),
        Objective(id="rail:o03", unit="u1", lesson="l1", verb="explain", statement="What it changed",
                  passage_ids=[pid("ch3", 1)]),
        Objective(id="rail:o04", unit="u1", lesson="l1", verb="explain", statement="Everything about it",
                  passage_ids=[pid("big", n) for n in range(1, 9)]),
        Objective(id="rail:o05", unit="u1", lesson="l1", verb="explain", statement="Not in the book",
                  passage_ids=[], coverage="not_covered"),
    ]
    store.save_map(CourseMap(course_id=CID, units=[Unit(id="u1", title="Unit", lessons=[
        Lesson(id="l1", title="Lesson", objectives=objs)])]))
    if items:
        store.add_item(cloze("it-a-cloze", "rail:o01", pid("ch1", 1), Q_A, "Promontory Summit"))
        store.add_item(mc("it-a-mc", "rail:o01", pid("ch1", 1), Q_A, ["Omaha", "Promontory Summit", "Sacramento"], 1))
        store.add_item(cloze("it-b-cloze", "rail:o01", pid("ch1", 3), Q_B, "solid granite"))
        store.add_item(cloze("it-c-cloze", "rail:o03", pid("ch3", 1), "farms in the West could ship their crops to cities in the East",
                             "crops"))
        store.add_item(cloze("it-wide", "rail:o01", pid("ch1", 1), Q_A, "Utah"))     # a second item on step 0
    svc = CodeCoachService(store, llm, ServiceConfig(), now_fn=Clock(), background=background, auto_bank=False)
    return svc, store


def reply(*sentences, answerable=True):
    return {"answerable": answerable, "sentences": [{"text": t, "cites": [{"passage_id": p, "quote": q} for p, q in c]}
                                                    for t, c in sentences]}


GOOD_A = ("The two lines met at Promontory Summit in Utah on May 10, 1869.", [(pid("ch1", 1), Q_A)])
GOOD_A2 = ("Workers on the Central Pacific were mostly immigrants from China.", [(pid("ch1", 1), Q_A_WORKERS)])
GOOD_B = ("Crews cut tunnels through solid granite with hand drills and black powder.", [(pid("ch1", 3), Q_B)])


def llm_for(by_passage):
    """A FakeLLM that answers by the first passage id in the prompt; anything else gets answerable=false."""
    def fn(purpose, text):
        m = re.search(r"\[([^\]]+)\]", text)
        return by_passage.get(m.group(1) if m else "", reply(answerable=False))
    return FakeLLM(fn)


def ids_in_calls(llm):
    return {m for c in llm.calls for m in re.findall(r"\[(book:[^\]]+)\]", c.text)}


def rows(store):
    """Every table, every row: what 'byte identical' means for a database."""
    c = store._conn()
    out = {}
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        out[name] = [tuple(r) for r in c.execute(f"SELECT * FROM {name}").fetchall()]
    return out


# ------------------------------------------------------------------ steps

def test_fixture_sanity():
    assert len(A.split()) > 55 and len(B.split()) > 55 and len(LONG.split()) > 55
    assert len(SHORT1.split()) + len(SHORT2.split()) < 120


def test_chunking_skips_non_quotable_and_formula_missing_and_merges_short_neighbours():
    svc, store = mk(FakeLLM([]), items=False)
    t = svc.learn_topic(CID, "rail:o01")
    shown = [[p["passage_id"] for p in s["passages"]] for s in t["steps"]]
    assert shown == [[pid("ch1", 1)], [pid("ch1", 3), pid("ch1", 5)], [pid("ch1", 6)]]   # heading + formula skipped; B+SHORT1 < 120 words
    assert all(s["index"] == i for i, s in enumerate(t["steps"]))
    for s in t["steps"]:
        assert all(set(p) == {"passage_id", "doc_id", "location", "text"} for p in s["passages"])


def test_chunking_caps_at_six_steps():
    svc, _ = mk(FakeLLM([]), items=False)
    t = svc.learn_topic(CID, "rail:o04")
    assert len(t["steps"]) == MAX_STEPS == 6
    assert [s["passages"][0]["passage_id"] for s in t["steps"]] == [pid("big", n) for n in range(1, 7)]


def test_plan_steps_long_neighbours_do_not_merge():
    ps = [p for p in _passages() if p.id in (pid("ch1", 1), pid("ch1", 3))]
    assert [len(s) for s in plan_steps(ps)] == [1, 1]


def test_not_covered_topic_is_refused_and_listed():
    svc, _ = mk(FakeLLM([]))
    with pytest.raises(ServiceError) as e:
        svc.learn_topic(CID, "rail:o05")
    assert e.value.status == 409
    with pytest.raises(ServiceError) as e:
        svc.learn_topic(CID, "rail:nope")
    assert e.value.status == 404
    st = {t["objective_id"]: t["status"] for t in svc.learn_overview(CID)["topics"]}
    assert st["rail:o05"] == "not_in_materials" and st["rail:o01"] == "new"


def test_check_item_is_inside_the_step_prefers_cloze_and_is_never_used_twice():
    svc, _ = mk(FakeLLM([]))
    t = svc.learn_topic(CID, "rail:o01")
    got = [s["check"]["item"]["id"] if s["check"] else None for s in t["steps"]]
    assert got == ["it-a-cloze", "it-b-cloze", None]                  # cloze before mc; one item per step, no repeats
    assert len({g for g in got if g}) == len([g for g in got if g])
    item = t["steps"][0]["check"]["item"]
    assert "correct_index" not in str(item) and "answer" not in item["payload"]    # the public view, no key


def test_item_citing_outside_the_step_is_not_offered():
    svc, store = mk(FakeLLM([]), items=False)
    store.add_item(cloze("it-two", "rail:o01", pid("ch1", 1), Q_A, "Utah"))
    store.items = None
    it = store.get_item("it-two")
    wide = it.model_copy(update={"id": "it-wide2", "citations": it.citations + [Citation(passage_id=pid("ch1", 3), quote=Q_B)]})
    store.add_item(wide)
    t = svc.learn_topic(CID, "rail:o01")
    assert t["steps"][0]["check"]["item"]["id"] == "it-two"
    assert t["steps"][1]["check"] is None                             # it-wide2 spans two steps, so it fits neither


# ------------------------------------------------------------------ the explanation gate, both directions

def explain_with(*sentences, answerable=True):
    svc, store = mk(llm_for({pid("ch1", 1): reply(*sentences, answerable=answerable)}))
    return svc.learn_topic(CID, "rail:o01")["steps"][0]["explain"], svc


def test_faithful_plain_words_paraphrase_is_kept_with_exact_chips():
    ex, svc = explain_with(GOOD_A, GOOD_A2)
    assert ex["status"] == "ready" and ex["removed"] == 0
    assert [s["text"] for s in ex["sentences"]] == [GOOD_A[0], GOOD_A2[0]]
    passage = A
    for s in ex["sentences"]:
        assert s["cites"]
        for c in s["cites"]:
            assert set(c) >= {"passage_id", "doc_id", "location", "quote", "quote_start", "quote_end"}
            assert passage[c["quote_start"]:c["quote_end"]] == c["quote"]        # the chip points at the exact span
    assert ex["sentences"][0]["cites"][0]["quote"] == Q_A


@pytest.mark.parametrize("bad", [
    ("The two lines met at Promontory Summit, Utah, on May 10, 1870.", [(pid("ch1", 1), Q_A)]),             # invented year
    ("The two lines met at Promontory Summit, Utah, on May 10, 1869, led by Leland Stanford.", [(pid("ch1", 1), Q_A)]),
    ("The two lines met at Promontory Summit, Utah, after 400 days.", [(pid("ch1", 1), Q_A)]),              # invented number
    ("Workers on the Central Pacific were not immigrants from China.", [(pid("ch1", 1), Q_A_WORKERS)]),     # negation flip
    ("Workers on the Central Pacific cut through the Sierra Nevada.", [(pid("ch1", 1), Q_A_WORKERS)]),      # stitched from B
    ("The Central Pacific built east from Sacramento in 1869.", [(pid("ch1", 1), "The Central Pacific Railroad built east from Sacramento, California")]),
    ("The railroad made the country rich and proud.", [(pid("ch1", 1), Q_A)]),                              # words not in quote
    ("The two lines met at Promontory Summit, Utah, on May 10, 1869.", []),                                 # no cite
    ("The two lines met somewhere in Utah.", [(pid("ch1", 1), "The two lines met somewhere in Utah, said the chief")]),   # quote not in passage
])
def test_unfaithful_sentence_is_dropped_and_a_faithful_neighbour_survives(bad):
    ex, _ = explain_with(bad, GOOD_A)
    assert [s["text"] for s in ex["sentences"]] == [GOOD_A[0]]
    assert ex["status"] == "ready" and ex["removed"] == 1


def test_a_fact_from_another_passage_is_dropped():
    # cites step 0's passage but states what only passage B says
    ex, _ = explain_with(("Crews cut tunnels through solid granite.", [(pid("ch1", 1), Q_A)]), GOOD_A)
    assert [s["text"] for s in ex["sentences"]] == [GOOD_A[0]] and ex["removed"] == 1
    # and a quote that belongs to B but names the wrong passage is not found there
    ex, _ = explain_with(("Crews cut tunnels through solid granite with hand drills and black powder.",
                          [(pid("ch1", 1), Q_B)]), GOOD_A)
    assert ex["removed"] == 1 and len(ex["sentences"]) == 1


def test_zero_kept_is_unavailable_and_the_passage_is_still_returned():
    bad = ("The two lines met at Promontory Summit, Utah, on May 10, 1870.", [(pid("ch1", 1), Q_A)])
    ex, svc = explain_with(bad, ("Workers were not immigrants from China at all.", [(pid("ch1", 1), Q_A_WORKERS)]))
    assert ex["status"] == "unavailable" and ex["sentences"] == [] and ex["removed"] == 2
    t = svc.learn_topic(CID, "rail:o01")
    assert t["steps"][0]["passages"][0]["text"] == A and t["steps"][0]["check"] is not None


def test_at_most_four_sentences_are_shown():
    five = [GOOD_A, GOOD_A2, ("The two lines met in Utah on May 10, 1869.", [(pid("ch1", 1), Q_A)]),
            ("Workers on the Central Pacific were immigrants from China.", [(pid("ch1", 1), Q_A_WORKERS)]),
            ("A golden spike was driven to mark the joining of the rails.",
             [(pid("ch1", 1), "A golden spike was driven to mark the joining of the rails")])]
    ex, _ = explain_with(*five)
    assert len(ex["sentences"]) == 4


def test_not_answerable_is_unavailable():
    ex, _ = explain_with(GOOD_A, answerable=False)
    assert ex["status"] == "unavailable" and ex["sentences"] == []


def test_a_model_failure_is_unavailable_and_not_cached_for_good():
    svc, _ = mk(FakeLLM(lambda p, t: LLMUnavailable("down")))
    ex = svc.learn_topic(CID, "rail:o01")["steps"][0]["explain"]
    assert ex["status"] == "unavailable"
    n = len(svc._learn.svc.llm.calls)
    svc.learn_topic(CID, "rail:o01")
    assert len(svc.llm.calls) == n                                    # not hammered on every poll


def test_unparseable_reply_gets_exactly_one_retry():
    svc, _ = mk(FakeLLM(lambda p, t: "not json at all"))
    svc.learn_topic(CID, "rail:o03")
    assert [c.text for c in svc.llm.calls if "[book:ch3:1]" in c.text].__len__() == 2    # one try, one retry


# ------------------------------------------------------------------ the engine

def test_no_llm_is_unavailable_at_once_and_learn_still_works():
    svc, _ = mk(None)
    t = svc.learn_topic(CID, "rail:o01")
    assert [s["explain"]["status"] for s in t["steps"]] == ["unavailable"] * 3
    assert t["building"] is False and t["steps"][0]["check"] is not None and t["steps"][0]["passages"]


def test_second_get_hits_the_cache_and_a_new_engine_does_too():
    llm = llm_for({pid("ch1", 1): reply(GOOD_A), pid("ch1", 3): reply(GOOD_B)})
    svc, store = mk(llm)
    t1 = svc.learn_topic(CID, "rail:o01")
    n = len(llm.calls)
    assert n > 0 and t1["steps"][0]["explain"]["status"] == "ready"
    t2 = svc.learn_topic(CID, "rail:o01")
    assert len(llm.calls) == n and t2["steps"][0]["explain"] == t1["steps"][0]["explain"]
    svc2 = CodeCoachService(store, llm, ServiceConfig(), now_fn=Clock(), background=False, auto_bank=False)
    svc2.learn_topic(CID, "rail:o01")
    assert len(llm.calls) == n                                         # survives a restart (store meta)


def test_a_changed_passage_invalidates_the_cached_explanation():
    llm = llm_for({pid("ch1", 1): reply(GOOD_A)})
    svc, store = mk(llm)
    svc.learn_topic(CID, "rail:o01")
    n = len(llm.calls)
    old = store.get_passage(pid("ch1", 1))
    store.put_passages([old.model_copy(update={"text": old.text + " It was a great day for the nation."})])
    svc.learn_topic(CID, "rail:o01")
    assert len(llm.calls) == n + 1                                     # only that step was rebuilt


def test_opening_a_topic_builds_it_and_the_next_one_never_the_whole_course():
    llm = llm_for({})
    svc, _ = mk(llm)
    svc.learn_topic(CID, "rail:o01")
    seen = ids_in_calls(llm)
    assert pid("ch2", 1) in seen                                      # the next topic (o02) was prefetched
    assert pid("ch3", 1) not in seen and not any(i.startswith("book:big") for i in seen)    # C and D never


def test_polling_shows_pending_then_ready_and_only_one_build_runs_at_a_time():
    gate = threading.Event()
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fn(purpose, text):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        gate.wait(5)
        time.sleep(0.02)
        with lock:
            live["now"] -= 1
        m = re.search(r"\[([^\]]+)\]", text)
        return {pid("ch1", 1): reply(GOOD_A)}.get(m.group(1), reply(answerable=False))

    svc, _ = mk(FakeLLM(fn), background=True)
    t = svc.learn_topic(CID, "rail:o01")                               # returns at once, nothing blocked on the model
    assert [s["explain"]["status"] for s in t["steps"]] == ["pending"] * 3 and t["building"] is True
    again = svc.learn_topic(CID, "rail:o01")
    assert again["building"] is True                                   # a poll does not start a second job
    gate.set()
    assert svc.wait_idle(20)
    done = svc.learn_topic(CID, "rail:o01")
    assert done["steps"][0]["explain"]["status"] == "ready" and done["building"] is False
    assert live["max"] == 1


# ------------------------------------------------------------------ check

def test_check_grades_by_code_and_writes_nothing():
    svc, store = mk(FakeLLM([]), items=True)
    svc.learn_topic(CID, "rail:o01")
    store.put_review_state(ReviewState(item_id="it-a-cloze", due=svc.now(), stability=2.0, difficulty=5.0, reps=2))
    before = rows(store)
    right = svc.learn_check(CID, "rail:o01", "it-a-cloze", "Promontory Summit")
    wrong = svc.learn_check(CID, "rail:o01", "it-a-cloze", "Sacramento")
    assert right["learn"] is True and right["correct"] is True and right["score"] == 1.0
    assert wrong["learn"] is True and wrong["correct"] is False
    assert wrong["citations"] and wrong["citations"][0]["quote"] == Q_A          # the same reveal Practice uses
    assert wrong["next_review"] is None and wrong["mastery"] is None
    assert rows(store) == before                                       # attempts, mastery, review_state: byte identical
    assert store.count_attempts(course_id=CID) == 0


def test_check_item_from_another_topic_is_422_and_unknown_is_404():
    svc, store = mk(FakeLLM([]))
    before = rows(store)
    with pytest.raises(ServiceError) as e:
        svc.learn_check(CID, "rail:o01", "it-c-cloze", "crops")
    assert e.value.status == 422
    with pytest.raises(ServiceError) as e:
        svc.learn_check(CID, "rail:o01", "no-such-item", "x")
    assert e.value.status == 404
    with pytest.raises(ServiceError) as e:
        svc.learn_check(CID, "rail:zzz", "it-a-cloze", "x")
    assert e.value.status == 404
    assert rows(store) == before


# ------------------------------------------------------------------ step / progress / first review

def test_step_progress_persists_and_the_last_step_gives_learned():
    llm = llm_for({pid("ch1", 1): reply(GOOD_A), pid("ch1", 3): reply(GOOD_B)})
    svc, store = mk(llm)
    t = svc.learn_topic(CID, "rail:o01")
    assert t["status"] == "new" and t["steps_done"] == 0
    assert svc.learn_step(CID, "rail:o01", 0) == {"steps_done": 1, "status": "in_progress"}
    svc2 = CodeCoachService(store, llm, ServiceConfig(), now_fn=Clock(), background=False, auto_bank=False)
    t = svc2.learn_topic(CID, "rail:o01")
    assert t["status"] == "in_progress" and t["steps_done"] == 1
    ov = svc2.learn_overview(CID)
    row = next(r for r in ov["topics"] if r["objective_id"] == "rail:o01")
    assert row["steps_total"] == 3 and row["steps_done"] == 1 and ov["next_objective_id"] == "rail:o01"
    svc2.learn_step(CID, "rail:o01", 1)
    assert svc2.learn_step(CID, "rail:o01", 2) == {"steps_done": 3, "status": "learned"}
    t = svc2.learn_topic(CID, "rail:o01")
    assert t["status"] == "learned" and t["steps_done"] == 3
    assert [s["text"] for s in t["summary"]] == [GOOD_A[0], GOOD_B[0]]
    ov = svc2.learn_overview(CID)
    assert ov["next_objective_id"] == "rail:o02"
    assert next(r for r in ov["topics"] if r["objective_id"] == "rail:o02")["steps_total"] is None   # never opened


def test_bad_step_index_is_422():
    svc, _ = mk(FakeLLM([]))
    for bad in (-1, 3, 99):
        with pytest.raises(ServiceError) as e:
            svc.learn_step(CID, "rail:o01", bad)
        assert e.value.status == 422


def test_learned_seeds_first_review_for_new_items_only_and_leaves_existing_state_alone():
    svc, store = mk(FakeLLM([]))
    existing = ReviewState(item_id="it-b-cloze", due=svc.now() + timedelta(days=9), stability=7.5, difficulty=4.2,
                           reps=3, lapses=1, last_review=svc.now())
    store.put_review_state(existing)
    svc.learn_step(CID, "rail:o01", 0)
    assert store.get_review_state("it-a-cloze") is None               # not until the topic is learned
    svc.learn_step(CID, "rail:o01", 2)
    now = svc.now()
    for iid in ("it-a-cloze", "it-a-mc", "it-wide"):
        rs = store.get_review_state(iid)
        assert rs is not None and rs.reps == 0 and rs.last_review is None
        assert timedelta(hours=23) < rs.due - now < timedelta(hours=25)
    assert store.get_review_state("it-b-cloze") == existing
    assert store.get_review_state("it-c-cloze") is None               # another topic: untouched
    assert store.count_attempts(course_id=CID) == 0


# ------------------------------------------------------------------ the HTTP shape

@pytest.fixture()
def http():
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    svc, store = mk(llm_for({pid("ch1", 1): reply(GOOD_A)}))
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/codecoach")
    return TestClient(app), svc


def test_http_routes_have_the_contract_shapes(http):
    c, _ = http
    base = f"/api/plugins/codecoach/courses/{CID}/learn"
    ov = c.get(base).json()
    assert set(ov) == {"course_id", "next_objective_id", "topics", "outline"}
    assert set(ov["topics"][0]) == {"objective_id", "title", "status", "steps_total", "steps_done"}
    t = c.get(f"{base}/rail:o01").json()
    assert set(t) == {"objective_id", "title", "status", "steps_done", "building", "steps", "summary"}
    s0 = t["steps"][0]
    assert set(s0) == {"index", "passages", "explain", "check"}
    assert set(s0["explain"]) == {"status", "removed", "sentences"} and set(s0["check"]) == {"item"}
    ok = c.post(f"{base}/rail:o01/check", json={"item_id": "it-a-cloze", "response": "Utah"}).json()
    assert ok["learn"] is True and {"item_id", "correct", "score", "feedback", "citations"} <= set(ok)
    assert c.post(f"{base}/rail:o01/check", json={"item_id": "it-c-cloze", "response": "x"}).status_code == 422
    assert c.post(f"{base}/rail:o01/step", json={"index": 0}).json() == {"steps_done": 1, "status": "in_progress"}
    assert c.post(f"{base}/rail:o01/step", json={"index": 7}).status_code == 422
    assert c.post(f"{base}/rail:o01/step", json={"index": "zero"}).status_code == 422
    assert c.get(f"{base}/rail:nope").status_code == 404
    assert c.get("/api/plugins/codecoach/courses/nope/learn").status_code == 404


def test_a_run_of_one_line_passages_becomes_one_explainable_step():
    """Orchestrator, live Strunk run: one-line rules and example lines each became their own step, too thin to
    explain (6 of 12 steps unavailable). A thin step keeps taking short neighbours until it has enough words."""
    from codecoach.core.models import Passage
    lines = ["Rule 1. Form the possessive singular of nouns by adding 's.",
             "Charles's friend came to the party.",
             "Burns's poems are still read today.",
             "We feared the witch's malice greatly.",
             "This is the usage of the United States Government Printing Office and of the Oxford University Press."]
    ps = [Passage(id=f"d:s:{i}", doc_id="d", location=f"para {i}", text=t) for i, t in enumerate(lines, 1)]
    steps = plan_steps(ps)
    assert len(steps) == 1 and [p.id for p in steps[0]] == [p.id for p in ps]


def test_a_thin_step_never_grows_past_the_word_limit_or_across_documents():
    from codecoach.core.models import Passage
    long = " ".join(["word"] * 100)
    ps = [Passage(id="a:s:1", doc_id="a", location="1", text="A short rule line here."),
          Passage(id="a:s:2", doc_id="a", location="2", text=long),
          Passage(id="a:s:3", doc_id="a", location="3", text="Another short line of text."),
          Passage(id="b:s:1", doc_id="b", location="1", text="Other file short line.")]
    assert [[p.id for p in s] for s in plan_steps(ps)] == [["a:s:1", "a:s:2"], ["a:s:3"], ["b:s:1"]]


def test_a_preface_at_the_start_of_a_topic_is_not_taught_but_the_middle_is_kept():
    """Orchestrator, live Strunk run: the 'possessive singular' lesson opened with the preface (the author thanking
    his Cornell colleagues). An off-topic run at the start or end is trimmed; an off-word passage in the middle stays."""
    from codecoach.core.models import Objective, Passage
    from codecoach.learn import LearnEngine
    texts = ["The writer's colleagues in the Department of English in Cornell University have greatly helped him.",
             "Form the possessive singular of nouns by adding an apostrophe and the letter s.",
             "Follow this rule whatever the final consonant of the word happens to be.",
             "The possessive singular of ancient proper names in -es and -is is an exception."]
    ps = [Passage(id=f"d:s:{i}", doc_id="d", location=f"para {i}", text=t) for i, t in enumerate(texts, 1)]
    obj = Objective(id="c:o01", unit="u1", lesson="l1", verb="apply", statement="Form the possessive singular",
                    passage_ids=[p.id for p in ps])
    svc, _ = mk(FakeLLM([]), items=False)
    kept = LearnEngine(svc)._on_topic(obj, ps)
    assert [p.id for p in kept] == ["d:s:2", "d:s:3", "d:s:4"]       # preface trimmed, off-word middle kept


def test_nothing_on_topic_keeps_every_passage():
    from codecoach.core.models import Objective, Passage
    from codecoach.learn import LearnEngine
    ps = [Passage(id=f"d:s:{i}", doc_id="d", location=f"para {i}", text=f"Some unrelated sentence number {i} here.")
          for i in range(1, 4)]
    obj = Objective(id="c:o01", unit="u1", lesson="l1", verb="apply", statement="Possessives",
                    passage_ids=[p.id for p in ps])
    svc, _ = mk(FakeLLM([]), items=False)
    assert LearnEngine(svc)._on_topic(obj, ps) == ps


# ------------------------------------------------------------------ a dropped limit changes the rule
# Live Strunk run (orchestrator): "use a comma after each term except the last" was explained as "Use a comma after
# each term in a series ...". Nothing was added, so the quote gate kept it; the rule had changed anyway.

@pytest.mark.parametrize("sentence,quote,lost", [
    ("Use a comma after each term in a series.", "use a comma after each term except the last", True),
    ("Use a comma after each term except the last.", "use a comma after each term except the last", False),
    ("Omit the commas when the break is slight.", "Omit them unless the break is slight.", True),
    ("Exceptions are the possessive of ancient names in -es.", "Exceptions are the possessive of ancient names in -es", False),
    ("Robert Lodge forged the silver into a rough spike.", "forged into a rough spike bearing only the stamp", False),
])
def test_a_limit_the_quote_states_must_stay_in_the_sentence(sentence, quote, lost):
    from codecoach.learn import dropped_limit
    assert dropped_limit(sentence, [quote]) is lost


def test_overview_says_when_a_course_is_only_an_outline():
    """Orchestrator: Anthony's C Basics was built from an outline with no materials, so Learn could only show outline
    lines ("By the end you can ..."). The overview flags it so the screen says so."""
    from codecoach.importer.build import OUTLINE_MODE_REASON
    svc, store = mk(FakeLLM([]), items=False)
    assert svc.learn_overview(CID)["outline"] is False
    store.set_course_mode(CID, "open", OUTLINE_MODE_REASON)
    assert svc.learn_overview(CID)["outline"] is True


# ------------------------------------------------------------------ fair blanks reach Learn and Review
# Orchestrator (Anthony: "it makes you do word for word"): fair cloze grading is in CodeCoachService._grade, so Learn's
# check and Review's answer both use it. The fixture's passage says "Promontory Summit, Utah".

def test_learn_and_review_accept_a_fair_form_and_still_reject_a_wrong_one():
    svc, store = mk(FakeLLM([]), items=True)
    svc.learn_topic(CID, "rail:o01")
    assert svc.learn_check(CID, "rail:o01", "it-a-cloze", "at promontory summit, utah")["correct"] is True
    typo = svc.learn_check(CID, "rail:o01", "it-a-cloze", "Promontory Sumit")
    assert typo["correct"] is True and "spelling" in typo["feedback"]
    assert svc.learn_check(CID, "rail:o01", "it-a-cloze", "Promontory")["correct"] is False
    assert svc.learn_check(CID, "rail:o01", "it-a-cloze", "Promontory Summit or Ogden")["correct"] is False
    review = svc.answer("it-a-cloze", "the Promontory summit.")
    assert review.correct is True
    assert svc.answer("it-b-cloze", "solid granit").correct is True      # one typo in a long word
    near = svc.answer("it-b-cloze", "granite")                            # a description, shortened: close
    assert near.correct is False and "solid granite" in near.feedback
