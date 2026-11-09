"""export_map / import_map: a course map as one JSON document users can edit and share.

Shape (version 1):
  {"codecoach_map": 1, "course": {...}, "docs": [...], "passages": [...], "map": CourseMap json, "import": {...}|null}
Passages travel with the map so every `passage_ids` entry stays checkable on the other side.
"""

import json
from typing import Any

from pydantic import ValidationError

from codecoach.core.models import Course, CourseMap, Doc, Passage
from codecoach.core.store import Store
from codecoach.importer.build import report_key
from codecoach.importer.verbs import VERBS

MAP_VERSION = 1


def export_map(store: Store, course_id: str) -> dict:
    """Schema-valid, JSON-serialisable dict. Raises KeyError for an unknown course or one with no map."""
    course = store.get_course(course_id)
    cmap = store.get_map(course_id)
    if course is None or cmap is None:
        raise KeyError(f"no course map for {course_id!r}")
    docs = store.list_docs(course_id)
    passages = [p for d in docs for p in store.list_passages(doc_id=d.id)]
    return json.loads(json.dumps({
        "codecoach_map": MAP_VERSION,
        "course": course.model_dump(mode="json"),
        "docs": [d.model_dump(mode="json") for d in docs],
        "passages": [p.model_dump(mode="json") for p in passages],
        "map": cmap.model_dump(mode="json"),
        "import": store.get_meta(report_key(course_id)),
    }))


def _swap(value: str, old: str, new: str) -> str:
    return new + value[len(old):] if value.startswith(old + ":") or value == old else value


def import_map(store: Store, data: dict[str, Any] | str, *, course_id: str | None = None) -> str:
    """Load an exported (and possibly edited) map. `course_id` renames it. Raises ValueError on a bad
    file, an unknown verb, or an id that already exists. Coverage is re-derived: an objective is
    covered only if at least one of its passage_ids exists in the file's (or store's) passages."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as e:
            raise ValueError(f"map file is not valid JSON: {e}") from e
    if not isinstance(data, dict) or data.get("codecoach_map") != MAP_VERSION:
        raise ValueError(f"not an CodeCoach map file (expected codecoach_map: {MAP_VERSION})")
    try:
        course = Course.model_validate(data["course"])
        docs = [Doc.model_validate(d) for d in data.get("docs", [])]
        passages = [Passage.model_validate(p) for p in data.get("passages", [])]
        raw_map = json.loads(json.dumps(data["map"]))
    except KeyError as e:
        raise ValueError(f"map file is missing {e}") from e
    except ValidationError as e:
        raise ValueError(f"map file is invalid: {e}") from e

    old = course.id
    new = course_id or old
    if store.get_course(new) is not None:
        raise ValueError(f"course {new!r} already exists; pass course_id= to import under another id")
    for d in docs:
        other = store.get_doc(d.id)
        if other is not None and other.course_id != new:
            raise ValueError(f"document id {d.id!r} already belongs to course {other.course_id!r}")

    known = {p.id for p in passages}
    known |= {p.id for d in docs for p in store.list_passages(doc_id=d.id)}
    issues: list[dict] = []
    if new != old:
        raw_map["course_id"] = new
    for u in raw_map.get("units", []):
        u["id"] = _swap(u["id"], old, new)
        for lsn in u.get("lessons", []):
            lsn["id"] = _swap(lsn["id"], old, new)
            for o in lsn.get("objectives", []):
                o["id"] = _swap(o["id"], old, new)
                o["unit"] = _swap(o["unit"], old, new)
                o["lesson"] = _swap(o["lesson"], old, new)
                o["prereqs"] = [_swap(p, old, new) for p in o.get("prereqs", [])]
                if str(o.get("verb", "")) not in VERBS:
                    raise ValueError(f"objective {o['id']}: verb {o.get('verb')!r} is not one of {', '.join(VERBS)}")
                ids = list(dict.fromkeys(p for p in o.get("passage_ids", []) if p in known))
                was = o.get("coverage", "covered")
                o["passage_ids"] = ids
                o["coverage"] = "covered" if ids else "not_covered"
                if was != o["coverage"]:
                    issues.append({"kind": "coverage_rechecked", "section": lsn.get("title", ""),
                                   "detail": f"{o['id']} is now {o['coverage']}"})
    if "edges" in raw_map:
        raw_map["edges"] = [{"prereq": _swap(e["prereq"], old, new), "objective": _swap(e["objective"], old, new)}
                            for e in raw_map["edges"]]
    try:
        cmap = CourseMap.model_validate(raw_map)
    except ValidationError as e:
        raise ValueError(f"map is invalid: {e}") from e

    store.put_passages(passages)
    for d in docs:
        store.put_doc(d.model_copy(update={"course_id": new}))
    store.save_map(cmap)
    raw_prior = data.get("import")
    prior: dict = raw_prior if isinstance(raw_prior, dict) else {}
    store.set_meta(report_key(new), {**prior, "status": "done", "source": "import_map",
                                     "issues": [*prior.get("issues", []), *issues]})
    store.put_course(course.model_copy(update={"id": new}))
    return new
