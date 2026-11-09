"""grammar_safe: a local model behind llama.cpp gets the JSON schema as a grammar and may stop as soon as the
required fields are written. Orchestrator probe (Nov 7): with Pydantic's schema, local Qwen answered every Learn step
with {"answerable": true} and no sentences, so Learn showed nothing on the live install."""
from pydantic import BaseModel, Field

from codecoach.ask.engine import Reply
from codecoach.core.llm import FakeLLM, grammar_safe, structured_call


def test_the_ask_and_learn_reply_requires_its_sentences_and_their_quotes():
    s = grammar_safe(Reply.model_json_schema())
    assert "sentences" in s["required"]
    assert set(s["$defs"]["Sentence"]["required"]) == {"text", "cites"}
    assert set(s["$defs"]["Cite"]["required"]) == {"passage_id", "quote"}
    assert "answerable" not in s["required"]          # a real default (True) stays optional


class _M(BaseModel):
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    origin: str = "materials"
    limit: int | None = None
    name: str


def test_empty_defaults_become_required_and_real_defaults_do_not():
    s = grammar_safe(_M.model_json_schema())
    assert set(s["required"]) == {"name", "title", "tags"}


def test_the_input_schema_is_not_changed():
    raw = _M.model_json_schema()
    before = repr(raw)
    grammar_safe(raw)
    assert repr(raw) == before


def test_structured_call_sends_the_grammar_safe_schema():
    seen = {}

    class Port(FakeLLM):
        def complete_structured(self, **kw):
            seen.update(kw)
            return {"name": "x", "title": "", "tags": []}

    structured_call(Port([]), _M, instructions="i", text="t", purpose="p")
    assert set(seen["schema"]["required"]) == {"name", "title", "tags"}
