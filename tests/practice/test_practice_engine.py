"""The drill flow end to end (REAL sandbox, scripted FakeLLM), P7, grading, counts, and the language seam."""

import json

import pytest
from prac_helpers import (C_CODE, C_EXPL, C_OUT, PY_CODE, PY_EXPL, PY_OUT, engine, make_svc, needs_sb, reply)

from codecoach.core.llm import FakeLLM
from codecoach.practice import engine as E
from codecoach.service import ServiceError

VIEW_KEYS = {"id", "language", "topic", "kind", "status", "code", "stdin", "line_count", "reason", "answered"}
ANSWER_KEYS = {"correct", "expected_stdout", "lines", "explanation"}


def other(code, tag):
    return code.replace("Total", tag)


# ------------------------------------------------------------------ pure grading (no sandbox needed)

def test_grade_exact_match():
    g = E.grade_prediction("1\n3\n6\n", "1\n3\n6")
    assert g["correct"] and g["lines"] == [{"n": i + 1, "expected": e, "got": e, "ok": True}
                                           for i, e in enumerate("136")]
    assert [r["ok"] for r in g["lines"]] == [True, True, True]


def test_grade_forgives_only_trailing_spaces_and_trailing_blank_lines():
    out = "1\n3\n6\n"
    assert E.grade_prediction(out, "1  \n3 \n6   ")["correct"]            # trailing spaces per line
    assert E.grade_prediction(out, "1\n3\n6\n\n\n")["correct"]            # trailing blank lines
    assert E.grade_prediction(out, "1\n3\n6\n  \n \n")["correct"]         # a trailing line of only spaces is blank
    assert E.grade_prediction("a b\n", "a b  \n\n")["correct"]


@pytest.mark.parametrize("pred", [" 1\n3\n6", "1\n3\n6\t", "1\n\n3\n6", "\n1\n3\n6", "1\n3", "1\n3\n6\n7", "1\n3\n6 x",
                                  "1 3 6", "A\n3\n6", "1\r\n3\r\n6", "", "1\n3\n 6"])
def test_grade_forgives_nothing_else(pred):
    assert not E.grade_prediction("1\n3\n6\n", pred)["correct"], repr(pred)


def test_grade_case_inner_spaces_and_blank_output_lines():
    assert not E.grade_prediction("Hello\n", "hello")["correct"]
    assert not E.grade_prediction("a  b\n", "a b")["correct"]
    assert E.grade_prediction("a\n\nb\n", "a\n\nb")["correct"]
    assert not E.grade_prediction("a\n\nb\n", "a\nb")["correct"]


def test_grade_per_line_rows_and_missing_lines():
    g = E.grade_prediction("1\n3\n6\n", "1\n2\n")
    assert not g["correct"]
    assert g["lines"] == [{"n": 1, "expected": "1", "got": "1", "ok": True},
                          {"n": 2, "expected": "3", "got": "2", "ok": False},
                          {"n": 3, "expected": "6", "got": "", "ok": False}]
    g = E.grade_prediction("1\n", "1\n2\n3")
    assert [r["ok"] for r in g["lines"]] == [True, False, False] and g["lines"][2]["expected"] == ""


def test_grade_rows_are_capped_but_correct_is_not():
    big = "\n".join(str(i) for i in range(500))
    g = E.grade_prediction("1\n", big)
    assert len(g["lines"]) == E.MAX_ROWS and not g["correct"]


def test_code_hash_ignores_whitespace_only():
    assert E.code_hash("a = 1\nprint( a )\n") == E.code_hash("a  = 1\n\n  print( a )")
    assert E.code_hash("a = 1") != E.code_hash("a = 2")


# ------------------------------------------------------------------ the language seam

def test_language_seam_knows_c_and_python():
    langs = E._languages()
    assert "c" in langs and "python" in langs
    name, style = langs["c"]
    assert name == "C" and style


def test_bad_language_and_topic_are_plain_422s():
    _svc, eng, llm = engine()
    with pytest.raises(ServiceError) as ei:
        eng.next("klingon", "loops")
    assert (ei.value.status, ei.value.code) == (422, "bad_language")
    assert ei.value.message == "That language is not set up on this machine yet."
    with pytest.raises(ServiceError) as ei:
        eng.next("python", "knitting")
    assert (ei.value.status, ei.value.code) == (422, "bad_topic")
    with pytest.raises(ServiceError) as ei:
        eng.topics("klingon")
    assert ei.value.code == "bad_language"
    assert llm.calls == []


def test_topics_are_the_fixed_eight_with_zero_counts():
    _svc, eng, _llm = engine()
    j = eng.topics("python")
    assert j["language"] == "python" and j["sandbox"] == E.sandbox_status()
    assert [(t["id"], t["title"]) for t in j["topics"]] == [
        ("variables", "Variables"), ("arithmetic", "Arithmetic"), ("strings", "Strings"), ("conditionals", "If and else"),
        ("loops", "Loops"), ("functions", "Functions"), ("lists", "Lists and arrays"), ("tracing", "Mixed tracing")]
    assert all(set(t) == {"id", "title", "seen", "correct", "ready"} and t["seen"] == t["correct"] == t["ready"] == 0
               for t in j["topics"])


def test_no_model_is_a_plain_503_and_stored_drills_still_serve():
    svc = make_svc(None)
    eng = E.engine_for(svc)
    if E.sandbox_status() == "bwrap":
        with pytest.raises(ServiceError) as ei:
            eng.next("python", "loops")
        assert (ei.value.status, ei.value.code) == (503, "no_model")


def test_engine_is_cached_on_the_service():
    svc = make_svc(FakeLLM([]))
    assert E.engine_for(svc) is E.engine_for(svc)


# ------------------------------------------------------------------ the good drill, end to end

@needs_sb
def test_good_python_drill_end_to_end():
    svc, eng, llm = engine(reply(), reply(other(PY_CODE, "Sum")))
    got = eng.next("python", "loops")
    assert set(got) == {"drill_id", "status"} and got["status"] == "ready" and got["drill_id"].startswith("d_")
    did = got["drill_id"]

    v = eng.view(did)                                          # the view before an answer
    assert set(v) == VIEW_KEYS
    assert v == {"id": did, "language": "python", "topic": "loops", "kind": "predict", "status": "ready",
                 "code": PY_CODE, "stdin": "", "line_count": 1, "reason": None, "answered": False}

    wrong = eng.answer(did, "Total: 136")
    assert set(wrong) == ANSWER_KEYS and wrong["correct"] is False
    assert wrong["expected_stdout"] == PY_OUT                  # the REAL stdout, from the sandbox
    assert wrong["lines"] == [{"n": 1, "expected": "Total: 137", "got": "Total: 136", "ok": False}]
    assert wrong["explanation"] == PY_EXPL
    assert eng.view(did)["answered"] is True

    right = eng.answer(did, "Total: 137   \n\n")
    assert right["correct"] is True and right["lines"][0]["ok"] is True

    # counts: seen/correct follow the FIRST answer of a drill; ready counts the one prefetched unseen drill
    row = [t for t in eng.topics("python")["topics"] if t["id"] == "loops"][0]
    assert (row["seen"], row["correct"], row["ready"]) == (1, 0, 1)
    log = svc.store.get_meta("practice:attempts")
    assert [a["correct"] for a in log] == [False, True] and log[0]["drill_id"] == did
    assert len(llm.calls) == 2                                 # the drill and ONE prefetch; never a bank


@needs_sb
def test_good_c_drill_end_to_end():
    _svc, eng, _llm = engine(reply(C_CODE, explanation=C_EXPL), reply(C_CODE.replace("i <= 4", "i <= 5"), explanation=C_EXPL))
    did = eng.next("c", "loops")["drill_id"]
    v = eng.view(did)
    assert v["status"] == "ready" and v["language"] == "c" and v["code"] == C_CODE and v["line_count"] == 1
    ok = eng.answer(did, "30")
    assert ok["correct"] and ok["expected_stdout"] == C_OUT


@needs_sb
def test_first_answer_right_counts_as_correct():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "Sum")))
    did = eng.next("python", "strings")["drill_id"]
    eng.answer(did, "Total: 137")
    eng.answer(did, "wrong now")
    row = [t for t in eng.topics("python")["topics"] if t["id"] == "strings"][0]
    assert (row["seen"], row["correct"]) == (1, 1)


@needs_sb
def test_counts_are_per_language():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "S")), reply(C_CODE, explanation=C_EXPL),
                          reply(C_CODE.replace("i <= 4", "i <= 5"), explanation=C_EXPL))
    eng.answer(eng.next("python", "loops")["drill_id"], "Total: 137")
    assert [t for t in eng.topics("c")["topics"] if t["id"] == "loops"][0]["seen"] == 0
    eng.next("c", "loops")
    assert [t for t in eng.topics("c")["topics"] if t["id"] == "loops"][0]["ready"] == 2


@needs_sb
def test_next_serves_unseen_then_builds_one_more_never_a_bank():
    _svc, eng, llm = engine(reply(), reply(other(PY_CODE, "B")), reply(other(PY_CODE, "C")), reply(other(PY_CODE, "D")))
    a = eng.next("python", "loops")["drill_id"]
    assert len(eng._index()) == 2 and len(llm.calls) == 2      # the drill + one prefetch
    assert eng.next("python", "loops")["drill_id"] == a        # unseen: the same drill again, nothing new built
    assert len(llm.calls) == 2
    eng.answer(a, "x")
    b = eng.next("python", "loops")["drill_id"]
    assert b != a and eng.record(b)["status"] == "ready"
    assert len(llm.calls) == 3 and len(eng._index()) == 3      # serving B prefetched exactly one more (C)
    c = eng.next("python", "loops")["drill_id"]
    assert c == b and len(llm.calls) == 3
    assert len(eng._index()) <= 3


@needs_sb
def test_other_topics_get_their_own_drills():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "B")), reply(other(PY_CODE, "C")), reply(other(PY_CODE, "D")))
    a = eng.next("python", "loops")["drill_id"]
    b = eng.next("python", "lists")["drill_id"]
    assert a != b and eng.record(b)["topic"] == "lists"


@needs_sb
def test_unavailable_drill_cannot_be_answered_and_next_tries_again():
    _svc, eng, _ = engine(RuntimeError("boom"))
    did = eng.next("python", "loops")["drill_id"]
    assert eng.view(did)["status"] == "unavailable"
    with pytest.raises(ServiceError) as ei:
        eng.answer(did, "x")
    assert (ei.value.status, ei.value.code) == (409, "drill_not_ready")
    assert [t for t in eng.topics("python")["topics"] if t["id"] == "loops"][0]["ready"] == 0


@needs_sb
def test_model_that_raises_is_unavailable_with_a_plain_reason():
    for exc in (RuntimeError("socket exploded at 0x7f"), TimeoutError("read timed out")):
        _svc, eng, llm = engine(exc)
        v = eng.view(eng.next("python", "loops")["drill_id"])
        assert v["status"] == "unavailable" and v["code"] == "" and v["line_count"] == 0
        assert v["reason"] == "The model could not be reached. Check that your model is running, then try again."
        assert "socket" not in v["reason"] and "0x" not in v["reason"]
        assert len(llm.calls) == 1                              # an unreachable model is not retried


@needs_sb
def test_a_model_that_keeps_returning_junk_gets_one_rebuild_then_unavailable():
    _svc, eng, llm = engine(None, {"nope": 1})
    v = eng.view(eng.next("python", "loops")["drill_id"])
    assert v["status"] == "unavailable" and "could not be used" in v["reason"]
    assert len(llm.calls) == 2


def test_unknown_drill_is_404():
    _svc, eng, _ = engine()
    for fn in (lambda: eng.view("d_nope"), lambda: eng.answer("d_nope", "x"), lambda: eng.view("a:b"),
               lambda: eng.view(None)):
        with pytest.raises(ServiceError) as ei:
            fn()
        assert (ei.value.status, ei.value.code) == (404, "unknown_drill")


@needs_sb
def test_answer_input_validation():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "B")))
    did = eng.next("python", "loops")["drill_id"]
    with pytest.raises(ServiceError) as ei:
        eng.answer(did, 5)
    assert ei.value.status == 422
    with pytest.raises(ServiceError) as ei:
        eng.answer(did, "x" * (E.MAX_PREDICTION_CHARS + 1))
    assert ei.value.status == 413


# ------------------------------------------------------------------ P7: no stdout, no explanation before an answer

@needs_sb
def test_p7_view_before_an_answer_carries_no_stdout_and_no_explanation():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "B")))
    did = eng.next("python", "loops")["drill_id"]
    rec = eng.record(did)
    assert rec["stdout"] == PY_OUT and rec["explanation"] == PY_EXPL     # they ARE stored, server-side
    v = eng.view(did)
    assert set(v) == VIEW_KEYS
    blob = json.dumps(v)
    for secret in ["Total: 137", "137", PY_OUT.strip(), "stdout", "explanation", "expected"] + PY_EXPL + \
            [s.strip(".") for s in PY_EXPL]:
        assert secret not in blob, secret
    # the code is shown (it must be); the key is not derivable from the view's other fields
    assert v["code"] == PY_CODE and v["line_count"] == 1


@needs_sb
def test_p7_status_and_next_responses_carry_nothing_either():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "B")))
    got = eng.next("python", "loops")
    topics = eng.topics("python")
    for blob in (json.dumps(got), json.dumps(topics)):
        assert "137" not in blob and "Total" not in blob and "explanation" not in blob


@needs_sb
def test_p7_an_unavailable_or_building_view_has_no_key_fields_either():
    svc, eng, _ = engine(reply(), reply(other(PY_CODE, "B")))
    did = eng.next("python", "loops")["drill_id"]
    rec = eng.record(did)
    svc.store.set_meta(f"drill:{did}", dict(rec, status="building"))      # a stale 'building' with a stored key
    v = eng.view(did)                                                     # no thread builds it: settles to unavailable
    assert v["status"] == "unavailable" and "interrupted" in v["reason"]
    assert "137" not in json.dumps(v) and v["code"] == ""


@needs_sb
def test_views_after_an_answer_still_carry_no_key():
    _svc, eng, _ = engine(reply(), reply(other(PY_CODE, "B")))
    did = eng.next("python", "loops")["drill_id"]
    eng.answer(did, "Total: 137")
    v = eng.view(did)
    assert v["answered"] is True and "137" not in json.dumps(v) and set(v) == VIEW_KEYS

