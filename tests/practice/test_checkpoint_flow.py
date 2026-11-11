"""Test (checkpoint) over HTTP on a bare router: REAL sandbox (python and c), scripted FakeLLM.

Covers the flow, scoring, answer changes, unanswered questions, finish twice, shrinking, the building state, that no
route waits on the model, and (in test_checkpoint_leaks.py) that an open test leaks nothing.
"""

import threading
import time

import pytest
from prac_helpers import C_CODE, C_EXPL, PY_EXPL, make_svc, needs_sb, reply

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.practice import engine as E  # noqa: E402
from codecoach.practice.api import register  # noqa: E402
from codecoach.practice.checkpoint import checkpoint_engine_for  # noqa: E402

B = "/api/plugins/codecoach"
Q_KEYS = {"n", "drill_id", "topic", "answered"}
GET_KEYS = {"id", "language", "status", "ready_count", "size", "questions", "result"}
FINISH_KEYS = {"score", "total", "by_topic", "weak_topics", "review"}


def py(k, tag="Total"):
    """A python drill whose output is computed: (reply, stdout). Distinct k gives distinct code."""
    return reply(f'n = sum(range({k}))\nprint(f"{tag}: {{n + 1}}")\n'), f"{tag}: {sum(range(k)) + 1}\n"


def c_expl(k):
    """An explanation that is true for cdrill(k): C_EXPL itself is only true for k = 4 (it says 30)."""
    return [f"The loop adds the squares of 1 to {k}.", f"That makes {sum(i * i for i in range(1, k + 1))}, and printf shows it."]


def cdrill(k, explanation=None):
    code = C_CODE.replace("i <= 4", f"i <= {k}")
    return reply(code, explanation=explanation or c_expl(k)), f"{sum(i * i for i in range(1, k + 1))}\n"


def seq(*replies):
    q = list(replies)
    return lambda purpose, text: q.pop(0) if q else None


def client(script, *, background=False):
    svc = make_svc(FakeLLM(script), background=background)
    router = APIRouter()
    register(router, lambda: svc)
    app = FastAPI()
    app.include_router(router, prefix=B)
    return TestClient(app), svc


def start(c, language="python", size=None):
    body = {"language": language} if size is None else {"language": language, "size": size}
    r = c.post(f"{B}/tests", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def get(c, tid):
    return c.get(f"{B}/tests/{tid}").json()


def put(c, tid, n, text):
    r = c.post(f"{B}/tests/{tid}/answer", json={"n": n, "prediction": text})
    assert r.status_code == 200 and r.json() == {"recorded": True}, r.text


def keys_of(svc, test):
    """Server-side stdout of each question (the test never shows these before finish)."""
    eng = E.engine_for(svc)
    return [eng.record(q["drill_id"])["stdout"] for q in test["questions"]]


def wait_for(fn, secs=30.0):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def test_routes_exist_on_a_bare_router():
    router = APIRouter()
    register(router, lambda: None)
    got = sorted((sorted(r.methods)[0], r.path) for r in router.routes if r.path.startswith("/tests"))
    assert got == [("GET", "/tests"), ("GET", "/tests/{test_id}"), ("POST", "/tests"),
                   ("POST", "/tests/{test_id}/answer"), ("POST", "/tests/{test_id}/finish")]


def test_bad_requests_and_unknown_ids():
    c, _ = client(seq())
    for body, code in (({"language": "nope", "size": 5}, "bad_language"), ({"language": "python", "size": 2}, "bad_size"),
                       ({"language": "python", "size": 11}, "bad_size")):
        r = c.post(f"{B}/tests", json=body)
        assert r.status_code == 422 and r.json()["code"] == code, (body, r.text)
    assert c.post(f"{B}/tests", content="not json").status_code == 422
    assert c.get(f"{B}/tests/t_none").status_code == 404
    assert c.post(f"{B}/tests/t_none/answer", json={"n": 1, "prediction": "x"}).json()["code"] == "unknown_test"
    assert c.post(f"{B}/tests/t_none/finish").status_code == 404
    assert c.get(f"{B}/tests").json() == {"tests": []}


def test_no_model_refuses_a_test_that_needs_building():
    svc = make_svc(None)
    router = APIRouter()
    register(router, lambda: svc)
    app = FastAPI()
    app.include_router(router, prefix=B)
    r = TestClient(app).post(f"{B}/tests", json={"language": "python"})
    assert r.status_code == 503 and r.json()["code"] == "no_model"


# ------------------------------------------------------------------ the flow

@needs_sb
def test_three_question_test_end_to_end_python():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3)]))
    made = start(c, "python", 3)
    assert set(made) == {"test_id", "status"} and made["status"] == "ready"
    tid = made["test_id"]

    t = get(c, tid)
    assert set(t) == GET_KEYS and t["status"] == "ready" and t["ready_count"] == 3 and t["size"] == 3
    assert t["result"] is None and all(set(q) == Q_KEYS and q["answered"] is False for q in t["questions"])
    assert [q["n"] for q in t["questions"]] == [1, 2, 3]
    assert [q["topic"] for q in t["questions"]] == ["variables", "arithmetic", "strings"]
    keys = keys_of(svc, t)

    put(c, tid, 1, keys[0])                           # right
    put(c, tid, 2, "wrong")                           # wrong
    put(c, tid, 3, keys[2].rstrip("\n") + "  \n\n")   # right (trailing spaces and blank lines are forgiven)
    assert [q["answered"] for q in get(c, tid)["questions"]] == [True, True, True]

    r = c.post(f"{B}/tests/{tid}/finish")
    assert r.status_code == 200
    res = r.json()
    assert set(res) == FINISH_KEYS and res["score"] == 2 and res["total"] == 3
    assert res["by_topic"] == [{"topic": "variables", "title": "Variables", "correct": 1, "total": 1},
                               {"topic": "arithmetic", "title": "Arithmetic", "correct": 0, "total": 1},
                               {"topic": "strings", "title": "Strings", "correct": 1, "total": 1}]
    assert res["weak_topics"] == ["arithmetic"]
    assert [x["correct"] for x in res["review"]] == [True, False, True]
    row = res["review"][1]
    assert set(row) == {"n", "topic", "correct", "code", "your_answer", "expected_stdout", "explanation"}
    assert row["n"] == 2 and row["your_answer"] == "wrong" and row["expected_stdout"] == keys[1]
    assert row["explanation"] == PY_EXPL and "sum(range(" in row["code"]

    done = get(c, tid)
    assert done["status"] == "done" and done["result"] == res
    lst = c.get(f"{B}/tests").json()["tests"]
    assert len(lst) == 1 and set(lst[0]) == {"id", "language", "status", "score", "total", "created_at"}
    assert lst[0]["id"] == tid and lst[0]["status"] == "done" and lst[0]["score"] == 2 and lst[0]["total"] == 3
    late = c.post(f"{B}/tests/{tid}/answer", json={"n": 1, "prediction": "x"})
    assert late.status_code == 409 and late.json()["code"] == "test_done"


@needs_sb
def test_three_question_test_end_to_end_c():
    c, svc = client(seq(*[cdrill(3 + i)[0] for i in range(3)]))
    tid = start(c, "c", 3)["test_id"]
    t = get(c, tid)
    assert t["status"] == "ready" and t["ready_count"] == 3
    keys = keys_of(svc, t)
    assert keys == [cdrill(3 + i)[1] for i in range(3)]          # the sandbox ran the C programs for real
    for i in range(3):
        put(c, tid, i + 1, keys[i])
    res = c.post(f"{B}/tests/{tid}/finish").json()
    assert res["score"] == 3 and res["weak_topics"] == [] and all(x["correct"] for x in res["review"])
    assert "#include" in res["review"][0]["code"] and res["review"][0]["explanation"] == c_expl(3)


@needs_sb
def test_review_leaves_out_an_explanation_that_disagrees_with_the_real_output():
    """C_EXPL says "That makes 30"; with the loop to 3 or to 5 the program prints 14 or 55. Only the true one is shown."""
    from codecoach.practice.explain_check import LEFT_OUT
    c, svc = client(seq(cdrill(3, C_EXPL)[0], cdrill(4, C_EXPL)[0], cdrill(5, C_EXPL)[0]))
    tid = start(c, "c", 3)["test_id"]
    t = get(c, tid)
    assert t["status"] == "ready" and keys_of(svc, t) == ["14\n", "30\n", "55\n"]
    for i, key in enumerate(["14", "30", "55"]):
        put(c, tid, i + 1, key)
    res = c.post(f"{B}/tests/{tid}/finish").json()
    assert res["score"] == 3                                             # the key is the real output either way
    assert [r["explanation"] for r in res["review"]] == [[LEFT_OUT], C_EXPL, [LEFT_OUT]]
    assert [r["expected_stdout"] for r in res["review"]] == ["14\n", "30\n", "55\n"]


@needs_sb
def test_mixed_score_and_weak_topics_are_topics_under_half():
    # size 10: variables and arithmetic get two questions each, the other six topics one each
    c, svc = client(seq(*[py(10 + i)[0] for i in range(10)]))
    tid = start(c, "python", 10)["test_id"]
    t = get(c, tid)
    topics = [q["topic"] for q in t["questions"]]
    assert topics == ["variables", "arithmetic", "strings", "conditionals", "loops", "functions", "lists", "tracing",
                      "variables", "arithmetic"]
    keys = keys_of(svc, t)
    # variables 1 of 2 (exactly half: not weak), arithmetic 0 of 2 (weak), strings 0 of 1 (weak), the rest right
    wrong = {2, 3, 10}
    for i in range(10):
        if i + 1 == 9:
            put(c, tid, i + 1, "nope")                  # second variables question wrong, first right
        elif i + 1 in wrong:
            put(c, tid, i + 1, "nope")
        else:
            put(c, tid, i + 1, keys[i])
    res = c.post(f"{B}/tests/{tid}/finish").json()
    by = {b["topic"]: (b["correct"], b["total"]) for b in res["by_topic"]}
    assert by["variables"] == (1, 2) and by["arithmetic"] == (0, 2) and by["strings"] == (0, 1)
    assert all(by[k] == (1, 1) for k in ("conditionals", "loops", "functions", "lists", "tracing"))
    assert res["score"] == 6 and res["total"] == 10
    assert res["weak_topics"] == ["arithmetic", "strings"]
    assert [b["topic"] for b in res["by_topic"]] == ["variables", "arithmetic", "strings", "conditionals", "loops",
                                                     "functions", "lists", "tracing"]


@needs_sb
def test_an_answer_can_be_changed_until_finish():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3)]))
    tid = start(c, "python", 3)["test_id"]
    keys = keys_of(svc, get(c, tid))
    put(c, tid, 1, "wrong at first")
    put(c, tid, 1, keys[0])                           # changed to the right one
    put(c, tid, 2, keys[1])
    put(c, tid, 2, "changed my mind")                 # changed to a wrong one
    put(c, tid, 3, keys[2])
    res = c.post(f"{B}/tests/{tid}/finish").json()
    assert [x["correct"] for x in res["review"]] == [True, False, True]
    assert res["review"][1]["your_answer"] == "changed my mind"


@needs_sb
def test_unanswered_questions_count_as_wrong_and_say_no_answer():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3)]))
    tid = start(c, "python", 3)["test_id"]
    keys = keys_of(svc, get(c, tid))
    put(c, tid, 1, keys[0])
    put(c, tid, 3, "   \n")                           # a blank answer is no answer
    res = c.post(f"{B}/tests/{tid}/finish").json()
    assert res["score"] == 1 and res["total"] == 3
    assert [x["your_answer"] for x in res["review"]] == [keys[0], "No answer", "No answer"]
    assert [x["correct"] for x in res["review"]] == [True, False, False]
    assert res["weak_topics"] == ["arithmetic", "strings"]
    assert res["review"][1]["expected_stdout"] == keys[1]       # the key is shown in the review


@needs_sb
def test_finish_twice_returns_the_same_result():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3)]))
    tid = start(c, "python", 3)["test_id"]
    keys = keys_of(svc, get(c, tid))
    put(c, tid, 1, keys[0])
    a = c.post(f"{B}/tests/{tid}/finish")
    log1 = list(svc.store.get_meta("practice:attempts", []))
    b = c.post(f"{B}/tests/{tid}/finish")
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert svc.store.get_meta("practice:attempts", []) == log1          # nothing counted twice


@needs_sb
def test_answer_validation():
    c, _ = client(seq(*[py(10 + i)[0] for i in range(3)]))
    tid = start(c, "python", 3)["test_id"]
    for body in ({"n": 0, "prediction": "x"}, {"n": 4, "prediction": "x"}, {"n": "one", "prediction": "x"},
                 {"n": 1, "prediction": 5}):
        r = c.post(f"{B}/tests/{tid}/answer", json=body)
        assert r.status_code == 422, (body, r.text)
    big = c.post(f"{B}/tests/{tid}/answer", json={"n": 1, "prediction": "x" * 5000})
    assert big.status_code == 413 and big.json()["code"] == "too_large"


# ------------------------------------------------------------------ stored drills first; unavailable drills

@needs_sb
def test_unseen_stored_drills_come_first_and_are_held_back_from_practice():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(2)], *[py(20 + i)[0] for i in range(2)]))
    nxt = c.post(f"{B}/practice/next", json={"language": "python", "topic": "strings"}).json()
    stored = set(E.engine_for(svc)._index())
    assert len(stored) == 2                                       # the drill and its one prefetched neighbour
    tid = start(c, "python", 3)["test_id"]
    t = get(c, tid)
    first = t["questions"][0]
    assert first["topic"] == "strings" and first["drill_id"] in stored   # reused, not rebuilt
    assert [q["topic"] for q in t["questions"]] == ["strings", "variables", "arithmetic"]
    assert len(E.engine_for(svc)._index()) == 4                   # only variables and arithmetic were built
    # the neighbour that is not in the test is still served by practice; the held one never is
    held = first["drill_id"]
    served = c.post(f"{B}/practice/next", json={"language": "python", "topic": "strings"}).json()["drill_id"]
    assert served != held and nxt["drill_id"] in stored


def _per_topic_script(fail_topic, fail_calls):
    """Good, distinct drills; the first `fail_calls` calls for `fail_topic` give a bad reply."""
    n = {"k": 10, "bad": 0}

    def script(purpose, text):
        if f"({fail_topic})" in text and n["bad"] < fail_calls:
            n["bad"] += 1
            return None
        n["k"] += 1
        return py(n["k"])[0]
    return script


@needs_sb
def test_an_unavailable_drill_is_replaced_once():
    # 2 calls = both build attempts (first build and one rebuild) of the first arithmetic drill; the replacement then succeeds
    c, _ = client(_per_topic_script("arithmetic", 2))
    tid = start(c, "python", 3)["test_id"]
    t = get(c, tid)
    assert t["size"] == 3 and t["ready_count"] == 3 and t["status"] == "ready" and "note" not in t
    assert [q["topic"] for q in t["questions"]] == ["variables", "arithmetic", "strings"]


@needs_sb
def test_a_drill_that_fails_twice_shrinks_the_test_and_says_so():
    c, svc = client(_per_topic_script("arithmetic", 99))
    r = start(c, "python", 3)
    t = get(c, r["test_id"])
    assert t["status"] == "ready" and t["size"] == 2 and t["ready_count"] == 2
    assert [q["topic"] for q in t["questions"]] == ["variables", "strings"] and [q["n"] for q in t["questions"]] == [1, 2]
    assert t["note"] == "1 question could not be written, so this test has 2 questions instead of 3."
    keys = keys_of(svc, t)
    put(c, r["test_id"], 1, keys[0])
    put(c, r["test_id"], 2, keys[1])
    res = c.post(f"{B}/tests/{r['test_id']}/finish").json()
    assert res["score"] == 2 and res["total"] == 2


# ------------------------------------------------------------------ the building state, and never waiting on the model

@needs_sb
def test_building_state_with_a_gated_fake():
    gate = threading.Semaphore(0)
    n = {"k": 10}

    def script(purpose, text):
        assert gate.acquire(timeout=30)
        n["k"] += 1
        return py(n["k"])[0]

    c, svc = client(script, background=True)
    made = start(c, "python", 3)
    tid = made["test_id"]
    assert made["status"] == "building"
    t = get(c, tid)
    assert t["status"] == "building" and t["ready_count"] == 0 and t["size"] == 3 and len(t["questions"]) == 3
    r = c.post(f"{B}/tests/{tid}/answer", json={"n": 1, "prediction": "x"})          # not ready yet
    assert r.status_code == 409 and r.json()["code"] == "drill_not_ready"
    r = c.post(f"{B}/tests/{tid}/finish")
    assert r.status_code == 409 and r.json()["code"] == "test_not_ready"
    assert get(c, tid)["result"] is None
    dv = c.get(f"{B}/practice/drills/{t['questions'][0]['drill_id']}").json()
    assert dv["status"] == "building" and dv["code"] == ""

    gate.release()
    wait_for(lambda: get(c, tid)["ready_count"] == 1)
    t = get(c, tid)
    assert t["status"] == "building" and t["ready_count"] == 1
    put(c, tid, 1, "early answer")                                                    # a ready question can be answered

    for _ in range(12):                               # spare permits: a rebuild on a loaded box must not starve
        gate.release()
    wait_for(lambda: get(c, tid)["status"] == "ready")
    t = get(c, tid)
    why = [(r["id"], r["status"], r["reason"]) for r in (svc.store.get_meta(f"drill:{d}") for d in svc.store.get_meta("practice:index"))]
    assert t["ready_count"] == 3 and t["questions"][0]["answered"] is True, (t, why)
    assert svc.wait_idle(30)


@needs_sb
def test_a_route_never_waits_on_a_slow_model():
    n = {"k": 10}

    def slow(purpose, text):
        time.sleep(2.0)
        n["k"] += 1
        return py(n["k"])[0]

    c, svc = client(slow, background=True)
    timings = {}

    def timed(name, fn):
        t0 = time.time()
        r = fn()
        timings[name] = time.time() - t0
        return r

    made = timed("create", lambda: c.post(f"{B}/tests", json={"language": "python", "size": 3})).json()
    tid = made["test_id"]
    assert made["status"] == "building"
    t = timed("get", lambda: c.get(f"{B}/tests/{tid}")).json()
    assert t["status"] == "building"
    timed("list", lambda: c.get(f"{B}/tests"))
    timed("answer", lambda: c.post(f"{B}/tests/{tid}/answer", json={"n": 1, "prediction": "x"}))
    timed("finish", lambda: c.post(f"{B}/tests/{tid}/finish"))
    timed("drill", lambda: c.get(f"{B}/practice/drills/{t['questions'][0]['drill_id']}"))
    timed("practice_answer", lambda: c.post(f"{B}/practice/drills/{t['questions'][0]['drill_id']}/answer",
                                            json={"prediction": "x"}))
    timed("topics", lambda: c.get(f"{B}/practice", params={"language": "python"}))
    timed("unknown", lambda: c.get(f"{B}/tests/t_none"))
    assert max(timings.values()) < 1.0, timings
    assert svc.wait_idle(90)


@needs_sb
def test_engine_caches_on_the_service():
    _, svc = client(seq())
    assert checkpoint_engine_for(svc) is checkpoint_engine_for(svc)
