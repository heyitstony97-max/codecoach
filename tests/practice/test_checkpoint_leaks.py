"""No leaks: while a test is open, no route reveals the stdout, the explanation or the correctness of a drill in it.
After finish the same drills open up to the practice routes."""

import json

import pytest
from prac_helpers import PY_EXPL, needs_sb
from test_checkpoint_flow import B, client, get, keys_of, put, py, seq, start

from codecoach.practice import engine as E


def setup_open_test(extra=0):
    """An open python test of 3 questions (plus `extra` spare drills for practice). Returns client, svc, tid, keys."""
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3 + extra)]))
    tid = start(c, "python", 3)["test_id"]
    t = get(c, tid)
    return c, svc, tid, t, keys_of(svc, t)


def assert_clean(blob, keys):
    text = blob if isinstance(blob, str) else json.dumps(blob)
    for k in keys:
        assert k.strip() not in text, f"the key {k!r} leaked"
    for line in PY_EXPL:
        assert line not in text, "an explanation leaked"
    assert "expected_stdout" not in text and "explanation" not in text and '"stdout"' not in text


@needs_sb
def test_every_test_route_is_clean_while_the_test_is_open():
    c, svc, tid, t, keys = setup_open_test()
    seen = [t, get(c, tid)]
    r = c.post(f"{B}/tests/{tid}/answer", json={"n": 1, "prediction": keys[0]})            # right
    assert r.json() == {"recorded": True}
    seen.append(r.json())
    w = c.post(f"{B}/tests/{tid}/answer", json={"n": 2, "prediction": "wrong"})            # wrong
    assert w.json() == r.json() == {"recorded": True}                                      # same shape: no verdict
    seen += [get(c, tid), c.get(f"{B}/tests").json()]
    for blob in seen:
        assert_clean(blob, keys)
    lst = c.get(f"{B}/tests").json()["tests"][0]
    assert lst["status"] == "ready" and lst["score"] is None
    t2 = get(c, tid)
    assert t2["result"] is None and set(t2["questions"][0]) == {"n", "drill_id", "topic", "answered"}


@needs_sb
def test_the_create_response_is_clean():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3)]))
    made = c.post(f"{B}/tests", json={"language": "python", "size": 3})
    assert set(made.json()) == {"test_id", "status"}
    keys = keys_of(svc, get(c, made.json()["test_id"]))
    assert_clean(made.text, keys)


@needs_sb
def test_practice_drill_route_hides_the_key_while_the_test_is_open():
    c, svc, tid, t, keys = setup_open_test()
    for q in t["questions"]:
        v = c.get(f"{B}/practice/drills/{q['drill_id']}")
        assert v.status_code == 200
        j = v.json()
        assert set(j) == {"id", "language", "topic", "kind", "status", "code", "stdin", "line_count", "reason", "answered"}
        assert j["answered"] is False                                  # a test answer does not mark the drill answered
        assert_clean(v.text, keys)
    # even after the learner answered inside the test, the drill route shows no verdict
    put(c, tid, 1, keys[0])
    again = c.get(f"{B}/practice/drills/{t['questions'][0]['drill_id']}")
    assert again.json()["answered"] is False
    assert_clean(again.text, keys)


@needs_sb
def test_practice_answer_route_refuses_and_reveals_nothing_while_the_test_is_open():
    c, svc, tid, t, keys = setup_open_test()
    for i, q in enumerate(t["questions"]):
        for guess in (keys[i], "wrong", ""):
            r = c.post(f"{B}/practice/drills/{q['drill_id']}/answer", json={"prediction": guess})
            assert r.status_code == 409 and r.json()["code"] == "in_test", r.text
            assert_clean(r.text, keys)
    assert get(c, tid)["questions"][0]["answered"] is False            # the refused answers recorded nothing
    assert svc.store.get_meta("practice:attempts", []) == []


@needs_sb
def test_practice_topics_and_next_do_not_hand_out_test_drills():
    c, svc, tid, t, keys = setup_open_test(extra=3)
    held = {q["drill_id"] for q in t["questions"]}
    topics = c.get(f"{B}/practice", params={"language": "python"}).json()["topics"]
    assert all(row["ready"] == 0 and row["seen"] == 0 for row in topics)
    for q in t["questions"]:
        got = c.post(f"{B}/practice/next", json={"language": "python", "topic": q["topic"]})
        assert got.status_code == 200 and got.json()["drill_id"] not in held
        assert_clean(got.text, keys)


@needs_sb
def test_drills_open_up_after_finish():
    c, svc, tid, t, keys = setup_open_test()
    put(c, tid, 1, keys[0])
    res = c.post(f"{B}/tests/{tid}/finish").json()
    ids = [q["drill_id"] for q in t["questions"]]
    # the practice drill route is the same whitelist; the key now comes through the practice answer route
    for i, did in enumerate(ids):
        v = c.get(f"{B}/practice/drills/{did}").json()
        assert v["status"] == "ready" and v["answered"] is True
        a = c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": keys[i]})
        assert a.status_code == 200 and a.json()["correct"] is True and a.json()["expected_stdout"] == keys[i]
        assert a.json()["explanation"] == PY_EXPL
    topics = c.get(f"{B}/practice", params={"language": "python"}).json()["topics"]
    assert sum(r["seen"] for r in topics) == 3
    first = {r["id"]: r for r in topics}
    assert first["variables"]["correct"] == 1                                # first answer = the test answer (right)
    assert first["arithmetic"]["correct"] == 0 and first["arithmetic"]["seen"] == 1     # unanswered counted as wrong
    assert res["score"] == 1


@needs_sb
def test_engine_level_view_and_answer_match_the_routes():
    """The routes are thin: the same guarantees hold on the engine directly."""
    c, svc, tid, t, keys = setup_open_test()
    eng = E.engine_for(svc)
    did = t["questions"][0]["drill_id"]
    assert "stdout" not in eng.view(did) and "explanation" not in eng.view(did)
    from codecoach.service import ServiceError
    with pytest.raises(ServiceError) as ei:
        eng.answer(did, keys[0])
    assert ei.value.code == "in_test" and keys[0].strip() not in str(ei.value)
