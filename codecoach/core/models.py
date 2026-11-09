"""The data contract (pydantic v2). FROZEN: see docs/SPEC.md.

Conventions
- All datetimes are timezone-aware UTC. Naive input is read as UTC.
- Models forbid unknown fields, so a typo in a lane fails loudly.
- `format` is the discriminator of the payload union and must equal Item.format.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------- literals

Mode = Literal["strict", "open"]
DocKind = Literal["text", "markdown", "pdf_text"]
ObjectiveKind = Literal["reading", "code", "math", "science"]
Coverage = Literal["covered", "not_covered"]
ItemFormat = Literal["mc", "cloze", "order", "match", "short", "explain", "code", "math", "numeric"]
ItemOrigin = Literal["materials", "model_knowledge"]
ItemStatus = Literal["active", "dropped", "reported"]
JobStatus = Literal["queued", "running", "done", "error"]
GateCode = Literal[
    "quote_missing", "unsupported_fact", "ambiguous", "bad_cloze", "framing_fact",
    "key_mismatch", "weak_tests", "self_check_failed", "schema_invalid",
    "ask_quote_missing", "ask_unsupported_fact", "ask_negation", "ask_no_cite", "ask_too_long",   # Ask, codecoach/ask
]

GATE_CODES: tuple[str, ...] = (
    "quote_missing", "unsupported_fact", "ambiguous", "bad_cloze", "framing_fact",
    "key_mismatch", "weak_tests", "self_check_failed", "schema_invalid",
)
GATE_LABELS: dict[str, str] = {
    "quote_missing": "Quote not found in your materials",
    "unsupported_fact": "Answer contains a fact (number, date or name) the quote does not support",
    "ambiguous": "More than one answer was supported by the source",
    "bad_cloze": "The blank was not an exact phrase from the source",
    "framing_fact": "Lesson intro added a fact that is not in the source",
    "key_mismatch": "Answer key disagreed with the computed answer",
    "weak_tests": "Code tests were too weak or did not pass the reference solution",
    "self_check_failed": "The question failed its own consistency check",
    "schema_invalid": "The model's output was malformed",
}

BLANK = "____"  # the cloze blank marker inside Cloze.text_with_blank
NOT_FROM_MATERIALS_LABEL = "Not from your materials"
FROM_MATERIALS_LABEL = "From your materials"
NOT_COVERED_LABEL = "Not in your materials"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(v: Any) -> Any:
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, v: Any) -> Any:
        return _aware(v)


def doc_of(passage_id: str) -> str:
    """The doc a passage id belongs to: ids are '<doc>:<page-or-slug>:<para>' and doc ids have no ':'."""
    return passage_id.split(":", 1)[0]


class _Located(_M):
    """Anything that points at a passage also names its doc, so the UI can open the Sources panel at it."""
    doc_id: str | None = None

    @model_validator(mode="after")
    def _fill_doc_id(self) -> "_Located":
        pid = getattr(self, "passage_id", None)
        if self.doc_id is None and isinstance(pid, str) and pid:
            self.doc_id = doc_of(pid)
        return self


_SLUG = r"^[a-z0-9][a-z0-9_-]{0,63}$"
Slug = Annotated[str, Field(pattern=_SLUG)]
NonEmpty = Annotated[str, Field(min_length=1)]

# ---------------------------------------------------------------- course / docs / passages


class Course(_M):
    id: Slug
    title: NonEmpty
    mode: Mode = "strict"
    mode_reason: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    code_language: Literal["python", "c"] | None = None   # language of this course's code items (None = unknown)
    subject: str | None = None     # Library grouping (History, Programming, ...); None = Uncategorized


class Doc(_M):
    id: Slug                      # globally unique, no ':'
    course_id: Slug
    name: NonEmpty
    kind: DocKind = "text"


class Passage(_M):
    id: NonEmpty                  # "<doc>:p<page>:<para>" or "<doc>:<heading-slug>:<para>"
    doc_id: Slug
    location: NonEmpty            # human readable, e.g. "Ch. 2, p. 14, para 3"
    text: NonEmpty
    start: int | None = None      # character offsets into docs.text (None on courses imported before offsets)
    end: int | None = None
    formula_missing: bool = False  # an equation stood here as a picture (PDF); CodeCoach can't read it

    @model_validator(mode="after")
    def _id_shape(self) -> "Passage":
        parts = self.id.split(":")
        if len(parts) != 3 or parts[0] != self.doc_id or not parts[1] or not parts[2].isdigit():
            raise ValueError(f"passage id {self.id!r} must be '<doc_id>:<page-or-slug>:<para>'")
        return self


class Citation(_M):
    passage_id: NonEmpty
    quote: NonEmpty


# ---------------------------------------------------------------- course map


class Objective(_M):
    id: NonEmpty
    unit: NonEmpty                # Unit.id
    lesson: NonEmpty              # Lesson.id
    verb: NonEmpty                # Bloom-ish verb: "identify", "compute", "write"...
    statement: NonEmpty
    kind: ObjectiveKind = "reading"
    prereqs: list[str] = Field(default_factory=list)   # objective ids that must come first
    passage_ids: list[str] = Field(default_factory=list)
    coverage: Coverage = "covered"

    @model_validator(mode="after")
    def _coverage(self) -> "Objective":
        if self.coverage == "covered" and not self.passage_ids and self.kind == "reading":
            raise ValueError(f"reading objective {self.id} is 'covered' but cites no passages")
        if self.coverage == "not_covered" and self.passage_ids:
            raise ValueError(f"objective {self.id} is 'not_covered' but lists passage_ids")
        if self.id in self.prereqs:
            raise ValueError(f"objective {self.id} lists itself as a prerequisite")
        return self


class Lesson(_M):
    id: NonEmpty
    title: NonEmpty
    objectives: list[Objective] = Field(default_factory=list)


class Unit(_M):
    id: NonEmpty
    title: NonEmpty
    lessons: list[Lesson] = Field(default_factory=list)


class Edge(_M):
    """prereq must be learned before objective."""
    prereq: NonEmpty
    objective: NonEmpty


class CourseMap(_M):
    course_id: Slug
    units: list[Unit]
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _derive_edges(cls, data: Any) -> Any:
        """If `edges` is absent it is derived from Objective.prereqs."""
        if isinstance(data, dict) and "edges" not in data:
            edges, seen = [], set()
            for u in data.get("units", []):
                for l in (u.get("lessons", []) if isinstance(u, dict) else u.lessons):
                    for o in (l.get("objectives", []) if isinstance(l, dict) else l.objectives):
                        oid = o["id"] if isinstance(o, dict) else o.id
                        pre = o.get("prereqs", []) if isinstance(o, dict) else o.prereqs
                        for p in pre:
                            if (p, oid) not in seen:
                                seen.add((p, oid))
                                edges.append({"prereq": p, "objective": oid})
            data = {**data, "edges": edges}
        return data

    @model_validator(mode="after")
    def _check(self) -> "CourseMap":
        unit_ids: set[str] = set()
        lesson_ids: set[str] = set()
        obj_ids: set[str] = set()
        for u in self.units:
            if u.id in unit_ids:
                raise ValueError(f"duplicate unit id {u.id}")
            unit_ids.add(u.id)
            for l in u.lessons:
                if l.id in lesson_ids:
                    raise ValueError(f"duplicate lesson id {l.id}")
                lesson_ids.add(l.id)
                for o in l.objectives:
                    if o.id in obj_ids:
                        raise ValueError(f"duplicate objective id {o.id}")
                    obj_ids.add(o.id)
                    if o.unit != u.id or o.lesson != l.id:
                        raise ValueError(f"objective {o.id} says unit/lesson {o.unit}/{o.lesson} "
                                         f"but sits under {u.id}/{l.id}")
        graph: dict[str, set[str]] = defaultdict(set)   # prereq -> {dependents}
        pairs = {(e.prereq, e.objective) for e in self.edges}
        for o in self.objectives():
            pairs.update((p, o.id) for p in o.prereqs)
        for a, b in pairs:
            for x in (a, b):
                if x not in obj_ids:
                    raise ValueError(f"dangling objective id {x!r} in prerequisite edge {a!r}->{b!r}")
            if a == b:
                raise ValueError(f"objective {a} is its own prerequisite")
            graph[a].add(b)
        # cycle detection (iterative DFS, three colours)
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {n: WHITE for n in obj_ids}
        for start in sorted(obj_ids):
            if colour[start] != WHITE:
                continue
            stack = [(start, iter(sorted(graph.get(start, ()))))]
            colour[start] = GREY
            while stack:
                node, it = stack[-1]
                for nxt in it:
                    if colour[nxt] == GREY:
                        raise ValueError(f"prerequisite cycle through {nxt!r}")
                    if colour[nxt] == WHITE:
                        colour[nxt] = GREY
                        stack.append((nxt, iter(sorted(graph.get(nxt, ())))))
                        break
                else:
                    colour[node] = BLACK
                    stack.pop()
        return self

    def objectives(self) -> list[Objective]:
        return [o for u in self.units for l in u.lessons for o in l.objectives]

    def objective(self, objective_id: str) -> Objective | None:
        for o in self.objectives():
            if o.id == objective_id:
                return o
        return None

    def all_prereq_pairs(self) -> list[tuple[str, str]]:
        """Sorted unique (prereq, objective) pairs from edges and Objective.prereqs."""
        pairs = {(e.prereq, e.objective) for e in self.edges}
        for o in self.objectives():
            pairs.update((p, o.id) for p in o.prereqs)
        return sorted(pairs)


# ---------------------------------------------------------------- payloads (discriminated by `format`)


class MCPayload(_M):
    format: Literal["mc"] = "mc"
    options: list[NonEmpty] = Field(min_length=2, max_length=8)
    correct_index: int

    @model_validator(mode="after")
    def _ok(self) -> "MCPayload":
        if not 0 <= self.correct_index < len(self.options):
            raise ValueError("correct_index out of range")
        return self


class ClozePayload(_M):
    format: Literal["cloze"] = "cloze"
    text_with_blank: str          # contains BLANK exactly once
    answer: NonEmpty
    aliases: list[str] = Field(default_factory=list)   # only from the materials

    @model_validator(mode="after")
    def _ok(self) -> "ClozePayload":
        if self.text_with_blank.count(BLANK) != 1:
            raise ValueError(f"text_with_blank must contain {BLANK!r} exactly once")
        return self


class OrderEvent(_M):
    label: NonEmpty
    date_text: NonEmpty           # as written in the materials, e.g. "July 4, 1776"
    passage_id: NonEmpty


class OrderPayload(_M):
    """`events` is stored in PRESENTATION order. `correct_order` lists indexes into `events`
    from earliest to latest. The learner's response is a list of indexes in the same shape."""
    format: Literal["order"] = "order"
    events: list[OrderEvent] = Field(min_length=2, max_length=12)
    correct_order: list[int]

    @model_validator(mode="after")
    def _ok(self) -> "OrderPayload":
        if sorted(self.correct_order) != list(range(len(self.events))):
            raise ValueError("correct_order must be a permutation of event indexes")
        return self


class MatchPair(_M):
    left: NonEmpty
    right: NonEmpty
    passage_id: str | None = None


class MatchPayload(_M):
    """Learner response: dict {left: right}."""
    format: Literal["match"] = "match"
    pairs: list[MatchPair] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def _ok(self) -> "MatchPayload":
        if len({p.left for p in self.pairs}) != len(self.pairs):
            raise ValueError("duplicate left side")
        if len({p.right for p in self.pairs}) != len(self.pairs):
            raise ValueError("duplicate right side")
        return self


class ShortPayload(_M):
    format: Literal["short"] = "short"
    required_terms: list[NonEmpty] = Field(min_length=1)
    min_terms: int | None = None  # default: all terms required


class ExplainPayload(_M):
    """Self-rated: the source passage is shown after the attempt. Half weight toward mastery."""
    format: Literal["explain"] = "explain"


class CodeTest(_M):
    """ONE concrete test format. Exactly one of the two forms:
    - stdio form:    stdin -> expected_stdout (compared after trimming trailing whitespace per line)
    - harness form:  `harness` is source text. Python: a module of `assert` statements, run with the
                     learner's code importable as `solution` (`from solution import *`). C: a separate
                     translation unit that defines main() and calls the learner's functions (the learner
                     file then must not define main); exit status 0 = pass.
    """
    name: NonEmpty
    stdin: str = ""
    expected_stdout: str | None = None
    harness: str | None = None

    @model_validator(mode="after")
    def _one_form(self) -> "CodeTest":
        if (self.expected_stdout is None) == (self.harness is None):
            raise ValueError("a CodeTest needs exactly one of expected_stdout or harness")
        return self


class CodePayload(_M):
    format: Literal["code"] = "code"
    language: Literal["python", "c"]
    starter: str = ""
    visible_tests: list[CodeTest] = Field(min_length=1)
    hidden_tests: list[CodeTest] = Field(default_factory=list)
    reference_solution: NonEmpty
    time_limit_s: float = Field(default=5.0, gt=0, le=60)
    mem_mb: int = Field(default=256, ge=16, le=4096)


class MathPayload(_M):
    format: Literal["math"] = "math"
    op: Literal["diff", "integrate", "solve", "simplify", "evaluate"]
    expr: NonEmpty                # SymPy-parsable, e.g. "x**2*sin(x)"; for solve, "x**2 - 4 = 0" or "x**2 - 4"
    var: str = "x"
    given_answer: str | None = None   # the LLM's claimed key. SymPy must agree or the item is dropped
    subs: dict[str, str] | None = None  # for evaluate: {"x": "2"}
    places: int | None = Field(default=None, ge=0, le=8)   # evaluate: decimal places the answer must give (inexact values)
    origin: Literal["source", "practice"] = "source"   # "practice": CodeCoach's own problem; its expression is not in the cited quote


class Quantity(_M):
    name: NonEmpty
    value: float
    unit: str = ""


class NumericPayload(_M):
    format: Literal["numeric"] = "numeric"
    given: list[Quantity] = Field(default_factory=list)
    quantity: str = ""            # what is asked, e.g. "the final speed"
    answer_value: float
    unit: str
    rel_tol: float = Field(default=0.01, gt=0, lt=1)


Payload = Annotated[
    Union[MCPayload, ClozePayload, OrderPayload, MatchPayload, ShortPayload,
          ExplainPayload, CodePayload, MathPayload, NumericPayload],
    Field(discriminator="format"),
]


class Item(_M):
    id: NonEmpty
    objective_id: NonEmpty
    format: ItemFormat
    prompt: NonEmpty
    payload: Payload
    citations: list[Citation] = Field(default_factory=list)
    origin: ItemOrigin = "materials"
    checker: NonEmpty             # key into the checker registry, e.g. "mc", "code_python"
    status: ItemStatus = "active"
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _consistent(self) -> "Item":
        if self.payload.format != self.format:
            raise ValueError(f"item format {self.format!r} != payload format {self.payload.format!r}")
        if self.origin == "materials" and not self.citations:
            raise ValueError("an item with origin 'materials' needs at least one citation")
        return self


# ---------------------------------------------------------------- logs and learner state


class Drop(_M):
    id: int | None = None
    course_id: str | None = None  # the store fills this from the objective when omitted
    objective_id: NonEmpty
    format: str
    gate_code: GateCode
    detail: str = ""
    draft_hash: str = ""
    at: datetime = Field(default_factory=utcnow)


class Attempt(_M):
    id: int | None = None
    item_id: NonEmpty
    response: Any = None
    correct: bool | None = None   # None = self-rated or undecided
    score: float = Field(ge=0, le=1)
    graded_by: NonEmpty           # checker kind, "self_rating", ...
    hints_used: int = Field(default=0, ge=0, le=3)
    at: datetime = Field(default_factory=utcnow)


class ReviewState(_M):
    item_id: NonEmpty
    due: datetime
    stability: float = Field(ge=0)
    difficulty: float = Field(ge=0)
    reps: int = Field(default=0, ge=0)
    lapses: int = Field(default=0, ge=0)
    last_review: datetime | None = None


class Span(_M):
    start: int = Field(ge=0)
    end: int = Field(gt=0)        # character offsets into Passage.text

    @model_validator(mode="after")
    def _ok(self) -> "Span":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Hint(_Located):
    level: int = Field(ge=1, le=3)  # 1 names the section, 2 the paragraph, 3 shows it highlighted
    text: NonEmpty
    passage_id: str | None = None
    highlight: Span | None = None


class Job(_M):
    id: NonEmpty
    kind: NonEmpty
    status: JobStatus = "queued"
    pct: float = Field(default=0, ge=0, le=100)
    stage: str = ""
    error: str | None = None
    course_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- REST: shared pieces


class RevealedCitation(_Located):
    passage_id: str
    quote: str
    location: str | None = None   # "Ch. 2, p. 14, para 3"
    key_spans: list[tuple[int, int]] = Field(default_factory=list)   # offsets into the passage of the answer words


class PublicItem(_M):
    """What a learner may see before answering. Built only by public.public_view()."""
    id: str
    objective_id: str
    format: ItemFormat
    prompt: str
    payload: dict[str, Any]
    origin: ItemOrigin
    source_label: str
    status: ItemStatus = "active"
    hints_available: int = 3


class PerTestResult(_M):
    name: str
    passed: bool
    visible: bool = True
    detail: str = ""
    stdout: str | None = None
    expected: str | None = None
    time_ms: int | None = None


# ---------------------------------------------------------------- REST: responses (all under /api/plugins/codecoach)


class CourseSummary(_M):
    id: str
    title: str
    mode: Mode
    mode_reason: str = ""
    subject: str | None = None
    created_at: datetime
    objectives: int = 0
    covered: int = 0
    not_covered: int = 0
    items_active: int = 0
    mastery: float = Field(default=0, ge=0, le=1)
    due_reviews: int = 0


class CoursesResponse(_M):                # GET /courses
    courses: list[CourseSummary]


class ImportRequest(_M):                  # POST /courses/import
    title: NonEmpty
    text: str | None = None
    path: str | None = None
    mode: Mode = "strict"
    outline: bool = False          # true = the text is a syllabus/outline: stored as an open-mode course
    syllabus_text: str | None = None   # optional: what the learner must learn; checked against the materials
    syllabus_path: str | None = None   # ... or a file holding it
    subject: str | None = Field(default=None, max_length=40)   # None = detect automatically

    @model_validator(mode="after")
    def _one_source(self) -> "ImportRequest":
        if (self.text is None) == (self.path is None):
            raise ValueError("give exactly one of text or path")
        if self.syllabus_text is not None and self.syllabus_path is not None:
            raise ValueError("give at most one of syllabus_text or syllabus_path")
        if self.outline and (self.syllabus_text is not None or self.syllabus_path is not None):
            raise ValueError("an outline import has no materials to check a syllabus against")
        return self


class SubjectPatch(_M):                   # PATCH /courses/{id}
    subject: str | None = Field(default=None, max_length=40)


class ImportResponse(_M):
    job_id: str


JobResponse = Job                         # GET /jobs/{id}


class SourceRef(_M):
    passage_id: str
    doc_id: str


class ObjectiveView(Objective):
    sources: list[SourceRef] = Field(default_factory=list)   # where the topic panel can open the Sources panel
    mastery: float = Field(default=0, ge=0, le=1)
    items_active: int = 0
    due_reviews: int = 0
    status_label: str | None = None       # NOT_COVERED_LABEL for not_covered objectives

    @model_validator(mode="after")
    def _fill_sources(self) -> "ObjectiveView":
        if not self.sources:
            self.sources = [SourceRef(passage_id=p, doc_id=doc_of(p)) for p in dict.fromkeys(self.passage_ids)]
        return self


class LessonNode(_M):
    id: str
    title: str
    objectives: list[ObjectiveView]


class UnitNode(_M):
    id: str
    title: str
    lessons: list[LessonNode]


class MapSummary(_M):
    objectives: int
    covered: int
    not_covered: int
    mastered: int
    mean_mastery: float = Field(ge=0, le=1)


class MapResponse(_M):                    # GET /courses/{id}/map
    course: CourseSummary
    units: list[UnitNode]
    edges: list[Edge]
    summary: MapSummary


class LessonPassage(_Located):
    passage_id: str
    location: str
    text: str


class LessonView(_M):
    objective_id: str
    unit_title: str
    lesson_title: str
    statement: str
    framing: str | None = None            # at most one gate-checked line
    passages: list[LessonPassage]
    source_label: str = FROM_MATERIALS_LABEL


class NextResponse(_M):                   # GET /courses/{id}/next
    kind: Literal["lesson", "item", "done"]
    reason: Literal["new", "review", "interleave", "placement", "none"] = "new"
    lesson: LessonView | None = None
    item: PublicItem | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _ok(self) -> "NextResponse":
        if (self.kind == "lesson") != (self.lesson is not None) or (self.kind == "item") != (self.item is not None):
            raise ValueError("lesson/item must be present exactly when kind says so")
        return self


class AnswerRequest(_M):                  # POST /items/{id}/answer
    response: Any


class NextReview(_M):
    due: datetime
    interval_days: float = Field(ge=0)


class AnswerResponse(_M):
    item_id: str
    correct: bool | None                  # None for self-rated (explain)
    score: float = Field(ge=0, le=1)
    feedback: str
    correct_answer: Any = None            # revealed only now, in display form
    citations: list[RevealedCitation] = Field(default_factory=list)
    source_label: str = FROM_MATERIALS_LABEL
    next_review: NextReview | None = None
    mastery: float | None = Field(default=None, ge=0, le=1)  # objective mastery after this attempt


class RunRequest(_M):                     # POST /items/{id}/run
    code: str


class RunResponse(_M):
    item_id: str
    ok: bool
    passed: int
    total: int
    results: list[PerTestResult]          # visible tests only
    compile_error: str | None = None
    timed_out: bool = False


class HintRequest(_M):                    # POST /items/{id}/hint
    level: int = Field(ge=1, le=3)


class HintResponse(_M):
    item_id: str
    hint: Hint
    location: str | None = None
    passage_text: str | None = None       # level 3 only
    hints_used: int


class ReportRequest(_M):                  # POST /items/{id}/report
    reason: str = ""


class ReportResponse(_M):
    item_id: str
    status: ItemStatus
    reports_total: int


class GateCount(_M):
    gate_code: str
    label: str
    count: int


class DropsResponse(_M):                  # GET /courses/{id}/drops
    course_id: str
    total: int
    by_gate: list[GateCount]              # every gate code present, zero counts included, GATE_CODES order
    recent: list[Drop] = Field(default_factory=list)


class ProgressResponse(_M):               # GET /courses/{id}/progress
    course_id: str
    objectives: int
    covered: int
    not_covered: int
    mastered: int
    mean_mastery: float = Field(ge=0, le=1)
    items_generated: int
    items_active: int
    items_dropped: int
    items_reported: int
    attempts: int
    accuracy: float | None = Field(default=None, ge=0, le=1)
    due_reviews: int
    drops_by_gate: dict[str, int]
    reports: int
    recent_days: list["DayActivity"] = Field(default_factory=list)


class DayActivity(_M):
    date: str                             # YYYY-MM-DD
    attempts: int
    correct: int


ProgressResponse.model_rebuild()


class TimelineEvent(_Located):
    label: str
    date_text: str
    sort_year: int | None = None          # for placement on the axis; None = unplaceable
    passage_id: str
    location: str
    quote: str


class TimelineResponse(_M):               # GET /courses/{id}/timeline
    course_id: str
    events: list[TimelineEvent]


class PlotSeries(_M):
    label: str
    expr: str
    points: list[tuple[float, float | None]]   # None = undefined/outside the domain


class PlotResponse(_M):                   # GET /items/{id}/plot
    item_id: str
    title: str
    x_label: str = "x"
    y_label: str = "y"
    x_range: tuple[float, float]
    series: list[PlotSeries]
    verified_by: Literal["sympy"] = "sympy"


class PassageResponse(_M):                # GET /passages/{id}
    passage: Passage
    doc_name: str
    doc_kind: DocKind
    prev_id: str | None = None
    next_id: str | None = None


class MaterialTag(_M):                    # an objective a passage supports
    id: str
    statement: str


class MaterialPassage(_Located):
    id: str
    location: str
    text: str                             # verbatim
    display: str | None = None            # the original text of the span (line breaks kept) when it differs from `text`
    objectives: list[MaterialTag] = Field(default_factory=list)


class MaterialSection(_M):
    id: str                               # "<doc_id>:<heading-slug or p<page>>"
    title: str                            # the heading, as the passage locations name it
    doc_name: str
    passages: list[MaterialPassage]


class MaterialsResponse(_M):              # GET /courses/{id}/materials
    course_id: str
    title: str
    source: Literal["materials", "outline"]
    label: str = ""                       # outline courses: "Course outline: not source material"
    sections: list[MaterialSection]


class SourceDoc(_M):                      # GET /courses/{id}/sources  -> list[SourceDoc]
    doc_id: str
    name: str
    kind: str
    chars: int
    passages: int
    has_text: bool                        # False = text would be rebuilt from the passages
    note: str | None = None               # e.g. PDF pages that had no text layer and were skipped


class SourcePassage(_M):
    id: str
    location: str
    start: int
    end: int


class SourceView(_M):                     # GET /courses/{id}/sources/{doc_id}
    doc_id: str
    name: str
    kind: str
    text: str
    reconstructed: bool
    passages: list[SourcePassage]


class ReleaseResponse(_M):                # POST /items/{id}/release
    item_id: str
    released: bool
    message: str


class AudioPayload(_M):                   # GET /courses/{id}/passages/{pid}/audio?encoding=base64
    passage_id: str
    mime: str
    voice: str
    cached: bool
    size: int
    data_base64: str


# ---------------------------------------------------------------- events (host.onEvent)

EVENT_JOB_PROGRESS = "job.progress"       # broadcast as plugin.codecoach.job.progress
EVENT_BANK_UPDATED = "bank.updated"       # broadcast as plugin.codecoach.bank.updated


class JobProgressEvent(_M):
    job_id: str
    pct: float = Field(ge=0, le=100)
    stage: str


class BankUpdatedEvent(_M):
    course_id: str


def new_id(prefix: str) -> str:
    """Random id for things with no natural key (jobs, courses): prefix + 12 hex chars."""
    import uuid
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class MistakesResponse(_M):               # GET /courses/{id}/mistakes
    course_id: str
    count: int                            # items whose latest attempt scored below full credit
    session: int                          # how many a session serves (at most 10)
    upto: int                             # cursor to pass back as next?mistakes_upto=


class StudyGuideExport(_M):               # POST /courses/{id}/export
    course_id: str
    filename: str
    path: str | None = None               # where the file was written (None when no exports folder is set)
    markdown: str
