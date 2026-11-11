"""Ready-made projects that need no model. Plain data, loaded lazily, no new dependency.

A starter is a package `codecoach/starters/<id with _>/` with
    __init__.py       SHARED: id, title, pitch, summary and, per step, title, goal, concept, task, visible and hidden tests
    lang_<id>.py      LANG:   per step: lesson (2-5 sentences), example {code, stdin}, solution (the FULL program after the
                              step) and hints (exactly 3, ladder order, none carrying a line of the solution)

The engine turns this data into the same plan and step replies a model would give, then runs the same gates G1-G8.
Nothing here touches the sandbox or the model.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

STARTER_IDS = ("grade-book",)           # display order


def _pkg(starter_id: str) -> str:
    return "codecoach.starters." + starter_id.replace("-", "_")


def known(starter_id: Any) -> bool:
    return isinstance(starter_id, str) and starter_id in STARTER_IDS


def shared(starter_id: str) -> dict:
    """The language-independent part. KeyError for an unknown starter."""
    if not known(starter_id):
        raise KeyError(starter_id)
    return importlib.import_module(_pkg(starter_id)).SHARED


def part(starter_id: str, language: str) -> dict | None:
    """The part for one language, or None when this starter has no data for it."""
    if not known(starter_id) or not isinstance(language, str) or not language.isidentifier():
        return None
    name = f"{_pkg(starter_id)}.lang_{language}"
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name).LANG


def languages(starter_id: str) -> list[str]:
    """Language ids that have data for this starter (not whether the machine can run them)."""
    from codecoach.lang import all_ids
    return [lid for lid in all_ids() if part(starter_id, lid) is not None]


def listing(ready: list[str]) -> list[dict]:
    """The `starters` rows of GET /projects: languages are those with data AND in `ready`."""
    rows = []
    for sid in STARTER_IDS:
        sh = shared(sid)
        rows.append({"id": sid, "title": sh["title"], "pitch": sh["pitch"], "steps": len(sh["steps"]),
                     "languages": [lid for lid in languages(sid) if lid in ready]})
    return rows


def plan(starter_id: str) -> dict:
    """The plan in PlanReply shape."""
    sh = shared(starter_id)
    return {"feasible": True, "why_not": "", "title": sh["title"], "summary": sh["summary"],
            "steps": [{"title": s["title"], "goal": s["goal"], "concept": s["concept"]} for s in sh["steps"]]}


def step_reply(starter_id: str, language: str, i: int) -> dict:
    """Step `i` in StepReply shape. KeyError/IndexError when the starter, language or step does not exist."""
    sh = shared(starter_id)
    lp = part(starter_id, language)
    if lp is None:
        raise KeyError(language)
    s, d = sh["steps"][i], lp["steps"][i]
    rows = lambda ts: [{"name": n, "stdin": a, "expected_stdout": b} for n, a, b in ts]  # noqa: E731
    return {"lesson": list(d["lesson"]), "example": dict(d["example"]), "task": s["task"],
            "reference_solution": d["solution"], "visible_tests": rows(s["visible"]), "hidden_tests": rows(s["hidden"]),
            "hints": list(d["hints"])}
