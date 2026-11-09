"""next_item: what the learner sees next.

Order: a running placement; due reviews (interleaved: never the same objective twice in a row while an
alternative exists); then the frontier objective (prerequisites known, itself not known), its lesson before
its first item; otherwise done. not_covered objectives and non-active items are never served.

Retrieval gap: right after a lesson, an item citing that lesson's passages is not served next if any other
active item on an already-taught objective is available.
"""

import random
from dataclasses import dataclass
from datetime import datetime

from codecoach.core.models import Item
from codecoach.core.store import Store

from . import placement
from .mastery import iso, mastery_map, parse_iso


@dataclass(frozen=True)
class Pick:
    kind: str                      # lesson | item | done
    reason: str                    # new | review | interleave | placement | none
    objective_id: str | None = None
    item: Item | None = None       # the full item: the caller must serialize it through public_view
    message: str | None = None


def lessons_key(course_id: str) -> str:
    return f"adapt:lessons:{course_id}"


def mark_lesson_seen(store: Store, course_id: str, objective_id: str, now: datetime) -> None:
    seen = dict(store.get_meta(lessons_key(course_id), {}) or {})
    seen[objective_id] = iso(now)
    store.set_meta(lessons_key(course_id), seen)


def default_rng(store: Store, course_id: str) -> random.Random:
    """Deterministic: same store state, same choice (so a repeated /next does not flicker)."""
    return random.Random(f"{course_id}:{store.count_attempts(course_id=course_id)}")


def _cites(item: Item, passage_ids: set[str]) -> bool:
    return bool({c.passage_id for c in item.citations} & passage_ids)


def _choose_item(items: list[Item], attempts_by_item: dict[str, list], rng: random.Random,
                 avoid_format: str | None) -> Item:
    def key(i: Item):
        a = attempts_by_item.get(i.id, [])
        return (len(a), i.format == avoid_format)
    best = min(key(i) for i in items)
    return rng.choice(sorted((i for i in items if key(i) == best), key=lambda i: i.id))


def next_item(store: Store, course_id: str, now: datetime, rng: random.Random | None = None,
              exclude: frozenset[str] | set[str] = frozenset()) -> Pick:
    """`exclude`: item ids not to serve this time (a question the learner left unanswered to go and read)."""
    rng = rng or default_rng(store, course_id)
    objs = [o for o in store.list_objectives(course_id) if o.coverage == "covered"]
    if not objs:
        return Pick("done", "none", message="Nothing in your materials to practise yet.")

    if placement.is_active(store, course_id):
        it = placement.current_item(store, course_id, now, rng)
        if it is not None and it.id not in exclude:    # a released placement question waits; ordinary picks cover meanwhile
            return Pick("item", "placement", it.objective_id, it)

    covered = {o.id: o for o in objs}
    order = [o.id for o in objs]
    active: dict[str, list[Item]] = {}
    for it in store.list_items(course_id=course_id, status="active"):
        if it.objective_id in covered and it.id not in exclude:
            active.setdefault(it.objective_id, []).append(it)
    mast = mastery_map(store, course_id)
    seen = store.get_meta(lessons_key(course_id), {}) or {}

    attempts = store.list_attempts(course_id=course_id)
    by_item: dict[str, list] = {}
    for a in attempts:
        by_item.setdefault(a.item_id, []).append(a)
    items_all = {i.id: i for i in store.list_items(course_id=course_id)}
    last_att = attempts[-1] if attempts else None
    last_item = items_all.get(last_att.item_id) if last_att else None

    # what happened last: an answer, or a lesson being read
    lesson_obj, lesson_at = None, None
    for oid, at in seen.items():
        t = parse_iso(at)
        if lesson_at is None or t >= lesson_at:
            lesson_obj, lesson_at = oid, t
    after_lesson = bool(lesson_obj and lesson_obj in covered
                        and (last_att is None or lesson_at > last_att.at))
    lesson_passages = set(covered[lesson_obj].passage_ids) if after_lesson else set()
    last_objective = (lesson_obj if after_lesson else last_item.objective_id if last_item else None)

    # 1. due reviews, interleaved
    due = []
    for rs in store.list_due(course_id=course_id, now=now):
        it = items_all.get(rs.item_id)
        if it and it.status == "active" and it.objective_id in covered and it.id not in exclude:
            due.append(it)
    last_id = last_item.id if last_item else None

    def strict(i: Item) -> bool:
        return (i.objective_id != last_objective and i.id != last_id
                and not (after_lesson and _cites(i, lesson_passages)))
    pick = next((i for i in due if strict(i)), None)
    if pick is not None:
        return Pick("item", "review", pick.objective_id, pick)

    # 2. the frontier
    def prereqs_known(o) -> bool:
        return all(p not in covered or p not in active or mast[p].state == "known" for p in o.prereqs)
    frontier = next((covered[i] for i in order
                     if i in active and mast[i].state != "known" and prereqs_known(covered[i])), None)
    if frontier is not None:
        if frontier.id not in seen:
            return Pick("lesson", "new", frontier.id)
        cand = _choose_item(active[frontier.id], by_item, rng, last_item.format if last_item else None)
        if after_lesson and _cites(cand, lesson_passages):
            alt = _gap_alternative(active, mast, by_item, seen, covered, order, lesson_passages, last_id)
            if alt is not None:
                return Pick("item", "interleave", alt.objective_id, alt)
        return Pick("item", "new", frontier.id, cand)

    # 3. reviews we held back to avoid a repeat: nothing else to do, so serve them
    if due:
        pick = due[0]
        return Pick("item", "review", pick.objective_id, pick)

    nxt = store.list_review_states(course_id=course_id)
    upcoming = sorted(r.due for r in nxt if r.due > now)
    msg = "Everything reachable is known." + (f" Next review: {upcoming[0].date().isoformat()}." if upcoming else "")
    return Pick("done", "none", message=msg)


def _gap_alternative(active, mast, by_item, seen, covered, order, lesson_passages, last_id) -> Item | None:
    """Another active item, on a taught objective, that does not cite the lesson just read."""
    taught = [o for o in order if o in active and (o in seen or any(i.id in by_item for i in active[o]))]
    taught.sort(key=lambda o: (mast[o].p, order.index(o)))
    for o in taught:
        for it in sorted(active[o], key=lambda i: (len(by_item.get(i.id, [])), i.id)):
            if it.id != last_id and not _cites(it, lesson_passages) and not _cites_objective(it, covered, lesson_passages):
                return it
    return None


def _cites_objective(item: Item, covered, lesson_passages: set[str]) -> bool:
    o = covered.get(item.objective_id)
    return bool(o and set(o.passage_ids) & lesson_passages)
