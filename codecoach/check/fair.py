"""Fair matching for fill-in-the-blank answers. Pure stdlib, no LLM; runs only after the exact check said no.

The exact check wanted the answer word for word: "at Promontory Summit", "Union Pacific railroad", "Stanford" (for
Leland Stanford) and "Promontory Sumit" were all marked wrong (Anthony: "if they are wrong it should be outright
wrong, but this makes you do word for word"). Three ways a right answer can pass here, each kept strict:

1. Same words, different surface: punctuation (William T Garratt = William T. Garratt), spacing (laurelwood = laurel
   wood), small words (at, of, the), singular/plural, and ONE typo in a long word (Sumit). Numbers never bend, and an
   accent is never treated as a typo (esta is not está).
2. The answer plus a few extra words: when the student's whole phrase appears in the materials that way ("Union
   Pacific Railroad"), or when the extra words appear nowhere in the materials, so they cannot be a rival answer
   from them ("Promontory Summit, Utah" in a text that never says Utah; the note says that part was not checked).
   An extra word the materials do use ("Promontory Summit Ogden"), a guess list ("... or Ogden"), an added "and",
   a number, or a hedge (not, maybe) never passes.
3. A shortened proper name ("Stanford", "Garratt Foundry"), only when it keeps the name's last word and at least
   half the name, keeps every number, and the materials never put another capitalised word in front of it.
   "Pacific" for "Union Pacific" is not accepted when the materials also say "Central Pacific"; "Leland" for
   "Leland Stanford" is wrong (no surname). A shortened description ("immigrants" for "Chinese immigrants",
   "granite" for "solid granite") is not right either: the dropped word is often the point. Those two get
   "Almost" with half credit and the full answer, never "Correct".
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from codecoach.check.reading import _is_name_a, _loose
from codecoach.core.registry import CheckResult

FILLER = frozenset("the a an of at in on by from for to its his her their".split())
HEDGE = frozenset("or not no never nor maybe either neither perhaps probably unsure".split())
# A word before a name that does not make it a different name ("Governor Stanford" is still Stanford).
TITLES = frozenset("mr mrs ms miss dr sir lady lord rev reverend father president vice governor general gen senator "
                   "judge justice captain capt colonel col major professor prof king queen prince princess saint st "
                   "chief commissioner mayor emperor pope bishop".split())
MAX_EXTRA = 3
MIN_TYPO_LEN = 6
_TOK = re.compile(r"\d+(?:\.\d+)?|[^\W_]+")
_POSS = re.compile(r"['\u2019]s\b")
_APOS = re.compile(r"['\u2019]")
# A sentence break ends a phrase; a soft break (comma, quote, dash, a dropped small word) only stops two words
# from counting as one name ("Spike in the Stanford University museum" does not make "Spike Stanford").
_STRONG = re.compile(r"[.;:!?()\[\]\n]")
_SOFT = re.compile(r"[,\"\u201c\u201d\u2014]")
_ABBR = frozenset("mr mrs ms dr st jr sr gen col capt rev prof no vs".split())
_HEAD_END = re.compile(r",|\(|\s(?:of|in|from|at|on|near)\s", re.I)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _sing(w: str) -> str:
    if not w.isalpha():
        return w
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _one_edit(a: str, b: str) -> bool:
    """Damerau distance exactly 1: one letter changed, added, dropped, or two neighbours swapped."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    s, t = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(s) and s[i] == t[i]:
        i += 1
    return s[i:] == t[i + 1:]


def _words(s: str, keep_a: bool) -> list[str]:
    t = _APOS.sub("", _POSS.sub("", _loose(s, keep_a=keep_a)))
    return _TOK.findall(t)


def _content(ws: list[str], keep_a: bool) -> list[str]:
    filler = FILLER - {"a"} if keep_a else FILLER
    return [w for w in ws if w not in filler]


def _is_num(w: str) -> bool:
    return w[:1].isdigit()


def _as_typed(response: str, words: list[str]) -> str:
    """The student's own spelling of these words, in their order ("Utah", not "utah")."""
    want = set(words)
    return " ".join(w for w in _TOK.findall(_APOS.sub("", _POSS.sub("", response))) if w.casefold() in want)


@dataclass
class Corpus:
    """The course's passages as word streams: (word, capitalised, sentence_break_before, soft_break_before), with
    small words dropped."""
    streams: list[list[tuple[str, bool, bool, bool]]]
    vocab: frozenset[str]

    @classmethod
    def build(cls, texts: list[str]) -> "Corpus":
        streams, vocab = [], set()
        for text in texts:
            t = _APOS.sub("", _POSS.sub("", unicodedata.normalize("NFKC", text or "")))
            out: list[tuple[str, bool, bool, bool]] = []
            last_end, prev, strong, soft = 0, "", True, True
            for m in _TOK.finditer(t):
                gap = t[last_end:m.start()]
                if gap.lstrip().startswith(".") and (len(prev) == 1 or prev.casefold() in _ABBR):
                    gap = gap.replace(".", " ", 1)  # "William T. Garratt", "Mr. Tritle": not a sentence end
                strong = strong or bool(_STRONG.search(gap))
                soft = soft or strong or bool(_SOFT.search(gap))
                last_end, prev = m.end(), m.group(0)
                w = m.group(0)
                low = w.casefold()
                if low in FILLER:
                    soft = True                   # "Foundry of San Francisco" reads foundry san francisco
                    continue
                out.append((low, w[:1].isupper(), strong, soft))
                vocab.add(low)
                strong = soft = False
            streams.append(out)
        return cls(streams, frozenset(vocab))

    def occurrences(self, phrase: list[str]):
        """(stream, start, end) for each place the phrase appears word for word (plural-tolerant), in one sentence."""
        want = [_sing(w) for w in phrase]
        n = len(want)
        for s in self.streams:
            for i in range(len(s) - n + 1):
                if all(_sing(s[i + k][0]) == want[k] for k in range(n)) and not any(s[i + k][2] for k in range(1, n)):
                    yield s, i, i + n


def _match(a: str, r: str, vocab: frozenset[str]) -> str | None:
    """How response word r matches answer word a: 'same', 'plural', 'typo', or None."""
    if a == r:
        return "same"
    if _is_num(a) or _is_num(r):
        return None
    if _sing(a) == _sing(r):
        return "plural"
    if _fold(a) == _fold(r):
        return None                               # an accent is not a typo: the accent note stays
    if len(a) >= MIN_TYPO_LEN and a[:1] == r[:1] and r not in vocab and _one_edit(a, r):
        return "typo"
    return None


def _subsequence(small: list[str], big: list[str]) -> list[int] | None:
    """Indexes in big where small's words appear in order (exact or plural), or None."""
    at, j = [], 0
    for w in small:
        while j < len(big) and _sing(big[j]) != _sing(w):
            j += 1
        if j == len(big):
            return None
        at.append(j)
        j += 1
    return at


def _unambiguous(phrase: list[str], answer: list[str], corpus: Corpus) -> bool:
    """The short form appears in the materials, and every time it does, it is part of this answer: no other
    capitalised word sits right before it ("Central Pacific" makes "Pacific" ambiguous). Only the word before
    counts: a short form always keeps the name's last word, so a rival name differs in front ("Stanford
    University" is named after Stanford; it is not another Stanford a student could mean)."""
    own = {_sing(w) for w in answer}
    seen = False
    for s, i, _j in corpus.occurrences(phrase):
        seen = True
        if i > 0 and not s[i][3]:
            w, cap, _, _ = s[i - 1]
            if cap and _sing(w) not in own and w not in TITLES and not _is_num(w):
                return False
    return seen


def fair_cloze(keys: list[str], response: str, corpus: Corpus | None) -> CheckResult | None:
    """A CheckResult(ok=True) with a note when the response is a fair form of one of the keys; ok=False with half
    credit and a reason when the whole answer is there plus words the materials do not back; else None."""
    if not isinstance(response, str) or not response.strip():
        return None
    raw = response.casefold()
    for key in keys:
        keep_a = _is_name_a(key)
        a_all, r_all = _words(key, keep_a), _words(response, keep_a)
        a, r = _content(a_all, keep_a), _content(r_all, keep_a)
        if not a or not r or len(r) > len(a) + MAX_EXTRA:
            continue
        vocab = corpus.vocab if corpus else frozenset()
        # 1. same words, different surface
        if "".join(a) == "".join(r):
            return CheckResult(ok=True, score=1.0, detail="Correct")
        if len(a) == len(r):
            kinds = [_match(x, y, vocab) for x, y in zip(a, r)]
            if None not in kinds and kinds.count("typo") <= 1:
                note = f" Check the spelling: {key.strip()}." if "typo" in kinds else ""
                return CheckResult(ok=True, score=1.0, detail="Correct." + note if note else "Correct")
        hedged = any(w in HEDGE for w in r_all) or "?" in raw or "/" in raw or " & " in raw
        if hedged or corpus is None:
            continue
        # 2. the answer plus a few extra words, phrased that way in the materials
        at = _subsequence(a, r)
        if at is not None and len(r) > len(a):
            extra = [w for k, w in enumerate(r) if k not in at]
            and_added = "and" in extra and "and" not in a
            if not any(_is_num(w) for w in extra) and not and_added:
                if any(True for _ in corpus.occurrences(r)):
                    return CheckResult(ok=True, score=1.0, detail=f"Correct. The answer in your materials: {key.strip()}.")
                # Words the materials never use cannot be a rival answer from them ("Utah" when the text never says
                # Utah); a word they do use can ("Promontory Summit Ogden"), so that stays wrong.
                unseen = [w for w in extra if w not in corpus.vocab]
                if len(unseen) == len(extra) and len(extra) <= 2:
                    said = _as_typed(response, extra) or " ".join(extra)
                    return CheckResult(ok=True, score=1.0, detail=f"Correct: {key.strip()}. Your materials don't "
                                                                  f"mention \u201c{said}\u201d, so that part wasn't checked.")
            # The right answer is in there, with words the materials do not put next to it: close, not right.
            said = _as_typed(response, extra) or " ".join(extra)
            return CheckResult(ok=False, score=0.5, detail=f"Not quite. \u201c{key.strip()}\u201d is right, but your "
                                                           f"materials don't say \u201c{said}\u201d with it. Type just the missing words.")
        # 3. a shortened name the materials never use for anything else
        if len(a) >= 2 and len(r) < len(a) and _subsequence(r, a) is not None:
            m = _HEAD_END.search(key)
            head = _content(_words(key[:m.start()] if m and m.start() > 0 else key, keep_a), keep_a) or a
            in_head = sum(1 for w in r if _sing(w) in {_sing(h) for h in head})
            nums_kept = all(w in r for w in a if _is_num(w))
            if _sing(head[-1]) in {_sing(w) for w in r} and in_head >= math.ceil(len(head) / 2) and nums_kept:
                # Only a proper name may be shortened (Leland Stanford -> Stanford). In a description the dropped
                # word is often the point ("immigrants" for "Chinese immigrants"): close, not right.
                head_raw = [w for w in _TOK.findall(_APOS.sub("", _POSS.sub("", key[:m.start()] if m and m.start() > 0
                                                                            else key)))
                            if w.casefold() not in FILLER]
                is_name = bool(head_raw) and all(w[:1].isupper() for w in head_raw)
                if is_name and _unambiguous(r, a, corpus):
                    return CheckResult(ok=True, score=1.0, detail=f"Correct. The full answer: {key.strip()}.")
                return CheckResult(ok=False, score=0.5, detail=f"Almost. The full answer is \u201c{key.strip()}\u201d.")
    return None
