"""Read keeps the line breaks and stanza gaps of the original text (poems); passage text and ids stay unchanged."""

import sys
from pathlib import Path

import pytest
from surface_helpers import FakeLLM, new_store

pytest.importorskip("fastapi")

from codecoach.importer import import_materials  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "importer"))
from importer_helpers import scripted  # noqa: E402

POEM = ("Poems, Series One\n\nI.\n\nI'm nobody!  Who are you?\nAre you nobody, too?\nThen there 's a pair of us -- don't tell!\n"
        "They 'd banish us, you know.\n\nHow dreary to be somebody!\nHow public, like a frog\nTo tell your name the livelong day\n"
        "To an admiring bog!\n")
PROSE = ("History\n\nThe kingdom grew slowly over many years and the people settled along the river banks.\n\n"
         "Trade followed the river, and towns appeared at every crossing.\n")


def _svc(text, name):
    store = new_store()
    cid = import_materials(store, scripted(), title="T", text=text, doc_name=name)
    return CodeCoachService(store, FakeLLM(lambda p, t: None), ServiceConfig(), background=False, auto_bank=False), store, cid


def test_verse_passage_carries_original_line_breaks():
    svc, store, cid = _svc(POEM, "poems.txt")
    ps = [p for s in svc.materials(cid).sections for p in s.passages]
    shown = [p for p in ps if p.display]
    assert shown, "no passage carried its original line breaks"
    p = next(x for x in shown if "nobody" in x.text)
    assert "\n" in p.display and "Who are you?\nAre you nobody, too?" in p.display
    assert " ".join(p.display.split()) == " ".join(p.text.split())      # same words, only the breaks differ
    stored = store.get_passage(p.id)
    assert p.text == stored.text and p.id == stored.id                    # passage text and id untouched


def test_prose_and_missing_offsets_fall_back_to_passage_text():
    svc, store, cid = _svc(PROSE, "history.txt")
    ps = [p for s in svc.materials(cid).sections for p in s.passages]
    assert ps and all(p.display is None for p in ps)
    svc, store, cid = _svc(POEM, "poems.txt")
    with store._tx() as c:
        c.execute("UPDATE passages SET start=NULL, end=NULL")
    ps = [p for s in svc.materials(cid).sections for p in s.passages]
    assert ps and all(p.display is None and p.text for p in ps)


def test_hard_wrapped_prose_is_not_shown_raw():
    wrapped = "Apology\n\n" + "\n".join(["How you, O Athenians, have been affected by my accusers, I cannot tell;",
                                         "but I know that they almost made me forget who I was, so persuasively",
                                         "did they speak; and yet they have hardly uttered a word of truth."]) + "\n"
    svc, store, cid = _svc(wrapped, "apology.txt")
    ps = [p for s in svc.materials(cid).sections for p in s.passages]
    assert ps and all(p.display is None for p in ps)
