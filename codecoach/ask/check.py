"""The Ask gate. Code, no model. A sentence survives only if ALL of these hold; otherwise just that sentence is
dropped (never the whole answer) with one of the gate names below.

  ask_no_cite          no quote at all
  ask_too_long         past the fifth sentence, or more than 40 words
  ask_quote_missing    a cite names a passage that was not offered, a quote that is too short, or a quote that is
                       not verbatim in that passage (gate.text_found, after normalising quotes, dashes, case)
  ask_unsupported_fact a number, date or name of the sentence that none of ITS quotes contains (missing_facts)
  ask_negation         the quote denies what the sentence says, or the sentence denies what the quote says
  ask_unsupported_fact (also) a content word the quotes lack, beyond the framing gate's tolerance (gate.framing_odd_words)

Before (b) fails a sentence, `extend_quotes` may look for the missing number, date or name in the OTHER sentences of
the passages that sentence already cites, and add that passage sentence as one more quote (Cite.extended_by_code).
It copies passage text only, never touches the answer sentence, never reaches into an uncited passage, and a fact
counts only if the passage sentence contains it word for word; then every check runs on the sentence again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from codecoach.core.models import Passage
from codecoach.core.text import normalize, sup_piece
from codecoach.ground import facts as F
from codecoach.ground import gate

MAX_SENTENCES = 5
MAX_WORDS = 40


@dataclass
class Cite:
    passage_id: str
    doc_id: str
    location: str
    quote: str
    quote_start: int
    quote_end: int
    extended_by_code: bool = False

    def as_dict(self) -> dict:
        d = dict(passage_id=self.passage_id, doc_id=self.doc_id, location=self.location, quote=self.quote,
                 quote_start=self.quote_start, quote_end=self.quote_end)
        if self.extended_by_code:
            d["extended_by_code"] = True
        return d


@dataclass
class Verdict:
    text: str
    gate: str = ""                      # "" = kept
    why: str = ""
    cites: list[Cite] = field(default_factory=list)

    @property
    def kept(self) -> bool:
        return not self.gate


def quote_span(passage_text: str, quote: str) -> tuple[int, int] | None:
    """(start, end) of `quote` inside the ORIGINAL passage text, found the way text_found finds it (normalised,
    word boundaries), or None. passage_text[start:end] is what the learner sees highlighted."""
    chars: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(passage_text):
        if ch.isspace():
            piece = " "
        elif ch in "\u2013\u2014\u2015":
            piece = " \u2014 "
        else:
            piece = normalize(sup_piece(passage_text, i) or ch)
        for c in piece:
            if c == " ":
                if prev_space:
                    continue
                prev_space = True
            else:
                prev_space = False
            chars.append(c)
            idx.append(i)
    nh = "".join(chars)
    nn = normalize(quote.translate(gate._SPACED_DASHES))      # noqa: SLF001 (same dash rule as text_found)
    if not nn:
        return None
    start = 0
    while True:
        i = nh.find(nn, start)
        if i < 0:
            return None
        j = i + len(nn)
        left = (not nn[0].isalnum()) or i == 0 or not nh[i - 1].isalnum()
        right = (not nn[-1].isalnum()) or j == len(nh) or not nh[j].isalnum()
        if left and right and not (nn[0].isalnum() and gate._glued(nh, i - 1, -1)) \
                and not (nn[-1].isalnum() and gate._glued(nh, j, 1) and not gate._possessive(nh, j)):   # noqa: SLF001
            return idx[i], idx[j - 1] + 1
        start = i + 1


def words(text: str) -> int:
    return len(text.split())


def check_sentence(text: str, raw_cites: list[tuple[str, str]], offered: dict[str, Passage]) -> Verdict:
    """`raw_cites` = [(passage_id, quote)] as the model wrote them; `offered` = the passages it was shown."""
    text = (text or "").strip()
    v = Verdict(text)
    if not raw_cites:
        v.gate, v.why = "ask_no_cite", "the sentence has no quote"
        return v
    if words(text) > MAX_WORDS:
        v.gate, v.why = "ask_too_long", f"{words(text)} words (limit {MAX_WORDS})"
        return v
    for pid, quote in raw_cites:
        quote = (quote or "").strip()
        p = offered.get((pid or "").strip())
        if p is None:
            v.gate, v.why = "ask_quote_missing", f"passage {pid!r} was not one of the passages offered"
            return v
        if not gate.quote_long_enough(quote):
            v.gate, v.why = "ask_quote_missing", f"quote too short: {quote[:60]!r}"
            return v
        if not gate.text_found(p.text, quote):
            v.gate, v.why = "ask_quote_missing", f"quote not found in {p.id}: {quote[:60]!r}"
            return v
        span = quote_span(p.text, quote)
        if span is None:
            v.gate, v.why = "ask_quote_missing", f"quote has no exact place in {p.id}: {quote[:60]!r}"
            return v
        s, e = span
        v.cites.append(Cite(p.id, p.doc_id, p.location, p.text[s:e], s, e))
    quotes = [c.quote for c in v.cites]
    miss = F.missing_facts(text, quotes)
    if miss:
        v.cites.extend(extend_quotes(text, v.cites, miss, offered))
        quotes = [c.quote for c in v.cites]
        miss = F.missing_facts(text, quotes)
    if miss:
        v.gate, v.why = "ask_unsupported_fact", f"not in its quote: {', '.join(miss[:5])}"
        return v
    # a negated clause of a quote that holds the sentence's words must be matched by a negation in the sentence,
    # and a sentence that denies something needs a quote that denies something
    neg_s = F.has_negation(text)
    flipped = [] if neg_s else F.negated_clauses([text], quotes)
    if neg_s and not any(F.has_negation(q) for q in quotes):
        flipped = ["the sentence denies what its quote states"]
    if flipped:
        v.gate, v.why = "ask_negation", f"negation differs from the quote: {flipped[0]}"
        return v
    odd = gate.framing_odd_words(text, quotes, morph=True)
    if odd:
        v.gate, v.why = "ask_unsupported_fact", "words its quote does not use: " + ", ".join(odd[:5])
        return v
    v.cites = _dedupe(v.cites)
    return v


_SENT_END = re.compile(r"(?<=[.!?])[\"\u201d\u2019')\]]*\s+(?=[A-Z0-9\"\u201c\u2018(])")
MAX_EXTENSIONS = 2


def passage_sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence of `text`, as slices of the original string."""
    out, pos = [], 0
    for m in _SENT_END.finditer(text):
        out.append((pos, m.start() + len(text[m.start():m.end()].rstrip())))
        pos = m.end()
    out.append((pos, len(text.rstrip())))
    return [(a, b) for a, b in out if b > a]


def _supplies(text: str, fact: str, sentence: str) -> bool:
    """`sentence` (a passage sentence) holds `fact` of the answer text. Names and plain numbers must also be there
    word for word (gate.text_found); dates and years are matched the way the fact gate already matches them."""
    if fact in F.missing_facts(text, [sentence]):
        return False
    if re.fullmatch(r"[0-9][0-9.,/:%]*|[a-z][a-z' .-]*", fact):
        return gate.text_found(sentence, fact)
    return True


def extend_quotes(text: str, cites: list[Cite], missing: list[str], offered: dict[str, Passage]) -> list[Cite]:
    """Extra quotes for the facts of `text` that its quotes lack: whole sentences of the passages it ALREADY cites,
    chosen only when they contain the missing fact (see the module doc). [] when nothing qualifies."""
    added: list[Cite] = []
    todo = list(missing)
    for _ in range(MAX_EXTENSIONS):
        if not todo:
            break
        best = None                                   # (facts resolved, overlap, -order, Cite)
        order = 0
        for pid in dict.fromkeys(c.passage_id for c in cites):
            p = offered.get(pid)
            if p is None:
                continue
            for a, b in passage_sentences(p.text):
                order += 1
                sent = p.text[a:b]
                if not gate.quote_long_enough(sent) or any(c.passage_id == pid and c.quote_start <= a and b <= c.quote_end
                                                           for c in cites + added):
                    continue
                # The added sentence must hold EVERY fact of the answer sentence by itself. Otherwise the code
                # would stitch a fact from a neighbouring sentence into a claim the passage never makes
                # ("Hewes paid for the spike" + "finished in 1869" -> "Hewes paid for the spike in 1869").
                if F.missing_facts(text, [sent]):
                    continue
                got = [f for f in todo if _supplies(text, f, sent)]
                span = quote_span(p.text, sent) if got else None
                if not got or span is None or p.text[span[0]:span[1]] != sent:
                    continue
                key = (len(got), len(set(normalize(sent).split()) & set(normalize(text).split())), -order)
                if best is None or key > best[0]:
                    best = (key, got, Cite(p.id, p.doc_id, p.location, sent, span[0], span[1], True))
        if best is None:
            break
        added.append(best[2])
        todo = [f for f in todo if f not in best[1]]
    return added


def _dedupe(cites: list[Cite]) -> list[Cite]:
    seen, out = set(), []
    for c in cites:
        k = (c.passage_id, c.quote_start, c.quote_end)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out
