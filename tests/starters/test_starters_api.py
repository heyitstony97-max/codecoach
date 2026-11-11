"""The shapes of the ready-made project in the engine's list/create/get, and its refusals."""

import pytest
from starters_helpers import NEEDS_SANDBOX, STARTER, make, shipped

from codecoach import lang, starters
from codecoach.check.code_runner import sandbox_status
from codecoach.project import ProjectCreateRequest
from codecoach.service import ServiceError

needs_sandbox = pytest.mark.skipif(sandbox_status() != "bwrap", reason=NEEDS_SANDBOX)


def test_shared_data_shape():
    sh = starters.shared(STARTER)
    assert sh["id"] == STARTER and sh["title"] == "Grade book" and len(sh["steps"]) == 4
    for k in ("pitch", "summary"):
        assert sh[k].strip() and sh[k].isascii()
    for s in sh["steps"]:
        assert all(s[k].strip() for k in ("title", "goal", "concept", "task"))
        assert len(s["visible"]) >= 1 and len(s["hidden"]) >= 1


def test_unknown_things_have_no_data():
    assert not starters.known("nope")
    assert starters.part(STARTER, "cobol") is None and starters.part("nope", "python") is None
    assert starters.part(STARTER, "../x") is None
    with pytest.raises(KeyError):
        starters.shared("nope")


@needs_sandbox
def test_list_shape():
    _, eng, _ = make("fake")
    out = eng.list()
    assert out["starters"] == [{"id": STARTER, "title": "Grade book", "pitch": starters.shared(STARTER)["pitch"],
                                "steps": 4, "languages": [x for x in shipped() if x in lang.ready_ids()]}]
    assert out["starters"][0]["pitch"].count(".") == 1


@needs_sandbox
def test_bad_starter_is_422_with_one_plain_sentence():
    _, eng, model = make("fake")
    for starter, language in (("nope", "python"), (STARTER, "cobol"), (STARTER, ""), ("", "python")):
        with pytest.raises(ServiceError) as e:
            eng.create("", language, starter=starter)
        assert e.value.status == 422 and e.value.code == "bad_starter" and e.value.message.endswith(".")
    assert eng.list()["projects"] == [] and model.calls == []


@needs_sandbox
def test_route_body_carries_a_starter_without_an_idea():
    if "python" not in shipped() or "python" not in lang.ready_ids():
        pytest.skip("no ready Python starter")
    body = ProjectCreateRequest(starter=STARTER, language="python")
    svc, eng, model = make("fake")
    pid = eng.create(body.idea, body.language)              # exactly what the route passes today
    assert eng.get(pid)["starter"] == STARTER and eng.get(pid)["status"] == "ready"
    assert eng.list()["projects"][0]["starter"] == STARTER
    assert model.calls == []


@needs_sandbox
def test_no_idea_and_no_starter_is_still_a_bad_idea():
    _, eng, _ = make("fake")
    with pytest.raises(ServiceError) as e:
        eng.create("", "python")
    assert e.value.code == "bad_idea"
