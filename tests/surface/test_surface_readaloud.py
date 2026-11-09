"""Read tab and read aloud: the materials route, the audio route with a fake synthesizer (verbatim text, cache,
LRU eviction), the fallback path, and the integrity rule (an open question put back ungraded)."""

import base64
import hashlib
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from surface_helpers import Clock, FakeLLM, build_course, make_service, new_store, oid

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.audio import AudioCache, TTSUnavailable, clip_key  # noqa: E402
from codecoach.core.models import Course, Doc, MaterialsResponse, Passage  # noqa: E402
from codecoach.importer.build import OUTLINE_MODE_REASON  # noqa: E402
from codecoach.service import RELEASE_MESSAGE, CodeCoachService, ServiceConfig, ServiceError  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class FakeSynth:
    """Records every text it is asked to speak. Audio = b'AUD:' + the text bytes."""

    def __init__(self, voice="fake/voice-1", fail=None):
        self._voice, self.fail, self.texts = voice, fail, []

    def voice(self):
        return self._voice

    def synthesize(self, text):
        self.texts.append(text)
        if self.fail:
            raise self.fail
        return b"AUD:" + text.encode("utf-8"), "mp3"


def svc_with_audio(tmp_path, synth=None, *, cap=10_000_000, clock=None, **kw):
    svc, store, cid = make_service(**kw)
    synth = synth or FakeSynth()
    svc.audio = AudioCache(tmp_path / "audio", synth, cap_bytes=cap, **({"clock": clock} if clock else {}))
    return svc, store, cid, synth


def client_for(svc):
    spec = importlib.util.spec_from_file_location("codecoach_plugin_api_ra", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    api.set_service(svc)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/codecoach")
    return TestClient(app)


# ------------------------------------------------------------------ materials

def test_materials_in_document_order_with_tags():
    svc, store, cid = make_service(not_covered=(2,), graph={1: [], 2: [1]})
    # section 2 is the last section stored, so a second paragraph appended now lands right after its first
    store.put_passages([Passage(id=f"{cid}:s02:2", doc_id=cid, location="Section 2, para 2", text="A second paragraph.")])
    m = svc.materials(cid)
    assert isinstance(m, MaterialsResponse) and m.source == "materials" and m.label == ""
    assert [s.title for s in m.sections] == ["Section 1", "Section 2"]
    s1, s2 = m.sections
    assert s1.doc_name == "Fixture book"
    assert [p.id for p in s1.passages] == [f"{cid}:s01:1"]
    assert [t.id for t in s1.passages[0].objectives] == [oid(1)]
    assert s1.passages[0].objectives[0].statement == "Know topic 1"
    assert [p.id for p in s2.passages] == [f"{cid}:s02:1", f"{cid}:s02:2"]       # paragraph order kept
    assert all(p.objectives == [] for p in s2.passages)                          # o02 not_covered: no tag, still shown
    assert s1.passages[0].text == store.get_passage(f"{cid}:s01:1").text         # verbatim


def test_materials_unknown_course_404():
    svc, _, _ = make_service()
    with pytest.raises(ServiceError) as e:
        svc.materials("zz")
    assert e.value.status == 404


def test_outline_course_is_labelled_not_source_material():
    svc, store, cid = make_service()
    store.put_course(Course(id="outl", title="C Basics", mode="open", mode_reason=OUTLINE_MODE_REASON))
    store.put_doc(Doc(id="outl", course_id="outl", name="outline", kind="markdown"))
    store.put_passages([Passage(id="outl:variables:1", doc_id="outl", location="Variables, para 1", text="- Types")])
    m = svc.materials("outl")
    assert m.source == "outline" and m.label == "Course outline: not source material"
    assert m.sections[0].title == "Variables"


def test_materials_route(tmp_path):
    svc, _, cid, _ = svc_with_audio(tmp_path)
    r = client_for(svc).get(f"/api/plugins/codecoach/courses/{cid}/materials")
    assert r.status_code == 200
    body = r.json()
    assert body["sections"][0]["passages"][0]["objectives"][0]["id"] == oid(1)
    assert client_for(svc).get("/api/plugins/codecoach/courses/nope/materials").status_code == 404


# ------------------------------------------------------------------ audio: verbatim, cache, eviction

def test_audio_is_synthesized_from_the_passage_text_verbatim(tmp_path):
    svc, store, cid, synth = svc_with_audio(tmp_path)
    pid = f"{cid}:s05:1"
    clip = svc.passage_audio(cid, pid)
    assert synth.texts == [store.get_passage(pid).text]                # exactly the stored text, once
    assert clip.path.read_bytes() == b"AUD:" + store.get_passage(pid).text.encode()
    assert clip.mime == "audio/mpeg" and clip.cached is False
    assert clip.path.name == hashlib.sha256((store.get_passage(pid).text + "\x00fake/voice-1").encode()).hexdigest() + ".mp3"
    assert clip.path.name == clip_key(store.get_passage(pid).text, "fake/voice-1") + ".mp3"


def test_audio_cache_hit_does_not_synthesize_again(tmp_path):
    svc, store, cid, synth = svc_with_audio(tmp_path)
    a = svc.passage_audio(cid, f"{cid}:s01:1")
    b = svc.passage_audio(cid, f"{cid}:s01:1")
    assert len(synth.texts) == 1 and a.cached is False and b.cached is True and a.path == b.path


def test_a_different_voice_is_a_different_clip(tmp_path):
    svc, store, cid, synth = svc_with_audio(tmp_path)
    a = svc.passage_audio(cid, f"{cid}:s01:1")
    synth._voice = "fake/voice-2"
    b = svc.passage_audio(cid, f"{cid}:s01:1")
    assert a.path != b.path and len(synth.texts) == 2


def test_lru_eviction_drops_the_least_recently_used_and_keeps_the_new_clip(tmp_path):
    t = [1000.0]

    def clock():
        t[0] += 1
        return t[0]
    cache = AudioCache(tmp_path / "audio", FakeSynth(), cap_bytes=3 * len(b"AUD:" + b"x" * 100), clock=clock)
    texts = ["a" * 100, "b" * 100, "c" * 100]
    clips = [cache.get(x) for x in texts]
    assert all(c.path.exists() for c in clips) and cache.total_bytes() == 3 * 104
    cache.get(texts[0])                                                # touch 'a': now 'b' is the oldest
    d = cache.get("d" * 100)                                           # over the cap: 'b' goes
    assert not clips[1].path.exists()
    assert clips[0].path.exists() and clips[2].path.exists() and d.path.exists()
    assert cache.total_bytes() <= cache.cap
    assert cache.get(texts[1]).cached is False                         # evicted: synthesized again


def test_a_clip_larger_than_the_cap_is_still_served(tmp_path):
    cache = AudioCache(tmp_path / "audio", FakeSynth(), cap_bytes=10)
    c = cache.get("x" * 50)
    assert c.path.exists()


def test_empty_synthesis_is_unavailable(tmp_path):
    class Empty(FakeSynth):
        def synthesize(self, text):
            return b"", "mp3"
    cache = AudioCache(tmp_path / "audio", Empty())
    with pytest.raises(TTSUnavailable):
        cache.get("hello")
    assert not list((tmp_path / "audio").glob("*.mp3"))


# ------------------------------------------------------------------ audio routes and the fallback signal

def test_audio_route_returns_bytes_and_a_base64_form(tmp_path):
    svc, store, cid, synth = svc_with_audio(tmp_path)
    c = client_for(svc)
    pid = f"{cid}:s02:1"
    r = c.get(f"/api/plugins/codecoach/courses/{cid}/passages/{pid}/audio")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"AUD:" + store.get_passage(pid).text.encode()
    assert r.headers["x-codecoach-voice"] == "fake/voice-1"
    j = c.get(f"/api/plugins/codecoach/courses/{cid}/passages/{pid}/audio?encoding=base64").json()
    assert base64.b64decode(j["data_base64"]) == r.content
    assert j["mime"] == "audio/mpeg" and j["voice"] == "fake/voice-1" and j["cached"] is True
    assert len(synth.texts) == 1


def test_audio_route_unknown_passage_and_other_courses_passage_are_404(tmp_path):
    svc, store, cid, _ = svc_with_audio(tmp_path)
    store.put_course(Course(id="other", title="Other"))
    store.put_doc(Doc(id="other", course_id="other", name="o", kind="markdown"))
    store.put_passages([Passage(id="other:x:1", doc_id="other", location="X, para 1", text="Elsewhere.")])
    c = client_for(svc)
    assert c.get(f"/api/plugins/codecoach/courses/{cid}/passages/nope:x:1/audio").status_code == 404
    r = c.get(f"/api/plugins/codecoach/courses/{cid}/passages/other:x:1/audio")
    assert r.status_code == 404 and r.json()["code"] == "unknown_passage"


def test_tts_failure_is_a_coded_503_the_desktop_turns_into_the_browser_voice(tmp_path):
    svc, _, cid, synth = svc_with_audio(tmp_path, FakeSynth(fail=TTSUnavailable("edge: no network")))
    c = client_for(svc)
    r = c.get(f"/api/plugins/codecoach/courses/{cid}/passages/{cid}:s01:1/audio?encoding=base64")
    assert r.status_code == 503 and r.json()["code"] == "tts_unavailable"
    assert "no network" in r.json()["detail"]
    assert not list((tmp_path / "audio").glob("*.mp3"))               # nothing cached from a failure


def test_no_synthesizer_wired_is_the_same_503():
    svc, _, cid = make_service()
    r = client_for(svc).get(f"/api/plugins/codecoach/courses/{cid}/passages/{cid}:s01:1/audio")
    assert r.status_code == 503 and r.json()["code"] == "tts_unavailable"


# ------------------------------------------------------------------ the integrity rule

def _open_question(svc, cid):
    return _next_item(svc, cid).id


def _next_item(svc, cid):
    """Next question, reading past any lessons the learner has not seen yet."""
    for _ in range(30):
        n = svc.next(cid)
        if n.kind == "item":
            return n.item
        assert n.kind == "lesson", n
    raise AssertionError("no question served")


def _served_ids(svc, cid, k=5):
    out = []
    for _ in range(k):
        n = svc.next(cid)
        if n.kind == "item":
            out.append(n.item.id)
    return out


def test_release_puts_the_open_question_back_ungraded_and_it_is_not_served_next():
    svc, store, cid = make_service()
    first = _open_question(svc, cid)               # a placement question
    svc.hint(first, 1)
    assert svc.hints_used(first) == 1
    before = (store.count_attempts(course_id=cid), len(store.list_review_states(course_id=cid)))
    r = svc.release(first)
    assert r.released is True and r.message == RELEASE_MESSAGE
    assert (store.count_attempts(course_id=cid), len(store.list_review_states(course_id=cid))) == before
    assert svc.hints_used(first) == 0
    assert store.get_item(first).status == "active"                    # still in the bank
    assert first not in _served_ids(svc, cid, 12)                      # repeated /next never hands it back


def test_released_question_comes_back_after_another_answer():
    svc, store, cid = make_service()
    first = _open_question(svc, cid)
    svc.release(first)
    other = _next_item(svc, cid)
    assert other.id != first
    item = store.get_item(other.id)
    svc.answer(item.id, item.payload.correct_index)                    # one real answer recorded
    assert svc._withheld(cid, svc.now()) == frozenset()                # the answer lifted the hold
    assert store.count_attempts(item_id=first) == 0                    # and the released one was never graded


def test_released_question_comes_back_after_the_window():
    svc, store, cid = make_service()
    first = _open_question(svc, cid)
    svc.release(first)
    assert first in svc._withheld(cid, svc.now())
    svc.now_fn.t += timedelta(minutes=16)
    assert svc._withheld(cid, svc.now()) == frozenset()


def test_release_with_nothing_else_to_serve_says_done_not_the_same_question():
    svc, store, cid = make_service(items_per=1, graph={1: []})
    only = _open_question(svc, cid)
    svc.release(only)
    for _ in range(3):
        n = svc.next(cid)
        assert n.kind != "item"


def test_release_of_an_answered_or_retired_question_is_a_no_op():
    svc, store, cid = make_service()
    first = _open_question(svc, cid)
    svc.report(first, "bad")
    assert svc.release(first).released is False
    with pytest.raises(ServiceError) as e:
        svc.release("zz")
    assert e.value.status == 404


def test_release_route():
    svc, store, cid = make_service()
    first = _open_question(svc, cid)
    r = client_for(svc).post(f"/api/plugins/codecoach/items/{first}/release")
    assert r.status_code == 200 and r.json() == {"item_id": first, "released": True, "message": RELEASE_MESSAGE}
