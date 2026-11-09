"""The source gate: pure functions, no LLM. FAIL CLOSED: when unsure, drop.

check_item(item, passages) -> [Failure]   gates a-d (CONSTRAINTS section 10) for the reading formats
check_citations(item, by_id) -> [Failure] gate a only (used for code/math/numeric items in strict mode)
check_framing(text, passages) -> [Failure] the lesson framing gate
An empty list means the item passed. The first failure's code is the one logged as the drop reason.

  a  quote_missing     every citation's quote is in the cited passage, at word boundaries,
                       after normalizing whitespace/quotes/dashes/case, and is long enough
  b  unsupported_fact  every number, date and name in the answer key appears in the cited quote(s)
  c  ambiguous         no multiple-choice distractor is fully supported by the quote(s)
  d  bad_cloze         the blank, filled with the answer, is an exact span of a cited quote
  -  framing_fact      a lesson framing line adds a fact the passages do not contain
Other structural problems report self_check_failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..core.models import (ClozePayload, ExplainPayload, Item, MathPayload, MatchPayload, MCPayload, NumericPayload, OrderPayload,
                           Passage, ShortPayload)
from ..core.text import normalize
from . import facts as F

MIN_QUOTE_WORDS = 5
MIN_QUOTE_CHARS = 25
MIN_QUOTE_ALPHA_WORDS = 3
_ALPHA_WORD = re.compile(r"[^\W\d_]+")
FRAMING_MAX_CHARS = 220
READING_FORMATS = ("mc", "cloze", "order", "match", "short", "explain")


@dataclass(frozen=True)
class Failure:
    code: str          # one of core.models.GATE_CODES
    detail: str = ""


# ------------------------------------------------------------------ gate a


def quote_long_enough(quote: str) -> bool:
    nq = normalize(quote)
    if len(_ALPHA_WORD.findall(nq)) < MIN_QUOTE_ALPHA_WORDS:        # a row of asterisks is not a quote
        return False
    return len(nq.split()) >= MIN_QUOTE_WORDS or len(nq) >= MIN_QUOTE_CHARS


_GLUE = ",.-'"
_SPACED_DASHES = str.maketrans({"\u2013": " \u2014 ", "\u2014": " \u2014 ", "\u2015": " \u2014 "})


def _glued(nh: str, k: int, step: int) -> bool:
    """True when the character at nh[k] is a ",", ".", "-" or "'" sitting BETWEEN two alphanumerics
    (the other neighbour is at k + step). Such a mark is part of the word: 1,863 / 1.5 / non-Confederate
    / can't. A hyphen between two digits is a range (1861-1865), not glue."""
    o = k + step
    if not (0 <= k < len(nh) and 0 <= o < len(nh)):
        return False
    c = nh[k]
    if c not in _GLUE or not nh[o].isalnum():
        return False
    return not (c == "-" and nh[o].isdigit() and 0 <= k - step < len(nh) and nh[k - step].isdigit())


def text_found(haystack: str, needle: str, *, glue: bool = True) -> bool:
    """`needle` occurs in `haystack` after normalize(), and only at word boundaries: "the 186" is NOT
    found inside "the 1863". With `glue` (the default), a "," "." "-" or "'" between two alphanumerics
    also belongs to the word, so "863 soldiers" is not found inside "1,863 soldiers", "5 million" not
    inside "1.5 million", "Confederate" not inside "non-Confederate", "can" not inside "can't"
    (a possessive "Lee" in "Lee's" still matches). Em and en dashes are real dashes, not glue.
    (core.text.quote_in is a plain substring test, so it is not used here.)"""
    nn = normalize(needle.translate(_SPACED_DASHES))
    if not nn:
        return False
    nh = normalize(haystack.translate(_SPACED_DASHES))
    start = 0
    while True:
        i = nh.find(nn, start)
        if i < 0:
            return False
        j = i + len(nn)
        left_ok = (not nn[0].isalnum()) or i == 0 or not nh[i - 1].isalnum()
        right_ok = (not nn[-1].isalnum()) or j == len(nh) or not nh[j].isalnum()
        if left_ok and right_ok and glue:
            if nn[0].isalnum() and _glued(nh, i - 1, -1):
                left_ok = False
            if nn[-1].isalnum() and _glued(nh, j, 1) and not _possessive(nh, j):
                right_ok = False
        if left_ok and right_ok:
            return True
        start = i + 1


def _possessive(nh: str, j: int) -> bool:
    """nh[j] is an apostrophe followed by 's' and then a non-letter: Lee's, it's."""
    return nh[j] == "'" and nh[j + 1:j + 2] == "s" and not nh[j + 2:j + 3].isalnum()


def check_citations(item: Item, by_id: dict[str, Passage]) -> list[Failure]:
    if not item.citations:
        return [Failure("quote_missing", "no citation")]
    out = []
    for c in item.citations:
        p = by_id.get(c.passage_id)
        if p is None:
            out.append(Failure("quote_missing", f"passage {c.passage_id} was not provided"))
        elif not quote_long_enough(c.quote):
            out.append(Failure("quote_missing",
                               f"quote too short (needs {MIN_QUOTE_WORDS} words or {MIN_QUOTE_CHARS} characters)"))
        elif not text_found(p.text, c.quote):
            out.append(Failure("quote_missing", f"quote not found in {c.passage_id}: {c.quote[:60]!r}"))
        elif _too_broad(p.text, c.quote):
            out.append(Failure("quote_missing", f"quote is most of {c.passage_id}: cite the one sentence that proves it"))
    return out


# ------------------------------------------------------------------ helpers


def _quotes(item: Item, pid: str | None = None) -> list[str]:
    return [c.quote for c in item.citations if pid is None or c.passage_id == pid]


def _unsupported(text: str, sources: list[str], what: str, *, small: bool = False) -> list[Failure]:
    miss = F.missing_facts(text, sources, ignore_small_words=small)
    if miss:
        return [Failure("unsupported_fact", f"{what}: {', '.join(miss[:5])} not in the quote")]
    return []


def _in_any(needle: str, quotes: Iterable[str]) -> bool:
    return any(text_found(q, needle) for q in quotes)


def _answer_in_prompt(item: Item) -> str:
    """The answer (correct option, cloze answer or alias, required term) stated verbatim in the prompt."""
    p = item.payload
    if isinstance(p, MCPayload):
        keys = [p.options[p.correct_index]]
    elif isinstance(p, ClozePayload):
        keys = [p.answer, *p.aliases]
    elif isinstance(p, ShortPayload):
        keys = list(p.required_terms)
    else:
        return ""
    for k in keys:
        if len(normalize(k)) >= 4 and text_found(item.prompt, k):
            return k
    return ""


_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def structural(item: Item) -> list[Failure]:
    """Checks that need no source text. Also run for open-mode model_knowledge items."""
    p = item.payload
    bad = lambda d: [Failure("self_check_failed", d)]  # noqa: E731
    leaked = _answer_in_prompt(item)
    if leaked:
        return bad(f"the prompt gives away the answer: {leaked[:40]!r}")
    if isinstance(p, MCPayload):
        norm = [normalize(o) for o in p.options]
        if len(p.options) < 3:
            return bad("multiple choice needs at least 3 options")
        if len(set(norm)) != len(norm):
            return [Failure("ambiguous", "duplicate options")]
    elif isinstance(p, ClozePayload):
        if not p.answer.strip():
            return bad("empty cloze answer")
        before, _, after = p.text_with_blank.partition("____")
        if text_found(before + " " + after, p.answer, glue=False):
            return bad("the answer appears outside the blank")
    elif isinstance(p, OrderPayload):
        if len(p.events) < 3:
            return bad("ordering needs at least 3 events")
        labels = [normalize(e.label) for e in p.events]
        if len(set(labels)) != len(labels):
            return [Failure("ambiguous", "duplicate event labels")]
        if list(p.correct_order) == list(range(len(p.events))):
            return bad("the events are already listed in the right order")
        years = {y for e in p.events for y in re.findall(r"\d{3,4}", e.date_text)}
        if any(y in years for e in p.events for y in re.findall(r"\d{3,4}", e.label)):
            return bad("an event label gives away a date")
        keys = [F.date_sort_key(e.date_text) for e in p.events]
        if any(k is None for k in keys):
            return bad("an event date could not be read")
        ordered = [keys[i] for i in p.correct_order if keys[i] is not None]
        if ordered != sorted(ordered):
            return bad("correct_order disagrees with the dates")
        if len(set(ordered)) != len(ordered):
            return [Failure("ambiguous", "two events share a date")]
        spans = [F.date_span(e.date_text) for e in p.events]
        for a in range(len(spans)):
            for b in range(a + 1, len(spans)):
                sa, sb = spans[a], spans[b]
                if sa and sb and sa[0] <= sb[1] and sb[0] <= sa[1]:
                    return [Failure("ambiguous", "two event dates overlap (a year and a date or decade inside it)")]
    elif isinstance(p, MatchPayload):
        if len(p.pairs) < 2:
            return bad("matching needs at least 2 pairs")
    elif isinstance(p, ShortPayload):
        if p.min_terms is not None and not 1 <= p.min_terms <= len(p.required_terms):
            return bad("min_terms out of range")
    elif isinstance(p, NumericPayload):
        shown = " ".join([item.prompt, p.quantity])
        for m in _NUM.finditer(shown.replace(",", "")):
            try:
                v = float(m.group())
            except ValueError:
                continue
            if abs(v - p.answer_value) <= max(abs(p.answer_value) * max(p.rel_tol, 1e-6), 1e-9):
                return bad(f"the prompt states the answer: {m.group()!r}")
    elif isinstance(p, MathPayload):
        key = re.sub(r"\s+", "", p.given_answer or "")
        if len(key) >= 3 and key in re.sub(r"\s+", "", item.prompt):
            return bad(f"the prompt states the answer: {key[:40]!r}")
    return []


# ------------------------------------------------------------------ gates b-d per format


def _negation(item: Item, keys: list[str], quotes: list[str]) -> list[Failure]:
    """The quote clause that carries the key says NOT / never / no / without / failed to / refused, and
    the question does not: the key would assert the opposite of the quote."""
    if F.has_negation(item.prompt):
        return []
    # a key that carries its own negation ("does not believe in the gods") repeats the quote, it does not flip it
    hit = F.negated_clauses([*(k for k in keys if not F.has_negation(k)), item.prompt], quotes)
    if hit:
        return [Failure("unsupported_fact", f"the quote negates the answer: {hit[0]!r}")]
    return []


def _one_quote_proves(item: Item, key: str, quotes: list[str]) -> list[Failure]:
    """Citations are pooled everywhere else. Here the question and the key must meet in ONE quote: a quote
    that shares a content word or fact with the prompt AND with the key."""
    ptoks = F.key_tokens(item.prompt)
    ktoks = F.key_tokens(key)
    if not ptoks or not ktoks:
        return []
    for q in quotes:
        sup = F.support_tokens([q])
        if ptoks & sup and ktoks & sup:
            return []
    return [Failure("unsupported_fact", "no single quote holds both the question's subject and the answer")]


_ELSEWHERE_OVERLAP = 0.6
_OPT_WORD = re.compile(r"\d+(?:[.,/]\d+)*|[^\W\d_][\w'\u2019]*")


def _plus_plain_words(correct: str, opt: str, ctoks: set[str], toks: set[str]) -> bool:
    """The option is the correct answer plus plain extra words ("The famous X" next to "The X"), so
    both read as true. Extra names and numbers (a capitalized word after the first, a digit) make it a
    different answer ("Roger Williams" next to "Rogers"), and one-word answers are left alone."""
    if len(ctoks) < 2 or not ctoks <= toks:
        return False
    for i, w in enumerate(_OPT_WORD.findall(opt)):
        if F.key_tokens(w) <= ctoks:
            continue
        if any(ch.isdigit() for ch in w) or (w[:1].isupper() and i > 0):
            return False
    return True


def _true_elsewhere(item: Item, opt_toks: set[str], passages: Sequence[Passage]) -> bool:
    """A distractor whose every content token sits in ONE sentence of the objective's passages that also
    carries most of the question's content (a sentence the learner could cite for it). Whole passages
    are not used: a wrong year that merely appears somewhere in the passage stays a fine distractor."""
    ptoks = F.key_tokens(item.prompt)
    if not opt_toks or not ptoks:
        return False
    for ps in passages:
        for sent in _sentences([ps.text]):
            sup = F.support_tokens([sent])
            if opt_toks <= sup and len(ptoks & sup) >= _ELSEWHERE_OVERLAP * len(ptoks):
                return True
    return False


def _gate_mc(item: Item, p: MCPayload, quotes: list[str], passages: Sequence[Passage] = ()) -> list[Failure]:
    correct = p.options[p.correct_index]
    ctoks = F.key_tokens(correct)
    out = _unsupported(correct, quotes, "answer") + _negation(item, [correct], quotes)
    support = F.support_tokens(quotes)
    if not (F.key_tokens(correct) & support):
        out.append(Failure("unsupported_fact", "answer shares nothing with the quote"))
    extra = F.unsupported_words(correct, quotes)
    if extra:
        out.append(Failure("unsupported_fact", f"answer: {', '.join(extra[:5])} not in the quote"))
    out += _one_quote_proves(item, correct, quotes)
    for i, opt in enumerate(p.options):
        if i == p.correct_index:
            continue
        toks = F.key_tokens(opt)
        if not toks or toks <= support:
            out.append(Failure("ambiguous", f"option {i + 1} is also supported by the quote: {opt[:50]!r}"))
        elif _plus_plain_words(correct, opt, ctoks, toks):
            out.append(Failure("ambiguous", f"option {i + 1} contains the whole correct answer: {opt[:50]!r}"))
        elif _true_elsewhere(item, toks, passages):
            out.append(Failure("ambiguous", f"option {i + 1} is also supported by another sentence of the passages: {opt[:50]!r}"))
    return out


def _gate_cloze(item: Item, p: ClozePayload, quotes: list[str]) -> list[Failure]:
    before, _, after = p.text_with_blank.partition("____")
    filled = (before + p.answer.strip() + after).strip(" \t\n.,;:!?\"'()")
    out = []
    if not _in_any(filled, quotes):
        out.append(Failure("bad_cloze", "the sentence with the blank filled is not an exact span of a quote"))
    for alias in p.aliases:
        if not _in_any(alias, quotes):
            out.append(Failure("bad_cloze", f"alias {alias[:40]!r} is not in the quote"))
        elif not _in_any((before + alias.strip() + after).strip(" \t\n.,;:!?\"'()"), quotes):
            out.append(Failure("bad_cloze", f"alias {alias[:40]!r} does not fit the blank (the filled sentence is not a quote)"))
    return out + _unsupported(" ".join([p.answer, *p.aliases]), quotes, "answer")


_SENT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
# a word in front of a date that turns it into a bound or an approximation: "before 1800", "by 1865"
_QUALIFIER = r"(?:before|after|by|until|till|since|circa|ca\.?|c\.|around|about|roughly|approximately|early|late|mid[- ]?)"


# a period after an initial or a title does not end a sentence: "L.W. Coe", "Mr. F.A. Tritle", "Dr. Harkness"
_ABBREV_END = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|Gov|Gen|Sen|Rev|St|Co|Jr|Sr|vs)|\b[A-Z]|\bU\.S)\.$")


def _sentences(quotes: Iterable[str]) -> list[str]:
    out: list[str] = []
    for q in quotes:
        part: list[str] = []
        for frag in _SENT.split(q.strip()):
            if part and _ABBREV_END.search(part[-1]):
                part[-1] += " " + frag
            else:
                part.append(frag)
        out += [s for s in part if s.strip()]
    return out


def _qualified(sentence: str, date_text: str) -> bool:
    nd = re.escape(normalize(date_text))
    return bool(re.search(rf"(?<![\w]){_QUALIFIER}\s+(?:(?:the|in|of|year)\s+)*{nd}", normalize(sentence)))


_STANZA_WORDS, _STANZA_MEAN = 40, 6     # a short passage of clipped sentences (verse), not prose


def _too_broad(passage_text: str, quote: str) -> bool:
    """A quote that is most of a multi-sentence passage proves nothing specific."""
    sents = _sentences([passage_text])
    words = len(passage_text.split())
    if sents and words <= _STANZA_WORDS and words / len(sents) <= _STANZA_MEAN:
        return False           # a stanza of clipped lines ("Who are you? Are you nobody, too?") has no narrower quote
    return len(sents) >= 3 and len(normalize(quote)) >= 0.6 * len(normalize(passage_text))


def _gate_order(item: Item, p: OrderPayload) -> list[Failure]:
    out = []
    cited = {c.passage_id for c in item.citations}
    for e in p.events:
        qs = _quotes(item, e.passage_id)
        if e.passage_id not in cited or not qs:
            out.append(Failure("quote_missing", f"event {e.label[:30]!r} cites no quote"))
            continue
        if not _in_any(e.date_text, qs):
            out.append(Failure("unsupported_fact", f"date {e.date_text!r} is not in the quote"))
            continue
        out += _unsupported(e.label, qs, f"event {e.label[:30]!r}")
        out += _unsupported(e.date_text, qs, "date")
        # the label and the date must be said together, in one sentence, and the date must be exact
        both = [s for s in _sentences(qs) if text_found(s, e.date_text) and not F.unsupported_words(e.label, [s])]
        if not both:
            out.append(Failure("unsupported_fact",
                               f"event {e.label[:30]!r} and date {e.date_text!r} are not in one quoted sentence"))
        elif all(_qualified(s, e.date_text) for s in both):
            out.append(Failure("ambiguous", f"date {e.date_text!r} is qualified in the quote (before, about, early...)"))
    return out


def _gate_match(item: Item, p: MatchPayload) -> list[Failure]:
    out = []
    cited = {c.passage_id for c in item.citations}
    for pair in p.pairs:
        if not pair.passage_id or pair.passage_id not in cited:
            out.append(Failure("quote_missing", f"pair {pair.left[:30]!r} cites no quote"))
            continue
        qs = _quotes(item, pair.passage_id)
        sides_found = True
        for side in (pair.left, pair.right):
            if not _in_any(side, qs):
                out.append(Failure("unsupported_fact", f"{side[:40]!r} is not quoted"))
                sides_found = False
        out += _unsupported(pair.left, qs, "pair") + _unsupported(pair.right, qs, "pair")
        if sides_found and not any(text_found(s, pair.left) and text_found(s, pair.right) for s in _sentences(qs)):
            out.append(Failure("unsupported_fact", f"{pair.left[:30]!r} and {pair.right[:30]!r} are not in one sentence"))
    return out


def _gate_short(item: Item, p: ShortPayload, quotes: list[str]) -> list[Failure]:
    out = []
    for t in p.required_terms:
        if not F.key_tokens(t):
            out.append(Failure("unsupported_fact", f"required term {t[:40]!r} has no content word"))
        elif not _in_any(t, quotes):
            out.append(Failure("unsupported_fact", f"required term {t[:40]!r} is not in the quote"))
    return (out + _unsupported(" ".join(p.required_terms), quotes, "required terms")
            + _negation(item, list(p.required_terms), quotes))


def check_item(item: Item, passages: Sequence[Passage], context: Sequence[str] = (), *,
               mode: str = "strict") -> list[Failure]:
    """Gates a-d for a reading-format item. `context` is extra trusted text prompts may echo
    (the objective statement). In 'open' mode an item with origin 'model_knowledge' gets structural
    checks only; in 'strict' mode (the default, fail closed) such an item is dropped: an item cannot
    switch the gate off by labelling itself."""
    out = structural(item)
    if item.origin == "model_knowledge":
        if mode != "open":
            return [Failure("quote_missing", "model_knowledge items are not allowed in a strict course")] + out
        return out
    by_id = {p.id: p for p in passages}
    cit = check_citations(item, by_id)
    if cit:
        return cit + out
    quotes = _quotes(item)
    cited_text = [by_id[pid].text for pid in dict.fromkeys(c.passage_id for c in item.citations)]
    p = item.payload
    if isinstance(p, MCPayload):
        out += _gate_mc(item, p, quotes, passages)
    elif isinstance(p, ClozePayload):
        out = _gate_cloze(item, p, quotes) + out
    elif isinstance(p, OrderPayload):
        out += _gate_order(item, p)
    elif isinstance(p, MatchPayload):
        out += _gate_match(item, p)
    elif isinstance(p, ShortPayload):
        out += _gate_short(item, p, quotes)
    elif isinstance(p, ExplainPayload):
        pass
    else:
        out.append(Failure("self_check_failed", f"{item.format} is not a reading format"))
    # `context` (the objective statement) is LLM-written and may carry a false premise: a prompt is
    # checked against the cited passages only
    out += _unsupported(item.prompt, cited_text, "prompt", small=True)
    return out


# ------------------------------------------------------------------ framing gate


_FRAME_WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)?")
_FRAME_OK = frozenset("""a an the this that these those it its is are was were be been being of in on at to for from by with
as and or but so then than if when while how why what who which where whom whose do does did not no
read reading reread notice note look watch pay attention consider think about answer answers question questions
passage text excerpt section following below above first next now here see before after you your we our us
can could will would should may might must also each both one two three more most some any all only""".split())


def _stem(w: str) -> str:
    w = w.split("'")[0].split("\u2019")[0]
    return w[:5]



def check_framing(text: str | None, passages: Sequence[Passage]) -> list[Failure]:
    """A lesson framing line: one short sentence, and no number, date or name that the shown
    passages do not contain."""
    if not text or not text.strip():
        return []
    t = text.strip()
    if "\n" in t or len(t) > FRAMING_MAX_CHARS or re.search(r"[.!?]\s+\S", t):
        return [Failure("framing_fact", "framing must be one short line")]
    out = [Failure("framing_fact", d.detail) for d in
           _unsupported(t, [p.text for p in passages], "framing")]
    # lowercase claims ("freed every enslaved person") carry no number or capital: every content word of
    # the line must come from the passages or from a short list of instruction and function words
    odd = framing_odd_words(t, [p.text for p in passages])
    if odd and not out:
        out.append(Failure("framing_fact", "framing uses words the passage does not: " + ", ".join(odd[:5])))
    return out


def framing_odd_words(text: str, sources: Sequence[str], *, morph: bool = False) -> list[str]:
    """The framing gate's word test, shared with Ask: content words of `text` (not function or instruction
    words) whose first five letters appear in none of the `sources`. `morph=True` also accepts a plain
    inflection of a source word (one word is the start of the other, both >= 4 letters, differing by <= 3
    letters: gold/golden, weigh/weighed), nothing else."""
    src_words = {w for s in sources for w in _FRAME_WORD.findall(normalize(s).lower())}
    seen = {_stem(w) for w in src_words}

    def known(w: str) -> bool:
        if _stem(w) in seen:
            return True
        return morph and any(len(w) >= 4 and len(v) >= 4 and abs(len(w) - len(v)) <= 3
                             and (w.startswith(v) or v.startswith(w)) for v in src_words)

    return sorted({w for w in _FRAME_WORD.findall(normalize(text).lower()) if w not in _FRAME_OK and not known(w)})


def check_prompt_dates(item: Item, passages: Sequence[Passage]) -> list[Failure]:
    """Numeric, math and code items are true because they are computed, but their prompt can still tell
    a false story ("Newton published in 1812"). Every year or date in the prompt must be in a cited
    passage or be a value of the problem itself (it is in the payload)."""
    by_id = {p.id: p.text for p in passages}
    cited = [by_id[c.passage_id] for c in item.citations if c.passage_id in by_id]
    sources = cited + [item.payload.model_dump_json()]
    miss = F.missing_facts(item.prompt, sources, dates_only=True)
    if miss:
        return [Failure("unsupported_fact", f"prompt: {', '.join(miss[:5])} not in the quote")]
    return []


def first_failure(failures: list[Failure]) -> Failure | None:
    return failures[0] if failures else None
