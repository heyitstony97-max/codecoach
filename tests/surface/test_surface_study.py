"""S3 study tools: source guide, mistakes review, listen to a topic, study guide export, key words in the
revealed quote. Everything is assembled by code from the user's text, the bank and the attempt log."""

import re
from datetime import timedelta

import pytest
from surface_helpers import make_service, oid

from codecoach import study
from codecoach.core.models import Course, Doc, Passage
from codecoach.core.text import normalize
from codecoach.importer.build import OUTLINE_MODE_REASON
from codecoach.service import ServiceError

from test_surface_readaloud import FakeSynth, svc_with_audio  # noqa: E402


def _item_for(store, n, k=1):
    return store.get_item(f"it-{n:02d}-{k}")


def _right(svc, n, k=1):
    return svc.answer(f"it-{n:02d}-{k}", 1)


def _wrong(svc, n, k=1):
    return svc.answer(f"it-{n:02d}-{k}", 0)


# ------------------------------------------------------------------ mistakes

def test_mistakes_count_matches_store_and_leaves_when_correct():
    svc, store, cid = make_service(graph={1: [], 2: [1], 3: [2]})
    assert svc.mistakes(cid).count == 0
    _wrong(svc, 1)
    _wrong(svc, 2)
    _right(svc, 3)
    m = svc.mistakes(cid)
    direct = {i.item_id for i in [max((a for a in store.list_attempts(item_id=it.id)), key=lambda a: a.id)
                                   for it in store.list_items(course_id=cid) if store.list_attempts(item_id=it.id)]
              if i.score < 1.0}
    assert m.count == len(direct) == 2
    # oldest first, and the item leaves once its latest attempt is correct
    first = svc.next(cid, mistakes_upto=m.upto)
    assert first.item.id == "it-01-1"
    _right(svc, 1)
    assert svc.mistakes(cid).count == 1
    second = svc.next(cid, mistakes_upto=m.upto)
    assert second.item.id == "it-02-1"
    _right(svc, 2)
    assert svc.mistakes(cid).count == 0
    assert svc.next(cid, mistakes_upto=m.upto).kind == "done"


def test_mistakes_hint_credit_counts_as_below_full():
    svc, store, cid = make_service(graph={1: []})
    svc.hint("it-01-1")
    _right(svc, 1)                    # correct but after a hint: 0.8 credit
    assert svc.mistakes(cid).count == 1


def test_mistakes_session_is_capped_at_ten():
    svc, store, cid = make_service(graph={n: [] for n in range(1, 13)}, items_per=1)
    for n in range(1, 13):
        _wrong(svc, n)
    m = svc.mistakes(cid)
    assert m.count == 12 and m.session == 10


def test_mistake_answer_goes_through_normal_grading_and_fsrs():
    svc, store, cid = make_service(graph={1: []})
    _wrong(svc, 1)
    before = len(store.list_attempts(item_id="it-01-1"))
    r = _right(svc, 1)
    assert r.correct and len(store.list_attempts(item_id="it-01-1")) == before + 1
    assert store.get_review_state("it-01-1") is not None


# ------------------------------------------------------------------ key words

def test_key_spans_only_inside_the_quote():
    text = "Alpha was early. The key idea of topic 1 is Alpha1 and nothing else. Alpha1 again later."
    quote = "The key idea of topic 1 is Alpha1"
    sp = study.key_spans(text, quote, ["Alpha1"])
    assert sp == [(text.index("Alpha1"), text.index("Alpha1") + 6)]          # not the later occurrence


def test_key_spans_never_mark_absent_words():
    text = "The treaty was signed in 1863 by the delegates."
    assert study.key_spans(text, "The treaty was signed in 1863", ["1864"]) == []
    assert study.key_spans(text, "The treaty was signed in 1863", ["863"]) == []     # glued digits
    assert study.key_spans(text, "not in the passage at all", ["treaty"]) == []


def test_key_spans_text_matches_key_after_normalisation():
    text = "He said \u201cthe Union\u201d \u2014 and the Confederacy \u2014 fell."
    quote = "the Confederacy \u2014 fell"
    for a, b in study.key_spans(text, quote, ["Confederacy"]):
        assert normalize(text[a:b]) == "confederacy"


def test_answer_reveals_key_spans_on_wrong_answer():
    svc, store, cid = make_service(graph={1: []})
    r = _wrong(svc, 1)
    c = r.citations[0]
    p = store.get_passage(c.passage_id)
    assert c.key_spans
    for a, b in c.key_spans:
        assert normalize(p.text[a:b]) == "alpha1"
        assert normalize(p.text[a:b]) in normalize(c.quote)


# ------------------------------------------------------------------ source guide

def test_source_guide_headings_topics_and_gaps():
    svc, store, cid = make_service(not_covered=(3,), graph={1: [], 2: [1], 3: [2]})
    v = svc.source(cid, cid)
    g = svc.source_guide(cid, cid)
    assert [t.objective_id for t in g.topics] == [oid(1), oid(2)]
    assert all(t.passage_id in {p.id for p in v.passages} for t in g.topics)
    assert g.not_covered == ["Know topic 3"] and g.not_covered_label == "Not in your materials"
    assert [h.title for h in g.headings]            # fallback to sections when the text has no headings
    for h in g.headings:
        assert 0 <= h.start <= len(v.text)


def test_headings_of_markdown_skips_fenced_code():
    text = "# One\n\ntext\n\n```\n# not a heading\n```\n\n## Two\n"
    hs = study.headings_of(text, [])
    assert [(h.title, h.level) for h in hs] == [("One", 1), ("Two", 2)]
    assert text[hs[1].start:].startswith("## Two")


# ------------------------------------------------------------------ listen: the text sent is the passage

def test_listen_sends_each_passage_verbatim(tmp_path):
    svc, store, cid, synth = svc_with_audio(tmp_path, graph={1: [], 2: [1]})
    obj = store.get_objective(oid(1))
    for pid in obj.passage_ids:
        clip = svc.passage_audio(cid, pid)
        assert clip.path.exists()
    assert synth.texts == [store.get_passage(pid).text for pid in obj.passage_ids]
    assert all(t.encode("utf-8") == store.get_passage(pid).text.encode("utf-8")
               for t, pid in zip(synth.texts, obj.passage_ids))


# ------------------------------------------------------------------ export

def _allowed_lines(svc, store, cid):
    allowed = set(study.EXPORT_LABELS) | {""}
    for p in store.list_passages(course_id=cid):
        for ln in p.text.split("\n"):
            allowed.add("> " + ln if ln else ">")
        allowed.add(study.LABEL_SOURCE + p.location)
    for o in store.list_objectives(cid):
        allowed.add(o.statement)
        allowed.add("### " + o.statement)
        allowed.add("- " + o.statement)
    return allowed


def test_export_every_line_is_known_content(tmp_path):
    svc, store, cid = make_service(not_covered=(3,), graph={1: [], 2: [1], 3: [2]})
    svc.exports_dir = tmp_path / "exports"
    out = svc.export_study_guide(cid)
    assert out.filename == f"{cid}-study-guide.md"
    assert (tmp_path / "exports" / out.filename).read_text(encoding="utf-8") == out.markdown
    allowed = _allowed_lines(svc, store, cid)
    prompts = {" ".join(i.prompt.split()) for i in store.list_items(course_id=cid)}
    answers = {" ".join(str(__import__("codecoach.service", fromlist=["x"]).correct_answer_text(i, store)).split())
               for i in store.list_items(course_id=cid)}
    quotes = {" ".join(c.quote.split()) for i in store.list_items(course_id=cid) for c in i.citations}
    locs = {p.location for p in store.list_passages(course_id=cid)}
    md_lines = out.markdown.split("\n")
    assert md_lines[0] == f"# Fixture"          # a materials course carries no outline line
    for ln in md_lines:
        if ln in allowed or ln.startswith("# ") and ln[2:] == "Fixture":
            continue
        if ln.startswith("## ") and (ln[3:] in study.EXPORT_LABELS or ln[3:].startswith(study.LABEL_UNIT)):
            continue
        m = re.match(r"^(\d+)\. (.*)$", ln)
        if m and (m.group(2) in prompts or m.group(2) in answers):
            continue
        s = ln.strip()
        if s.startswith("> ") and s[2:] in quotes:
            continue
        if s.startswith(study.LABEL_SOURCE) and s[len(study.LABEL_SOURCE):] in locs:
            continue
        pytest.fail(f"unexpected export line: {ln!r}")
    assert "Know topic 3" in out.markdown and out.markdown.index("Not in your materials") < out.markdown.index("Practice questions")
    assert out.markdown.index("Practice questions") < out.markdown.index("Answer key")
    # prompts come before the key, answers only after it
    head = out.markdown.split("## Answer key")[0]
    assert "Alpha1" not in head.split("## Practice questions")[1]


def test_export_open_course_has_outline_line(tmp_path):
    svc, store, cid = make_service(graph={1: []})
    store.put_course(Course(id=cid, title="Fixture", mode="open", mode_reason=OUTLINE_MODE_REASON))
    svc.exports_dir = tmp_path
    assert svc.export_study_guide(cid).markdown.split("\n")[0] == "Course outline: not source material"


def test_export_without_exports_dir_still_returns_markdown():
    svc, store, cid = make_service(graph={1: []})
    out = svc.export_study_guide(cid)
    assert out.path is None and out.markdown.startswith("# Fixture")
