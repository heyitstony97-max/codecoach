"""Adaptive placement: at most 12 items or 10 minutes, covered objectives only.

Start at mid-depth in the prerequisite graph. A correct answer marks the objective and all its
prerequisites known and moves on to its dependents; a wrong answer marks it (and everything that depends
on it) new and tests its prerequisites; a partial score marks it shaky and also tests prerequisites.
Objectives never reached are marked new. The state lives in store meta, so every call is a pure function
of (store, clock, rng).
"""

import random
from datetime import datetime, timedelta

from codecoach.core.models import Item
from codecoach.core.store import Store

from .mastery import DEFAULT_PARAMS, iso, parse_iso, set_priors

MAX_ITEMS = 12
MAX_SECONDS = 600
PASS_AT = 0.8        # score >= this: correct
PARTIAL_AT = 0.4     # score >= this: shaky; below: wrong
PRIOR = {"known": 0.90, "shaky": 0.50, "new": DEFAULT_PARAMS.p_init}


def state_key(course_id: str) -> str:
    return f"adapt:placement:{course_id}"


class Graph:
    """The prerequisite graph over COVERED objectives (edges to not_covered objectives are dropped)."""

    def __init__(self, store: Store, course_id: str):
        objs = [o for o in store.list_objectives(course_id) if o.coverage == "covered"]
        self.order = [o.id for o in objs]
        ids = set(self.order)
        self.prereqs = {o.id: [p for p in o.prereqs if p in ids] for o in objs}
        self.dependents: dict[str, list[str]] = {i: [] for i in self.order}
        for o, ps in self.prereqs.items():
            for p in ps:
                self.dependents[p].append(o)
        self.depth: dict[str, int] = {}
        for o in self.order:
            self._depth(o)
        self._anc: dict[str, set[str]] = {}
        self._desc: dict[str, set[str]] = {}

    def _depth(self, o: str) -> int:
        if o not in self.depth:
            self.depth[o] = 0   # guards against a cycle (the map validator forbids them)
            ps = self.prereqs[o]
            self.depth[o] = 1 + max(self._depth(p) for p in ps) if ps else 0
        return self.depth[o]

    def ancestors(self, o: str) -> set[str]:
        if o not in self._anc:
            out: set[str] = set()
            for p in self.prereqs[o]:
                out |= {p} | self.ancestors(p)
            self._anc[o] = out
        return self._anc[o]

    def descendants(self, o: str) -> set[str]:
        if o not in self._desc:
            out: set[str] = set()
            for d in self.dependents[o]:
                out |= {d} | self.descendants(d)
            self._desc[o] = out
        return self._desc[o]


def _testable(store: Store, course_id: str, graph: Graph) -> dict[str, list[Item]]:
    by: dict[str, list[Item]] = {o: [] for o in graph.order}
    for it in store.list_items(course_id=course_id, status="active"):
        if it.objective_id in by:
            by[it.objective_id].append(it)
    # self-rated explain items are a poor probe: use them only when nothing else exists
    out = {}
    for o, items in by.items():
        items = sorted(items, key=lambda i: i.id)
        firm = [i for i in items if i.format != "explain"]
        if firm or items:
            out[o] = firm or items
    return out


def _result(score: float) -> str:
    return "correct" if score >= PASS_AT else "shaky" if score >= PARTIAL_AT else "wrong"


def get_state(store: Store, course_id: str) -> dict | None:
    return store.get_meta(state_key(course_id), None)


def is_active(store: Store, course_id: str) -> bool:
    st = get_state(store, course_id)
    return bool(st) and st["status"] == "active"


def is_current(store: Store, course_id: str, item_id: str) -> bool:
    st = get_state(store, course_id)
    return bool(st and st["status"] == "active" and st["current"] and st["current"]["item_id"] == item_id)


def _choose(graph: Graph, st: dict, testable: dict[str, list[Item]]) -> tuple[str, str] | None:
    """(objective_id, why) for the next probe, or None when there is nothing left worth asking."""
    marks = st["marks"]
    open_ = [o for o in graph.order if o not in marks and o in testable]
    if not open_:
        return None
    last = st.get("last")
    if last:
        lo, res = last["objective_id"], last["result"]
        if res == "correct":
            cands = [d for d in graph.dependents[lo] if d in open_]
            if cands:
                d = min(cands, key=lambda c: (sum(1 for p in graph.prereqs[c] if p not in marks
                                                  or marks[p]["status"] != "known"), graph.order.index(c)))
                return d, f"dependent of {lo} after a correct answer"
        else:
            cands = [p for p in graph.ancestors(lo) if p in open_]
            if cands:
                direct = [p for p in graph.prereqs[lo] if p in cands]
                pool = direct or cands
                p = max(pool, key=lambda c: (len(graph.ancestors(c)), -graph.order.index(c)))
                return p, f"prerequisite of {lo} after a {res} answer"
    lo_d = min(graph.depth[o] for o in open_)
    hi_d = max(graph.depth[o] for o in open_)
    mid = (lo_d + hi_d) // 2
    near = min(abs(graph.depth[o] - mid) for o in open_)
    pool = [o for o in open_ if abs(graph.depth[o] - mid) == near]
    open_set = set(open_)

    def info(o: str) -> int:
        return len((graph.ancestors(o) | graph.descendants(o)) & open_set)
    pick = max(pool, key=lambda o: (info(o), -graph.order.index(o)))
    tag = "start" if not st["trace"] else "fallback"
    return pick, f"{tag}: mid-depth {mid} (open objectives span depth {lo_d}-{hi_d})"


def _over_budget(st: dict, now: datetime) -> bool:
    return (st["asked"] >= MAX_ITEMS
            or now - parse_iso(st["started_at"]) >= timedelta(seconds=MAX_SECONDS))


def _finish(store: Store, course_id: str, graph: Graph, st: dict, now: datetime) -> dict:
    for o in graph.order:
        st["marks"].setdefault(o, {"status": "new", "via": "untested"})
    st["status"], st["current"], st["finished_at"] = "done", None, iso(now)
    set_priors(store, course_id, {o: PRIOR[m["status"]] for o, m in st["marks"].items()}, now)
    store.set_meta(state_key(course_id), st)
    return st


def _advance(store: Store, course_id: str, graph: Graph, st: dict, now: datetime,
             rng: random.Random) -> dict:
    """Pick the next probe (or finish) and save."""
    testable = _testable(store, course_id, graph)
    ch = None if _over_budget(st, now) else _choose(graph, st, testable)
    if ch is None:
        return _finish(store, course_id, graph, st, now)
    oid, why = ch
    item = rng.choice(testable[oid])
    st["current"] = {"objective_id": oid, "item_id": item.id, "why": why}
    store.set_meta(state_key(course_id), st)
    return st


def start_placement(store: Store, course_id: str, now: datetime, rng: random.Random | None = None) -> dict:
    """Begin (or resume) a placement. Returns the state dict; `current` names the item to ask."""
    st = get_state(store, course_id)
    if st and st["status"] == "active":
        return st
    graph = Graph(store, course_id)
    st = {"status": "active", "started_at": iso(now), "asked": 0, "marks": {}, "current": None,
          "last": None, "trace": []}
    return _advance(store, course_id, graph, st, now, rng or random.Random(f"placement:{course_id}"))


def current_item(store: Store, course_id: str, now: datetime,
                 rng: random.Random | None = None) -> Item | None:
    """The item the placement is waiting on; finishes the placement when the budget is spent."""
    st = get_state(store, course_id)
    if not st or st["status"] != "active":
        return None
    graph = Graph(store, course_id)
    if st["current"] is None:
        st = _advance(store, course_id, graph, st, now, rng or random.Random(f"placement:{course_id}"))
    if st["status"] != "active":
        return None
    return store.get_item(st["current"]["item_id"])


def record_result(store: Store, course_id: str, item: Item, score: float, now: datetime,
                  rng: random.Random | None = None) -> dict | None:
    """Apply an answer to the placement. Returns the new state, or None when `item` was not the
    placement's current item (the answer is then an ordinary attempt)."""
    st = get_state(store, course_id)
    if not st or st["status"] != "active" or not st["current"] or st["current"]["item_id"] != item.id:
        return None
    graph = Graph(store, course_id)
    oid, why = st["current"]["objective_id"], st["current"]["why"]
    res = _result(score)
    changed: dict[str, str] = {}
    marks = st["marks"]
    status = {"correct": "known", "shaky": "shaky", "wrong": "new"}[res]
    marks[oid] = {"status": status, "via": "tested"}
    changed[oid] = status
    if res == "correct":
        for a in sorted(graph.ancestors(oid), key=graph.order.index):
            if a not in marks:
                marks[a] = {"status": "known", "via": f"inferred from {oid}"}
                changed[a] = "known"
    elif res == "wrong":
        for d in sorted(graph.descendants(oid), key=graph.order.index):
            if d not in marks:
                marks[d] = {"status": "new", "via": f"inferred from {oid}"}
                changed[d] = "new"
    st["asked"] += 1
    st["trace"].append({"step": st["asked"], "objective_id": oid, "item_id": item.id, "format": item.format,
                        "why": why, "score": round(float(score), 3), "result": res, "marked": changed})
    st["last"] = {"objective_id": oid, "result": res}
    st["current"] = None
    return _advance(store, course_id, graph, st, now, rng or random.Random(f"placement:{course_id}"))


def trace_lines(st: dict) -> list[str]:
    lines = []
    for t in st["trace"]:
        marked = ", ".join(f"{o}={s}" for o, s in t["marked"].items())
        lines.append(f"{t['step']}. ask {t['objective_id']} ({t['format']}) because {t['why']}; "
                     f"score {t['score']} -> {t['result']}; marked {marked}")
    done = ", ".join(f"{o}={m['status']}" for o, m in st["marks"].items())
    lines.append(f"final: {done}" if st["status"] == "done" else "placement still running")
    return lines


def placement_result(store: Store, course_id: str) -> dict[str, str] | None:
    """objective_id -> known | shaky | new once the placement is done, else None."""
    st = get_state(store, course_id)
    if not st or st["status"] != "done":
        return None
    return {o: m["status"] for o, m in st["marks"].items()}
