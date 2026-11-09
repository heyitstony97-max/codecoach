"""Syllabus coverage check: what the learner MUST learn, checked against what they were GIVEN.

One objective per syllabus line. The statement is the line verbatim. Retrieval is deterministic
(BM25 over the materials' passages), the model only proposes supporting quotes from those
candidates, and a topic counts as covered only when a quote passes BOTH:
  (a) the source gate (word-boundary match inside that exact candidate passage, long enough), and
  (b) it shares >= MIN_SHARED content words (stemmed) with the syllabus line, so a real quote that is
      about something else cannot mark a topic covered.
Everything else is not_covered, with a reason. FAIL CLOSED: a model error leaves the topic
not_covered ("could not check") and the import goes on.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codecoach.core.llm import LLMError, LLMPort, LLMUnavailable, structured_call
from codecoach.core.models import CourseMap, Edge, Lesson, Objective, Passage, Unit
from codecoach.core.text import normalize
from codecoach.ground import gate
from codecoach.importer.verbs import COMPUTED_VERBS, VERBS, looks_like_code, normalize_verb

PURPOSE_VERB = "codecoach.import.syllabus_verb"
PURPOSE_COVER = "codecoach.import.syllabus_cover"

TOP_K = 6                       # candidate passages per objective
MIN_SHARED = 2                  # content words a quote must share with the syllabus line
MAX_LINES = 200
MIN_LINE_CHARS = 6
CALL_ATTEMPTS = 3
LOOKS = 2            # cover checks per line when the first yields no verified quote
ABORT_AFTER_UNAVAILABLE = 5     # this many objectives in a row with the model unreachable aborts
PASSAGE_CHARS_IN_PROMPT = 1200

REASON_NONE = "no passage in your materials covers this"
REASON_UNCHECKED = "could not check"


# ---------------------------------------------------------------- parsing

_LIST_MARK = re.compile(r"^\s*(?:\d{1,3}\s*[.)]|\(\d{1,3}\)|[-*\u2022+\u2013])\s+(?=\S)")
_HEADING = re.compile(r"^\s*#{1,6}\s")


def parse_syllabus(text: str) -> list[str]:
    """Syllabus lines, verbatim apart from the list marker. No LLM.

    Numbered (1. / 1) / (1)) and bulleted (- * + bullet) lines are the topics when there are any;
    headings and the intro sentence around them are ignored. With no list lines at all, every
    plain line that is not a heading and does not end in a colon is a topic."""
    rows = [ln.rstrip() for ln in (text or "").replace("\r", "\n").split("\n")]
    listed = [_LIST_MARK.sub("", ln, count=1).strip() for ln in rows if _LIST_MARK.match(ln)]
    if listed:
        lines = listed
    else:
        lines = [ln.strip() for ln in rows if ln.strip() and not _HEADING.match(ln)
                 and not ln.strip().endswith(":")]
    return [" ".join(ln.split()) for ln in lines if len(" ".join(ln.split())) >= MIN_LINE_CHARS]


# ---------------------------------------------------------------- words, stems, retrieval

_STOP = frozenset("""
a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having
he her here hers him his how i if in into is it its itself just me more most my no nor not now of off on
once only or other our out over own same she should so some such than that the their them then there these
they this those through to too under until up very was we were what when where which while who whom why
will with would you your able unit units course lesson end learner learners
describe explain trace summarize summarise identify define compare list discuss outline understand
state give show use know learn tell say name
""".split())

_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "ies", "ied", "er", "ed", "es", "s", "ly")
_WORD = re.compile(r"[a-z0-9]+(?:['\u2019][a-z]+)?")


def stem(w: str) -> str:
    w = w.replace("\u2019", "'")
    if w.endswith("'s"):
        w = w[:-2]
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
            break
    if w.endswith("e") and len(w) > 4:          # spike / spikes / spiked all meet at "spik"
        w = w[:-1]
    return w


def content_words(text: str) -> list[str]:
    """Stemmed content words, stopwords removed, in order (duplicates kept)."""
    out = []
    for w in _WORD.findall(normalize(text)):
        if w in _STOP or len(w) < 2:
            continue
        s = stem(w)
        if s and s not in _STOP:
            out.append(s)
    return out


_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
TOP_SIMPLE = 3                  # candidates in the simpler second ask


def heading_paths(passages: list[Passage], text: str) -> dict[str, str]:
    """passage id -> "Four Special Spikes > Nevada's Silver Spike" for markdown materials (else {}).

    Re-walks the headings the way split_document does (same slug and duplicate numbering) so the slug in
    a passage id leads back to the full chain of titles above it."""
    from codecoach.core.text import slugify
    chain: dict[str, str] = {}
    stack: list[tuple[int, str]] = []
    used: dict[str, int] = {"intro": 1}
    fence = False
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _MD_FENCE.match(raw):
            fence = not fence
            continue
        m = None if fence else _MD_HEADING.match(raw.rstrip())
        if not m:
            continue
        level, title = len(m.group(1)), (m.group(2).strip() or "Untitled")
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        base = slugify(title)
        used[base] = used.get(base, 0) + 1
        slug = base if used[base] == 1 else f"{base}-{used[base]}"
        chain[slug] = " > ".join(t for lv, t in stack if lv > 1)   # the h1 is the book title: noise
    out = {}
    for p in passages:
        slug = p.id.split(":")[1] if p.id.count(":") == 2 else ""
        if slug in chain:
            out[p.id] = chain[slug]
    return out


class Index:
    """BM25 over passages, scored on their text PLUS their heading path. Deterministic: ties break on
    passage order."""

    K1, B = 1.5, 0.75

    def __init__(self, passages: list[Passage], heads: dict[str, str] | None = None):
        self.passages = passages
        self.heads = heads or {}
        self.docs = [content_words(self.heads.get(p.id, "") + " " + p.text) for p in passages]
        self.avg = (sum(len(d) for d in self.docs) / len(self.docs)) if self.docs else 0.0
        self.df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1

    def top(self, query: str, k: int = TOP_K) -> list[Passage]:
        q = set(content_words(query))
        n = len(self.docs)
        scored = []
        for i, d in enumerate(self.docs):
            if not d:
                continue
            tf: dict[str, int] = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * len(d) / (self.avg or 1.0)))
            if s > 0:
                scored.append((-s, i))
        scored.sort()
        return [self.passages[i] for _, i in scored[:k]]


# ---------------------------------------------------------------- model schemas

class VerbReply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verb: str

    @field_validator("verb")
    @classmethod
    def _v(cls, v: str) -> str:
        v = normalize_verb(v)
        if v not in VERBS:
            raise ValueError(f"verb must be one of {', '.join(VERBS)}; got {v!r}")
        return v


class Support(BaseModel):
    model_config = ConfigDict(extra="ignore")
    passage_id: str
    quote: str = Field(min_length=1)


class CoverReply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    support: list[Support] = Field(default_factory=list, max_length=12)


VERB_INSTRUCTIONS = (
    "Pick the ONE verb that best describes what a learner is asked to do in this syllabus line. "
    "Reply with JSON {\"verb\": \"...\"} where verb is exactly one of: " + ", ".join(VERBS) + ". "
    "identify = pick out a fact, define = state a term, order = put events in sequence, compare = similarities or "
    "differences, explain = say why or how (also for describe and summarize), trace = follow a process or code "
    "step by step, write-code = write a small program, compute/solve/convert = numeric work."
)

SIMPLE_INSTRUCTIONS = (
    "Below is a topic and a few passages from a learner's study materials. Copy the single sentence, word for "
    "word, from ONE passage that best supports the topic. Reply with JSON {\"support\": [{\"passage_id\": id, "
    "\"quote\": sentence}]} with exactly one entry. If no passage supports the topic, answer none: "
    "{\"support\": []}. Never paraphrase and never use outside knowledge."
)

COVER_INSTRUCTIONS = (
    "A learner's syllabus lists a topic. Below are the ONLY passages of their study materials that might cover it. "
    "Decide whether these passages actually teach the topic. If they do, reply with JSON "
    "{\"support\": [{\"passage_id\": id, \"quote\": text}]} where each quote is copied WORD FOR WORD from that one "
    "passage (one or two sentences) and states something the topic asks about. If the passages do not cover the "
    "topic, or only mention a neighbouring subject, reply {\"support\": []}. Never use outside knowledge, never "
    "paraphrase, and never guess: an empty list is the right answer when the materials are silent."
)

_VERB_BY_WORD = {
    "describe": "explain", "explain": "explain", "summarize": "explain", "summarise": "explain",
    "discuss": "explain", "justify": "explain", "why": "explain", "how": "explain",
    "identify": "identify", "list": "identify", "name": "identify", "recognize": "identify",
    "recognise": "identify", "state": "identify", "recall": "identify",
    "define": "define", "order": "order", "sequence": "order", "arrange": "order",
    "compare": "compare", "contrast": "compare", "distinguish": "compare",
    "compute": "compute", "calculate": "compute", "solve": "solve", "convert": "convert",
    "trace": "trace", "follow": "trace", "write": "write-code", "implement": "write-code",
    "code": "write-code", "program": "write-code",
}


def fallback_verb(line: str) -> str:
    first = (_WORD.findall(line.lower()) or [""])[0]
    return _VERB_BY_WORD.get(first, "explain")


def pick_verb(llm: LLMPort, line: str) -> str:
    """One small structured call; any trouble falls back to the deterministic first-word map."""
    try:
        return structured_call(llm, VerbReply, instructions=VERB_INSTRUCTIONS, text=f"Syllabus line: {line}",
                               purpose=PURPOSE_VERB, retries=2).verb
    except LLMError:
        return fallback_verb(line)


def cover_prompt(line: str, candidates: list[Passage]) -> str:
    rows = [f"Topic: {line}", "Passages:"]
    for p in candidates:
        body = p.text if len(p.text) <= PASSAGE_CHARS_IN_PROMPT else p.text[:PASSAGE_CHARS_IN_PROMPT] + " ..."
        rows.append(f"[{p.id}] {body}")
    return "\n".join(rows)


# ---------------------------------------------------------------- the verdict

@dataclass
class Verdict:
    line: str
    verb: str = "explain"
    covered: bool = False
    reason: str = REASON_NONE
    passage_ids: list[str] = field(default_factory=list)
    quote: str = ""
    quote_passage: str = ""
    candidates: list[str] = field(default_factory=list)
    unavailable: bool = False


def judge(line: str, support: list[Support], candidates: list[Passage]) -> tuple[list[str], str, str]:
    """Apply gates (a) and (b) to the model's proposals. Returns (passage_ids, first good quote, its passage)."""
    by_id = {p.id: p for p in candidates}
    want = set(content_words(line))
    ids: list[str] = []
    first, first_pid = "", ""
    for s in support:
        p = by_id.get(s.passage_id.strip())
        quote = s.quote.strip()
        if p is None or not gate.quote_long_enough(quote) or not gate.text_found(p.text, quote):
            continue                                        # (a) not a real quote of that passage
        if gate._too_broad(p.text, quote):                  # noqa: SLF001 (same gate as items)
            continue
        if len(want & set(content_words(quote))) < MIN_SHARED:
            continue                                        # (b) real, but about something else
        if p.id not in ids:
            ids.append(p.id)
        if not first:
            first, first_pid = quote, p.id
    return ids, first, first_pid


def check_line(llm: LLMPort, index: Index, line: str) -> Verdict:
    """First look: the full ask over the top 6. Second look (only when the first yields no quote that passes
    BOTH gates): the simple ask over the top 3. The gates are deterministic, so another sample can only turn
    a line covered with a quote that is really in the passage and really on topic."""
    v = Verdict(line=line, verb=pick_verb(llm, line))
    candidates = index.top(line)
    v.candidates = [p.id for p in candidates]
    if not candidates:
        return v
    for look in range(LOOKS):
        asked = candidates if look == 0 else candidates[:TOP_SIMPLE]
        try:
            got = structured_call(llm, CoverReply,
                                  instructions=COVER_INSTRUCTIONS if look == 0 else SIMPLE_INSTRUCTIONS,
                                  text=cover_prompt(line, asked), purpose=PURPOSE_COVER, retries=CALL_ATTEMPTS)
        except LLMUnavailable:
            v.reason, v.unavailable = REASON_UNCHECKED, True
            return v
        except LLMError:
            v.reason = REASON_UNCHECKED
            return v
        ids, quote, pid = judge(line, got.support, asked)
        if ids:
            v.covered, v.reason, v.passage_ids, v.quote, v.quote_passage = True, "", ids, quote, pid
            break
    return v


def objective_for(course_id: str, n: int, v: Verdict, passages_text: dict[str, str]) -> Objective:
    kind = "reading"
    if v.verb == "write-code":
        kind = "code"
    elif v.verb == "trace" and looks_like_code(v.line, " ".join(passages_text.get(i, "") for i in v.passage_ids)):
        kind = "code"
    elif v.verb in COMPUTED_VERBS:
        kind = "math"
    return Objective(id=f"{course_id}:o{n:02d}", unit="-", lesson="-", verb=v.verb, statement=v.line, kind=kind,
                     passage_ids=v.passage_ids if v.covered else [],
                     coverage="covered" if v.covered else "not_covered")  # type: ignore[arg-type]


@dataclass
class Checked:
    objectives: list[Objective]
    verdicts: list[Verdict]
    issues: list[dict]


def check_syllabus(llm: LLMPort, course_id: str, passages: list[Passage], lines: list[str], report,
                   heads: dict[str, str] | None = None) -> Checked:
    """Verdict for every line (in order). `report(stage, pct)` is called once per line."""
    from codecoach.importer.build import ImportFailed          # local: build imports this module

    index = Index(passages, heads)
    text_of = {p.id: p.text for p in passages}
    issues: list[dict] = []
    verdicts: list[Verdict] = []
    objectives: list[Objective] = []
    unavailable_run = 0
    seen: set[str] = set()
    for line in lines:
        key = normalize(line)
        if key in seen:
            issues.append({"kind": "duplicate_objective", "section": "syllabus", "detail": line})
            continue
        seen.add(key)
        i = len(verdicts)
        report(f"Checking your syllabus ({i + 1}/{len(lines)}): {line[:60]}", 8 + 72 * i / max(1, len(lines)))
        v = check_line(llm, index, line)
        unavailable_run = unavailable_run + 1 if v.unavailable else 0
        if unavailable_run >= ABORT_AFTER_UNAVAILABLE:
            raise ImportFailed(f"the model was unreachable for {unavailable_run} syllabus lines in a row", course_id)
        verdicts.append(v)
        obj = objective_for(course_id, len(verdicts), v, text_of)
        objectives.append(obj)
        if not v.covered:
            issues.append({"kind": "not_covered", "section": "syllabus", "detail": f"{line} ({v.reason})"})
    return Checked(objectives, verdicts, issues)


def course_map(course_id: str, title: str, objectives: list[Objective], edges: list[tuple[str, str]]) -> CourseMap:
    """One unit, one lesson per syllabus line (a not-covered line stays visible, greyed, in its own lesson)."""
    uid = f"{course_id}:u01"
    lessons = []
    fixed: list[Objective] = []
    for i, o in enumerate(objectives, 1):
        lid = f"{uid}:l{i:02d}"
        o2 = o.model_copy(update={"unit": uid, "lesson": lid})
        fixed.append(o2)
        lessons.append(Lesson(id=lid, title=o.statement[:90], objectives=[o2]))
    return CourseMap(course_id=course_id, units=[Unit(id=uid, title=title, lessons=lessons)],
                     edges=[Edge(prereq=a, objective=b) for a, b in edges])


def syllabus_report(objectives: list[Objective], verdicts: list[Verdict]) -> list[dict]:
    return [{"objective_id": o.id, "line": v.line, "verb": v.verb, "coverage": o.coverage, "reason": v.reason,
             "passage_ids": o.passage_ids, "quote": v.quote, "quote_passage": v.quote_passage,
             "candidates": v.candidates} for o, v in zip(objectives, verdicts)]
