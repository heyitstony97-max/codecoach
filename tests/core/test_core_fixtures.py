"""Every JSON in tests/fixtures/api validates against its response model, and the files
cover every endpoint in docs/SPEC.md."""

import json
from pathlib import Path

import pytest

from codecoach.core import models as m
from codecoach.core.models import CourseMap

API = Path(__file__).resolve().parent.parent / "fixtures" / "api"

# fixture file -> (HTTP method + path, response model)
FIXTURES = {
    "courses": ("GET /courses", m.CoursesResponse),
    "courses_import": ("POST /courses/import", m.ImportResponse),
    "job": ("GET /jobs/{id}", m.JobResponse),
    "map": ("GET /courses/{id}/map", m.MapResponse),
    "next_lesson": ("GET /courses/{id}/next (lesson)", m.NextResponse),
    "next_item_mc": ("GET /courses/{id}/next (item)", m.NextResponse),
    "next_item_code": ("GET /courses/{id}/next (code item)", m.NextResponse),
    "answer_mc": ("POST /items/{id}/answer", m.AnswerResponse),
    "run": ("POST /items/{id}/run", m.RunResponse),
    "hint": ("POST /items/{id}/hint", m.HintResponse),
    "report": ("POST /items/{id}/report", m.ReportResponse),
    "progress": ("GET /courses/{id}/progress", m.ProgressResponse),
    "drops": ("GET /courses/{id}/drops", m.DropsResponse),
    "timeline": ("GET /courses/{id}/timeline", m.TimelineResponse),
    "plot": ("GET /items/{id}/plot", m.PlotResponse),
    "passage": ("GET /passages/{id}", m.PassageResponse),
}


def load(name):
    return json.loads((API / f"{name}.json").read_text())


def test_every_json_file_is_registered_here():
    on_disk = {p.stem for p in API.glob("*.json")}
    assert on_disk == set(FIXTURES) | {"events"}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_validates(name):
    model = FIXTURES[name][1]
    obj = model.model_validate(load(name))
    # round-trips through JSON without loss of required content
    assert model.model_validate_json(obj.model_dump_json()) == obj


def test_events_fixture():
    d = load("events")
    m.JobProgressEvent.model_validate(d["job_progress"])
    m.BankUpdatedEvent.model_validate(d["bank_updated"])


def test_map_fixture_is_rich_and_a_valid_course_map():
    r = m.MapResponse.model_validate(load("map"))
    objs = [o for u in r.units for l in u.lessons for o in l.objectives]
    assert len(objs) == 40
    assert sum(o.coverage == "not_covered" for o in objs) == 3
    assert all(o.status_label == m.NOT_COVERED_LABEL for o in objs if o.coverage == "not_covered")
    assert len({round(o.mastery, 1) for o in objs}) >= 5            # mixed mastery
    assert r.summary.objectives == 40 and r.summary.not_covered == 3
    # the nested map is itself a valid CourseMap (no cycles, no dangling ids)
    raw = load("map")
    cm = CourseMap.model_validate({"course_id": raw["course"]["id"], "edges": raw["edges"], "units": [
        {"id": u["id"], "title": u["title"], "lessons": [
            {"id": l["id"], "title": l["title"], "objectives": [
                {k: o[k] for k in ("id", "unit", "lesson", "verb", "statement", "kind", "prereqs", "passage_ids", "coverage")}
                for o in l["objectives"]]} for l in u["lessons"]]} for u in raw["units"]]})
    assert len(cm.objectives()) == 40


def test_code_item_fixture_hides_secrets():
    raw = load("next_item_code")["item"]
    assert raw["format"] == "code" and raw["payload"]["language"] == "c"
    text = json.dumps(raw)
    assert "reference_solution" not in text and "hidden_tests" not in text and "46\\n" not in text
    assert raw["payload"]["hidden_test_count"] == 3


def test_mc_and_answer_fixtures():
    item = load("next_item_mc")["item"]
    assert "correct_index" not in json.dumps(item) and "citations" not in item
    ans = m.AnswerResponse.model_validate(load("answer_mc"))
    assert ans.citations[0].quote and ans.citations[0].location == "Ch. 2, p. 14, para 3"
    assert ans.citations[0].quote in load("next_lesson")["lesson"]["passages"][0]["text"]


def test_timeline_has_8_events_in_order():
    t = m.TimelineResponse.model_validate(load("timeline"))
    years = [e.sort_year for e in t.events]
    assert len(t.events) == 8 and years == sorted(years)


def test_progress_and_drops_agree():
    p, d = m.ProgressResponse.model_validate(load("progress")), m.DropsResponse.model_validate(load("drops"))
    assert p.drops_by_gate == {g.gate_code: g.count for g in d.by_gate}
    assert d.total == sum(p.drops_by_gate.values()) == p.items_dropped
    assert [g.gate_code for g in d.by_gate] == list(m.GATE_CODES)
    assert all(g.label == m.GATE_LABELS[g.gate_code] for g in d.by_gate)
    assert p.objectives == 40 and p.not_covered == 3


def test_plot_has_gaps():
    pl = m.PlotResponse.model_validate(load("plot"))
    assert any(y is None for _, y in pl.series[2].points) and len(pl.series) == 3
