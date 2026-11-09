"""Shared builders for the red-team suite (R1). Nothing here attacks anything by itself.

Every attack is one test function named `test_rtNN_<slug>` whose docstring is a small record:

    RT37: one-line title of the attack
    severity: high | medium | low
    fix: one-line fix suggestion (what would close it)
    kind: fixable | honest-limit

Severity answers "would a learner be told something false?":
  high    a false fact is shown as true, or the answer key is false
  medium  a right answer is graded wrong or a wrong answer graded right, but no false fact is stated
  low     nothing false is said; the item leaks its own answer or the shown proof proves nothing
`kind` says whether a deterministic code change closes the demonstrated attack (fixable) or only
heuristics could shrink it because the gate would need to understand meaning (honest-limit).

An attack the gate stops passes as a plain test. An attack that gets through is the same test body
marked `@pytest.mark.xfail(strict=True, reason="HOLE: ...")`: the suite stays green, the hole stays
visible, and the day somebody fixes it the strict xfail turns red until the marker is removed.
docs/REDTEAM.md is generated from these docstrings and marks by `redteam_doc.py`.
"""

from __future__ import annotations

import contextlib
from typing import Iterator, Sequence

from codecoach.core.models import (Citation, ClozePayload, ExplainPayload, Item, MatchPayload, MCPayload,
                                OrderPayload, Passage, ShortPayload)
from codecoach.ground.gate import check_item

# ------------------------------------------------------------------ passages


def passage(text: str, pid: str = "hist:p1:1") -> Passage:
    doc = pid.split(":")[0]
    return Passage(id=pid, doc_id=doc, location=f"{doc}, {pid.split(':')[1]}", text=text)


LINCOLN_TEXT = ("On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President. "
                "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states. "
                "About 1,000 soldiers camped near Gettysburg that summer, and Lee's army retreated.")
COLONY_TEXT = ("The Stamp Act of 1765 taxed printed paper. The Tea Act of 1773 gave a monopoly on tea. "
               "Independence was declared in 1776 by the Continental Congress.")

P1 = passage(LINCOLN_TEXT, "hist:p1:1")
P2 = passage(COLONY_TEXT, "hist:p2:1")
BOTH = [P1, P2]

Q_OATH = "On March 4, 1861, Abraham Lincoln took the oath of office as the sixteenth President."
Q_PROC = "In 1863, the Emancipation Proclamation freed enslaved people in the rebel states."
Q_STAMP = "The Stamp Act of 1765 taxed printed paper."
Q_TEA = "The Tea Act of 1773 gave a monopoly on tea."
Q_INDEP = "Independence was declared in 1776 by the Continental Congress."

# ------------------------------------------------------------------ items


def cites(pid: str, *quotes: str) -> list[Citation]:
    return [Citation(passage_id=pid, quote=q) for q in quotes]


def build(fmt: str, payload, prompt: str, citations: Sequence[Citation]) -> Item:
    return Item(id="it_rt", objective_id="o1", format=fmt, prompt=prompt, payload=payload,
                citations=list(citations), checker=fmt)


def mc(prompt: str, options: list[str], correct: int, citations: Sequence[Citation]) -> Item:
    return build("mc", MCPayload(options=options, correct_index=correct), prompt, citations)


def cloze(prompt: str, text_with_blank: str, answer: str, citations: Sequence[Citation],
          aliases: Sequence[str] = ()) -> Item:
    return build("cloze", ClozePayload(text_with_blank=text_with_blank, answer=answer,
                                       aliases=list(aliases)), prompt, citations)


def short(prompt: str, terms: list[str], citations: Sequence[Citation], min_terms: int | None = None) -> Item:
    return build("short", ShortPayload(required_terms=terms, min_terms=min_terms), prompt, citations)


def explain(prompt: str, citations: Sequence[Citation]) -> Item:
    return build("explain", ExplainPayload(), prompt, citations)


def ev(label: str, date_text: str, pid: str) -> dict:
    return {"label": label, "date_text": date_text, "passage_id": pid}


def order(prompt: str, events: list[dict], correct_order: list[int], citations: Sequence[Citation]) -> Item:
    return build("order", OrderPayload(events=events, correct_order=correct_order), prompt, citations)


def match(prompt: str, pairs: list[dict], citations: Sequence[Citation]) -> Item:
    return build("match", MatchPayload(pairs=pairs), prompt, citations)


# ------------------------------------------------------------------ registry stand-in


@contextlib.contextmanager
def numeric_key_verified() -> Iterator[None]:
    """Registers a stub 'numeric' checker that says the computed key is right, and restores the registry
    after. Other test modules clear the global registry, so these tests must not depend on `import
    codecoach.check` having registered the real one. What the attacks need is only: the KEY checks out."""
    from codecoach.core import registry

    saved = dict(registry._checkers)
    registry.register_checker("numeric", lambda item, response, *, mode="submit": registry.CheckResult(
        ok=True, score=1.0, detail="stub: the key matches the computed value"))
    try:
        yield
    finally:
        registry._checkers.clear()
        registry._checkers.update(saved)


# ------------------------------------------------------------------ verdicts


def codes(item: Item, passages: Sequence[Passage] = BOTH, context: Sequence[str] = ()) -> list[str]:
    return [f.code for f in check_item(item, passages, context=context)]


def assert_dropped(item: Item, passages: Sequence[Passage] = BOTH, code: str | None = None,
                   context: Sequence[str] = (), why: str = "") -> None:
    """`why` labels a CONTROL assertion ("control: ..."), so a failure message shows whether the control
    or the attack itself got through."""
    got = codes(item, passages, context)
    assert got, f"the gate let this item through{' [' + why + ']' if why else ''}"
    if code is not None:
        assert code in got, f"dropped, but for {got} instead of {code}"


def assert_passes(item: Item, passages: Sequence[Passage] = BOTH, context: Sequence[str] = ()) -> None:
    """Used for control items: the same item with an honest fact must pass, so a drop proves the gate
    reacted to the attack and not to some unrelated flaw in the fixture."""
    got = codes(item, passages, context)
    assert got == [], f"control item should pass but the gate said {got}"
