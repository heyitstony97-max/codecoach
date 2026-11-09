"""Deterministic parts: verbs, graph repair, section split, and the real files when they exist."""

import os
from pathlib import Path

import pytest

from codecoach.core.models import Passage
from codecoach.core.passages import split_document
from codecoach.importer.graph import break_cycles, clean_edges, fallback_edges, is_acyclic
from codecoach.importer.sections import has_headings, split_sections
from codecoach.importer.verbs import VERB_FORMATS, VERBS, allowed_formats, normalize_verb
from importer_helpers import HISTORY, OUTLINE

FORMATS = {"mc", "cloze", "order", "match", "short", "explain", "code", "math", "numeric"}


# ---------------------------------------------------------------- verbs

def test_every_verb_has_known_formats_and_nothing_extra():
    assert VERBS == ("identify", "define", "order", "compare", "explain",
                     "compute", "trace", "write-code", "solve", "convert")
    assert set(VERB_FORMATS) == set(VERBS)
    for v, fs in VERB_FORMATS.items():
        assert fs and set(fs) <= FORMATS, v


def test_allowed_formats_respect_kind():
    assert allowed_formats("write-code", "code") == ("code",)
    assert allowed_formats("order", "reading") == ("order",)
    assert allowed_formats("trace", "code") == ("mc", "short", "code")
    assert allowed_formats("compute", "math") == ("math", "numeric")
    assert allowed_formats("compute", "reading") == ("explain",)      # nothing checkable fits: self-rated
    assert normalize_verb(" Write_Code ") == "write-code"
    with pytest.raises(KeyError):
        allowed_formats("appreciate")


# ---------------------------------------------------------------- graph

def test_cycle_break_two_node_cycle_drops_one():
    kept, dropped, n = break_cycles([("a", "b"), ("b", "a")], ["a", "b"])
    assert n == 1 and dropped == [("b", "a")] and kept == [("a", "b")]


def test_cycle_break_shared_edge_drops_just_one():
    # cycles a-b-c-a and a-b-d-a share a->b: dropping it alone fixes both (not two back edges)
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("b", "d"), ("d", "a")]
    kept, dropped, n = break_cycles(edges, ["a", "b", "c", "d"])
    assert n == 1 and dropped == [("a", "b")] and is_acyclic(kept)
    shared = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "b")]
    # cycles a-b-c-a and b-c-d-b share b->c: dropping it alone fixes both
    kept, dropped, n = break_cycles(shared, ["a", "b", "c", "d"])
    assert dropped == [("b", "c")] and is_acyclic(kept)


def test_cycle_break_counts_components_and_leaves_dags_alone():
    dag = [("a", "b"), ("b", "c"), ("a", "c")]
    assert break_cycles(dag, ["a", "b", "c"]) == (dag, [], 0)
    two = [("a", "b"), ("b", "a"), ("c", "d"), ("d", "c"), ("b", "c")]
    kept, dropped, n = break_cycles(two, ["a", "b", "c", "d"])
    assert n == 2 and len(dropped) == 2 and is_acyclic(kept) and ("b", "c") in kept


def test_cycle_break_large_component_uses_greedy_and_still_ends_acyclic():
    ids = [f"n{i:02d}" for i in range(30)]
    ring = [(ids[i], ids[(i + 1) % 30]) for i in range(30)]
    chords = [(ids[i], ids[(i + 7) % 30]) for i in range(0, 30, 3)]
    kept, dropped, n = break_cycles(ring + chords, ids)
    assert n >= 1 and is_acyclic(kept) and dropped
    assert len(kept) + len(dropped) == len(ring + chords)


def test_clean_and_fallback_edges():
    assert clean_edges([("a", "b"), ("a", "b"), ("a", "a"), ("x", "b"), ("b", "c")], {"a", "b", "c"}) == \
        [("a", "b"), ("b", "c")]
    assert fallback_edges(["a", "b", "c"]) == [("a", "b"), ("b", "c")]
    assert fallback_edges(["a"]) == []


# ---------------------------------------------------------------- sections

def test_history_text_splits_by_chapter():
    ps = split_document("h", HISTORY, "text")
    secs = split_sections(ps, HISTORY, "text", "Valdoria")
    assert [(s.unit, s.title) for s in secs] == [("Ch. 1", "Ch. 1, p. 1"), ("Ch. 2", "Ch. 2, p. 2")] or \
        [s.unit for s in secs] == ["Ch. 1", "Ch. 2"]
    assert sum(len(s.passages) for s in secs) == len(ps)
    assert len(secs) == 2


def test_markdown_outline_sections_follow_headings_and_units_follow_level_two():
    ps = split_document("o", OUTLINE, "markdown")
    secs = split_sections(ps, OUTLINE, "markdown", "C")
    assert has_headings(OUTLINE)
    assert [s.title for s in secs][:3] == ["Your first program", "Variables and types", "Operators and expressions"]
    assert {s.unit for s in secs} == {"Unit 1: Getting started", "Unit 2: Control flow", "Unit 3: Memory",
                                       "Unit 4: Structure"}
    assert len(secs) == 11
    assert sum(len(s.passages) for s in secs) == len(ps)


def test_sections_are_deterministic_and_chunked():
    body = "\n\n".join(f"Sentence number {i} about nothing in particular." for i in range(25))
    ps = split_document("d", body, "text")
    a = split_sections(ps, body, "text", "T")
    b = split_sections(ps, body, "text", "T")
    assert [(s.title, s.passage_ids) for s in a] == [(s.title, s.passage_ids) for s in b]
    assert len(a) == 3 and all(len(s.passages) <= 10 for s in a)
    assert [s.title for s in a] == ["T, part 1", "T, part 2", "T, part 3"]


def test_markdown_repeated_headings_and_intro():
    md = "Opening words.\n\n# Same\n\nOne.\n\n# Same\n\nTwo.\n"
    ps = split_document("m", md, "markdown")
    secs = split_sections(ps, md, "markdown", "M")
    assert [s.title for s in secs] == ["Introduction", "Same", "Same"]
    assert [s.passage_ids for s in secs] == [["m:intro:1"], ["m:same:1"], ["m:same-2:1"]]


def test_empty_passages_give_no_sections():
    assert split_sections([], "", "text", "T") == []
    assert isinstance(Passage, type)


# ---------------------------------------------------------------- the demo materials (in the repo, always run)

MATERIALS = Path(__file__).resolve().parents[2] / "demo" / "materials"
CBASICS = MATERIALS / "c-basics.md"
HIST = MATERIALS / "history.md"


def test_demo_materials_split_deterministically_and_report_counts(capsys):
    counts = {}
    for name, path in (("c-basics.md", CBASICS), ("history.md", HIST)):
        text = path.read_text()
        kind = "markdown" if has_headings(text) else "text"
        ps = split_document("real", text, kind)
        secs = split_sections(ps, text, kind, name)
        assert ps and secs
        assert sum(len(s.passages) for s in secs) == len(ps)       # nothing lost, nothing duplicated
        assert [p.id for s in secs for p in s.passages] == [p.id for p in ps]
        assert len({p.id for p in ps}) == len(ps)                   # ids unique
        again = split_sections(split_document("real", text, kind), text, kind, name)
        assert [s.passage_ids for s in again] == [s.passage_ids for s in secs]
        assert all(len(s.passages) <= 10 for s in secs)
        counts[name] = (len(ps), len(secs))
    with capsys.disabled():
        print(f"\nDEMO FILE COUNTS (passages, sections): {counts}")
    assert counts["c-basics.md"][1] >= 8                            # at least one section per unit


# ---- course code language (A5c)

from codecoach.importer import infer_code_language  # noqa: E402


@pytest.mark.parametrize("text,lang", [
    ("Learn C on Linux with gcc. Use printf and scanf.", "c"),
    ("#include <stdio.h>\nint main(void){}", "c"),
    ("An introduction to the C programming language.", "c"),
    ("Python basics: def greet(name): and pip install requests", "python"),
    ("The causes of the French Revolution", None),
    ("Python and gcc", None),
])
def test_infer_code_language(text, lang):
    assert infer_code_language(text) == lang


def test_c_basics_demo_infers_c():
    from pathlib import Path
    assert infer_code_language("C Basics", (Path(__file__).parents[2] / "demo/materials/c-basics.md").read_text()) == "c"
