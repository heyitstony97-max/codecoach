"""Tool handlers, the /codecoach command, the root register(), and the Hermes glue with fakes."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from surface_helpers import make_service

from codecoach import hermes_glue, tools
from codecoach.core.llm import LLMUnavailable
from codecoach.service import ServiceError

ROOT = Path(__file__).resolve().parents[2]
GARBAGE = [None, "", "x", 0, -1, 3.5, True, [], [1, "a"], {}, {"course_id": 5}, {"item_id": ["a"]},
           {"answer": {"a": 1}}, {"answer": None, "item_id": None}, {"level": "high", "item_id": "zz"},
           {"path": "/nonexistent/file.txt"}, {"text": "x" * 10, "title": 5, "mode": "weird"},
           {"path": "/etc/hostname", "text": "both"}, {"job_id": "zz"}, {"reason": 5}, "{not json"]


def handlers():
    svc, store, cid = make_service()
    return tools.make_handlers(lambda: svc), svc, store, cid


def test_all_handlers_survive_garbage():
    hs, svc, store, cid = handlers()
    for name, h in hs.items():
        for g in GARBAGE:
            out = h(g)
            d = json.loads(out)
            assert isinstance(d, dict) and "ok" in d and isinstance(d.get("text", ""), str), (name, g)
            out2 = h(g, task_id="t", session_id="s")
            json.loads(out2)


def test_handler_survives_broken_service():
    def boom():
        raise RuntimeError("db gone")
    for h in tools.make_handlers(boom).values():
        d = json.loads(h({"course_id": "x", "answer": "1"}))
        assert d["ok"] is False and d["text"]


def test_command_survives_garbage():
    svc, store, cid = make_service()
    cmd = tools.make_command(lambda: svc)
    for g in ["", "help", "bogus", "answer", "import", "import /nope.pdf", "next zz", "hint", "progress job zz",
              "map zz", "courses", "report", None, 5, "answer " + "x" * 5000]:
        assert isinstance(cmd(g), str)


def test_flow_through_tools_numbered_options_then_citations_after_answer():
    hs, svc, store, cid = handlers()
    d = json.loads(hs["codecoach_next"]({"course_id": cid}))
    for _ in range(20):
        if d["kind"] == "item":
            break
        d = json.loads(hs["codecoach_next"]({"course_id": cid}))
    assert d["kind"] == "item" and "1. " in d["text"] and "Reply with the option number" in d["text"]
    assert "Source:" not in d["text"]
    quotes = [c.quote for c in store.get_item(d["item_id"]).citations]
    assert not any(q in d["text"] for q in quotes)
    hint = json.loads(hs["codecoach_hint"]({}))
    assert hint["ok"] and hint["text"].startswith("Hint")
    ans = json.loads(hs["codecoach_answer"]({"answer": "1"}))
    assert ans["ok"] and ans["text"]
    again = json.loads(hs["codecoach_answer"]({"answer": "1"}))
    assert again["ok"] is False
    pr = json.loads(hs["codecoach_progress"]({}))
    assert pr["ok"] and "topics covered" in pr["text"]
    m = tools.op_map(svc, cid)["text"]
    assert m.splitlines()[0].startswith(svc.courses().courses[0].title) and "\n  " in m


def test_import_via_tool_and_command_returns_job():
    hs, svc, store, cid = handlers()
    d = json.loads(hs["codecoach_import"]({"text": "Para one is here.\n\nPara two is here.", "title": "Mini"}))
    assert d["ok"] and d["job_id"]
    assert json.loads(hs["codecoach_progress"]({"job_id": d["job_id"]}))["ok"]
    cmd = tools.make_command(lambda: svc)
    assert "Import started" in cmd("import Mini2 | Some text.\n\nMore text.")
    assert "Usage" in cmd("import")


def test_parse_answer_shapes():
    assert tools.parse_answer("mc", {"options": ["a", "b"]}, "2") == 1
    assert tools.parse_answer("order", {}, "2 1 3") == [1, 0, 2]
    assert tools.parse_answer("match", {}, "1A 2c") == [[0, 0], [1, 2]]
    assert tools.parse_answer("explain", {}, "0.5") == 0.5
    assert tools.parse_answer("short", {}, None) == ""


# ---------------------------------------------------------------- register() and the glue

class FakeCtx:
    def __init__(self):
        self.tools, self.commands, self.aux = {}, {}, {}
        self.llm = SimpleNamespace()

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler, kw)

    def register_command(self, name, handler, **kw):
        self.commands[name] = (handler, kw)

    def register_auxiliary_task(self, key, *, display_name, description, **kw):
        self.aux[key] = (display_name, description)

    def get_config(self, key, default=None):
        return {"default_mode": "open", "bank_per_objective": "5"}.get(key, default)

    def dispatch_tool(self, name, args):
        raise RuntimeError("not available")


def load_root():
    spec = importlib.util.spec_from_file_location("codecoach_root_plugin", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_register_matches_plugin_yaml():
    ctx = FakeCtx()
    load_root().register(ctx)
    yaml_text = (ROOT / "plugin.yaml").read_text()
    declared = [l.strip()[2:] for l in yaml_text.split("provides_tools:")[1].split("python_dependencies:")[0].splitlines()
                if l.strip().startswith("- ")]
    assert sorted(declared) == sorted(ctx.tools) == sorted(tools.TOOL_NAMES)
    assert "codecoach" in ctx.aux and "codecoach" in ctx.commands
    for name, (ts, schema, handler, kw) in ctx.tools.items():
        assert schema["name"] == name and schema["parameters"]["type"] == "object"


def test_config_from_ctx():
    cfg = hermes_glue.config_from_ctx(FakeCtx())
    assert cfg.default_mode == "open" and cfg.bank_per_objective == 5 and cfg.allow_unsandboxed_code is False


class FakeCaller:
    def __init__(self, fail_task=False, raises=None, parsed="obj", content_type="json"):
        self.calls, self.fail_task, self.raises, self.parsed = [], fail_task, raises, parsed
        self.content_type = content_type

    def _go(self, kw):
        self.calls.append(kw)
        if self.raises:
            raise self.raises
        if self.fail_task and kw.get("task"):
            raise PermissionError("cannot route through auxiliary task - register")
        return SimpleNamespace(text='{"a": 1}', parsed={"a": 1} if self.parsed == "obj" else self.parsed,
                               content_type=self.content_type)

    def complete_structured(self, **kw):
        return self._go(kw)

    def complete(self, **kw):
        return self._go(kw)


def test_llm_port_maps_and_passes_task_purpose_timeout():
    fc = FakeCaller()
    llm = hermes_glue.CtxLLM(SimpleNamespace(llm=fc))
    out = llm.complete_structured(instructions="i", text="t", schema={"type": "object"}, purpose="codecoach.x", timeout=7)
    assert out == {"a": 1}
    kw = fc.calls[0]
    assert kw["task"] == "codecoach" and kw["purpose"] == "codecoach.x" and kw["timeout"] == 7
    assert kw["input"] == [{"type": "text", "text": "t"}] and kw["json_schema"] == {"type": "object"}
    assert llm.complete(messages=[{"role": "user", "content": "x"}], purpose="p") == '{"a": 1}'


def test_llm_port_non_object_json_and_text_fallback():
    assert hermes_glue.CtxLLM(SimpleNamespace(llm=FakeCaller(parsed=[1]))).complete_structured(
        instructions="i", text="t", schema={}, purpose="p") is None
    fc = FakeCaller(parsed=None, content_type="text")
    assert hermes_glue.CtxLLM(SimpleNamespace(llm=fc)).complete_structured(
        instructions="i", text="t", schema={}, purpose="p") == {"a": 1}


def test_llm_port_provider_error_is_unavailable_and_task_denial_falls_back():
    with pytest.raises(LLMUnavailable):
        hermes_glue.PluginLLM(FakeCaller(raises=TimeoutError("slow"))).complete(messages=[], purpose="p")
    fc = FakeCaller(fail_task=True)
    llm = hermes_glue.PluginLLM(fc)
    assert llm.complete(messages=[], purpose="p") == '{"a": 1}'
    assert llm.task_applied is False and "task" not in fc.calls[-1] and "task" in fc.calls[0]


def test_pdf_reader_via_dispatch_tool_strips_prefixes(monkeypatch, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")   # the path policy needs a real regular file
    pdf_path = str(tmp_path / "a.pdf")
    monkeypatch.setattr(hermes_glue.pdf, "available", lambda: False)   # the fallback when pdftotext is absent
    ctx = FakeCtx()
    ctx.dispatch_tool = lambda n, a: json.dumps({"content": "     1|Hello\n     2|World", "total_lines": 2})
    assert hermes_glue.make_ctx_reader(ctx)(pdf_path) == "Hello\nWorld"
    ctx.dispatch_tool = lambda n, a: json.dumps({"error": "no such file"})
    with pytest.raises(ServiceError) as e:
        hermes_glue.make_ctx_reader(ctx)(pdf_path)
    assert e.value.code == "pdf_unavailable"
    with pytest.raises(ServiceError) as e:
        hermes_glue.make_ctx_reader(FakeCtx())(pdf_path)
    assert e.value.code == "pdf_unavailable"
    assert hermes_glue.strip_line_numbers("  10|a\n 11|b") == "a\nb"
