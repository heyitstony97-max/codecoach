"""last_route() on the LLM port: FakeLLM, and both Hermes doors (ctx.llm and PluginLlm) through the glue."""

from types import SimpleNamespace

import pytest

from codecoach import hermes_glue
from codecoach.core.llm import FakeLLM, LLMPort


class Host:
    """Stands in for ctx.llm / PluginLlm: results carry provider and model like the host's do."""

    def __init__(self, provider="custom", model="qwen3.8-27b", as_dict=False, fail_task_once=False):
        self.provider, self.model, self.as_dict, self.fail_task_once = provider, model, as_dict, fail_task_once

    def _res(self, **extra):
        d = {"provider": self.provider, "model": self.model, **extra}
        return d if self.as_dict else SimpleNamespace(**d)

    def complete_structured(self, **kw):
        if self.fail_task_once and "task" in kw:
            raise RuntimeError("unknown auxiliary task: register it first")
        return self._res(parsed={"a": 1})

    def complete(self, **kw):
        return self._res(text="hi")


def test_fake_llm_route_is_none_until_set_and_settable():
    f = FakeLLM()
    assert isinstance(f, LLMPort)
    assert f.last_route() is None
    f.set_route("custom", "m1")
    assert f.last_route() == {"provider": "custom", "model": "m1"}
    f.route = {"provider": "p", "model": "m2"}
    assert f.last_route() == {"provider": "p", "model": "m2"}
    f.last_route()["model"] = "tampered"
    assert f.last_route()["model"] == "m2"
    f.set_route(None)
    assert f.last_route() is None


@pytest.mark.parametrize("door", ["ctx", "plugin"])
@pytest.mark.parametrize("as_dict", [False, True])
def test_glue_reports_provider_and_model_from_the_host_result(door, as_dict):
    host = Host(as_dict=as_dict)
    llm = hermes_glue.CtxLLM(SimpleNamespace(llm=host)) if door == "ctx" else hermes_glue.PluginLLM(host)
    assert llm.last_route() is None
    llm.complete_structured(instructions="i", text="t", schema={}, purpose="p")
    assert llm.last_route() == {"provider": "custom", "model": "qwen3.8-27b"}
    host.provider, host.model = "openrouter", "other/model"
    llm.complete(messages=[{"role": "user", "content": "x"}], purpose="p")
    assert llm.last_route() == {"provider": "openrouter", "model": "other/model"}


def test_glue_route_survives_the_task_fallback_and_missing_fields():
    host = Host(fail_task_once=True)
    llm = hermes_glue.PluginLLM(host)
    llm.complete_structured(instructions="i", text="t", schema={}, purpose="p")
    assert llm.task_applied is False
    assert llm.last_route() == {"provider": "custom", "model": "qwen3.8-27b"}
    host.provider = None                     # the host did not say: keep the last known route, do not invent
    llm.complete_structured(instructions="i", text="t", schema={}, purpose="p")
    assert llm.last_route() == {"provider": "custom", "model": "qwen3.8-27b"}
