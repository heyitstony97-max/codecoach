"""import_with_syllabus: the syllabus is checked against the materials, line by line, fail closed."""

import re

import pytest

from codecoach.core.llm import FakeLLM, LLMUnavailable
from codecoach.gen import bank as _bank
from codecoach.importer import (ImportFailed, get_import_report, import_materials, import_with_syllabus,
                             parse_syllabus)
from codecoach.importer import syllabus as SY
from importer_helpers import HISTORY, new_store, scripted

ROAD = "Edric paved the Salt Road between Teshwick and the harbour of Port Calder"
WINTER = "The Long Winter killed a third of the kingdom's cattle"
ROAD_LINE = "Explain who paved the Salt Road and which two places it joined."
WINTER_LINE = "Describe what the Long Winter did to the kingdom's cattle."
PONY_LINE = "Explain how the Pony Express carried mail across the desert."


def passage_with(prompt: str, needle: str) -> str:
    for m in re.finditer(r"^\[([^\]]+)\] (.*)$", prompt, re.M):
        if needle in m.group(2):
            return m.group(1)
    return "nope:p1:1"


def model(*, quote_for=None, on_cover=None, verb="explain") -> FakeLLM:
    """quote_for(topic line) -> (needle to find the passage, quote) or None for an empty answer."""
    def fn(purpose: str, text: str):
        if purpose == SY.PURPOSE_VERB:
            return {"verb": verb}
        if purpose == SY.PURPOSE_COVER:
            if on_cover:
                r = on_cover(text)
                if r is not None:
                    return r
            line = re.search(r"^Topic: (.*)$", text, re.M).group(1)
            got = quote_for(line) if quote_for else None
            if not got:
                return {"support": []}
            needle, quote = got
            return {"support": [{"passage_id": passage_with(text, needle), "quote": quote}]}
        return {"edges": []}
    return FakeLLM(fn)


def run(syllabus, llm, **kw):
    store = new_store()
    cid = import_with_syllabus(store, llm, title="Valdoria", materials_text=HISTORY, doc_name="history",
                               syllabus_text=syllabus, **kw)
    return store, cid, {o.statement: o for o in store.list_objectives(cid)}


# ---------------------------------------------------------------- the verdicts

def test_a_line_with_a_real_relevant_quote_is_covered():
    store, cid, objs = run(f"1. {ROAD_LINE}", model(quote_for=lambda l: (ROAD, ROAD)))
    o = objs[ROAD_LINE]
    assert o.coverage == "covered" and o.verb == "explain" and o.passage_ids
    assert ROAD in store.get_passage(o.passage_ids[0]).text
    rep = get_import_report(store, cid)
    assert rep["source"] == "syllabus" and rep["covered"] == 1 and rep["not_covered"] == 0
    assert rep["syllabus"][0]["quote"] == ROAD and rep["syllabus"][0]["passage_ids"] == o.passage_ids


def test_a_hallucinated_quote_is_not_covered():
    fake = "Edric paved the Salt Road with gold bricks all the way to Port Calder"
    store, cid, objs = run(f"1. {ROAD_LINE}", model(quote_for=lambda l: (ROAD, fake)))
    o = objs[ROAD_LINE]
    assert o.coverage == "not_covered" and o.passage_ids == []
    rep = get_import_report(store, cid)
    assert rep["syllabus"][0]["reason"] == SY.REASON_NONE == "no passage in your materials covers this"
    assert any(i["kind"] == "not_covered" and SY.REASON_NONE in i["detail"] for i in rep["issues"])


def test_a_quote_cited_to_the_wrong_passage_is_not_covered():
    def wrong(text):
        return {"support": [{"passage_id": passage_with(text, WINTER), "quote": ROAD}]}
    _, _, objs = run(f"1. {ROAD_LINE}", model(on_cover=wrong))
    assert objs[ROAD_LINE].coverage == "not_covered"


def test_a_real_but_irrelevant_quote_is_not_covered():
    """The quote exists word for word, but shares fewer than two content words with the topic."""
    def irrelevant(text):
        # cite the Long Winter sentence for the road topic: real text, wrong subject
        return {"support": [{"passage_id": passage_with(text, "Long Winter"), "quote": WINTER}]}
    llm = model(on_cover=irrelevant)
    _, _, objs = run(f"1. {ROAD_LINE}", llm)
    assert objs[ROAD_LINE].coverage == "not_covered"
    # the same quote is accepted for a topic it does answer (so the rule, not the gate, rejected it above)
    _, _, objs = run(f"1. {WINTER_LINE}", model(quote_for=lambda l: (WINTER, WINTER)))
    assert objs[WINTER_LINE].coverage == "covered"


def test_the_overlap_rule_counts_stemmed_content_words():
    ps = SY.Support(passage_id="x", quote="q")
    assert SY.MIN_SHARED == 2
    assert set(SY.content_words(ROAD_LINE)) & set(SY.content_words(ROAD)) >= {"salt", "road", "pav"}
    assert len(set(SY.content_words(ROAD_LINE)) & set(SY.content_words(WINTER))) < SY.MIN_SHARED
    assert ps.passage_id == "x"


def test_a_topic_with_no_matching_words_asks_the_model_nothing():
    zebra = "Explain the zebra migration of Kenya."
    llm = model(quote_for=lambda l: (ROAD, ROAD))
    _, _, objs = run(f"1. {zebra}", llm)
    assert objs[zebra].coverage == "not_covered"
    assert not [c for c in llm.calls if c.purpose == SY.PURPOSE_COVER]


def test_an_llm_error_is_could_not_check_and_the_import_continues():
    def boom_on_road(text):
        if "Salt Road" in text.split("Passages:")[0]:
            return LLMUnavailable("down")
        return None
    store, cid, objs = run(f"1. {ROAD_LINE}\n2. {WINTER_LINE}",
                           model(quote_for=lambda l: (WINTER, WINTER), on_cover=boom_on_road))
    assert objs[ROAD_LINE].coverage == "not_covered"
    assert objs[WINTER_LINE].coverage == "covered"
    rep = get_import_report(store, cid)
    assert rep["syllabus"][0]["reason"] == "could not check"
    assert rep["covered"] == 1 and rep["not_covered"] == 1


def test_a_second_look_can_rescue_an_empty_first_answer_but_never_a_fake_one():
    n = {"i": 0}

    def empty_then_real(text):
        n["i"] += 1
        return {"support": []} if n["i"] == 1 else {"support": [
            {"passage_id": passage_with(text, ROAD), "quote": ROAD}]}
    _, _, objs = run(f"1. {ROAD_LINE}", model(on_cover=empty_then_real))
    assert objs[ROAD_LINE].coverage == "covered" and n["i"] == 2
    fake = "Edric paved the Salt Road with gold bricks all the way to Port Calder"
    llm = model(quote_for=lambda l: (ROAD, fake))
    _, _, objs = run(f"1. {ROAD_LINE}", llm)
    assert objs[ROAD_LINE].coverage == "not_covered"
    assert len([c for c in llm.calls if c.purpose == SY.PURPOSE_COVER]) == SY.LOOKS


def test_bad_model_output_is_could_not_check():
    store, cid, objs = run(f"1. {ROAD_LINE}", model(on_cover=lambda t: {"support": "banana"}))
    assert objs[ROAD_LINE].coverage == "not_covered"
    assert get_import_report(store, cid)["syllabus"][0]["reason"] == "could not check"


def test_an_unreachable_model_aborts_the_import_after_five_lines_in_a_row():
    store = new_store()
    lines = "\n".join(f"{i}. Explain the Salt Road toll gates, part {i}." for i in range(1, 8))
    llm = model(on_cover=lambda t: LLMUnavailable("down"))
    with pytest.raises(ImportFailed):
        import_with_syllabus(store, llm, title="V", materials_text=HISTORY, doc_name="h", syllabus_text=lines)
    assert store.list_courses() == []


def test_the_model_sees_only_the_top_candidate_passages():
    llm = model(quote_for=lambda l: (ROAD, ROAD))
    run(f"1. {ROAD_LINE}", llm)
    cover = [c for c in llm.calls if c.purpose == SY.PURPOSE_COVER][0]
    shown = re.findall(r"^\[([^\]]+)\] ", cover.text, re.M)
    assert 1 <= len(shown) <= SY.TOP_K


# ---------------------------------------------------------------- shape of the course

def test_not_covered_objectives_get_no_items():
    store, cid, objs = run(f"1. {ROAD_LINE}\n2. {PONY_LINE}", model(quote_for=lambda l: (ROAD, ROAD)))
    pony = objs[PONY_LINE]
    assert pony.coverage == "not_covered"
    llm = FakeLLM(lambda p, t: {})                      # a generator that must never be asked
    rep = _bank.fill_objective(store, llm, pony, store.list_passages(course_id=cid), mode="strict", wanted=2)
    assert not llm.calls
    assert pony.id in rep.not_covered
    assert store.count_items(course_id=cid, status="active") == 0


def test_every_line_is_an_objective_in_order_with_the_line_verbatim():
    syl = f"1. {ROAD_LINE}\n2. {PONY_LINE}\n3. {WINTER_LINE}"
    store, cid, objs = run(syl, model(quote_for=lambda l: (ROAD, ROAD) if "Salt" in l else None))
    assert [o.statement for o in store.list_objectives(cid)] == [ROAD_LINE, PONY_LINE, WINTER_LINE]
    cmap = store.get_map(cid)
    assert len(cmap.units) == 1 and len(cmap.units[0].lessons) == 3


def test_prerequisite_edges_cover_only_covered_objectives():
    syl = f"1. {ROAD_LINE}\n2. {PONY_LINE}\n3. {WINTER_LINE}"
    pick = lambda l: (ROAD, ROAD) if "Salt" in l else ((WINTER, WINTER) if "Winter" in l else None)  # noqa: E731
    store, cid, _ = run(syl, model(quote_for=pick))
    objs = {o.statement: o for o in store.list_objectives(cid)}
    ids = {objs[ROAD_LINE].id, objs[WINTER_LINE].id}
    cmap = store.get_map(cid)
    assert cmap.edges and all(e.prereq in ids and e.objective in ids for e in cmap.edges)
    assert objs[PONY_LINE].prereqs == []


def test_the_verb_comes_from_the_model_and_falls_back_to_the_first_word():
    _, _, objs = run(f"1. {ROAD_LINE}", model(quote_for=lambda l: (ROAD, ROAD), verb="Order"))
    assert objs[ROAD_LINE].verb == "order"
    bad = FakeLLM(lambda p, t: {"verb": "appreciate"} if p == SY.PURPOSE_VERB
                  else {"support": [{"passage_id": passage_with(t, ROAD), "quote": ROAD}]})
    _, _, objs = run("1. Compare the Salt Road toll gates with the road paved by Edric.", bad)
    assert next(iter(objs.values())).verb == "compare"
    assert SY.fallback_verb("Summarize the road") == "explain"
    assert SY.fallback_verb("Calculate the toll") == "compute"
    assert SY.fallback_verb("Something odd") == "explain"


def test_progress_and_job_are_reported():
    store = new_store()
    job = store.create_job("import")
    seen = []
    cid = import_with_syllabus(store, model(quote_for=lambda l: (ROAD, ROAD)), title="V", materials_text=HISTORY,
                               doc_name="h", syllabus_text=f"1. {ROAD_LINE}", progress=lambda s, p: seen.append(s),
                               job_id=job.id)
    assert store.get_job(job.id).status == "done" and store.get_job(job.id).course_id == cid
    assert any("syllabus" in s.lower() for s in seen)


def test_an_empty_syllabus_fails_and_saves_nothing():
    store = new_store()
    with pytest.raises(ImportFailed):
        import_with_syllabus(store, model(), title="V", materials_text=HISTORY, doc_name="h",
                             syllabus_text="# Heading only\n\nIntro line:\n")
    assert store.list_courses() == []


def test_a_plain_import_has_no_syllabus_section():
    store = new_store()
    cid = import_materials(store, scripted(), title="V", text=HISTORY, doc_name="h")
    assert "syllabus" not in get_import_report(store, cid)


# ---------------------------------------------------------------- parsing

def test_parse_numbered_lines_and_ignore_intro_and_headings():
    text = "# Unit 4\n\nBy the end you should be able to:\n\n1. Explain the road.\n2) Describe the gates.\n(3) Trace the toll.\n"
    assert parse_syllabus(text) == ["Explain the road.", "Describe the gates.", "Trace the toll."]


def test_parse_bulleted_lines():
    text = "Topics\n- Explain the road.\n* Describe the gates.\n+ Trace the toll.\n\u2022 Compare the towers.\n"
    assert parse_syllabus(text) == ["Explain the road.", "Describe the gates.", "Trace the toll.",
                                    "Compare the towers."]


def test_parse_plain_lines():
    text = "# Syllabus\nThe Salt Road\n\nBeacon towers and the border\nIntro to the Long Winter:\n"
    assert parse_syllabus(text) == ["The Salt Road", "Beacon towers and the border"]


def test_parse_drops_markers_blank_lines_and_runs_of_spaces():
    assert parse_syllabus("1.   The   Salt  Road\r\n\r\n2. ok\r\n") == ["The Salt Road"]
    assert parse_syllabus("") == [] and parse_syllabus(None) == []


def test_numbers_inside_a_plain_line_are_not_markers():
    assert parse_syllabus("1869 was the year of the golden spike\n") == ["1869 was the year of the golden spike"]


# ---------------------------------------------------------------- the pieces

def test_stemming_and_content_words():
    assert SY.stem("laborers") == "labor" and SY.stem("spikes") == "spik" and SY.stem("ceremony") == "ceremony"
    assert "pony" in SY.content_words("Explain how the Pony Express carried mail")
    assert "explain" not in SY.content_words("Explain how the Pony Express carried mail")


def test_retrieval_is_deterministic_and_ranks_the_matching_passage_first():
    from codecoach.core.passages import split_document
    idx = SY.Index(split_document("h", HISTORY, "text"))
    top = idx.top(ROAD_LINE)
    assert top and ROAD in top[0].text and len(top) <= SY.TOP_K
    assert [p.id for p in idx.top(ROAD_LINE)] == [p.id for p in top]
    assert idx.top("zebra migration") == []
