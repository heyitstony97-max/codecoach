"""Pre-generate the item bank so a quiz never waits on the LLM, and refill it later.

build_bank / top_up are synchronous (call them from a worker thread or job); top_up(background=True)
starts that thread. The clock and RNG are injected.
"""

from __future__ import annotations

import random
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

from ..core.llm import LLMPort, LLMUnavailable
from ..core.models import Item, Objective, Passage, utcnow
from ..core.passages import is_quotable
from ..core.registry import NotRegistered, get_generator
from ..core.store import Store
from .generate import NON_READING, generate_one, is_course_meta

READING_ROTATION = ("mc", "cloze", "short", "match", "order", "explain")
KIND_FORMATS = {
    "reading": READING_ROTATION,
    "code": ("code",),
    "math": ("math",),
    "science": ("numeric", "mc"),
}
MAX_PASSAGES_SHOWN = 3
ATTEMPT_FACTOR = 3

Progress = Callable[[float, str], None]


@dataclass
class BankReport:
    attempts: int = 0
    kept: int = 0
    dropped: int = 0
    duplicates: int = 0
    drops_by_code: Counter = field(default_factory=Counter)
    not_covered: list[str] = field(default_factory=list)
    no_passages: list[str] = field(default_factory=list)
    skipped_meta: list[str] = field(default_factory=list)
    llm_unavailable: bool = False
    stopped: bool = False


def _generator(fmt: str):
    try:
        return get_generator(fmt)
    except NotRegistered:
        return lambda **kw: generate_one(fmt=fmt, **kw)


_WORDS = re.compile(r"[a-z0-9]+")


def _norm_words(text: str) -> frozenset[str]:
    return frozenset(_WORDS.findall(text.lower()))


def item_key(item: Item) -> str:
    """What the item asks the learner to produce (the right option, the blank's answer, the required terms)."""
    pl = item.payload
    if pl.format == "mc":
        return pl.options[pl.correct_index]
    if pl.format == "cloze":
        return pl.answer
    if pl.format == "short":
        return " ".join(sorted(pl.required_terms))
    if pl.format == "numeric":
        return f"{pl.answer_value} {pl.unit}"
    if pl.format == "math":
        return pl.expr
    if pl.format == "order":
        return " ".join(pl.events[i].label for i in pl.correct_order)
    return ""


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def is_near_duplicate(item: Item, existing: Sequence[Item]) -> bool:
    """Same format, the same question reworded: prompts nearly equal, or a similar prompt with the same key.
    Different formats on one fact are kept on purpose (that is the format variety)."""
    mine, key = _norm_words(item.prompt), _norm_words(item_key(item))
    for o in existing:
        if o.format != item.format or o.status != "active":
            continue
        pj = _jaccard(mine, _norm_words(o.prompt))
        if pj >= 0.85:
            return True
        if pj >= 0.5 and key and key == _norm_words(item_key(o)):
            return True
    return False


def _pick_passages(all_passages: Sequence[Passage], rng: random.Random) -> list[Passage]:
    if len(all_passages) <= MAX_PASSAGES_SHOWN:
        return list(all_passages)
    chosen = set(rng.sample(range(len(all_passages)), MAX_PASSAGES_SHOWN))
    return [p for i, p in enumerate(all_passages) if i in chosen]


def fill_objective(store: Store, llm: LLMPort, objective: Objective, all_passages: Sequence[Passage], *,
                   mode: str, wanted: int, formats: Sequence[str] | None = None, max_attempts: int | None = None,
                   rng: random.Random | None = None, now_fn: Callable[[], datetime] = utcnow,
                   report: BankReport | None = None, start: int = 0,
                   on_attempt: Callable[[], None] | None = None,
                   should_stop: Callable[[], bool] | None = None,
                   code_language: str | None = None) -> BankReport:
    """Try up to `max_attempts` drafts (default wanted * 3) until `wanted` new items are stored."""
    rep = report or BankReport()
    rng = rng or random.Random()
    fmts = tuple(formats or KIND_FORMATS.get(objective.kind, READING_ROTATION))
    if objective.coverage == "not_covered":
        rep.not_covered.append(objective.id)
        return rep
    if not all_passages:
        fmts = tuple(f for f in fmts if f in NON_READING)
        if not fmts:
            rep.no_passages.append(objective.id)
            return rep
    if objective.kind == "code" and "code" in fmts and is_course_meta(objective.statement):
        rep.skipped_meta.append(objective.id)   # about the outline itself: no exercise to write
        fmts = tuple(f for f in fmts if f != "code")
        if not fmts:
            return rep
    kept = 0
    for a in range(max_attempts if max_attempts is not None else wanted * ATTEMPT_FACTOR):
        if kept >= wanted:
            break
        if should_stop and should_stop():
            rep.stopped = True
            return rep
        fmt = fmts[(start + a) % len(fmts)]
        rep.attempts += 1
        try:
            res = _generator(fmt)(objective=objective, passages=_pick_passages(all_passages, rng), llm=llm,
                                  mode=mode, rng=rng, now=now_fn(),
                                  **({"code_language": code_language} if fmt == "code" else {}))
        except LLMUnavailable:
            rep.llm_unavailable = True
            return rep
        for item in res.items:
            if is_near_duplicate(item, store.list_items(objective_id=objective.id)):
                rep.duplicates += 1
                continue
            if store.add_item(item):
                kept += 1
                rep.kept += 1
            else:
                rep.duplicates += 1
        for d in res.drops:
            store.log_drop(d)
            rep.dropped += 1
            rep.drops_by_code[d.gate_code] += 1
        if on_attempt:
            on_attempt()
    return rep


def build_bank(store: Store, llm: LLMPort, course_id: str, per_objective: int = 3,
               progress: Progress | None = None, *, rng: random.Random | None = None,
               now_fn: Callable[[], datetime] = utcnow, formats: Sequence[str] | None = None,
               should_stop: Callable[[], bool] | None = None) -> BankReport:
    """Generate up to `per_objective` active items for every covered objective that has fewer.
    Not-covered objectives get nothing. Stops early (llm_unavailable) when the model is down."""
    course = store.get_course(course_id)
    if course is None:
        raise ValueError(f"unknown course {course_id!r}")
    rng = rng or random.Random()
    rep = BankReport()
    work = []
    for o in store.list_objectives(course_id):
        if o.coverage == "not_covered":
            rep.not_covered.append(o.id)
            continue
        have = store.count_items(objective_id=o.id, status="active")
        if have < per_objective:
            work.append((o, per_objective - have, have))
    total = sum(w for _, w, _ in work) or 1
    done = [0]

    def tick() -> None:
        done[0] += 1
        if progress:
            progress(min(99.0, 100.0 * done[0] / (total * ATTEMPT_FACTOR)), f"items {rep.kept}/{total}")

    if progress:
        progress(0.0, "generating questions")
    for o, wanted, have in work:
        passages = [p for pid in o.passage_ids if (p := store.get_passage(pid))]
        passages = [p for p in passages if is_quotable(p.text)] or passages
        fill_objective(store, llm, o, passages, mode=course.mode, wanted=wanted, formats=formats, rng=rng,
                       now_fn=now_fn, report=rep, start=have, on_attempt=tick,
                       code_language=course.code_language, should_stop=should_stop)
        if rep.llm_unavailable or rep.stopped:
            break
    if progress:
        progress(100.0, f"done: {rep.kept} kept, {rep.dropped} dropped")
    return rep


def top_up(store: Store, llm: LLMPort, course_id: str, target: int = 3, progress: Progress | None = None, *,
           background: bool = False, rng: random.Random | None = None,
           now_fn: Callable[[], datetime] = utcnow, formats: Sequence[str] | None = None):
    """Refill objectives that fell below `target` active items (reports and drops shrink the bank).
    background=True runs on a daemon thread and returns the Thread; otherwise returns the BankReport."""
    def run() -> BankReport:
        return build_bank(store, llm, course_id, target, progress, rng=rng, now_fn=now_fn, formats=formats)

    if not background:
        return run()
    t = threading.Thread(target=run, name=f"codecoach-topup-{course_id}", daemon=True)
    t.start()
    return t
