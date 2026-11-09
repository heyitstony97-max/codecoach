"""Deterministic retrieval for Ask: the syllabus BM25 `Index` over the course's passages, scored on text plus
the section heading each passage sits in. No model is involved. A question "clears the threshold" when its best
passage shares at least min(2, number of question terms) distinct content words with it, so a single shared word
("Express" in "Pony Express") is not enough to call the model."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from codecoach.core.models import Passage
from codecoach.core.passages import is_quotable
from codecoach.importer.syllabus import Index, content_words

TOP_K = 12                  # was 8: lexical rank cannot tell 'what happened after X' from X's own passages
TOP_RETRY = 16
CLOSEST = 3
MIN_MATCHED = 2


def heads_from_locations(passages: list[Passage]) -> dict[str, str]:
    """passage id -> its section title (the location minus ', para N'), so section headings count."""
    return {p.id: p.location.rsplit(", para", 1)[0] for p in passages if ", para" in p.location}


@dataclass
class Ranked:
    passage: Passage
    score: float
    matched: int


class AskIndex(Index):
    def ranked(self, query: str) -> list[Ranked]:
        q = set(content_words(query))
        n = len(self.docs)
        out: list[tuple[float, int, int]] = []
        for i, d in enumerate(self.docs):
            if not d:
                continue
            tf: dict[str, int] = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            s, hit = 0.0, 0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                hit += 1
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * len(d) / (self.avg or 1.0)))
            if s > 0:
                out.append((-s, i, hit))
        out.sort()
        return [Ranked(self.passages[i], -ns, hit) for ns, i, hit in out]


def build_index(passages: list[Passage]) -> AskIndex:
    """Bare headings, numerals and chapter labels are never retrieved (they stay in Read/Sources)."""
    quotable = [p for p in passages if is_quotable(p.text)]
    return AskIndex(quotable or passages, heads_from_locations(passages))


_WHY = re.compile(r"^\s*(?:why\b|for what reason|how come\b|what (?:is|was|were|are) the (?:reason|cause)s?\b)", re.I)


def is_why(question: str) -> bool:
    return bool(_WHY.match(question or ""))


def retrieve(index: AskIndex, question: str) -> tuple[list[Ranked], bool]:
    """(ranked passages with any overlap, best 16 first; cleared). `cleared` False means: do not call the model."""
    ranked = index.ranked(question)
    # A "why" question is worded by the asker ("flee", "creature") while the source states the reason in its own
    # words ("rushed out", "the wretch"), so one shared content word is enough to ask the model. Everything the model
    # returns still goes through the per-sentence quote gate, which is what keeps an invented motive out.
    need = min(1 if is_why(question) else MIN_MATCHED, len(set(content_words(question))))
    cleared = bool(ranked) and need > 0 and max(r.matched for r in ranked) >= need
    return ranked[:TOP_RETRY], cleared
