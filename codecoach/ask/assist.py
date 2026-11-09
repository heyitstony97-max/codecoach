"""Ask helpers that need no model: location labels in quotes, question language, premise hints, and the
writing-help route for a pasted sentence. Everything here only ADDS a short fixed note or shows passage text
word for word; it never changes what the gate keeps (except the label strip, which only shortens a quote that
is then checked verbatim like any other)."""

from __future__ import annotations

import re
import unicodedata

from codecoach.ask.check import Cite, passage_sentences, quote_span
from codecoach.core.models import Passage
from codecoach.core.text import normalize
from codecoach.ground import facts as F
from codecoach.ground import gate

# ------------------------------------------------------------------ location labels the prompt puts before a passage

_LABEL = re.compile(r"\([^()]*\bpara(?:graph)?s?\.?\s*\d+[^()]*\)")


def strip_location_label(quote: str, passage: Passage) -> str:
    """The prompt shows each passage as "[id] (location) text"; a model sometimes copies "(Ch. 5, para 56)" into its
    quote. If the quote is not in the passage as written, remove location labels and use the rest, but only if
    that rest IS verbatim in the passage. Otherwise the quote is returned unchanged (and the gate drops it)."""
    if gate.text_found(passage.text, quote):
        return quote
    cut = quote.replace(f"({passage.location})", " ")
    cut = _LABEL.sub(" ", cut)
    cut = re.sub(r"\s+", " ", cut).strip()
    if cut and cut != quote.strip() and gate.text_found(passage.text, cut):
        return cut
    return quote


# ------------------------------------------------------------------ language of the question vs the quotes

_SCRIPTS = (("cjk", 0x4E00, 0x9FFF), ("kana", 0x3040, 0x30FF), ("hangul", 0xAC00, 0xD7AF), ("cyrillic", 0x0400, 0x04FF),
            ("arabic", 0x0600, 0x06FF), ("hebrew", 0x0590, 0x05FF), ("devanagari", 0x0900, 0x097F),
            ("greek", 0x0370, 0x03FF), ("thai", 0x0E00, 0x0E7F))
_STOPS = {
    "en": "the of and to in is are was were what which who why how when where does do did that this it for with as on "
          "by from be an or at not they his her their its has have had can will would about".split(),
    "es": "el la los las de del que qué es son un una unos unas y en por para con como cómo cuál cuáles quién "
          "dónde cuándo cuánto se su sus al lo más pero porque".split(),
    "fr": "le la les des du de un une et est sont que qui quoi pourquoi comment où quand dans pour avec sur au aux "
          "ce cette ses son leur il elle ne pas".split(),
    "de": "der die das den dem des und ist sind war wer wie warum wann wo was ein eine einen mit von zu im auf für "
          "nicht auch sich".split(),
    "pt": "o os as um uma uns umas de do da dos das e em para com por que quem como onde quando não é são "
          "foi pelo pela".split(),
    "it": "il lo la gli le di del della dei che chi come perché dove quando è sono un una e in per con su non "
          "cosa quale".split(),
}
_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def detect_language(text: str) -> str | None:
    """A cheap guess: a script name for non-Latin text, else the language whose stopwords the text uses most
    (en/es/fr/de/pt/it). None when it cannot tell (too short, no stopword hits, a tie)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    for name, lo, hi in _SCRIPTS:
        if sum(lo <= ord(c) <= hi for c in letters) * 2 > len(letters):
            return name
    toks = [t.lower() for t in _TOKEN.findall(text)]
    if not toks:
        return None
    score: dict[str, int] = {}
    for lang, stops in _STOPS.items():
        plain = {_strip_accents(w) for w in stops}
        score[lang] = sum(1 for t in toks if t in stops or _strip_accents(t) in plain and lang != "en")
    if "¿" in text or "¡" in text:
        score["es"] += 2
    best = max(score.values())
    if best < 1:
        return None
    top = [k for k, v in score.items() if v == best]
    if len(top) > 1:
        return None
    if top[0] != "en" and best < 2 and len(toks) > 3:
        return None
    return top[0]


LANGUAGE_NOTE = "Your materials are in English, so the answer quotes them in English."


def language_note(question: str, quotes: list[str]) -> str:
    """One short line when the question is plainly not English and the quoted source is English, else ''."""
    ql = detect_language(question)
    if ql in (None, "en"):
        return ""
    sl = detect_language(" ".join(quotes))
    return LANGUAGE_NOTE if sl == "en" else ""


# ------------------------------------------------------------------ a premise the materials do not support

PREMISE_NOTE = "Your question assumes something your materials don't support."
_ASKWORDS = {"why", "how", "what", "which", "who", "when", "where", "does", "do", "did", "is", "are", "was", "were"}


def _content(text: str) -> set[str]:
    from codecoach.importer.syllabus import content_words
    return set(content_words(text))


def premise_note(question: str, kept_texts: list[str], kept_quotes: list[str], removed: list[dict]) -> str:
    """PREMISE_NOTE when (a) a sentence the gate removed repeated the question's own terms (it agreed with the
    premise) while no kept sentence does, or (b) a kept quote denies words the question uses. It never says the
    premise is false: it says the materials do not support it, and the kept quotes below are what they do say."""
    q = _content(question)
    if len(q) < 3:
        return ""
    need = max(3, int(0.8 * len(q) + 0.999))
    for quote in kept_quotes:                       # a quote that denies (nearly all of) the question's own words
        if F.has_negation(quote) and len(q & _content(quote)) >= need and F.negated_clauses([question], [quote]):
            return PREMISE_NOTE
    if any(len(q & _content(t)) >= need for t in kept_texts):
        return ""
    for r in removed:
        if r.get("gate") in ("ask_unsupported_fact", "ask_negation", "ask_quote_missing") and len(q & _content(r.get("text", ""))) >= need:
            return PREMISE_NOTE
    return ""


# ------------------------------------------------------------------ writing help: a pasted sentence

WRITING_LINE = "CodeCoach doesn't judge or rewrite your own text. Here is what your guide says about this:"
MAX_WRITING_QUOTES = 5
_TRIGGER = re.compile(
    r"\b(?:fix|rewrite|rephrase|correct|correction|proofread|edit|revise|improve|grammatical|grammar|check|"
    r"is (?:this|my|that)\b[^:]{0,40}\b(?:right|wrong|ok|okay|good|fine))\b", re.I)
_QUOTED = re.compile(r"[\"\u201c\u2018']([^\"\u201d\u2019]{8,})[\"\u201d\u2019']")
_META = {"sentence", "correct", "guide", "fix", "rewrite", "check", "according", "grammar", "grammatical", "right",
         "wrong", "proofread", "edit", "revise", "improve", "text", "paragraph", "mine", "please", "help", "write"}

# rule words per pattern the student's own text shows (applied to the pasted text only)
_RULES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\b(?:its|it['\u2019]s)\b", re.I), ["its", "it's", "possessive", "apostrophe"]),
    (re.compile(r"\b\w+['\u2019]s?\b|\b\w+s['\u2019]", re.I), ["possessive", "apostrophe"]),
    (re.compile(r",\s*(?:who|which|whom|whose)\b|\b(?:who|which)\b[^,]*,", re.I), ["comma", "commas", "parenthetic", "relative", "clause"]),
    (re.compile(r"^\s*(?:\w+ing|being|having)\b[^,]*,", re.I), ["participial", "participle", "phrase", "subject"]),
    (re.compile(r"\b\w+ing\b[^,.]*,\s*the\b", re.I), ["participial", "participle", "phrase", "subject"]),
    (re.compile(r",\s*(?:and|but|or|nor|for|so|yet)\b", re.I), ["conjunction", "comma", "clauses"]),
    (re.compile(r";"), ["semicolon", "clauses"]),
    (re.compile(r"\b(?:very|really|quite|rather|somewhat)\b", re.I), ["qualifiers", "needless"]),
    (re.compile(r"\b(?:is|are|was|were|be|been|being)\s+\w+ed\b\s+by\b", re.I), ["passive", "active", "voice"]),
    (re.compile(r"\b(?:i|we|they|he|she)\s+\w+[^.,;]*\b(?:and|or)\s+\w+ing\b", re.I), ["parallel", "construction"]),
]


# The question must be ABOUT writing. "Is this correct: the golden spike was silver" is a fact check: it goes to the
# normal Ask path (model, quote gate, premise note), never to writing help. Orchestrator probe 2026-11-06 found 4 of
# 5 fact checks routed here before this rule.
_WRITING = re.compile(
    r"\b(?:sentences?|paragraphs?|essays?|grammar|grammatical(?:ly)?|punctuat\w*|spell(?:ing|ed)?|wording|phrasing|"
    r"writing|rewrite|rephrase|reword|proofread|edit|revise|fix|improve)\b", re.I)


def pasted_text(question: str) -> str:
    """The student's own sentence inside a writing-help question, or ''. It needs a helper verb (fix, rewrite,
    is this correct, ...), a writing word (sentence, grammar, rewrite, ...; a bare "is this correct" is a fact
    check) AND text after a colon or inside quotes of at least 3 words. Only the part before the pasted text is
    searched for the trigger and the writing word, so the student's own words never route the question."""
    q = (question or "").strip()
    m = _QUOTED.search(q)
    ask_part = q.split(":", 1)[0] if ":" in q else (q[:m.start()] + " " + q[m.end():] if m else q)
    if not _TRIGGER.search(ask_part) or not _WRITING.search(ask_part):
        return ""
    payload = ""
    if ":" in q:
        payload = q.split(":", 1)[1].strip()
    if len(payload.split()) < 3:
        m = _QUOTED.search(q)
        payload = m.group(1).strip() if m else ""
    payload = payload.strip(" \"'\u201c\u201d\u2018\u2019")
    return payload if len(payload.split()) >= 3 else ""


def rule_words(text: str) -> list[str]:
    out: list[str] = []
    for pat, ws in _RULES:
        if pat.search(text):
            out.extend(w for w in ws if w not in out)
    return out


def _tok(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+(?:['\u2019][a-z]+)?", normalize(s).replace("\u2019", "'"))}


_GENERIC = {"comma", "commas", "clause", "clauses", "phrase", "subject", "relative", "conjunction"}


def _sentences(text: str) -> list[tuple[int, int]]:
    """passage_sentences, but a leading rule number ("3.") stays with its sentence."""
    spans = passage_sentences(text)
    if len(spans) > 1 and re.fullmatch(r"\d{1,3}\.", text[spans[0][0]:spans[0][1]].strip()):
        spans = [(spans[0][0], spans[1][1])] + spans[2:]
    return spans


def writing_help(question: str, text: str, passages: list[Passage]) -> list[dict]:
    """Exact quotes (whole passage sentences, never edited) from the guide that bear on the rule words found in
    the student's `text` and its own words, best first, with the guide's own example when the next passage is a
    short one. [] when nothing in the guide matches: then the caller says not covered."""
    rw = rule_words(text)
    own = {w for w in _tok(text) if len(w) > 3 and w not in _META} - {w for w in _tok(question) if w in _META}
    rwset = {w for r in rw for w in _tok(r)} | set(rw)
    scored: list[tuple[float, int, int, str]] = []        # score, passage index, sentence order, sentence
    for pi, p in enumerate(passages):
        if len(p.text) > 2400:
            continue
        for si, (a, b) in enumerate(_sentences(p.text)):
            s = p.text[a:b]
            if len(s.split()) < 4:
                continue
            toks = _tok(s)
            hit = rwset & toks
            spec = len(hit - _GENERIC)
            if rw and (not hit or hit <= {"its", "it's"}):      # the bare word "its" alone is just prose
                continue
            if not rw and len(own & toks) < 2:
                continue
            score = 3.0 * spec + 1.0 * len(hit & _GENERIC) + 1.0 * len(own & toks)
            if re.match(r"\d{1,3}\.\s", s) and spec:
                score += 2.0                                 # the numbered rule itself
            if score < 3:
                continue
            scored.append((score, pi, si, s))
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    best = scored[0][0]
    picked: list[tuple[int, int, str]] = []
    for score, pi, si, s in scored:
        if len(picked) >= 3 or score < 0.6 * best:
            break
        if all(pi != x[0] for x in picked):
            picked.append((pi, si, s))
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def add(p: Passage, s: str) -> None:
        sp = quote_span(p.text, s)
        if sp is None or (p.id, sp[0]) in seen or len(out) >= MAX_WRITING_QUOTES:
            return
        seen.add((p.id, sp[0]))
        c = Cite(p.id, p.doc_id, p.location, p.text[sp[0]:sp[1]], sp[0], sp[1])
        out.append({"text": c.quote, "cites": [c.as_dict()]})

    for pi, si, s in picked:
        p = passages[pi]
        add(p, s)
        nxt = passages[pi + 1] if pi + 1 < len(passages) else None   # the guide's own example, if it follows
        if nxt is not None and nxt.doc_id == p.doc_id and len(nxt.text) <= 260 and len(nxt.text.split()) >= 4:
            sents = _sentences(nxt.text)
            if len(sents) == 1:
                add(nxt, nxt.text[sents[0][0]:sents[0][1]])
    return out


# ------------------------------------------------------------------ one sentence stitched from different passages

_CLAUSE_SPLIT = re.compile(r";\s*|,\s*(?:and|but|while|whereas|or)\s+|,\s+|\s+(?:and|but|while|whereas)\s+")


def split_mashup(v, offered: dict[str, Passage]):
    """A kept sentence whose quotes come from two or more different passages is only trusted when one passage's
    quotes back all of it (then the other quotes are dropped) or when each of its clauses is backed by the quotes of a
    single passage (then it becomes one sentence per clause, each re-checked). Otherwise None: the sentence is left
    as the gate kept it. Every produced sentence passes check_sentence again with only its own quotes."""
    from codecoach.ask.check import check_sentence
    by_p: dict[str, list] = {}
    for c in v.cites:
        by_p.setdefault(c.passage_id, []).append(c)
    if len(by_p) < 2:
        return None
    for pid, cs in by_p.items():
        v1 = check_sentence(v.text, [(pid, c.quote) for c in cs], offered)
        if v1.kept:
            return [v1]
    clauses = [c.strip(" ,;") for c in _CLAUSE_SPLIT.split(v.text) if c and len(c.strip(" ,;").split()) >= 3]
    if len(clauses) < 2:
        return None
    out = []
    for cl in clauses:
        text = cl[0].upper() + cl[1:] + ("" if cl.endswith((".", "!", "?")) else ".")
        for pid, cs in by_p.items():
            v1 = check_sentence(text, [(pid, c.quote) for c in cs], offered)
            if v1.kept:
                out.append(v1)
                break
        else:
            return None
    return out
