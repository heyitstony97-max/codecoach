"""Study tools assembled by code from the learner's own text, the question bank and the attempt log.

No LLM text appears here. Pure functions over a Store (Hermes-free):

  source_guide()   headings of one file, the topics whose passages sit in it, the uncovered syllabus lines
  mistakes()       items whose LATEST attempt scored below full credit (after hint credit), oldest first
  key_spans()      where the words that carry the correct answer sit inside a cited quote, in the passage
  study_guide_md() the Markdown export (every line is a fixed label or the learner's own text / bank content)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from codecoach.adapt.mastery import hint_credit
from codecoach.core.models import (ClozePayload, Item, MCPayload, MatchPayload, NOT_COVERED_LABEL, NumericPayload,
                                OrderPayload, ShortPayload)
from codecoach.core.store import Store
from codecoach.core.text import _TABLE, sup_piece
from codecoach.ground import gate

MISTAKES_SESSION = 10
FULL_CREDIT = 1.0 - 1e-9

# ------------------------------------------------------------------ fixed export labels (the whole list)
OUTLINE_LINE = "Course outline: not source material"
LABEL_SOURCE = "Source: "
LABEL_NOT_COVERED = NOT_COVERED_LABEL
LABEL_QUESTIONS = "Practice questions"
LABEL_KEY = "Answer key"
LABEL_UNIT = "Unit: "
LABEL_NONE = "(no passages)"
LABEL_NO_TOPICS = "(none)"
EXPORT_LABELS = {OUTLINE_LINE, LABEL_NOT_COVERED, LABEL_QUESTIONS, LABEL_KEY, LABEL_NONE, LABEL_NO_TOPICS}


# ------------------------------------------------------------------ source guide


class GuideHeading(BaseModel):
    title: str
    level: int
    start: int                      # character offset into the document text


class GuideTopic(BaseModel):
    objective_id: str
    statement: str
    mastery: float
    passage_id: str                 # first passage of the topic inside this file
    start: int | None = None


class SourceGuide(BaseModel):
    doc_id: str
    headings: list[GuideHeading] = Field(default_factory=list)
    topics: list[GuideTopic] = Field(default_factory=list)
    not_covered: list[str] = Field(default_factory=list)      # course-level
    not_covered_label: str = NOT_COVERED_LABEL


_MD_HEAD = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def headings_of(text: str, passages: list[tuple[str, int | None]]) -> list[GuideHeading]:
    """Markdown headings in order. A file with none (plain text, PDF) falls back to its sections:
    the passage locations with the paragraph number cut off, at the offset of their first passage."""
    out: list[GuideHeading] = []
    pos, fenced = 0, False
    for line in text.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            m = _MD_HEAD.match(line)
            if m and m.group(2).strip():
                out.append(GuideHeading(title=m.group(2).strip(), level=len(m.group(1)), start=pos))
        pos += len(line) + 1
    if out:
        return out
    seen: set[str] = set()
    for loc, start in passages:
        title = loc.rsplit(", para", 1)[0] if ", para" in loc else loc
        if title not in seen and start is not None:
            seen.add(title)
            out.append(GuideHeading(title=title, level=1, start=start))
    return out


def source_guide(store: Store, course_id: str, doc_id: str, text: str, spans: dict[str, tuple[int, int]],
                 locations: dict[str, str], mastery: dict[str, Any]) -> SourceGuide:
    order = [pid for pid in spans]
    heads = headings_of(text, [(locations.get(pid, ""), spans[pid][0]) for pid in order])
    topics: list[GuideTopic] = []
    gaps: list[str] = []
    for o in store.list_objectives(course_id):
        if o.coverage != "covered":
            gaps.append(o.statement)
            continue
        here = [pid for pid in dict.fromkeys(o.passage_ids) if pid in spans]
        if not here:
            continue
        first = min(here, key=lambda pid: spans[pid][0])
        m = mastery.get(o.id)
        topics.append(GuideTopic(objective_id=o.id, statement=o.statement, mastery=round(m.p, 4) if m else 0.0,
                                 passage_id=first, start=spans[first][0]))
    topics.sort(key=lambda t: (t.start if t.start is not None else 0, t.objective_id))
    return SourceGuide(doc_id=doc_id, headings=heads, topics=topics, not_covered=gaps)


# ------------------------------------------------------------------ mistakes


def latest_attempts(store: Store, course_id: str) -> dict[str, Any]:
    """item_id -> its newest attempt (by attempt id)."""
    last: dict[str, Any] = {}
    for a in store.list_attempts(course_id=course_id):          # ascending ids
        last[a.item_id] = a
    return last


def mistakes(store: Store, course_id: str, upto: int | None = None) -> list[tuple[Item, int]]:
    """Active items whose latest attempt scored below full credit once hint credit is applied, oldest latest
    attempt first. `upto` keeps only items whose latest attempt id is <= upto (a session started at that
    point: an item answered again during the session has a newer attempt and so leaves the session)."""
    rows: list[tuple[Item, int]] = []
    for item_id, a in latest_attempts(store, course_id).items():
        if upto is not None and (a.id or 0) > upto:
            continue
        if a.score * hint_credit(a.hints_used) >= FULL_CREDIT:
            continue
        item = store.get_item(item_id)
        if item is not None and item.status == "active":
            rows.append((item, a.id or 0))
    rows.sort(key=lambda r: r[1])
    return rows


def newest_attempt_id(store: Store, course_id: str) -> int:
    return max((a.id or 0 for a in store.list_attempts(course_id=course_id)), default=0)


# ------------------------------------------------------------------ why it's right: key words inside a quote


def _norm_map(text: str) -> tuple[str, list[int]]:
    """gate's normalisation (en/em dashes spaced, NFKC, quotes, casefold, spaces collapsed) built char by
    char, with the raw index of every normalised char."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        ch = ch.translate(gate._SPACED_DASHES)
        piece = unicodedata.normalize("NFKC", sup_piece(text, i) or ch).translate(_TABLE) or ""
        for c in piece.casefold():
            if c.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    idx.append(i)
            else:
                out.append(c)
                idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _boundary_ok(nh: str, i: int, j: int) -> bool:
    nn_first, nn_last = nh[i], nh[j - 1]
    left = (not nn_first.isalnum()) or i == 0 or not nh[i - 1].isalnum()
    right = (not nn_last.isalnum()) or j == len(nh) or not nh[j].isalnum()
    if not (left and right):
        return False
    if nn_first.isalnum() and gate._glued(nh, i - 1, -1):
        return False
    if nn_last.isalnum() and gate._glued(nh, j, 1) and not gate._possessive(nh, j):
        return False
    return True


def _occurrences(nh: str, nn: str, lo: int, hi: int) -> list[tuple[int, int]]:
    out, start = [], lo
    while nn:
        i = nh.find(nn, start, hi)
        if i < 0:
            break
        if _boundary_ok(nh, i, i + len(nn)):
            out.append((i, i + len(nn)))
            start = i + len(nn)
        else:
            start = i + 1
    return out


def answer_keys(item: Item) -> list[str]:
    """The strings that carry the correct answer, in display form. Order and match items contribute their
    dates and sides; math and code items none (their answers are computed, not quoted)."""
    p = item.payload
    if isinstance(p, MCPayload):
        return [p.options[p.correct_index]]
    if isinstance(p, ClozePayload):
        return [p.answer, *p.aliases]
    if isinstance(p, ShortPayload):
        return list(p.required_terms)
    if isinstance(p, NumericPayload):
        return [f"{p.answer_value:g}"]
    if isinstance(p, OrderPayload):
        return [e.date_text for e in p.events]
    if isinstance(p, MatchPayload):
        return [s for pr in p.pairs for s in (pr.left, pr.right)]
    return []


def key_spans(passage_text: str, quote: str, keys: list[str]) -> list[tuple[int, int]]:
    """Offsets into `passage_text` of the answer words, found INSIDE the cited quote only.

    Fails closed: the quote must be found in the passage and each key must satisfy gate.text_found against
    the quote; only occurrences that sit at word boundaries within the quote are returned, so a word that is
    not in the quote is never highlighted. normalize(passage_text[a:b]) always equals normalize(key)."""
    nh, idx = _norm_map(passage_text)
    nq, _ = _norm_map(quote)
    if not nq or not nh:
        return []
    where = _occurrences(nh, nq, 0, len(nh))
    if not where:
        return []
    qa, qb = where[0]
    found: list[tuple[int, int]] = []
    for key in keys:
        nk, _ = _norm_map(key)
        if len(nk) < 2 or not gate.text_found(quote, key):
            continue
        for a, b in _occurrences(nh, nk, qa, qb):
            found.append((idx[a], idx[b - 1] + 1))
    found.sort()
    merged: list[tuple[int, int]] = []
    for a, b in found:
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


# ------------------------------------------------------------------ the Markdown export


def _bq(text: str) -> list[str]:
    return [("> " + ln) if ln else ">" for ln in text.split("\n")]


def study_guide_md(store: Store, course_id: str, *, outline_label: bool, answer_text) -> str:
    course = store.get_course(course_id)
    cmap = store.get_map(course_id)
    if course is None or cmap is None:
        raise KeyError(course_id)
    lines: list[str] = []
    if outline_label:
        lines += [OUTLINE_LINE, ""]
    lines += [f"# {course.title}", ""]
    gaps: list[str] = []
    ordered_objs = []
    for u in cmap.units:
        lines += [f"## {LABEL_UNIT}{u.title}", ""]
        for l in u.lessons:
            for o in l.objectives:
                ob = store.get_objective(o.id) or o
                if ob.coverage != "covered":
                    gaps.append(ob.statement)
                    continue
                ordered_objs.append(ob)
                lines += [f"### {ob.statement}", ""]
                got = False
                for pid in dict.fromkeys(ob.passage_ids):
                    p = store.get_passage(pid)
                    if p is None:
                        continue
                    got = True
                    lines += _bq(p.text) + ["", LABEL_SOURCE + p.location, ""]
                if not got:
                    lines += [LABEL_NONE, ""]
    lines += [f"## {LABEL_NOT_COVERED}", ""]
    lines += [f"- {g}" for g in gaps] if gaps else [LABEL_NO_TOPICS]
    lines += ["", f"## {LABEL_QUESTIONS}", ""]
    rows: list[tuple[Item, str]] = []
    for ob in ordered_objs:
        for it in store.list_items(objective_id=ob.id, status="active"):
            if it.format == "code" or not it.citations:
                continue
            ans = answer_text(it)
            if ans is None or ans == "":
                continue
            rows.append((it, ans if isinstance(ans, str) else "; ".join(str(x) for x in ans)))
    for n, (it, _) in enumerate(rows, 1):
        lines.append(f"{n}. {' '.join(it.prompt.split())}")
    lines += ["", f"## {LABEL_KEY}", ""]
    for n, (it, ans) in enumerate(rows, 1):
        lines.append(f"{n}. {' '.join(ans.split())}")
        for c in it.citations:
            p = store.get_passage(c.passage_id)
            lines.append("   > " + " ".join(c.quote.split()))
            lines.append("   " + LABEL_SOURCE + (p.location if p else c.passage_id))
    return "\n".join(lines) + "\n"
