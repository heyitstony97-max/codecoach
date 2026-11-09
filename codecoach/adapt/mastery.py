"""Mastery per objective: Bayesian Knowledge Tracing with fixed parameters, replayed from attempts.

Pure and deterministic: mastery is never stored, it is recomputed from the attempt log (plus an optional
placement prior), so there is no state to drift. Two rules from the contract:

- an `explain` attempt is self-rated, so it counts at half weight (EXPLAIN_WEIGHT)
- hints lower an attempt's credit: the score is multiplied by HINT_CREDIT[hints_used]
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from codecoach.core.store import Store


@dataclass(frozen=True)
class BKTParams:
    p_init: float = 0.20
    p_learn: float = 0.15
    p_slip: float = 0.10
    p_guess: float = 0.20


DEFAULT_PARAMS = BKTParams()
KNOWN_AT = 0.85       # p >= this: known
NEW_BELOW = 0.30      # p < this: new (not learned yet); in between: shaky
EXPLAIN_WEIGHT = 0.5
HINT_CREDIT = (1.0, 0.8, 0.6, 0.35)   # indexed by hints_used, clamped to the last entry


def hint_credit(hints_used: int) -> float:
    return HINT_CREDIT[min(max(hints_used, 0), len(HINT_CREDIT) - 1)]


def attempt_weight(fmt: str) -> float:
    return EXPLAIN_WEIGHT if fmt == "explain" else 1.0


def attempt_evidence(fmt: str, score: float, hints_used: int = 0) -> tuple[float, float]:
    """(q, weight): q in 0..1 is how much the attempt counts as a correct answer, weight how much it counts at all."""
    q = min(max(float(score), 0.0), 1.0) * hint_credit(hints_used)
    return q, attempt_weight(fmt)


def bkt_update(p: float, q: float, weight: float = 1.0, params: BKTParams = DEFAULT_PARAMS) -> float:
    """One soft BKT step. q=1 is a correct observation, q=0 a wrong one, in between a mixture.
    weight scales both the evidence and the learning transition."""
    pc = p * (1 - params.p_slip) / (p * (1 - params.p_slip) + (1 - p) * params.p_guess)
    pw = p * params.p_slip / (p * params.p_slip + (1 - p) * (1 - params.p_guess))
    post = q * pc + (1 - q) * pw
    blended = (1 - weight) * p + weight * post
    return min(1.0, max(0.0, blended + (1 - blended) * params.p_learn * weight))


def state_of(p: float) -> str:
    if p >= KNOWN_AT:
        return "known"
    if p < NEW_BELOW:
        return "new"
    return "shaky"


@dataclass(frozen=True)
class Mastery:
    objective_id: str
    p: float
    state: str            # new | shaky | known
    attempts: int         # attempts replayed into p
    last_at: datetime | None


def prior_key(course_id: str) -> str:
    return f"adapt:prior:{course_id}"


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def set_priors(store: Store, course_id: str, priors: dict[str, float], at: datetime) -> None:
    """Placement writes these. Attempts at or before `at` are ignored by the replay (they fed the placement)."""
    cur = dict(store.get_meta(prior_key(course_id), {}) or {})
    for oid, p in priors.items():
        cur[oid] = {"p": float(p), "at": iso(at)}
    store.set_meta(prior_key(course_id), cur)


def mastery_map(store: Store, course_id: str, params: BKTParams = DEFAULT_PARAMS) -> dict[str, Mastery]:
    """Mastery for every COVERED objective of the course."""
    objs = [o for o in store.list_objectives(course_id) if o.coverage == "covered"]
    items = {i.id: i for i in store.list_items(course_id=course_id)}
    priors = store.get_meta(prior_key(course_id), {}) or {}
    by_obj: dict[str, list] = defaultdict(list)
    for a in store.list_attempts(course_id=course_id):
        it = items.get(a.item_id)
        if it is not None:
            by_obj[it.objective_id].append((a, it.format))
    out: dict[str, Mastery] = {}
    for o in objs:
        p, since = params.p_init, None
        if o.id in priors:
            p, since = float(priors[o.id]["p"]), parse_iso(priors[o.id]["at"])
        n, last = 0, None
        for a, fmt in by_obj.get(o.id, []):
            if since is not None and a.at <= since:
                continue
            q, w = attempt_evidence(fmt, a.score, a.hints_used)
            p = bkt_update(p, q, w, params)
            n, last = n + 1, a.at
        out[o.id] = Mastery(o.id, p, state_of(p), n, last)
    return out


def objective_mastery(store: Store, objective_id: str) -> Mastery | None:
    cid = store.course_of_objective(objective_id)
    return mastery_map(store, cid).get(objective_id) if cid else None


def mean_mastery(store: Store, course_id: str) -> float:
    m = mastery_map(store, course_id)
    return sum(x.p for x in m.values()) / len(m) if m else 0.0
