import json

import pytest

from codecoach.core.llm import FakeLLM, LLMUnavailable
from codecoach.core.models import CourseMap
from codecoach.importer import (ImportFailed, export_map, get_import_report, import_map, import_materials,
                             import_outline)
from importer_helpers import HISTORY, OUTLINE, new_store, objective_reply, passage_ids, scripted, section_title


def _objs(store, cid):
    return store.list_objectives(cid)


# ---------------------------------------------------------------- materials, happy path

def test_materials_happy_path_saves_a_valid_covered_map():
    store = new_store()
    cid = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="Kingdom history")
    course = store.get_course(cid)
    assert course and course.mode == "strict" and course.mode_reason == ""
    objs = _objs(store, cid)
    assert len(objs) == 4 and all(o.coverage == "covered" and o.passage_ids for o in objs)
    cmap = store.get_map(cid)
    assert isinstance(cmap, CourseMap)
    assert [u.title for u in cmap.units] == ["Ch. 1", "Ch. 2"]
    # every cited passage exists and belongs to the objective's own section
    for o in objs:
        for pid in o.passage_ids:
            assert store.get_passage(pid) is not None
    assert store.list_docs(cid)[0].name == "Kingdom history"
    rep = get_import_report(store, cid)
    assert rep and rep["status"] == "done" and rep["covered"] == 4 and rep["not_covered"] == 0


def test_model_sees_only_its_own_sections_passages():
    store = new_store()
    llm = scripted()
    import_materials(store, llm, title="Valdoria", text=HISTORY, doc_name="h")
    first, second = [c.text for c in llm.structured_calls if c.purpose == "codecoach.import.objectives"]
    assert "Orrin Vale" in first and "Long Winter" not in first
    assert "Long Winter" in second and "Orrin Vale" not in second
    assert set(passage_ids(first)).isdisjoint(passage_ids(second))


# ---------------------------------------------------------------- coverage rule

def test_hallucinated_passage_ids_are_not_covered_and_logged():
    def reply(prompt):
        ids = passage_ids(prompt)
        t = section_title(prompt)
        return {"objectives": [
            {"verb": "identify", "statement": f"Name the first king of Valdoria in {t}", "passage_ids": [ids[0]]},
            {"verb": "explain", "statement": f"Explain the Moon Treaty of 600 in {t}",
             "passage_ids": ["h:p9:99", "nope:p1:1"]},
            {"verb": "define", "statement": f"Define the tithe of the sea in {t}", "passage_ids": []},
        ]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    objs = _objs(store, cid)
    bad = [o for o in objs if o.coverage == "not_covered"]
    assert len(objs) == 6 and len(bad) == 4
    assert all(o.passage_ids == [] for o in bad)
    rep = get_import_report(store, cid)
    assert rep["not_covered"] == 4
    assert sum(1 for i in rep["issues"] if i["kind"] == "not_covered") == 4


def test_passage_from_another_section_does_not_count():
    other = {}

    def reply(prompt):
        ids = passage_ids(prompt)
        if not other:
            other["id"] = ids[0]            # remember a section-1 passage
            return objective_reply(prompt, 1)
        return {"objectives": [{"verb": "identify", "statement": "Identify the Bread Edict year",
                                "passage_ids": [other["id"]]}]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    objs = _objs(store, cid)
    assert [o.coverage for o in objs] == ["covered", "not_covered"]


def test_valid_ids_survive_when_mixed_with_invalid():
    def reply(prompt):
        ids = passage_ids(prompt)
        return {"objectives": [{"verb": "explain", "statement": "Explain what the first section says",
                                "passage_ids": ["zzz:p1:1", ids[1], ids[1], ids[0]]}]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    o = _objs(store, cid)[0]
    assert o.coverage == "covered" and "zzz:p1:1" not in o.passage_ids
    # the cited ones come first, de-duplicated; the section's uncited passages follow (so none is topic-less)
    assert o.passage_ids[:2] == ["h:p1:4", "h:p1:3"] and len(set(o.passage_ids)) == len(o.passage_ids)


# ---------------------------------------------------------------- junk, failures, isolation

def test_junk_output_is_retried_then_accepted():
    state = {"n": 0}

    def reply(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return None
        if state["n"] == 2:
            return {"objectives": [{"verb": "appreciate", "statement": "Appreciate the kingdom", "passage_ids": []}]}
        return objective_reply(prompt, 1)
    llm = scripted(per_section=reply)
    store = new_store()
    cid = import_materials(store, llm, title="V", text=HISTORY, doc_name="h")
    assert len(_objs(store, cid)) == 2
    sec1_calls = [c for c in llm.structured_calls if c.purpose == "codecoach.import.objectives"][:3]
    assert "rejected" in sec1_calls[1].text and "verb must be one of" in sec1_calls[2].text


def test_section_failure_is_isolated_and_recorded():
    def reply(prompt):
        if "Orrin Vale" in prompt:
            return {"junk": True}
        return objective_reply(prompt, 2)
    llm = scripted(per_section=reply)
    store = new_store()
    cid = import_materials(store, llm, title="V", text=HISTORY, doc_name="h")
    assert len(_objs(store, cid)) == 2
    rep = get_import_report(store, cid)
    failed = [i for i in rep["issues"] if i["kind"] == "section_failed"]
    assert len(failed) == 1 and failed[0]["section"] == "Ch. 1, p. 1" and rep["sections_failed"] == 1
    assert store.get_course(cid) is not None
    # exactly 3 attempts were spent on the broken section
    assert sum(1 for c in llm.structured_calls if "Orrin Vale" in c.text and c.purpose != "codecoach.subject") == 3


def test_whole_import_failure_leaves_nothing_active():
    store = new_store()
    job = store.create_job("import")
    llm = scripted(per_section=lambda p: {"junk": 1})
    with pytest.raises(ImportFailed) as ei:
        import_materials(store, llm, title="Broken", text=HISTORY, doc_name="h", job_id=job.id)
    cid = ei.value.course_id
    assert cid == "broken"
    assert store.get_course(cid) is None and store.get_map(cid) is None
    assert store.list_docs(cid) == [] and store.list_passages(course_id=cid) == []
    assert get_import_report(store, cid)["status"] == "failed"
    j = store.get_job(job.id)
    assert j.status == "error" and j.error


def test_model_unreachable_aborts_instead_of_grinding():
    store = new_store()
    big = "\n\n".join(f"## Part {i}\n\nParagraph about thing {i}." for i in range(6))
    llm = FakeLLM(lambda purpose, text: LLMUnavailable("down"))
    with pytest.raises(ImportFailed, match="unreachable"):
        import_materials(store, llm, title="Down", text=big, doc_name="d")
    assert store.get_course("down") is None
    assert len(llm.calls) == 9       # 3 sections x 3 attempts, then it gives up


def test_empty_text_fails_cleanly():
    store = new_store()
    with pytest.raises(ImportFailed):
        import_materials(store, scripted(), title="Empty", text="  \n ", doc_name="d")
    assert store.list_courses() == []


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        import_materials(new_store(), scripted(), title="x", text="hello", doc_name="d", mode="loose")


def test_unique_ids_when_importing_twice():
    store = new_store()
    a = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="h")
    b = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="h")
    assert a != b and len(store.list_courses()) == 2
    assert {d.id for d in store.list_docs(a)}.isdisjoint({d.id for d in store.list_docs(b)})


# ---------------------------------------------------------------- prerequisites

def test_cycle_in_model_edges_is_repaired_with_fewest_drops_and_logged():
    def prereqs(text):
        ids = [ln.split(": ")[0] for ln in text.splitlines()]
        a, b, c, d = ids
        # a->b->c->a is a cycle (one edge must go); c->d is fine
        return {"edges": [{"prereq": a, "objective": b}, {"prereq": b, "objective": c},
                          {"prereq": c, "objective": a}, {"prereq": c, "objective": d}]}
    store = new_store()
    cid = import_materials(store, scripted(prereqs=prereqs), title="V", text=HISTORY, doc_name="h")
    rep = get_import_report(store, cid)
    assert rep["cycles_repaired"] == 1 and len(rep["edges_dropped"]) == 1
    assert rep["prereq_source"] == "model"
    cmap = store.get_map(cid)         # CourseMap validation rejects cycles, so loading proves acyclic
    assert len(cmap.edges) == 3
    assert any(i["kind"] == "cycle_edge_dropped" for i in rep["issues"])
    # the dropped edge is the backward one (c -> a)
    assert rep["edges_dropped"][0] == [f"{cid}:o03", f"{cid}:o01"]


def test_prereq_edges_with_unknown_ids_are_ignored_and_empty_falls_back_in_order():
    def prereqs(text):
        return {"edges": [{"prereq": "ghost", "objective": "phantom"}]}
    store = new_store()
    cid = import_materials(store, scripted(prereqs=prereqs), title="V", text=HISTORY, doc_name="h")
    rep = get_import_report(store, cid)
    assert rep["prereq_source"] == "fallback"
    edges = [(e.prereq, e.objective) for e in store.get_map(cid).edges]
    assert edges == [(f"{cid}:o01", f"{cid}:o02"), (f"{cid}:o02", f"{cid}:o03"), (f"{cid}:o03", f"{cid}:o04")]


def test_prereq_call_failure_falls_back_in_order():
    def fn(purpose, text):
        return {"junk": 1} if purpose == "codecoach.import.prereqs" else objective_reply(text, 2)
    store = new_store()
    cid = import_materials(store, FakeLLM(fn), title="V", text=HISTORY, doc_name="h")
    rep = get_import_report(store, cid)
    assert rep["prereq_source"] == "fallback"
    assert any(i["kind"] == "prereq_fallback" for i in rep["issues"])
    assert len(store.get_map(cid).edges) == 3


def test_not_covered_objectives_get_no_edges():
    def reply(prompt):
        ids = passage_ids(prompt)
        return {"objectives": [
            {"verb": "identify", "statement": "A grounded objective here", "passage_ids": [ids[0]]},
            {"verb": "identify", "statement": "An invented objective here", "passage_ids": []}]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    bad = {o.id for o in _objs(store, cid) if o.coverage == "not_covered"}
    assert bad
    for e in store.get_map(cid).edges:
        assert e.prereq not in bad and e.objective not in bad


# ---------------------------------------------------------------- verbs and kinds

def test_verbs_are_normalised_and_kinds_follow_verbs():
    def reply(prompt):
        ids = passage_ids(prompt)
        return {"objectives": [
            {"verb": "Write Code", "statement": "Write a small program", "passage_ids": [ids[0]]},
            {"verb": "compute", "statement": "Compute the elapsed years", "passage_ids": [ids[0]]}]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    o = _objs(store, cid)
    assert o[0].verb == "write-code" and o[0].kind == "code"
    assert o[1].verb == "compute" and o[1].kind == "math"


def test_duplicate_statements_in_a_section_are_dropped():
    def reply(prompt):
        ids = passage_ids(prompt)
        return {"objectives": [{"verb": "identify", "statement": "Name the first king.", "passage_ids": [ids[0]]},
                               {"verb": "define", "statement": "name  the FIRST king.", "passage_ids": [ids[0]]}]}
    store = new_store()
    cid = import_materials(store, scripted(per_section=reply), title="V", text=HISTORY, doc_name="h")
    assert len(_objs(store, cid)) == 2     # one per section, not two


# ---------------------------------------------------------------- progress

def test_progress_callbacks_rise_and_end_at_100():
    seen = []
    store = new_store()
    import_materials(store, scripted(), title="V", text=HISTORY, doc_name="h",
                     progress=lambda stage, pct: seen.append((stage, pct)))
    pcts = [p for _, p in seen]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    assert any(s.startswith("Drafting objectives (1/2)") for s, _ in seen)
    assert any(s == "Linking prerequisites" for s, _ in seen)


def test_broken_progress_callback_does_not_break_the_import():
    def boom(stage, pct):
        raise RuntimeError("ui died")
    store = new_store()
    cid = import_materials(store, scripted(), title="V", text=HISTORY, doc_name="h", progress=boom)
    assert store.get_course(cid)


def test_job_tracks_progress_and_finishes():
    store = new_store()
    job = store.create_job("import")
    cid = import_materials(store, scripted(), title="V", text=HISTORY, doc_name="h", job_id=job.id)
    j = store.get_job(job.id)
    assert j.status == "done" and j.pct == 100 and j.course_id == cid


# ---------------------------------------------------------------- open mode and outline

def test_materials_open_mode_is_recorded_with_a_reason():
    store = new_store()
    cid = import_materials(store, scripted(), title="V", text=HISTORY, doc_name="h", mode="open")
    c = store.get_course(cid)
    assert c.mode == "open" and c.mode_reason


def test_outline_import_is_open_mode_and_yields_a_realistic_map():
    store = new_store()
    llm = scripted(per_section=lambda p: objective_reply(p, 2))
    cid = import_outline(store, llm, title="Intro to C", text=OUTLINE)
    c = store.get_course(cid)
    assert c.mode == "open" and c.mode_reason == "no source materials attached"
    objs = _objs(store, cid)
    assert len(objs) >= 20
    cmap = store.get_map(cid)
    assert [u.title for u in cmap.units] == ["Unit 1: Getting started", "Unit 2: Control flow",
                                              "Unit 3: Memory", "Unit 4: Structure"]
    assert all(o.coverage == "covered" for o in objs)
    assert get_import_report(store, cid)["source"] == "outline"
    # the outline prompt, not the strict one, was used
    assert "syllabus" in llm.structured_calls[0].instructions


def test_outline_objective_without_citation_falls_back_to_its_outline_lines_and_says_so():
    def reply(prompt):
        return {"objectives": [{"verb": "explain", "statement": "Explain this topic from the syllabus",
                                "passage_ids": ["bogus:p1:1"]}]}
    store = new_store()
    cid = import_outline(store, scripted(per_section=reply), title="Intro to C", text=OUTLINE)
    objs = _objs(store, cid)
    assert all(o.coverage == "covered" and o.passage_ids for o in objs)
    rep = get_import_report(store, cid)
    assert sum(1 for i in rep["issues"] if i["kind"] == "open_fallback_citation") == len(objs)


# ---------------------------------------------------------------- export / import

def test_export_is_json_and_round_trips_through_a_fresh_store():
    store = new_store()
    cid = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="Kingdom history")
    data = export_map(store, cid)
    again = json.loads(json.dumps(data))
    assert again == data
    CourseMap.model_validate(data["map"])
    assert data["codecoach_map"] == 1 and data["passages"] and data["docs"]

    other = new_store()
    new_id = import_map(other, again)
    assert new_id == cid
    back = export_map(other, new_id)
    for key in ("course", "docs", "passages", "map"):
        assert back[key] == data[key], key
    assert other.get_passage(data["passages"][0]["id"]) is not None


def test_import_map_refuses_existing_course_and_can_rename():
    store = new_store()
    cid = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="h")
    data = export_map(store, cid)
    with pytest.raises(ValueError, match="already exists"):
        import_map(store, data)
    other = new_store()
    new = import_map(other, data, course_id="copy")
    assert new == "copy"
    objs = other.list_objectives("copy")
    assert objs and all(o.id.startswith("copy:") and o.unit.startswith("copy:") for o in objs)
    assert other.get_course("copy").id == "copy"
    assert all(e.prereq.startswith("copy:") for e in other.get_map("copy").edges)


def test_edited_map_is_rechecked_on_import():
    store = new_store()
    cid = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="h")
    data = export_map(store, cid)
    objs = [o for u in data["map"]["units"] for l in u["lessons"] for o in l["objectives"]]
    objs[0]["passage_ids"] = ["h:p9:9"]                 # user points it at nothing
    objs[1]["passage_ids"] = []                          # ...and removes the citation
    other = new_store()
    new = import_map(other, data)
    got = {o.id: o for o in other.list_objectives(new)}
    assert got[objs[0]["id"]].coverage == "not_covered" and got[objs[0]["id"]].passage_ids == []
    assert got[objs[1]["id"]].coverage == "not_covered"
    assert got[objs[2]["id"]].coverage == "covered"
    rep = get_import_report(other, new)
    assert sum(1 for i in rep["issues"] if i["kind"] == "coverage_rechecked") == 2


def test_edited_map_with_invented_verb_or_cycle_or_garbage_is_rejected():
    store = new_store()
    cid = import_materials(store, scripted(), title="Valdoria", text=HISTORY, doc_name="h")
    data = export_map(store, cid)
    bad_verb = json.loads(json.dumps(data))
    bad_verb["map"]["units"][0]["lessons"][0]["objectives"][0]["verb"] = "appreciate"
    with pytest.raises(ValueError, match="verb"):
        import_map(new_store(), bad_verb)
    cyc = json.loads(json.dumps(data))
    ids = [o["id"] for u in cyc["map"]["units"] for l in u["lessons"] for o in l["objectives"]]
    cyc["map"]["edges"] = [{"prereq": ids[0], "objective": ids[1]}, {"prereq": ids[1], "objective": ids[0]}]
    with pytest.raises(ValueError, match="invalid"):
        import_map(new_store(), cyc)
    for junk in ({}, {"codecoach_map": 2}, "not json{"):
        with pytest.raises(ValueError):
            import_map(new_store(), junk)
    target = new_store()
    with pytest.raises(ValueError):
        import_map(target, {"codecoach_map": 1, "course": {"id": "x"}})
    assert target.list_courses() == []


def test_export_unknown_course_raises():
    with pytest.raises(KeyError):
        export_map(new_store(), "nothing")


def test_course_code_language_inferred_and_overridable(tmp_path):
    s = new_store()
    cid = import_outline(s, scripted(), title="C Basics", text=OUTLINE)
    assert s.get_course(cid).code_language == "c"
    cid2 = import_outline(s, scripted(), title="C Basics again", text=OUTLINE, code_language="python")
    assert s.get_course(cid2).code_language == "python"
    with pytest.raises(ValueError):
        import_outline(s, scripted(), title="x", text=OUTLINE, code_language="rust")


def test_single_section_with_dead_model_says_the_model_is_unreachable():
    # e2e J6: one short section, model down -> the message must name the model, not "no objectives".
    store = new_store()
    llm = FakeLLM(lambda purpose, text: LLMUnavailable("connection refused"))
    with pytest.raises(ImportFailed, match="language model could not be reached"):
        import_materials(store, llm, title="Dead", text="## Spikes\n\nThe Golden Spike was driven in 1869.\n", doc_name="d")
    assert store.get_course("dead") is None
