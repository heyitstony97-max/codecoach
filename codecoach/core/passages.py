"""Split materials into passages with stable ids. Deterministic: same text -> same ids.

Ids:  text / pdf_text -> "<doc>:p<page>:<para>"      (page is 1 when the text has no markers)
      markdown        -> "<doc>:<heading-slug>:<para>"
Page markers: a form-feed character, or a line like "--- page 12 ---".
A form-feed advances the page by one; an explicit marker sets the page number.
Paragraphs are blank-line separated blocks, numbered from 1 within their page/section.
Single newlines inside a paragraph become spaces (PDF text is hard wrapped).
"""

import re
from bisect import bisect_left

from .models import Passage
from .text import slugify

_PAGE_RE = re.compile(r"^\s*-{2,}\s*page\s+(\d+)\s*-{2,}\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_CHAPTER_RE = re.compile(r"^\s*(?:chapter|ch\.?)\s+(\d+|[ivxlcdm]+)\b", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def normalize_newlines(text: str) -> str:
    """The text CodeCoach keeps as the document: line endings become \\n and nothing else changes."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_document(doc_id: str, text: str, kind: str = "text") -> list[Passage]:
    """kind: 'text' | 'pdf_text' | 'markdown'. Returns passages in document order."""
    if not _DOC_ID_RE.match(doc_id):
        raise ValueError(f"bad doc id {doc_id!r}: lowercase letters, digits, '_' '-' only (no ':')")
    if kind not in ("text", "pdf_text", "markdown"):
        raise ValueError(f"unknown doc kind {kind!r}")
    text = normalize_newlines(text).replace("\f", "\n\f\n")
    # Offsets are reported against the text WITHOUT the two newlines added around each form feed
    ff = [i for i, ch in enumerate(text) if ch == "\f"]

    def orig(e: int) -> int:
        return e - 2 * bisect_left(ff, e)
    markdown = kind == "markdown"

    out: list[Passage] = []
    counters: dict[str, int] = {}   # key (page or slug) -> paragraphs so far
    used_slugs: dict[str, int] = {}
    page = 1
    # decided up front so labels are consistent: "p. N" appears on every passage or on none
    saw_marker = "\f" in text or any(_PAGE_RE.match(ln) for ln in text.split("\n"))
    chapter: str | None = None
    section_title = "Introduction"
    section_slug = "intro"
    used_slugs["intro"] = 1
    block: list[str] = []
    bspan: list[int] = []           # [start, end] of the open paragraph, in expanded-text offsets
    fence: list[str] | None = None
    fstart = fend = 0

    def key() -> str:
        return section_slug if markdown else f"p{page}"

    def flush() -> None:
        nonlocal chapter
        if not block:
            return
        para_text = " ".join(line.strip() for line in block).strip()
        block.clear()
        span = (bspan[0], bspan[1])
        bspan.clear()
        if not para_text:
            return
        _emit(para_text, *span)

    def _emit(para_text: str, s: int, e: int) -> None:
        nonlocal chapter
        if not markdown:
            m = _CHAPTER_RE.match(para_text)
            if m and len(para_text) <= 80:
                chapter = "Ch. " + m.group(1).upper()
        k = key()
        counters[k] = counters.get(k, 0) + 1
        n = counters[k]
        parts: list[str] = []
        if markdown:
            parts.append(section_title)
            if saw_marker:
                parts.append(f"p. {page}")
        else:
            if chapter:
                parts.append(chapter)
            if saw_marker:
                parts.append(f"p. {page}")
        parts.append(f"para {n}")
        out.append(Passage(id=f"{doc_id}:{k}:{n}", doc_id=doc_id, location=", ".join(parts), text=para_text,
                           start=orig(s), end=orig(e)))

    pos = 0
    for raw in text.split("\n"):
        line_pos = pos
        pos += len(raw) + 1
        line = raw.rstrip()
        if fence is not None:
            fence.append(line)
            fend = line_pos + len(line)
            if _FENCE_RE.match(line):
                _emit("\n".join(fence), fstart, fend)
                fence = None
            continue
        if "\f" in raw:   # (rstrip above eats form feeds, so test the raw line)
            flush()
            page += 1
            continue
        pm = _PAGE_RE.match(line)
        if pm:
            flush()
            page = int(pm.group(1))
            continue
        if markdown:
            if _FENCE_RE.match(line):
                flush()
                fence = [line]
                fstart, fend = line_pos, line_pos + len(line)
                continue
            hm = _HEADING_RE.match(line)
            if hm:
                flush()
                title = hm.group(2).strip() or "Untitled"
                base = slugify(title)
                used_slugs[base] = used_slugs.get(base, 0) + 1
                section_slug = base if used_slugs[base] == 1 else f"{base}-{used_slugs[base]}"
                section_title = title
                continue
        if not line.strip():
            flush()
        else:
            block.append(line)
            if not bspan:
                bspan.extend((line_pos + len(line) - len(line.lstrip()), 0))
            bspan[1] = line_pos + len(line)
    flush()
    if fence is not None:  # unterminated fence: keep the text anyway
        _emit("\n".join(fence), fstart, fend)
    return out


# ---------------------------------------------------------------- what may be quoted
# A passage is always stored (ids, Read and Sources keep it). It is QUOTABLE, meaning topics may rest on it,
# items may cite it and Ask may retrieve it, only when it says something: not a bare heading, numeral or label.

MIN_QUOTABLE_WORDS = 4
_LABEL_RE = re.compile(
    r"^(?:(?:chapter|ch\.?|section|sec\.?|part|book|canto|poem|act|scene|volume|vol\.?|lesson|unit|page|p\.?|"
    r"no\.?|number|figure|fig\.?|table|appendix|rule|article|verse|stanza|psalm)\s*)?"
    r"(?:[ivxlcdm]+|\d+(?:\.\d+)*[a-z]?)[.):]?$", re.IGNORECASE)
_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*[.)]?\s+\S")
_SENTENCE_END = re.compile(r"[.!?]['\")\]]*$")


_SMALL_WORDS = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but", "nor", "by", "with", "from",
                "as", "is", "vs", "de", "la", "el", "y", "von", "van"}


def _title_case(words: list[str]) -> bool:
    """Every word capitalised (or a small linking word): a title line, not a sentence."""
    big = [w for w in words if w.lower().strip(",:;") not in _SMALL_WORDS]
    return bool(big) and all(w[:1].isupper() or not w[:1].isalpha() for w in big) and words[0][:1].isupper()


def is_quotable(text: str) -> bool:
    """False for passages under 4 words and for ones that are only a heading, numeral or chapter label
    ("I.", "Chapter 3", "VIII. TRIUMPHANT", "2.5 Measures of the Center"). Those stay in the stored text."""
    t = " ".join((text or "").split())
    words = t.split()
    if len(words) < MIN_QUOTABLE_WORDS:
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    if _LABEL_RE.match(t):
        return False
    if len(words) <= 7 and not _SENTENCE_END.search(t) and _title_case(words):
        return False                                           # "The Rise of Valdoria"
    if len(words) <= 10 and not _SENTENCE_END.search(t):
        if all(c.isupper() for c in letters):                  # "THE ELEMENTS OF STYLE"
            return False
        if _NUMBERED_HEADING_RE.match(t) and len(words) <= 8:  # "2.5 Measures of the Center"
            return False
        # "VIII. TRIUMPHANT" style: a leading numeral label plus capitalised words only
        head = words[0].rstrip(".):")
        if _LABEL_RE.match(head) and all(w[:1].isupper() or not w[:1].isalpha() for w in words[1:]) and len(words) <= 6:
            return False
    return True


def quotable_passages(passages):
    return [p for p in passages if is_quotable(p.text)]
