"""Deterministic section split: passages -> sections the objective drafter sees one at a time."""

import re
from dataclasses import dataclass, field

from codecoach.core.models import Passage
from codecoach.core.text import slugify

MAX_PASSAGES = 10      # a section sent to the model never has more passages than this
MAX_CHARS = 6000       # ... or more text than this
MAX_CAP = 8            # objectives one section may produce (the draft schema allows no more)

# Topic budget. Without it every section asks for up to 8 objectives, so a 12-page chapter cut into 26 headings
# came out as 159 topics. The budget scales with the text; tiny files (a short outline) are left alone.
CHARS_PER_TOPIC = 1800
MIN_BUDGET = 6
MAX_BUDGET = 40
SMALL_DOC_CHARS = 6000
MERGE_MAX_CHARS = 9000
MERGE_MAX_PASSAGES = 24

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_PAGE_NUM = re.compile(r":p(\d+):")


@dataclass
class Section:
    index: int
    title: str                 # lesson title
    unit: str                  # unit title
    passages: list[Passage] = field(default_factory=list)
    cap: int = MAX_CAP         # most objectives this section may yield
    base: str = ""             # the heading the title came from, when the title is replaced by a content title
    retitle: bool = False      # title is generic ("part 2", several headings merged): name it by content

    @property
    def passage_ids(self) -> list[str]:
        return [p.id for p in self.passages]


def has_headings(text: str) -> bool:
    fenced = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _HEADING.match(line):
            return True
    return False


def _headings(text: str) -> dict[str, tuple[str, int]]:
    """slug -> (title, level), reproducing core.passages slug numbering."""
    out: dict[str, tuple[str, int]] = {"intro": ("Introduction", 0)}
    used = {"intro": 1}
    fenced = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line.rstrip())
        if m:
            title = m.group(2).strip() or "Untitled"
            base = slugify(title)
            used[base] = used.get(base, 0) + 1
            slug = base if used[base] == 1 else f"{base}-{used[base]}"
            out[slug] = (title, len(m.group(1)))
    return out


def _chunks(passages: list[Passage]) -> list[list[Passage]]:
    chunks: list[list[Passage]] = []
    cur: list[Passage] = []
    size = 0
    for p in passages:
        if cur and (len(cur) >= MAX_PASSAGES or size + len(p.text) > MAX_CHARS):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p.text)
    if cur:
        chunks.append(cur)
    return chunks


def split_sections(passages: list[Passage], text: str, kind: str, course_title: str) -> list[Section]:
    """Group passages into sections. Markdown: one per heading (long ones are chunked). Text: one run per
    chapter (or the whole document), chunked by size. Same input -> same sections."""
    if not passages:
        return []
    groups: list[tuple[str, str, list[Passage]]] = []   # (unit, title, passages)
    if kind == "markdown":
        heads = _headings(text)
        levels = [lv for _, lv in heads.values() if lv > 0]
        unit_level = max(2, min(levels)) if levels else 2
        unit = "Introduction"
        order: dict[str, list[Passage]] = {}
        for p in passages:
            order.setdefault(p.id.split(":")[1], []).append(p)
        seq = list(heads)   # document order of slugs
        for slug in seq:
            title, level = heads[slug]
            if 0 < level <= unit_level:
                unit = title
            ps = order.get(slug)
            if ps:
                groups.append((unit, title, ps))
        for slug, ps in order.items():     # defensive: slugs we failed to map
            if slug not in heads:
                groups.append((unit, ps[0].location.rsplit(", para", 1)[0], ps))
    else:
        numbered = any(", p. " in p.location or p.location.startswith("p. ") for p in passages)
        cur_unit, run = None, []
        runs: list[tuple[str, list[Passage]]] = []
        for p in passages:
            m = re.match(r"(Ch\. [^,]+)", p.location)
            u = m.group(1) if m else course_title
            if run and u != cur_unit:
                runs.append((cur_unit or course_title, run))
                run = []
            cur_unit = u
            run.append(p)
        if run:
            runs.append((cur_unit or course_title, run))
        for u, ps in runs:
            for k, chunk in enumerate(_chunks(ps), 1):
                pages = [int(m.group(1)) for q in chunk if (m := _PAGE_NUM.search(q.id))]
                if numbered and pages:
                    lo, hi = min(pages), max(pages)
                    t = f"{u}, p. {lo}" if lo == hi else f"{u}, pp. {lo}-{hi}"
                else:
                    t = f"{u}, part {k}" if len(ps) > len(chunk) else u
                groups.append((u, t, chunk))
        secs = _number(groups)
        for sec, (_, t, _) in zip(secs, groups):
            if re.search(r", part \d+$", t):
                sec.base, sec.retitle = t.rsplit(", part", 1)[0], True
        return secs
    expanded: list[tuple[str, str, list[Passage]]] = []
    for unit, title, ps in groups:
        parts = _chunks(ps)
        for k, chunk in enumerate(parts, 1):
            expanded.append((unit, title if len(parts) == 1 else f"{title} (part {k})", chunk))
    secs = _number(expanded)
    for sec in secs:
        m = re.match(r"(.*) \(part \d+\)$", sec.title)
        if m:
            sec.base, sec.retitle = m.group(1), True
    return secs


def _number(groups: list[tuple[str, str, list[Passage]]]) -> list[Section]:
    return [Section(index=i, title=t, unit=u, passages=ps) for i, (u, t, ps) in enumerate(groups, 1)]


# ---------------------------------------------------------------- topic budget


def _chars(sec: Section) -> int:
    return sum(len(p.text) for p in sec.passages)


def topic_budget(sections: list[Section]) -> int | None:
    """Roughly how many topics this text deserves; None for a small file, which keeps its natural map."""
    total = sum(_chars(s) for s in sections)
    if total < SMALL_DOC_CHARS:
        return None
    return max(MIN_BUDGET, min(MAX_BUDGET, round(total / CHARS_PER_TOPIC)))


def plan_sections(sections: list[Section]) -> list[Section]:
    """Merge neighbouring small sections of one unit until the count fits the budget, then give each section
    its share of the budget as `cap`. Deterministic. No-op for a small file."""
    budget = topic_budget(sections)
    if budget is None or not sections:
        return sections
    secs = list(sections)
    while len(secs) > budget:
        best = None
        for i in range(len(secs) - 1):
            a, b = secs[i], secs[i + 1]
            if a.unit != b.unit:
                continue
            size, count = _chars(a) + _chars(b), len(a.passages) + len(b.passages)
            if size > MERGE_MAX_CHARS or count > MERGE_MAX_PASSAGES:
                continue
            if best is None or size < best[0]:
                best = (size, i)
        if best is None:
            break
        i = best[1]
        a, b = secs[i], secs[i + 1]
        merged = Section(index=0, title=f"{a.base or a.title}; {b.base or b.title}" if not a.retitle or a.base != b.base
                         else a.base, unit=a.unit, passages=a.passages + b.passages,
                         base=a.base or a.title, retitle=True)
        secs[i:i + 2] = [merged]
    total = sum(_chars(s) for s in secs) or 1
    raw = [budget * _chars(s) / total for s in secs]
    caps = [max(1, min(MAX_CAP, int(r))) for r in raw]
    # hand the leftover budget to the sections with the biggest remainders
    left = budget - sum(caps)
    for i in sorted(range(len(secs)), key=lambda i: raw[i] - int(raw[i]), reverse=True):
        if left <= 0:
            break
        if caps[i] < MAX_CAP:
            caps[i] += 1
            left -= 1
    for sec, cap in zip(secs, caps):
        sec.cap = cap
    for i, sec in enumerate(secs, 1):
        sec.index = i
    return secs
