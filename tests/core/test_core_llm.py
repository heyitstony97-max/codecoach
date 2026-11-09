import pytest
from pydantic import BaseModel

from codecoach.core.llm import FakeLLM, LLMBadOutput, LLMPort, LLMUnavailable, structured_call


class Obj(BaseModel):
    statement: str
    n: int


GOOD = {"statement": "Explain x", "n": 3}


def test_first_try_good():
    llm = FakeLLM([GOOD])
    out = structured_call(llm, Obj, instructions="do", text="TEXT", purpose="codecoach.map")
    assert out == Obj(**GOOD) and len(llm.calls) == 1
    c = llm.calls[0]
    assert c.purpose == "codecoach.map" and c.text == "TEXT" and c.instructions == "do"
    assert c.schema["properties"]["n"]["type"] == "integer"


def test_bad_then_good_feeds_error_back():
    llm = FakeLLM([{"statement": "x", "n": "not a number"}, GOOD])
    out = structured_call(llm, Obj, instructions="i", text="TEXT", purpose="p")
    assert out.n == 3 and len(llm.calls) == 2
    assert "n:" in llm.calls[1].text and "TEXT" in llm.calls[1].text and "rejected" in llm.calls[1].text
    assert llm.calls[0].text == "TEXT"


def test_none_then_good():
    llm = FakeLLM([None, GOOD])
    assert structured_call(llm, Obj, instructions="i", text="t", purpose="p").n == 3


def test_three_bad_raises_bad_output():
    llm = FakeLLM([None, {"statement": "x"}, ["list"], GOOD])
    with pytest.raises(LLMBadOutput) as ei:
        structured_call(llm, Obj, instructions="i", text="t", purpose="codecoach.x", retries=3)
    assert len(llm.calls) == 3                # the fourth scripted reply is never requested
    assert ei.value.attempts == 3 and ei.value.purpose == "codecoach.x" and ei.value.last_error


def test_retries_one_means_single_attempt():
    llm = FakeLLM([None, GOOD])
    with pytest.raises(LLMBadOutput):
        structured_call(llm, Obj, instructions="i", text="t", purpose="p", retries=1)
    assert len(llm.calls) == 1


def test_unavailable_not_retried():
    llm = FakeLLM([LLMUnavailable("down"), GOOD])
    with pytest.raises(LLMUnavailable):
        structured_call(llm, Obj, instructions="i", text="t", purpose="p")
    assert len(llm.calls) == 1


def test_other_exceptions_become_unavailable():
    llm = FakeLLM([TimeoutError("slow"), GOOD])
    with pytest.raises(LLMUnavailable, match="TimeoutError"):
        structured_call(llm, Obj, instructions="i", text="t", purpose="p")


def test_value_error_is_retried():
    llm = FakeLLM([ValueError("empty input"), GOOD])
    assert structured_call(llm, Obj, instructions="i", text="t", purpose="p").n == 3


def test_exhausted_script_is_unavailable():
    with pytest.raises(LLMUnavailable, match="exhausted"):
        structured_call(FakeLLM([]), Obj, instructions="i", text="t", purpose="p")


def test_function_script_and_models_and_json_strings():
    seen = []
    def fn(purpose, text):
        seen.append((purpose, text))
        return Obj(statement=text, n=len(seen))
    llm = FakeLLM(fn)
    out = structured_call(llm, Obj, instructions="i", text="hello", purpose="codecoach.a")
    assert out.statement == "hello" and seen == [("codecoach.a", "hello")]
    assert structured_call(FakeLLM(['{"statement": "s", "n": 1}']), Obj, instructions="i", text="t", purpose="p").n == 1
    assert FakeLLM(["not json"]).complete_structured(instructions="", text="", schema={}, purpose="p") is None


def test_complete_text_and_recording():
    llm = FakeLLM(text_script=["hello", lambda p, t: f"echo:{t}"])
    assert llm.complete(messages=[{"role": "user", "content": "a"}], purpose="codecoach.t") == "hello"
    assert llm.complete(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "b"}],
                        purpose="codecoach.t") == "echo:b"
    assert llm.purposes() == ["codecoach.t", "codecoach.t"] and len(llm.text_calls) == 2
    assert llm.structured_calls == []


def test_fake_satisfies_port():
    assert isinstance(FakeLLM(), LLMPort)
