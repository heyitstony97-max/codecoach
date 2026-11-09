"""Ask: every gate path with adversarial model outputs, the outcomes, the cache, the integrity release,
the open-mode refusal and the routes staying fast while a slow model runs. FakeLLM only."""

import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

from codecoach.ask import retrieve as R

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "surface"))
from surface_helpers import Clock, FakeLLM, make_service, new_store  # noqa: E402

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.core.llm import LLMUnavailable  # noqa: E402
from codecoach.core.models import Course, Doc  # noqa: E402
from codecoach.core.passages import split_document  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig, ServiceError  # noqa: E402

HISTORY = (ROOT / "demo" / "materials" / "history.md").read_text()
CID = "railroad"


def mk(llm, *, mode="strict", background=False, text=HISTORY):
    store = new_store()
    store.put_course(Course(id=CID, title="Railroad", mode=mode))
    if text:
        store.put_doc(Doc(id="history-md", course_id=CID, name="history.md", kind="markdown"))
        store.put_passages(split_document("history-md", text, "markdown"))
    svc = CodeCoachService(store, llm, ServiceConfig(), now_fn=Clock(), background=background, auto_bank=False)
    return svc, store


def pid(store, needle):
    return next(p.id for p in store.list_passages(course_id=CID) if needle in p.text)


def reply(*sentences, answerable=True):
    return {"answerable": answerable, "sentences": [{"text": t, "cites": [{"passage_id": p, "quote": q}
                                                                         for p, q in c]} for t, c in sentences]}


def ask(svc, q="How heavy was the golden spike?", **kw):
    out = svc.ask_start(CID, q, **kw)
    assert out["job_id"], out
    return svc.ask_job(CID, out["job_id"])["result"]


@pytest.fixture
def spike():
    svc, store = mk(FakeLLM([]))
    p = pid(store, "14.03 ounces")
    return p, "The spike was 5 5/8 inches long, weighed 14.03 ounces and was made of 17.6 carat gold."


def good(p, q):
    return ("The spike weighed 14.03 ounces.", [(p, q)])


def run(llm_reply, q="How heavy was the golden spike?", **kw):
    svc, store = mk(FakeLLM(lambda purpose, text: llm_reply))
    return ask(svc, q, **kw), svc, store


# ------------------------------------------------------------------ the happy path and its shape

def test_answered_sentence_has_cite_with_offsets(spike):
    p, q = spike
    res, svc, store = run(reply(good(p, q)))
    assert res["status"] == "answered" and res["removed"] == []
    s = res["sentences"][0]
    c = s["cites"][0]
    assert set(c) == {"passage_id", "doc_id", "location", "quote", "quote_start", "quote_end"}
    text = store.get_passage(c["passage_id"]).text
    assert text[c["quote_start"]:c["quote_end"]] == c["quote"] and "14.03 ounces" in c["quote"]
    assert c["location"] == "The Golden Spike, para 1"


def test_curly_quote_and_case_differences_still_get_exact_offsets():
    svc, store = mk(FakeLLM([]))
    p = pid(store, "Pathway to Commerce")
    res = ask_with(svc, reply(("The words engraved were Ribbed with Iron Clad in Silver and Crowned with Gold.",
                               [(p, "engraved with decorative flourishes and these words: \"ribbed with iron clad in silver and crowned with gold.\"")])))
    assert res["status"] == "answered", res
    c = res["sentences"][0]["cites"][0]
    assert store.get_passage(p).text[c["quote_start"]:c["quote_end"]] == c["quote"]


def ask_with(svc, rep, q="What was written on the Arizona spike?"):
    svc.llm = FakeLLM(lambda purpose, text: rep)
    return ask(svc, q)


# ------------------------------------------------------------------ adversarial outputs: one sentence dropped each

def gates(res):
    return [r["gate"] for r in res["removed"]]


def test_invented_year_is_dropped(spike):
    p, q = spike
    res, svc, store = run(reply(("The spike weighed 14.03 ounces and was cast in 1871.", [(p, q)]),
                                good(p, q)))
    assert res["status"] == "answered" and len(res["sentences"]) == 1
    assert "ask_unsupported_fact" in gates(res)
    assert any(d.gate_code == "ask_unsupported_fact" and "1871" in d.detail for d in store.list_drops(CID))


def test_invented_name_is_dropped(spike):
    p, q = spike
    res, *_ = run(reply(("Cornelius Vanderbilt ordered a spike that weighed 14.03 ounces.", [(p, q)]), good(p, q)))
    assert "ask_unsupported_fact" in gates(res) and len(res["sentences"]) == 1


def test_a_word_the_quote_lacks_is_dropped_under_the_framing_tolerance(spike):
    p, q = spike
    res, *_ = run(reply(("The spike weighed 14.03 ounces and was incredibly valuable.", [(p, q)]), good(p, q)))
    assert "ask_unsupported_fact" in gates(res) and len(res["sentences"]) == 1


def test_quote_that_is_not_verbatim_is_dropped(spike):
    p, q = spike
    res, svc, store = run(reply(("The spike weighed 14.03 ounces.",
                                 [(p, "The spike weighed 14.03 ounces and was 17.6 carat gold")]), good(p, q)))
    assert gates(res) == ["ask_quote_missing"] and len(res["sentences"]) == 1
    assert store.count_drops_by_gate(CID)["ask_quote_missing"] == 1


def test_one_changed_word_in_a_quote_is_dropped(spike):
    p, q = spike
    res, *_ = run(reply(("The spike weighed 15.03 ounces.", [(p, q.replace("14.03", "15.03"))])))
    assert res["status"] == "not_covered" and gates(res) == ["ask_quote_missing"]


def test_quote_from_a_passage_that_was_not_offered_is_dropped(spike):
    p, q = spike
    other = pid(svc_store := mk(FakeLLM([]))[1], "Laurelwood")
    res, *_ = run(reply(("The spike weighed 14.03 ounces.", [("history-md:nope:9", q)]), good(p, q)))
    assert "ask_quote_missing" in gates(res) and len(res["sentences"]) == 1


def test_flipped_negation_is_dropped():
    svc, store = mk(FakeLLM([]))
    p = pid(store, "No personal writings")
    q = "No personal writings, letters, or diaries authored by Chinese workers on the first transcontinental railroad have been found."
    res = ask_with(svc, reply(("Personal writings by Chinese workers on the first transcontinental railroad have been found.", [(p, q)])),
                   "Did Chinese workers leave diaries?")
    assert res["status"] == "not_covered" and gates(res) == ["ask_negation"]
    res = ask_with(svc, reply(("No personal writings by Chinese workers on the first transcontinental railroad have been found.", [(p, q)])),
                   "Are there diaries by Chinese workers?")
    assert res["status"] == "answered", res


def test_sentence_that_denies_its_quote_is_dropped(spike):
    p, q = spike
    res, *_ = run(reply(("The spike was not made of gold.", [(p, q)]), good(p, q)))
    assert "ask_negation" in gates(res) and len(res["sentences"]) == 1


def test_sentence_without_a_cite_is_dropped(spike):
    p, q = spike
    res, svc, store = run(reply(("The spike weighed 14.03 ounces.", []), good(p, q)))
    assert gates(res) == ["ask_no_cite"] and len(res["sentences"]) == 1
    assert store.count_drops_by_gate(CID)["ask_no_cite"] == 1


def test_long_sentence_and_sixth_sentence_are_dropped(spike):
    p, q = spike
    long = "The spike " + "weighed 14.03 ounces " * 15 + "."
    res, *_ = run(reply((long, [(p, q)]), *[good(p, q)] * 1))
    assert "ask_too_long" in gates(res)
    five = [(f"The spike weighed 14.03 ounces number{i}.", [(p, q)]) for i in range(6)]
    res, *_ = run(reply(*five))
    assert len(res["sentences"]) <= 5 and "ask_too_long" in gates(res)


def test_every_sentence_dropped_is_not_covered_with_no_prose():
    svc, store = mk(FakeLLM([]))
    p = pid(store, "14.03 ounces")
    res = ask_with(svc, reply(("The spike was cast in 1871 by Vanderbilt.", [(p, "weighed 14.03 ounces and was made of")])),
                   "How heavy was the golden spike?")
    assert res["status"] == "not_covered" and res["sentences"] == [] and res["removed"]
    assert res["closest"] and all(c["label"] == "closest, not an answer" for c in res["closest"])


# ------------------------------------------------------------------ uncovered questions

def test_fabricated_answer_to_an_uncovered_question_is_not_covered():
    svc, store = mk(FakeLLM([]))
    p = pid(store, "Pacific Express Company")
    fab = reply(("The Pony Express carried mail from Missouri to California in 1860.",
                 [(p, "president of San Francisco's Pacific Express Company")]))
    res = ask_with(svc, fab, "How fast was the Pony Express?")
    assert res["status"] == "not_covered" and res["sentences"] == []
    assert svc.llm.calls == [], "Pony and Express: one shared word is not enough to call the model"


def test_model_says_not_answerable_even_with_good_looking_sentences(spike):
    p, q = spike
    res, svc, store = run(reply(good(p, q), answerable=False))
    assert res["status"] == "not_covered" and res["sentences"] == []


def test_no_retrieval_hit_means_no_model_call_and_three_closest_passages():
    svc, store = mk(FakeLLM([]))
    res = ask_with(svc, reply(), "Who won the 1868 presidential election?")
    assert res["status"] == "not_covered" and res["model_calls"] == 0 and svc.llm.calls == []
    assert len(res["closest"]) <= 3
    for c in res["closest"]:
        assert c["text"] == store.get_passage(c["passage_id"]).text and c["location"]


def test_model_not_covered_gets_one_wider_retry_then_success(spike):
    p, q = spike
    answers = iter([reply(answerable=False), reply(good(p, q))])
    svc, store = mk(FakeLLM(lambda purpose, text: next(answers)))
    res = ask(svc)
    assert res["status"] == "answered" and res["model_calls"] == 2
    first, second = svc.llm.calls
    assert first.text.count("\n[") == R.TOP_K and second.text.count("\n[") > R.TOP_K


def test_not_covered_twice_is_accepted(spike):
    res, svc, store = run(reply(answerable=False))
    assert res["status"] == "not_covered" and res["model_calls"] == 2


# ------------------------------------------------------------------ errors

def test_bad_json_twice_is_an_error_that_says_so():
    svc, store = mk(FakeLLM(lambda p, t: None))
    res = ask(svc)
    assert res["status"] == "error" and "twice" in res["message"] and res["sentences"] == []
    assert len(svc.llm.calls) == 2


def test_bad_json_once_is_retried(spike):
    p, q = spike
    answers = iter([None, reply(good(p, q))])
    svc, store = mk(FakeLLM(lambda purpose, text: next(answers)))
    assert ask(svc)["status"] == "answered"


def test_unreachable_model_is_an_error():
    svc, store = mk(FakeLLM([LLMUnavailable("down")]))
    res = ask(svc)
    assert res["status"] == "error" and "could not be reached" in res["message"]


# ------------------------------------------------------------------ refusals

def test_open_mode_course_is_unavailable_without_a_model_call():
    svc, store = mk(FakeLLM([]), mode="open")
    out = svc.ask_start(CID, "How heavy was the golden spike?")
    assert out["status"] == "unavailable" and out["reason"] and out["job_id"] is None and svc.llm.calls == []


def test_course_without_source_text_is_unavailable():
    svc, store = mk(FakeLLM([]), text="")
    out = svc.ask_start(CID, "anything at all?")
    assert out["status"] == "unavailable" and svc.llm.calls == []


@pytest.mark.parametrize("q", ["", "   ", "x" * 301])
def test_question_length_is_1_to_300(q):
    svc, store = mk(FakeLLM([]))
    with pytest.raises(ServiceError) as e:
        svc.ask_start(CID, q)
    assert e.value.status == 422


# ------------------------------------------------------------------ cache and history

def test_cache_hit_skips_the_model_and_normalises_the_question(spike):
    p, q = spike
    svc, store = mk(FakeLLM(lambda purpose, text: reply(good(p, q))))
    first = ask(svc, "How heavy was the golden spike?")
    n = len(svc.llm.calls)
    out = svc.ask_start(CID, "  how HEAVY was the golden spike ")
    assert out["cached"] is True and len(svc.llm.calls) == n
    again = svc.ask_job(CID, out["job_id"])["result"]
    assert again["status"] == "answered" and again["cached"] is True
    assert again["sentences"] == first["sentences"]
    assert len(svc.asks(CID)["asks"]) == 1


def test_errors_are_not_cached(spike):
    p, q = spike
    answers = iter([None, None, reply(good(p, q))])
    svc, store = mk(FakeLLM(lambda purpose, text: next(answers)))
    assert ask(svc)["status"] == "error"
    assert ask(svc)["status"] == "answered"


def test_history_is_newest_first_and_capped_at_50(spike):
    p, q = spike
    svc, store = mk(FakeLLM(lambda purpose, text: reply(good(p, q))))
    for i in range(55):
        ask(svc, f"How heavy was the golden spike number {i}?")
    h = svc.asks(CID)["asks"]
    assert len(h) == 50 and h[0]["question"].endswith("number 54?") and h[0]["status"] == "answered"
    assert set(h[0]) == {"job_id", "question", "asked_at", "status", "result"}


def test_deleting_the_course_deletes_its_asks(spike):
    p, q = spike
    svc, store = mk(FakeLLM(lambda purpose, text: reply(good(p, q))))
    ask(svc)
    assert store.get_meta(f"codecoach:asks:{CID}")
    svc.delete_course(CID, confirm=True)
    assert store.get_meta(f"codecoach:asks:{CID}") is None


# ------------------------------------------------------------------ integrity

def test_asking_with_a_practice_question_open_puts_it_back_ungraded():
    svc, store, cid = make_service()
    # a strict fixture course with items; add a model that answers nothing
    item = None
    for _ in range(30):
        n = svc.next(cid)
        if n.kind == "item":
            item = n.item
            break
    attempts = store.count_attempts(course_id=cid)
    out = svc.ask_start(cid, "What is the key idea of topic 3?", open_item_id=item.id)
    assert out["released_item"] == {"item_id": item.id, "released": True, "message": out["released_item"]["message"]}
    assert store.count_attempts(course_id=cid) == attempts, "nothing is graded"
    assert item.id not in {svc.next(cid).item.id for _ in range(1) if svc.next(cid).kind == "item"}
    assert svc.ask_start(cid, "What is the key idea of topic 4?")["released_item"] is None


def test_an_item_of_another_course_is_not_released():
    svc, store, cid = make_service()
    out = svc.ask_start(cid, "What is the key idea of topic 3?", open_item_id="nope")
    assert out["released_item"] is None


# ------------------------------------------------------------------ routes

def client_for(svc):
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_ask", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/codecoach")
    return TestClient(app)


def test_routes_answer_fast_while_a_slow_model_runs(spike):
    p, q = spike
    gate = threading.Event()

    def slow(purpose, text):
        gate.wait(10)
        return reply(good(p, q))

    svc, store = mk(FakeLLM(slow), background=True)
    c = client_for(svc)
    base = f"/api/plugins/codecoach/courses/{CID}"
    t0 = time.time()
    r = c.post(f"{base}/ask", json={"question": "How heavy was the golden spike?"})
    assert r.status_code == 200 and time.time() - t0 < 2
    job = r.json()["job_id"]
    assert r.json()["status"] in ("queued", "running")
    seen = set()
    for _ in range(40):
        t0 = time.time()
        g = c.get(f"{base}/ask/{job}").json()
        assert time.time() - t0 < 2
        seen.add(g["stage"])
        if g["stage"] == "Writing the answer":
            break
        time.sleep(0.05)
    assert g["status"] == "running" and g["stage"] == "Writing the answer" and g["result"] is None
    t0 = time.time()
    assert c.get(f"{base}/asks").status_code == 200 and time.time() - t0 < 2
    assert c.get("/api/plugins/codecoach/courses").status_code == 200
    gate.set()
    svc.wait_idle(10)
    done = c.get(f"{base}/ask/{job}").json()
    assert done["status"] == "done" and done["result"]["status"] == "answered"
    assert c.get(f"{base}/asks").json()["asks"][0]["job_id"] == job


def test_route_errors_are_coded():
    svc, store = mk(FakeLLM([]))
    c = client_for(svc)
    base = f"/api/plugins/codecoach/courses/{CID}"
    assert c.post(f"{base}/ask", json={"question": ""}).json()["code"] == "bad_question"
    assert c.post(f"{base}/ask", json={}).status_code == 422
    assert c.get(f"{base}/ask/ask-nope").status_code == 404
    assert c.post("/api/plugins/codecoach/courses/zzz/ask", json={"question": "hello there"}).status_code == 404
    assert c.get("/api/plugins/codecoach/courses/zzz/asks").status_code == 404


# ------------------------------------------------------------------ the repair try

def test_all_dropped_gets_one_repair_try_that_names_the_reason(spike):
    p, q = spike
    answers = iter([reply(("The spike was cast in 1871 and weighed 14.03 ounces.", [(p, q)])), reply(good(p, q))])
    svc, store = mk(FakeLLM(lambda purpose, text: next(answers)))
    res = ask(svc)
    assert res["status"] == "answered" and res["model_calls"] == 2
    assert "rejected by an automatic check" in svc.llm.calls[1].text and "1871" in svc.llm.calls[1].text
    assert [r["gate"] for r in res["removed"]] == ["ask_unsupported_fact"] and res["removed"][0]["why"]


def test_repair_is_tried_once_only(spike):
    p, q = spike
    res, svc, store = run(reply(("The spike was cast in 1871.", [(p, q)])))
    assert res["status"] == "not_covered" and res["model_calls"] == 2


def test_inflection_of_a_quote_word_is_accepted_but_a_new_word_is_not(spike):
    p, q = spike
    res, *_ = run(reply(("The golden spike weighed 14.03 ounces.", [(p, q)])))
    assert res["status"] == "answered"
    res, *_ = run(reply(("The spike weighed 14.03 ounces and was priceless.", [(p, q)])))
    assert res["status"] == "not_covered"


# ------------------------------------------------------------------ the Hermes tool

def test_codecoach_ask_tool_returns_only_checked_sentences(spike):
    import json
    from codecoach import tools
    p, q = spike
    svc, store = mk(FakeLLM(lambda purpose, text: reply(good(p, q), ("It was cast in 1871.", [(p, q)]))))
    out = json.loads(tools.make_handlers(lambda: svc)["codecoach_ask"]({"course": CID, "question": "How heavy was the golden spike?"}))
    assert out["ok"] and out["status"] == "answered" and len(out["sentences"]) == 1
    assert "1871" not in json.dumps(out) and "14.03 ounces" in out["sentences"][0]["cites"][0]["quote"]


def test_codecoach_ask_tool_not_covered_and_refusals():
    import json
    from codecoach import tools
    svc, store = mk(FakeLLM([]))
    h = tools.make_handlers(lambda: svc)["codecoach_ask"]
    out = json.loads(h({"course": CID, "question": "Who won the 1868 presidential election?"}))
    assert out["status"] == "not_covered" and out["sentences"] == [] and svc.llm.calls == []
    assert json.loads(h({"course": CID, "question": ""}))["error"] == "bad_request"
    svc2, _ = mk(FakeLLM([]), mode="open")
    assert json.loads(tools.make_handlers(lambda: svc2)["codecoach_ask"]({"course": CID, "question": "hello there friend"}))["status"] == "unavailable"


# ------------------------------------------------------------------ code-side quote extension (S2c)

USING = "Using $400 of his own gold, he had the William T. Garratt Foundry of San Francisco cast a golden spike."
WHO = "David Hewes had a golden spike cast using $400 of his own gold."


def _who(extra=None):
    svc, store = mk(FakeLLM([]))
    p = pid(store, "14.03 ounces")
    return svc, store, p


FRIEND = "David Hewes was a friend of Central Pacific President Leland Stanford."
WRONG_SENTENCE = "Unable to persuade anyone to finance the casting of a solid gold or silver section of rail"


def test_extension_adds_the_passage_sentence_that_holds_the_missing_name():
    svc, store, p = _who()
    res = ask_with(svc, reply((FRIEND, [(p, WRONG_SENTENCE)])), "Who was David Hewes a friend of?")
    assert res["status"] == "answered" and res["removed"] == []
    cites = res["sentences"][0]["cites"]
    assert [c.get("extended_by_code", False) for c in cites] == [False, True]
    assert "Leland Stanford" in cites[1]["quote"] and cites[1]["passage_id"] == p
    assert res["sentences"][0]["text"] == FRIEND                  # the answer sentence is never edited


def test_extension_never_stitches_facts_from_two_sentences():
    """'David Hewes' is in one sentence and '$400' in the next ('Using $400 of his own gold, he had...'). True
    here, but code cannot know who 'he' is, so it must not join them. Live probe that found it: 'Hewes paid for
    the gold spike in 1869' passed by borrowing 1869 from a sentence about the railroad being finished."""
    svc, store, p = _who()
    res = ask_with(svc, reply((WHO, [(p, USING)])), "Who paid for the golden spike to be cast?")
    assert res["status"] == "not_covered" and gates(res) == ["ask_unsupported_fact"]
    svc, store, p = _who()
    res = ask_with(svc, reply(("David Hewes paid for the golden spike in 1869.",
                               [(p, "Unable to persuade anyone to finance the casting of a solid gold")])),
                   "When did Hewes pay for the spike?")
    assert res["status"] == "not_covered"


def test_extended_quote_offsets_are_exact():
    svc, store, p = _who()
    res = ask_with(svc, reply((FRIEND, [(p, WRONG_SENTENCE)])), "Who was David Hewes a friend of?")
    text = store.get_passage(p).text
    for c in res["sentences"][0]["cites"]:
        assert text[c["quote_start"]:c["quote_end"]] == c["quote"]
    ext = res["sentences"][0]["cites"][1]
    assert ext["quote"].startswith("San Francisco contractor, David Hewes") and ext["quote"].endswith("1869.")


def test_extension_refuses_a_fact_found_only_in_an_uncited_passage():
    svc, store, p = _who()
    other = pid(store, "Tilden G. Abbot")                         # holds "David Hewes", but is not cited
    assert other != p
    res = ask_with(svc, reply(("Tilden G. Abbot had a golden spike cast using $400 of his own gold.", [(p, USING)])),
                   "Who paid for the golden spike to be cast?")
    assert res["status"] == "not_covered" and gates(res) == ["ask_unsupported_fact"]
    assert not res["sentences"]


def test_extension_refuses_a_near_miss_spelling():
    svc, store, p = _who()
    res = ask_with(svc, reply((WHO.replace("Hewes", "Hewis"), [(p, USING)])), "Who paid for the golden spike to be cast?")
    assert res["status"] == "not_covered" and gates(res) == ["ask_unsupported_fact"]


def test_extension_refuses_a_number_the_passage_does_not_hold():
    svc, store, p = _who()
    res = ask_with(svc, reply(("David Hewes had a golden spike cast using $450 of his own gold.", [(p, USING)])),
                   "Who paid for the golden spike to be cast?")
    assert res["status"] == "not_covered" and gates(res) == ["ask_unsupported_fact"]


def test_extension_does_not_rescue_an_unsupported_word():
    svc, store, p = _who()
    res = ask_with(svc, reply(("David Hewes had an incredibly beautiful spike cast using $400 of his own gold.",
                               [(p, USING)])), "Who paid for the golden spike to be cast?")
    assert res["status"] == "not_covered"


def test_relevant_sentences_come_first_and_none_is_dropped_for_overlap():
    svc, store, p = _who()
    a = ("The spike was 5 5/8 inches long.", [(p, "The spike was 5 5/8 inches long, weighed 14.03 ounces and was made")])
    b = ("The spike weighed 14.03 ounces.", [(p, "The spike was 5 5/8 inches long, weighed 14.03 ounces and was made")])
    res = ask_with(svc, reply(a, b), "How much did the golden spike weigh?")
    assert [s["text"] for s in res["sentences"]] == [b[0], a[0]]


def test_ranking_sends_twelve_passages_first():
    assert R.TOP_K == 12 and R.TOP_RETRY > R.TOP_K
