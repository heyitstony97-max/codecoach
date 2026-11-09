"""Small LLM schemas and prompts for the importer. One section per call; one call for prerequisites."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codecoach.importer.verbs import VERBS, normalize_verb

PURPOSE_OBJECTIVES = "codecoach.import.objectives"
PURPOSE_PREREQS = "codecoach.import.prereqs"
MAX_OBJECTIVES_PER_SECTION = 8
PASSAGE_CHARS_IN_PROMPT = 1200


class DraftObjective(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verb: str
    statement: str = Field(min_length=6, max_length=400)
    passage_ids: list[str] = Field(default_factory=list)
    kind: Literal["reading", "code", "math", "science"] = "reading"

    @field_validator("verb")
    @classmethod
    def _verb(cls, v: str) -> str:
        v = normalize_verb(v)
        if v not in VERBS:
            raise ValueError(f"verb must be one of {', '.join(VERBS)}; got {v!r}")
        return v

    @field_validator("statement")
    @classmethod
    def _statement(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 6:
            raise ValueError("statement is too short")
        return v


class SectionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(default="", max_length=120)    # a content title, asked for when the section is one of several parts
    objectives: list[DraftObjective] = Field(min_length=1, max_length=MAX_OBJECTIVES_PER_SECTION)


class DraftEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prereq: str
    objective: str


class PrereqDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    edges: list[DraftEdge] = Field(default_factory=list)


_VERB_HELP = ("identify (pick out a fact), define (state a term), order (put events in sequence), "
              "compare (similarities or differences), explain (say why or how), compute (calculate a number), "
              "trace (follow code or a process step by step), write-code (write a small program), "
              "solve (solve a math problem), convert (change units or forms)")

TITLE_RULE = ("title: a short descriptive title (2 to 6 words) naming what this part of the text is about; "
              "never 'part 2' or a number.\n")
TOPIC_RULE = ("Each objective is one TOPIC a learner studies: group related facts, examples and lines into one "
              "objective instead of one objective per sentence, image or poem line. Never write an objective "
              "about a title, a heading, a numeral, or how the text is organised.\n")

OBJECTIVE_INSTRUCTIONS_STRICT = (
    "You are mapping ONE section of the learner's own study materials into learning objectives. "
    f"Write 1 to {MAX_OBJECTIVES_PER_SECTION} objectives that THIS section actually teaches. "
    "Reply with JSON {\"objectives\": [{\"verb\", \"statement\", \"passage_ids\", \"kind\"}], \"title\"}.\n"
    + TOPIC_RULE + TITLE_RULE +
    f"verb must be exactly one of: {_VERB_HELP}.\n"
    "statement: one sentence a learner could be tested on. Use only what the passages say; never add facts.\n"
    "passage_ids: ids copied exactly from the bracketed list below, naming the passages that state what the "
    "objective needs. If no passage supports an objective, leave passage_ids empty.\n"
    "kind: reading (default), code, math or science."
)

OBJECTIVE_INSTRUCTIONS_OUTLINE = (
    "You are turning ONE part of a syllabus or outline into learning objectives. There is no textbook: the "
    "outline lines are the only source of what to teach. "
    f"Write 1 to {MAX_OBJECTIVES_PER_SECTION} objectives for the topics named in THIS part. "
    "Reply with JSON {\"objectives\": [{\"verb\", \"statement\", \"passage_ids\", \"kind\"}], \"title\"}.\n"
    + TITLE_RULE +
    f"verb must be exactly one of: {_VERB_HELP}.\n"
    "statement: one sentence a learner could be tested on, about a topic the outline names.\n"
    "passage_ids: ids copied exactly from the bracketed list below for the outline lines that name the topic.\n"
    "kind: reading (concepts), code (programming), math or science (numeric work)."
)

PREREQ_INSTRUCTIONS = (
    "Below are learning objectives in course order, each as `id: statement`. List the prerequisite edges: "
    "pairs where the learner must master `prereq` before `objective`. Only list real dependencies, usually "
    "one to three per objective, and only between ids shown. Never make an objective its own prerequisite. "
    "Reply with JSON {\"edges\": [{\"prereq\": id, \"objective\": id}]}."
)


def section_prompt(unit: str, title: str, passages: list) -> str:
    lines = [f"Unit: {unit}", f"Section: {title}", "Passages:"]
    for p in passages:
        body = p.text if len(p.text) <= PASSAGE_CHARS_IN_PROMPT else p.text[:PASSAGE_CHARS_IN_PROMPT] + " ..."
        lines.append(f"[{p.id}] {body}")
    return "\n".join(lines)


def prereq_prompt(objectives: list[tuple[str, str]]) -> str:
    return "\n".join(f"{oid}: {stmt}" for oid, stmt in objectives)


def objective_instructions(outline: bool, cap: int = MAX_OBJECTIVES_PER_SECTION) -> str:
    """The section prompt with this section's own objective cap (its share of the course topic budget)."""
    base = OBJECTIVE_INSTRUCTIONS_OUTLINE if outline else OBJECTIVE_INSTRUCTIONS_STRICT
    cap = max(1, min(MAX_OBJECTIVES_PER_SECTION, cap))
    return base.replace(f"1 to {MAX_OBJECTIVES_PER_SECTION} objectives", f"1 to {cap} objectives" if cap > 1
                        else "exactly 1 objective")
