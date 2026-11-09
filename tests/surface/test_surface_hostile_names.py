"""B5: hostile course titles and ids must never reach outside the exports / audio folders."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for sub in ("importer", "surface"):
    if str(ROOT / "tests" / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / "tests" / sub))

from importer_helpers import HISTORY, new_store, scripted  # noqa: E402

from codecoach.audio import AudioCache  # noqa: E402
from codecoach.service import CodeCoachService  # noqa: E402

TITLES = ["../..", "../../pwned", "a/b/c", "..", ".", "x" * 5000, "\u202eevil\u202c \u05e9\u05dc\u05d5\u05dd",
          "\U0001F680\U0001F4DA", "C:\\Windows\\x", "a\x00b", "/etc/passwd", "title\nnewline", "~/x", "-rf"]


@pytest.mark.parametrize("title", TITLES)
def test_hostile_titles_export_inside_the_exports_folder(tmp_path, title):
    exports = tmp_path / "exports"
    svc = CodeCoachService(new_store(), scripted(), background=False, auto_bank=False, exports_dir=exports)
    cid = svc.job(svc.start_import(title=title, text=HISTORY)).course_id
    assert cid and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", cid)
    ex = svc.export_study_guide(cid)
    assert re.fullmatch(r"[A-Za-z0-9._-]+", ex.filename) and not ex.filename.startswith(".")
    assert Path(ex.path).resolve().parent == exports.resolve()
    assert [p.name for p in tmp_path.iterdir()] == ["exports"]


def test_export_refuses_a_course_id_that_would_leave_the_folder(tmp_path, monkeypatch):
    """Defence in depth: even if a store ever held an id with slashes, the file name is cleaned."""
    exports = tmp_path / "exports"
    svc = CodeCoachService(new_store(), scripted(), background=False, auto_bank=False, exports_dir=exports)
    cid = svc.job(svc.start_import(title="Kings", text=HISTORY)).course_id
    real = svc._course(cid)
    evil = real.model_copy(update={"id": "../../escaped"})
    svc._course = lambda _id: evil
    svc.store.get_map = lambda _id: object()
    import codecoach.service as S
    monkeypatch.setattr(S.study, "study_guide_md", lambda *a, **k: "# guide")
    ex = svc.export_study_guide("../../escaped")
    assert Path(ex.path).resolve().parent == exports.resolve()
    assert not (tmp_path.parent / "escaped-study-guide.md").exists()


class _Synth:
    def __init__(self, ext):
        self.ext = ext

    def voice(self):
        return "v"

    def synthesize(self, text):
        return b"audio", self.ext


@pytest.mark.parametrize("ext", ["mp3", "../../evil", "mp3/../../x", "M P3", "a" * 200, "\u202e"])
def test_audio_cache_file_stays_in_its_folder_whatever_the_voice_extension(tmp_path, ext):
    root = tmp_path / "audio"
    cache = AudioCache(root, _Synth(ext))
    clip = cache.get("Hello there.")
    assert clip.path.resolve().parent == root.resolve()
    assert re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]{1,5}", clip.path.name)
    assert [p.name for p in tmp_path.iterdir()] == ["audio"]
