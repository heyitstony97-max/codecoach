"""llm_timeout_s: the plugin.yaml key, the config parse, and the per-call timeout the glue sends."""

from pathlib import Path
from types import SimpleNamespace

from codecoach import hermes_glue
from codecoach.service import ServiceConfig

ROOT = Path(__file__).resolve().parents[2]


class Recorder:
    def __init__(self):
        self.calls = []

    def complete_structured(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(parsed={"a": 1})

    def complete(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text="hi")


def test_plugin_yaml_declares_llm_timeout_s():
    text = (ROOT / "plugin.yaml").read_text()
    block = text.split("llm_timeout_s:")[1].split("\n  ")[0:6]
    joined = "\n".join(block)
    assert "type: int" in joined and "default: 600" in joined
    assert "Seconds to wait for each model call; local models need minutes" in joined


def test_config_parses_clamps_and_defaults():
    assert ServiceConfig().llm_timeout_s == 600
    parse = lambda v: ServiceConfig.from_mapping({"llm_timeout_s": v}.get).llm_timeout_s  # noqa: E731
    assert parse(900) == 900 and parse("900") == 900
    assert parse("soon") == 600 and parse(None) == 600
    assert parse(0) == 10 and parse(10**9) == 7200
    assert ServiceConfig.from_mapping({}.get).llm_timeout_s == 600


def test_every_call_carries_the_configured_timeout_unless_overridden():
    rec = Recorder()
    llm = hermes_glue.PluginLLM(rec, default_timeout=900)
    llm.complete_structured(instructions="i", text="t", schema={}, purpose="p")
    llm.complete(messages=[{"role": "user", "content": "x"}], purpose="p")
    llm.complete_structured(instructions="i", text="t", schema={}, purpose="p", timeout=5)
    assert [c["timeout"] for c in rec.calls] == [900, 900, 5]
    assert hermes_glue.PluginLLM(rec).default_timeout == 600


def test_ctx_service_wires_the_config_value_into_the_llm():
    ctx = SimpleNamespace(llm=Recorder(), get_config=lambda k, d: 750 if k == "llm_timeout_s" else d)
    cfg = hermes_glue.config_from_ctx(ctx)
    assert cfg.llm_timeout_s == 750
    assert hermes_glue.CtxLLM(ctx, default_timeout=cfg.llm_timeout_s).default_timeout == 750
