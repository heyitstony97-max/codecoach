"""Shared fixtures for the adaptive-engine tests: a 12-objective graph, temp stores, a fake learner."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codecoach.core.models import (Citation, Course, CourseMap, Doc, Item, Lesson, MCPayload, Objective,
                                Passage, Unit, ExplainPayload)
from codecoach.core.registry import CheckResult
from codecoach.core.store import Store

T0 = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
_DIRS: list = []

# objective number -> prerequisite numbers. Depths: o01-o03 = 0; o04-o06 = 1; o07-o09 = 2; o10, o11 = 3; o12 = 4
GRAPH12 = {1: [], 2: [], 3: [], 4: [1], 5: [1, 2], 6: [3], 7: [4], 8: [4, 5], 9: [6], 10: [7, 8],
           11: [9], 12: [10, 11]}


def oid(n: int) -> str:
    return f"fx:o{n:02d}"


def new_store() -> Store:
    d = tempfile.TemporaryDirectory(prefix="codecoach-adapt-")
    _DIRS.append(d)
    return Store(Path(d.name) / "codecoach.db")


def passage_text(n: int) -> str:
    return f"Fact {n} is established here. The key idea of topic {n} is Alpha{n}. More detail follows."


def make_item(n: int, k: int = 1, *, fmt: str = "mc", **kw) -> Item:
    payload = MCPayload(options=[f"wrong-{n}-a", f"Alpha{n}", f"wrong-{n}-b"], correct_index=1) \
        if fmt == "mc" else ExplainPayload()
    data = dict(id=f"it-{n:02d}-{k}", objective_id=oid(n), format=fmt, prompt=f"Question {k} on topic {n}?",
                payload=payload, citations=[Citation(passage_id=f"fx:s{n:02d}:1", quote=f"The key idea of topic {n} is Alpha{n}")],
                origin="materials", checker=fmt)
    data.update(kw)
    return Item(**data)


def build_course(store: Store, graph: dict[int, list[int]] | None = None, *, items_per: int = 2,
                 not_covered: tuple[int, ...] = (), course_id: str = "fx", explain_on: tuple[int, ...] = ()) -> str:
    graph = graph or GRAPH12
    store.put_course(Course(id=course_id, title="Fixture"))
    store.put_doc(Doc(id=course_id, course_id=course_id, name="Fixture book", kind="markdown"))
    store.put_passages([Passage(id=f"{course_id}:s{n:02d}:1", doc_id=course_id, location=f"Section {n}, para 1",
                                text=passage_text(n)) for n in graph])
    objs = []
    for n, pre in graph.items():
        nc = n in not_covered
        objs.append(Objective(id=oid(n), unit="u1", lesson="l1", verb="explain", statement=f"Know topic {n}",
                              prereqs=[oid(p) for p in pre], coverage="not_covered" if nc else "covered",
                              passage_ids=[] if nc else [f"{course_id}:s{n:02d}:1"]))
    store.save_map(CourseMap(course_id=course_id, units=[Unit(id="u1", title="Unit", lessons=[
        Lesson(id="l1", title="Lesson", objectives=objs)])]))
    for n in graph:
        if n in not_covered:
            continue
        for k in range(1, items_per + 1):
            store.add_item(make_item(n, k))
        if n in explain_on:
            store.add_item(make_item(n, 9, fmt="explain"))
    return course_id


def ok(score: float = 1.0) -> CheckResult:
    return CheckResult(ok=score >= 0.5, score=score)


class Clock:
    """Injected clock: each tick advances by `step`."""

    def __init__(self, start: datetime = T0, step: timedelta = timedelta(seconds=30)):
        self.t, self.step = start, step

    def __call__(self) -> datetime:
        self.t += self.step
        return self.t
