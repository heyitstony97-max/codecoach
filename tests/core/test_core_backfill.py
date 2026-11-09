"""backfill_doc_text and reconstruct_doc, and the devtools CLI."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from codecoach.core.models import Doc
from codecoach.core.passages import normalize_newlines, split_document
from codecoach.core.sources import AlignmentError, backfill_doc_text, reconstruct_doc
from codecoach.core.store import Store

ROOT = Path(__file__).resolve().parents[2]
DEMOS = [("history.md", "markdown"), ("c-basics.md", "markdown")]


def old_style_store(tmp_path, name, kind):
    """A doc saved the old way: passages without spans, no text."""
    raw = (ROOT / "demo" / "materials" / name).read_text(encoding="utf-8")
    s = Store(tmp_path / "old.db")
    s.put_doc(Doc(id="doc", course_id="c", name=name, kind=kind))
    ps = split_document("doc", raw, kind)
    s.put_passages([p.model_copy(update={"start": None, "end": None}) for p in ps])
    return s, raw, ps


@pytest.mark.parametrize("name,kind", DEMOS)
def test_backfill_matches_what_a_fresh_split_records(tmp_path, name, kind):
    s, raw, fresh = old_style_store(tmp_path, name, kind)
    assert s.get_doc_text("doc") is None
    r = backfill_doc_text(s, "doc", raw.replace("\n", "\r\n"))   # file with Windows endings still aligns
    assert (r["aligned"], r["total"]) == (len(fresh), len(fresh))
    assert s.get_doc_text("doc") == normalize_newlines(raw)
    got = s.list_passages(doc_id="doc")
    assert [(p.start, p.end) for p in got] == [(p.start, p.end) for p in fresh]


def test_wrong_file_is_refused_and_nothing_is_written(tmp_path):
    s, raw, ps = old_style_store(tmp_path, "history.md", "markdown")
    other = (ROOT / "demo" / "materials" / "c-basics.md").read_text(encoding="utf-8")
    with pytest.raises(AlignmentError) as e:
        backfill_doc_text(s, "doc", other)
    assert ps[0].id in str(e.value)
    assert s.get_doc_text("doc") is None
    assert all(p.start is None for p in s.list_passages(doc_id="doc"))


def test_one_missing_passage_refuses_all_and_names_it(tmp_path):
    s, raw, ps = old_style_store(tmp_path, "history.md", "markdown")
    victim = ps[10]
    broken = raw.replace(victim.text.split(". ")[0], "REDACTED", 1)
    assert broken != raw
    with pytest.raises(AlignmentError) as e:
        backfill_doc_text(s, "doc", broken)
    assert e.value.passage_id == victim.id and e.value.aligned == 10 and e.value.total == len(ps)
    assert s.get_doc_text("doc") is None
    assert all(p.start is None for p in s.list_passages(doc_id="doc"))


def test_dry_run_writes_nothing_and_unknown_doc_is_an_error(tmp_path):
    s, raw, ps = old_style_store(tmp_path, "history.md", "markdown")
    r = backfill_doc_text(s, "doc", raw, dry_run=True)
    assert r["aligned"] == len(ps) and not r["written"] and s.get_doc_text("doc") is None
    with pytest.raises(AlignmentError):
        backfill_doc_text(s, "nope", raw)


@pytest.mark.parametrize("name,kind", DEMOS + [("syllabus.md", "markdown")])
def test_reconstruct_spans_are_exact(tmp_path, name, kind):
    _, _, ps = old_style_store(tmp_path, name, kind)
    text, spans = reconstruct_doc(ps, kind)
    assert [p.id for p in ps] == list(spans)
    last = 0
    for p in ps:
        a, b = spans[p.id]
        assert text[a:b] == p.text and a >= last
        last = b
    if kind == "markdown":
        assert "## " in text


def test_cli_prints_aligned_total_and_refuses_a_wrong_file(tmp_path):
    s, raw, ps = old_style_store(tmp_path, "history.md", "markdown")
    s.close()
    cli = str(ROOT / "devtools" / "backfill_sources.py")
    db = str(tmp_path / "old.db")
    good = ROOT / "demo" / "materials" / "history.md"
    bad = ROOT / "demo" / "materials" / "c-basics.md"
    run = lambda f, *x: subprocess.run([sys.executable, cli, "--store", db, "--doc", "doc", "--file", str(f), *x],  # noqa: E731
                                       capture_output=True, text=True)
    r = run(bad)
    assert r.returncode == 2 and "aligned 0/" in r.stderr
    r = run(good, "--dry-run")
    assert r.returncode == 0 and f"aligned {len(ps)}/{len(ps)}" in r.stdout
    assert sqlite3.connect(db).execute("SELECT text FROM docs").fetchone()[0] is None
    r = run(good)
    assert r.returncode == 0 and "written" in r.stdout
    assert sqlite3.connect(db).execute("SELECT text FROM docs").fetchone()[0] is not None
