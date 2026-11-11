"""Side channels around an open test (real sandbox), beyond the per-route leak tests.

A. the practice topic counts must not move while a test is open (they would give away right/wrong per answer)
B. a second test started while the first is open must not share drills with it, and finishing the second must
   not open up the first one's drills
"""

from prac_helpers import needs_sb
from test_checkpoint_flow import B, client, get, keys_of, py, seq, start

pytestmark = needs_sb


def topics(c):
    return c.get(f"{B}/practice", params={"language": "python"}).json()["topics"]


def test_a_topic_counts_do_not_move_while_a_test_is_open():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(3)]))
    tid = start(c, "python", 3)["test_id"]
    t = get(c, tid)
    keys = keys_of(svc, t)
    before = topics(c)
    assert c.post(f"{B}/tests/{tid}/answer", json={"n": 1, "prediction": keys[0]}).json() == {"recorded": True}
    after_right = topics(c)
    assert c.post(f"{B}/tests/{tid}/answer", json={"n": 2, "prediction": "wrong"}).json() == {"recorded": True}
    after_wrong = topics(c)
    assert before == after_right == after_wrong, "topic counts moved while the test is open"
    done = c.post(f"{B}/tests/{tid}/finish").json()
    assert done["score"] == 1 and done["total"] == 3
    assert topics(c) != before, "counts should include the test once it is finished"


def test_b_a_second_open_test_shares_no_drills_and_finishing_it_opens_nothing_of_the_first():
    c, svc = client(seq(*[py(10 + i)[0] for i in range(6)]))
    a = start(c, "python", 3)["test_id"]
    ta = get(c, a)
    b = start(c, "python", 3)["test_id"]
    tb = get(c, b)
    ids_a = {q["drill_id"] for q in ta["questions"]}
    ids_b = {q["drill_id"] for q in tb["questions"]}
    assert len(ids_a) == 3 and len(ids_b) == 3
    assert not (ids_a & ids_b), f"two open tests share drills: {ids_a & ids_b}"
    keys_a = keys_of(svc, ta)
    fin = c.post(f"{B}/tests/{b}/finish")
    assert fin.status_code == 200, fin.text
    for did in ids_a:
        view = c.get(f"{B}/practice/drills/{did}")
        for k in keys_a:
            assert k.strip() not in view.text, "finishing test B revealed a key of test A"
        assert "explanation" not in view.text and "expected_stdout" not in view.text
        r = c.post(f"{B}/practice/drills/{did}/answer", json={"prediction": "x"})
        assert r.status_code == 409, r.text
        for k in keys_a:
            assert k.strip() not in r.text
    still = get(c, a)
    assert still["status"] == "ready" and still["result"] is None
