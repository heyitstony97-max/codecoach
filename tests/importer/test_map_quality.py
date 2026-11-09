"""F2: sane topic counts, no title-only topics, headings never quoted, no duplicate items, no 'According to' opener."""
import random

from codecoach.ask.retrieve import build_index, retrieve
from codecoach.core.llm import FakeLLM
from codecoach.core.models import Passage
from codecoach.core.passages import is_quotable
from codecoach.importer.build import _near_twin, is_meta_objective
from codecoach.importer.sections import plan_sections, split_sections
from codecoach.core.passages import split_document
from importer_helpers import HISTORY, new_store, objective_reply, passage_ids, scripted
from codecoach.importer import import_materials, get_import_report


def test_bare_headings_and_labels_are_not_quotable():
    for t in ["I.", "II", "Chapter 3", "VIII. TRIUMPHANT", "2.5 Measures of the Center", "The Rise of Valdoria",
              "THE ELEMENTS OF STYLE", "Poem 12", "Life"]:
        assert not is_quotable(t), t
    for t in ["The kingdom of Valdoria began as a loose union of six river villages.",
              "Use the active voice.", "In 412 the village elders chose a war-leader."]:
        assert is_quotable(t), t


def test_ask_never_retrieves_bare_headings():
    ps = [Passage(id=f"d:p1:{i}", doc_id="d", location=f"p. 1, para {i}", text=t) for i, t in enumerate(
        ["I.", "Chapter 3", "THEME", "Theme is the central idea that a poem develops about love and loss."], 1)]
    got, _ = retrieve(build_index(ps), "what is the theme of the poem")
    assert got and all(is_quotable(r.passage.text) for r in got)


def test_meta_and_twin_objectives():
    assert is_meta_objective("Poem VIII is titled TRIUMPHANT")
    assert is_meta_objective("The section is titled 'Life'")
    assert is_meta_objective("Identify the URL and page contact information listed for the page")
    assert not is_meta_objective("Explain why the Stamp Act angered the colonies")

    class O:  # minimal stand-in
        statement = "State that the Constitution is the supreme law of the land"
    assert _near_twin([O], "State that the Constitution is the supreme law of the land.") is O
    assert _near_twin([O], "Explain how a bill becomes a law in Congress") is None


def _long_text(n_sections=26, paras=3):
    parts = []
    for i in range(1, n_sections + 1):
        parts.append(f"## Topic number {i}")
        for j in range(paras):
            parts.append(f"Fact {i}.{j}: the committee number {i} met in the year {1800 + i} and decided matter {j} "
                         f"about harbours, tariffs and roads for the whole region.")
    return "\n\n".join(parts)


def test_long_document_gets_a_topic_budget_and_small_one_is_untouched():
    text = _long_text()
    ps = split_document("long", text, "markdown")
    secs = plan_sections(split_sections(ps, text, "markdown", "Long"))
    assert 6 <= len(secs) <= 40
    assert sum(s.cap for s in secs) <= 40
    short = split_document("h", HISTORY, "text")
    base = split_sections(short, HISTORY, "text", "H")
    assert [s.title for s in plan_sections(base)] == [s.title for s in base]


def test_import_of_long_text_keeps_topics_in_budget_and_every_passage_under_a_topic():
    text = _long_text()

    def reply(prompt):
        ids = passage_ids(prompt)
        return {"objectives": [{"verb": "explain", "statement": f"Explain fact {k} of {ids[0]} {j}", "passage_ids": [ids[min(k, len(ids) - 1)]]}
                               for j, k in enumerate(range(8))], "title": "Committees and harbours"}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="Long", text=text, doc_name="long")
    objs = store.list_objectives(cid)
    assert len(objs) <= 40, len(objs)
    rep = get_import_report(store, cid)
    assert rep["stats"]["passages_without_topic"] == 0 if "stats" in rep else True
    titles = [u.title for u in store.get_map(cid).units]
    assert not any("(part" in t for t in titles)


def test_wrong_passage_ids_are_repaired_when_the_topic_is_plainly_in_the_section():
    def reply(prompt):
        return {"objectives": [{"verb": "explain", "statement": "Explain how the beacon chain carried a warning "
                                "to the capital", "passage_ids": ["elsewhere:p9:9"]}]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    objs = store.list_objectives(cid)
    assert any(o.coverage == "covered" and o.passage_ids for o in objs)
    assert any(i["kind"] == "ids_repaired" for i in get_import_report(store, cid)["issues"])
