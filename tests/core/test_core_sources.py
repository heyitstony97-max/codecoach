"""Sources: docs.text, passage spans, the migration, and the span property over every fixture and demo file."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from codecoach.core.models import Doc, Passage
from codecoach.core.passages import normalize_newlines, split_document
from codecoach.core.store import MIGRATIONS, Store

ROOT = Path(__file__).resolve().parents[2]


def collapse(s: str) -> str:
    return " ".join(s.split())


def _sample_files() -> list[Path]:
    files = sorted((ROOT / "demo" / "materials").glob("*.md"))
    files += sorted((ROOT / "tests" / "importer" / "fixtures").rglob("*.md"))
    files += sorted((ROOT / "tests" / "importer" / "fixtures").rglob("*.txt"))
    return files


EXTRA = {
    "crlf": "# Title\r\n\r\nFirst line\r\nsecond line.\r\n\r\nNext.\r\n",
    "formfeed": "Chapter 2\n\nOne.\nTwo.\n\fThree starts here.\n\nFour.\n--- page 9 ---\nNine.\n\fTen.\n",
    "indent_tabs": "# H\n\n   indented\tpara  with   gaps  \n\t second line\n\n\n\nlast",
    "fence": "# Code\n\nIntro text.\n\n```c\nint main() {\n    return 0;\n}\n```\n\nAfter.\n\n```\nunterminated\n\n  keep",
    "dup_heading": "# A\n\none\n\n# A\n\ntwo\n",
}


def check_spans(doc_id: str, raw: str, kind: str) -> int:
    text = normalize_newlines(raw)
    ps = split_document(doc_id, raw, kind)
    prev_end = -1
    for p in ps:
        assert p.start is not None and p.end is not None, p.id
        assert 0 <= p.start < p.end <= len(text), p.id
        assert p.start >= prev_end, f"{p.id} overlaps or is out of order"
        prev_end = p.end
        span = text[p.start:p.end]
        if "\n" in p.text:   # fenced code: lines keep their newlines, so the span matches line by line
            assert [ln.rstrip() for ln in span.split("\n")] == p.text.split("\n"), p.id
        assert collapse(span) == collapse(p.text), p.id
    return len(ps)


@pytest.mark.parametrize("name", sorted(EXTRA))
@pytest.mark.parametrize("kind", ["markdown", "text", "pdf_text"])
def test_spans_on_synthetic_texts(name, kind):
    assert check_spans("doc", EXTRA[name], kind) >= 1


@pytest.mark.parametrize("path", _sample_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_spans_on_every_fixture_and_demo_file(path):
    raw = path.read_text(encoding="utf-8")
    for kind in ("markdown", "text", "pdf_text"):
        assert check_spans("doc", raw, kind) >= 1


def test_demo_files_exist():
    assert len(_sample_files()) >= 3


def test_normalize_newlines_changes_only_line_endings():
    assert normalize_newlines("a\r\nb\rc\nd\f e  \t") == "a\nb\nc\nd\f e  \t"


# ---------------------------------------------------------------- store


def _cols(db: Path, table: str) -> set[str]:
    c = sqlite3.connect(db)
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def test_new_store_has_the_columns(tmp_path):
    s = Store(tmp_path / "a.db")
    s.close()
    assert "text" in _cols(tmp_path / "a.db", "docs")
    assert {"start", "end"} <= _cols(tmp_path / "a.db", "passages")


def test_put_doc_text_roundtrip_and_text_kept_when_not_given(tmp_path):
    s = Store(tmp_path / "a.db")
    s.put_doc(Doc(id="d", course_id="c", name="n.md", kind="markdown"), text="# A\n\nhello\n")
    s.put_doc(Doc(id="d", course_id="c", name="renamed.md", kind="markdown"))
    assert s.get_doc_text("d") == "# A\n\nhello\n"
    assert s.get_doc("d").name == "renamed.md"
    s.put_passages([Passage(id="d:a:1", doc_id="d", location="A, para 1", text="hello", start=5, end=10)])
    p = s.get_passage("d:a:1")
    assert (p.start, p.end) == (5, 10)
    assert s.list_passages(doc_id="d")[0].end == 10


LIVE = Path.home() / ".hermes" / "plugin-data" / "codecoach" / "codecoach.db"


def _old_store(path: Path) -> None:
    """A store as it was before this change: migrations 0..2 only."""
    c = sqlite3.connect(path)
    for i, script in enumerate(MIGRATIONS[:3]):
        for stmt in [x.strip() for x in script.split(";") if x.strip()]:
            c.execute(stmt)
        c.execute(f"PRAGMA user_version={i + 1}")
    c.execute("INSERT INTO courses(id,title,mode,created_at) VALUES('c','T','strict','2026-01-01T00:00:00.000000+00:00')")
    c.execute("INSERT INTO docs(id,course_id,name,kind) VALUES('d','c','d.md','markdown')")
    c.execute("INSERT INTO passages(id,doc_id,location,text) VALUES('d:a:1','d','A, para 1','hello')")
    c.commit()
    c.close()


def test_migration_keeps_an_old_store_and_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    _old_store(db)
    for _ in range(2):          # opening twice: the second open must change nothing
        s = Store(db)
        assert s.schema_version() == len(MIGRATIONS)
        assert s.get_doc_text("d") is None
        p = s.get_passage("d:a:1")
        assert (p.text, p.start, p.end) == ("hello", None, None)
        assert s.get_doc("d").name == "d.md"
        s.close()


def _snapshot(db: Path) -> dict:
    c = sqlite3.connect(db)
    try:
        return {
            "courses": c.execute("SELECT * FROM courses ORDER BY id").fetchall(),
            "passages": c.execute("SELECT seq,id,doc_id,location,text FROM passages ORDER BY seq").fetchall(),
            "attempts": c.execute("SELECT * FROM attempts ORDER BY id").fetchall(),
            "docs": c.execute("SELECT id,course_id,name,kind FROM docs ORDER BY id").fetchall(),
        }
    finally:
        c.close()


@pytest.mark.skipif(not LIVE.exists(), reason="no live store on this machine")
def test_migration_twice_over_a_copy_of_the_live_store(tmp_path):
    copy = tmp_path / "live.db"
    src = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    dst.close()
    src.close()
    before = _snapshot(copy)
    for _ in range(2):
        s = Store(copy)
        assert s.schema_version() == len(MIGRATIONS)
        s.close()
        assert _snapshot(copy) == before
