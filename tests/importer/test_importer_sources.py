"""Every import path keeps the document text as read and a span for each passage."""

from codecoach.core.llm import FakeLLM
from codecoach.core.passages import normalize_newlines
from codecoach.importer import import_materials, import_outline, import_with_syllabus
from importer_helpers import HISTORY, OUTLINE, new_store, scripted


def collapse(s):
    return " ".join(s.split())


def assert_kept(store, cid, raw):
    docs = store.list_docs(cid)
    assert len(docs) == 1
    text = store.get_doc_text(docs[0].id)
    assert text == normalize_newlines(raw)
    ps = store.list_passages(doc_id=docs[0].id)
    assert ps
    last = 0
    for p in ps:
        assert p.start is not None and p.end is not None and p.start >= last
        assert collapse(text[p.start:p.end]) == collapse(p.text)
        last = p.end


def test_file_text_is_kept_with_unix_line_endings():
    raw = HISTORY.replace("\n", "\r\n")
    store = new_store()
    cid = import_materials(store, scripted(), title="Valdoria", text=raw, doc_name="history")
    assert_kept(store, cid, raw)
    assert "\r" not in store.get_doc_text(store.list_docs(cid)[0].id)


def test_outline_text_is_kept():
    store = new_store()
    cid = import_outline(store, scripted(), title="C basics", text=OUTLINE)
    assert_kept(store, cid, OUTLINE)


def test_syllabus_import_keeps_the_materials_text():
    store = new_store()
    llm = FakeLLM(lambda purpose, text: {"edges": []} if "prereq" in purpose else {"support": []})
    cid = import_with_syllabus(store, llm, title="Valdoria", materials_text=HISTORY, doc_name="history",
                               syllabus_text="1. Explain the road to revolution")
    assert_kept(store, cid, HISTORY)


def test_pdf_style_text_with_form_feeds_is_kept():
    raw = "Chapter 1\n\nOne.\n\n\fTwo starts here.\n\nThree.\n--- page 9 ---\nNine.\n"
    store = new_store()
    cid = import_materials(store, scripted(), title="Pdf book", text=raw, doc_name="book", doc_kind="pdf_text")
    assert_kept(store, cid, raw)
