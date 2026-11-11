"""Projects take their languages from codecoach.lang: create, starters, ideas, and the C/Python prompts staying
byte-identical to master (contract rule 2.7). No sandbox runs here."""

import json
from pathlib import Path

import pytest
from proj_helpers import Script, make_svc, plan_reply

import codecoach.project as P
from codecoach import lang
from codecoach.service import ServiceError

GOLD = json.loads((Path(__file__).parent / "fixtures" / "prompts_master.json").read_text())
PLAN = GOLD["plan"]


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch):
    monkeypatch.setattr(P, "sandbox_status", lambda *a, **k: "bwrap")


def _eng():
    return P.ProjectEngine(make_svc())


# ---- rule 2.7: C and Python prompts are byte-identical to master

def test_prompt_version_not_bumped():
    assert P.PROJECT_PROMPT_VERSION == GOLD["version"] == "2"


def test_plan_and_step_instructions_match_master():
    assert P.PLAN_INSTRUCTIONS == GOLD["plan_instructions"]
    assert P.STEP_INSTRUCTIONS == GOLD["step_instructions"]
    for lid in ("c", "python"):
        assert P.step_instructions(lid) == GOLD["step_instructions"]


@pytest.mark.parametrize("lid", ["c", "python"])
def test_step_prompts_match_master(lid):
    g = GOLD["prompts"][lid]
    assert P.starter_for(lid) == g["starter"]
    p = {"id": "p_x", "language": lid, "idea": GOLD["plan_idea"], "plan": PLAN}
    assert P.ProjectEngine.step_prompt(p, 0, g["starter"]) == g["step0"]
    assert P.ProjectEngine.step_prompt(p, 2, "print('x')\n", "bad thing") == g["step2"]


def test_plan_call_text_for_c_and_python_matches_master():
    for lid, name in (("c", "C"), ("python", "Python")):
        assert P.language_name(lid) == name


# ---- other languages: same rules, the style line comes from the LangSpec

@pytest.mark.parametrize("lid", [i for i in lang.all_ids() if i not in ("c", "python")])
def test_other_languages_get_their_style_line(lid):
    text = P.step_instructions(lid)
    assert text.endswith("- Use the language you are told. " + lang.spec(lid).style_note)
    assert text.rsplit("\n", 1)[0] == GOLD["step_instructions"].rsplit("\n", 1)[0]
    assert "Plain C11 or Python 3" not in text


def test_step_prompt_names_the_language_and_starts_from_its_starter():
    p = {"id": "p_x", "language": "rust", "idea": "x", "plan": PLAN}
    t = P.ProjectEngine.step_prompt(p, 0, P.starter_for("rust"))
    assert t.startswith("Language: Rust\n") and "fn main() {\n}\n" in t


# ---- create

def test_create_accepts_every_ready_language(monkeypatch):
    eng = _eng()
    for lid in lang.ready_ids():
        pid = eng.create("a greeter that says hello", lid)
        g = eng.get(pid)
        assert g["language"] == lid and g["code"] == lang.spec(lid).starter


def test_create_refuses_a_language_that_is_not_ready(monkeypatch):
    eng = _eng()
    monkeypatch.setattr(lang, "ready_ids", lambda: ["c", "python"])
    for bad in ("rust", "brainfuck", "", None, 5, ["c"]):
        with pytest.raises(ServiceError) as e:
            eng.create("a greeter that says hello", bad)
        assert (e.value.status, e.value.code) == (422, "bad_language")
        assert e.value.message == "That language is not set up on this machine yet."
    assert eng.list()["projects"] == []
    eng.create("a greeter that says hello", "c")           # the ready ones still work


# ---- ideas: at least one per Tier A language, deterministic stdin/stdout only

def test_ideas_cover_every_tier_a_language_in_the_same_shape():
    ideas = _eng().ideas()
    assert {i["language"] for i in ideas} >= set(lang.all_ids()[:9])   # the nine Tier A ids; Tier B has no ideas
    for i in ideas:
        assert set(i) == {"title", "language", "pitch"} and i["title"].strip() and i["pitch"].strip()
        assert i["language"] in lang.all_ids()
    assert len({i["title"] for i in ideas}) == len(ideas)
    blob = " ".join(i["pitch"].lower() for i in ideas)
    for banned in ("random", "clock", "time of", "file", "network", "graphic", "emoji"):
        assert banned not in blob


def test_the_six_v10_ideas_are_unchanged():
    old = [("Number-guessing game", "c"), ("Times-table printer", "c"), ("Simple calculator", "c"),
           ("Temperature converter", "python"), ("Word counter", "python"), ("Palindrome checker", "python")]
    assert [(i["title"], i["language"]) for i in _eng().ideas()[:6]] == old


def test_starters_mapping_reads_the_langspec():
    for lid in lang.all_ids():
        assert P.STARTERS[lid] == lang.spec(lid).starter == P.starter_for(lid)
    assert P.starter_for("nonsense") == ""
