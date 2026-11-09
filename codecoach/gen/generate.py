"""Item generation: one item per LLM call, then draft -> pydantic -> gate or self-check -> Item or Drop.

generate_one(...) has the registry's generator signature and returns GenResult(items, drops).
It never touches the store; bank.py persists. Items leave here either verified or not at all.
"""

from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Sequence

from pydantic import ValidationError

from ..core.llm import LLMBadOutput, LLMPort, structured_call
from ..core.models import (Citation, ClozePayload, CodePayload, Drop, ExplainPayload, Item, MathPayload,
                           MatchPayload, MCPayload, NumericPayload, Objective, OrderPayload, Passage,
                           ShortPayload, utcnow)
from ..core.registry import GenResult, register_generator
from ..core.text import stable_hash
from ..ground.gate import READING_FORMATS, Failure, check_citations, check_item, check_prompt_dates, structural
from ..ground.mathsource import check_math_source, math_origin, trivial
from .precision import needs_places, with_precision
from . import drafts as D
from ..importer.verbs import looks_like_code
from .selfcheck import self_check

FORMATS = tuple(D.DRAFT_MODELS)
NON_READING = ("code", "math", "numeric")
MAX_DETAIL = 300

_FORMAT_HELP = {
    "mc": "A multiple-choice question: 3-5 options, exactly ONE supported by a quote. Each wrong option must "
          "be clearly NOT supported by the passages. correct_index is the 0-based index of the right option.",
    "cloze": "One sentence copied word for word from a passage with a key phrase replaced by ____ (four "
             "underscores, once). `answer` is the exact removed phrase. Aliases only if the passage uses them.",
    "order": "3-5 events from the passages with their dates, listed in CHRONOLOGICAL order. date_text must be "
             "copied exactly from the passage and passage_id says where. Use only events whose sentence gives a "
             "full date WITH a year (e.g. 'May 10, 1869' or '1869'); skip events dated only by month and day "
             "or by phrases like 'before noon'. No two events may share a date. Cite each event's sentence.",
    "match": "2-5 pairs {left, right, passage_id}. Both sides must be exact words from one cited quote.",
    "short": "A short-answer question. required_terms are 1-4 key words or phrases copied exactly from the quote.",
    "explain": "An open 'explain why / how' question the learner answers in their own words, then compares "
               "to the passage. Cite the sentence(s) that answer it.",
    "code": "A programming exercise. Give `starter` code that compiles/runs but is INCOMPLETE: it must FAIL at "
            "least one visible test AND at least one hidden test (a learner who runs the visible tests before "
            "typing anything must see a failure, so never give a starter that already prints the right answer "
            "for the visible example). Give visible_tests (shown), hidden_tests (not shown) and a "
            "reference_solution that passes all of them. Each test uses either stdin + expected_stdout, or a "
            "harness. At least one hidden test must use DIFFERENT input and expected output than every "
            "visible test; never copy a visible test into the hidden list.",
    "math": "A computational problem. `op` is the operation on `expr` in variable `var`. `given_answer` is the "
            "final result as a plain expression like 2*x*sin(x) + x**2*cos(x).",
    "numeric": "A numeric science problem. given = quantities with units; answer_value and unit are the result.",
}


# a code objective needs a programming action or a code thing to work on; one that only talks about the
# outline, the course, its units or its tooling-free structure cannot become an exercise
_PROGRAMMING_WORDS = (
    "write", "implement", "trace", "compile", "declare", "initialize", "initialise", "print", "read", "use",
    "call", "define", "loop", "convert", "compute", "parse", "debug", "fix", "predict", "build", "create",
    "return", "assign", "modify", "pass", "split", "combine", "sum", "count", "find", "calculate", "run",
    "format", "allocate", "iterate", "sort", "search", "reverse", "copy", "compare", "store", "classify")
_META_WORDS = ("outline", "course", "syllabus", "curriculum", "units", "this unit", "lesson plan")


def is_course_meta(statement: str) -> bool:
    """True when the objective is about the outline or course itself, with no programming verb or code
    word: such an objective gets no code exercise (found live: 'The C Basics outline is organized into
    eight units' became a Python exercise)."""
    import re
    low = statement.lower()
    if not any(m in low for m in _META_WORDS):
        return False
    if looks_like_code(statement):
        return False
    return not any(re.search(rf"\b{w}", low) for w in _PROGRAMMING_WORDS)


PHRASING_RULE = ("Phrasing: ask the question directly, as a plain fact. Do NOT begin the prompt with \"According to "
                 "the passage\", \"According to the text\", \"According to the reading\" or \"The passage says\"; use "
                 "such an opener only when the point is the AUTHOR'S OWN claim or opinion (\"According to Strunk, ...\").")


def _instructions(fmt: str, mode: str, language: str | None = None) -> str:
    if mode == "strict":
        rules = ("Use ONLY the passages below. Do not use any outside knowledge. Every fact, number, date and "
                 "name in your answer must appear in a quote you cite. citations = a list of "
                 "{passage_id, quote}: copy each quote EXACTLY, word for word, at least 5 words, from the "
                 "passage with that id. Never invent or paraphrase a quote.")
    else:
        rules = ("Prefer the passages below and cite exact quotes as {passage_id, quote}. If a fact is not in "
                 "the passages, set origin to 'model_knowledge' and leave citations empty; it will be labelled "
                 "'not from your materials'.")
    lang = ""
    if fmt == "code" and language:
        name = {"c": "C", "python": "Python"}[language]
        lang = (f"The course teaches {name}: set language to \"{language}\" and write the starter, tests and "
                f"reference solution in {name}. The exercise must practise the {name} skill named in the "
                "OBJECTIVE (a concrete program or function the learner writes); never ask about the course, "
                "its outline or its units.\n")
    return (f"You write ONE {fmt} item for a learner. {rules}\n{PHRASING_RULE}\n{_FORMAT_HELP[fmt]}\n{lang}"
            "Reply with only a JSON object that matches the schema.")


def _prompt_text(objective: Objective, passages: Sequence[Passage]) -> str:
    body = "\n\n".join(f"[{p.id}] ({p.location})\n{p.text}" for p in passages)
    return f"OBJECTIVE: {objective.statement}\n\nPASSAGES:\n{body}" if passages else \
        f"OBJECTIVE: {objective.statement}"


# ------------------------------------------------------------------ draft -> Item


def _shuffled_mc(d: D.MCDraft, rng: random.Random) -> MCPayload:
    if d.correct_index >= len(d.options):
        raise ValueError("correct_index out of range")
    order = list(range(len(d.options)))
    rng.shuffle(order)
    return MCPayload(options=[d.options[i] for i in order], correct_index=order.index(d.correct_index))


def _shuffled_order(d: D.OrderDraft, rng: random.Random) -> OrderPayload:
    n = len(d.events)
    ident = list(range(n))
    perm = list(ident)                      # perm[position] = chronological index
    for _ in range(25):
        rng.shuffle(perm)
        if perm != ident:
            break
    else:
        perm = ident[1:] + ident[:1]
    return OrderPayload(events=[d.events[o] for o in perm],
                        correct_order=[perm.index(o) for o in ident])


def _payload(fmt: str, d: D.Draft, rng: random.Random):
    if fmt == "mc":
        return _shuffled_mc(d, rng)           # type: ignore[arg-type]
    if fmt == "cloze":
        blank = re.sub(r"_{4,}", "____", d.text_with_blank)   # type: ignore[attr-defined]  (models draw 5+)
        return ClozePayload(text_with_blank=blank, answer=d.answer,   # type: ignore[attr-defined]
                            aliases=d.aliases)                        # type: ignore[attr-defined]
    if fmt == "order":
        return _shuffled_order(d, rng)        # type: ignore[arg-type]
    if fmt == "match":
        return MatchPayload(pairs=d.pairs)    # type: ignore[attr-defined]
    if fmt == "short":
        return ShortPayload(required_terms=d.required_terms, min_terms=d.min_terms)  # type: ignore[attr-defined]
    if fmt == "explain":
        return ExplainPayload()
    if fmt == "code":
        return CodePayload(language=d.language, starter=d.starter, visible_tests=d.visible_tests,  # type: ignore
                           hidden_tests=d.hidden_tests, reference_solution=d.reference_solution,   # type: ignore
                           time_limit_s=d.time_limit_s, mem_mb=d.mem_mb)                           # type: ignore
    if fmt == "math":
        return MathPayload(op=d.op, expr=d.expr, var=d.var, given_answer=d.given_answer, subs=d.subs)  # type: ignore
    if fmt == "numeric":
        return NumericPayload(given=d.given, quantity=d.quantity, answer_value=d.answer_value,  # type: ignore
                              unit=d.unit, rel_tol=d.rel_tol)                                   # type: ignore
    raise ValueError(f"unknown format {fmt!r}")


def _drop(objective: Objective, fmt: str, code: str, detail: str, draft_hash: str, now: datetime) -> Drop:
    return Drop(objective_id=objective.id, format=fmt, gate_code=code,  # type: ignore[arg-type]
                detail=detail[:MAX_DETAIL], draft_hash=draft_hash, at=now)


def generate_one(*, fmt: str, objective: Objective, passages: Sequence[Passage], llm: LLMPort,
                 mode: str = "strict", rng: random.Random | None = None,
                 now: datetime | None = None, code_language: str | None = None) -> GenResult:
    """Ask for one item of `fmt` for `objective` and verify it. LLMUnavailable propagates."""
    rng = rng or random.Random()
    now = now or utcnow()
    if fmt not in D.DRAFT_MODELS:
        raise ValueError(f"unknown format {fmt!r}")
    # enforced in code, whatever the caller does: nothing is generated for an uncovered objective,
    # and strict mode never generates without passages
    if objective.coverage == "not_covered":
        return GenResult()
    if fmt in READING_FORMATS and not passages:
        return GenResult()                   # reading formats need something to quote, in either mode

    if fmt == "code" and is_course_meta(objective.statement):
        return GenResult()                   # about the outline itself, not a skill: nothing to practise
    lang = code_language if fmt == "code" else None
    try:
        draft = structured_call(llm, D.draft_model(fmt, mode, lang), instructions=_instructions(fmt, mode, lang),
                                text=_prompt_text(objective, passages), purpose=f"codecoach.gen.{fmt}",
                                retries=3)
    except LLMBadOutput as e:
        return GenResult(drops=[_drop(objective, fmt, "schema_invalid", e.last_error or str(e), "", now)])

    dh = stable_hash(objective.id, fmt, draft.model_dump(mode="json"))
    if lang and getattr(draft, "language", lang) != lang:
        return GenResult(drops=[_drop(objective, fmt, "schema_invalid",
                                      f"draft language {draft.language!r} but the course teaches {lang!r}", dh, now)])
    if fmt in READING_FORMATS:
        # strict: always materials (needs citations, gate a). open: the model may label model_knowledge.
        origin = draft.origin if mode == "open" else "materials"
        if mode == "open" and origin == "materials" and not draft.citations:
            origin = "model_knowledge"
    else:
        # code/math/numeric are true because they are executed/computed, not because they are quoted.
        # With a citation they are "materials"; without one the Item contract forces the label.
        origin = "materials" if draft.citations else "model_knowledge"
    citations = list(draft.citations) if origin == "materials" else []
    try:
        pl = _payload(fmt, draft, rng)
        if isinstance(pl, MathPayload) and citations:
            # decided here from the quotes, never by the model: an expression not written in its quote is CodeCoach's own
            quote_texts = [c.quote for c in citations]
            pl = pl.model_copy(update={"origin": math_origin(pl, quote_texts)})
        prompt = draft.prompt.strip()
        if isinstance(pl, MathPayload) and pl.op == "evaluate" and pl.places is None and needs_places(pl.given_answer):
            pl = pl.model_copy(update={"places": 2})
            prompt = with_precision(prompt, 2)
        item = Item(id=f"it_{dh}", objective_id=objective.id, format=fmt, prompt=prompt,  # type: ignore
                    payload=pl, citations=citations, origin=origin,  # type: ignore
                    checker=fmt, created_at=now)
    except (ValidationError, ValueError) as e:
        return GenResult(drops=[_drop(objective, fmt, "schema_invalid", str(e).replace("\n", " "), dh, now)])

    failures = verify(item, passages, objective, mode)
    if failures:
        f = failures[0]
        extra = f" (+{len(failures) - 1} more)" if len(failures) > 1 else ""
        return GenResult(drops=[_drop(objective, fmt, f.code, f.detail + extra, dh, now)])
    return GenResult(items=[item])


def verify(item: Item, passages: Sequence[Passage], objective: Objective, mode: str = "strict"):
    """Everything an item must pass before it can be stored. Empty list = verified."""
    if item.format in READING_FORMATS:
        return check_item(item, passages, context=[objective.statement], mode=mode)
    out = structural(item)
    if item.origin == "materials":
        by_id = {p.id: p for p in passages}
        out = check_citations(item, by_id) + out
        if not out and isinstance(item.payload, MathPayload):
            cited = [by_id[c.passage_id] for c in item.citations]
            out = [Failure(code, detail) for code, detail in
                   check_math_source(item.payload, [c.quote for c in item.citations], cited, objective.statement)]
        if not out:
            out = check_prompt_dates(item, passages)
    elif isinstance(item.payload, MathPayload):
        bad = trivial(item.payload, "")
        out = out + ([Failure("self_check_failed", bad)] if bad else [])
    return out or self_check(item)


for _fmt in FORMATS:
    register_generator(_fmt, lambda *, _f=_fmt, **kw: generate_one(fmt=_f, **kw))
