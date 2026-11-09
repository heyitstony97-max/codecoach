"""PDF text through poppler's `pdftotext` (local, no network, no Python dependency).

Why not only Hermes `read_file`: its converter refuses a whole PDF when ANY page needs OCR (a map or a
photo page), so ordinary government and textbook PDFs failed to import. `pdftotext` reads every page
that has a text layer and keeps page breaks as form feeds, so each passage cites its page ("p. 14").
Pages without a text layer (images, scans) are reported to the learner, never filled in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from html import unescape
from typing import NamedTuple

TIMEOUT_S = 120
EMPTY_PAGE_CHARS = 20          # fewer extracted characters than this = a page with no text layer
NOTE_MAX_PAGES = 12


class PdfError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PdfText(NamedTuple):
    text: str                  # pages joined by "\f" (the splitter turns those into page numbers)
    pages: int
    empty_pages: list[int]     # 1-based pages with no text layer


_END = tuple('.!?:;"\u201d\')]')


def _continues(prev: str, nxt: str) -> bool:
    """True when a blank line split one sentence: the previous block does not end a sentence and the
    next starts in lower case (or the previous ends with a hyphen or comma)."""
    p, n = prev.rstrip(), nxt.lstrip()
    if not p or not n:
        return False
    if p.endswith(("-", ",")):
        return True
    return not p.endswith(_END) and n[0].islower()


def rejoin_page(page: str) -> str:
    """pdftotext puts a blank line wherever line spacing grows, which can cut a sentence in two. Join
    such blocks with a single newline (the splitter reads that as one paragraph). Only whitespace
    between words changes; every character of text is kept."""
    blocks = page.split("\n\n")
    out: list[str] = []
    for b in blocks:
        if out and b.strip() and _continues(out[-1], b):
            out[-1] = out[-1].rstrip("\n") + "\n" + b.lstrip("\n")
        else:
            out.append(b)
    return "\n\n".join(out)


_WORD = re.compile(r"[^\W\d_]{2,}")

# ---- formulas stored as pictures: pdftotext drops them and leaves the sentence cut in two
GAP_MARK = "  "                  # a formula gap is written into the text as a double space inside a sentence
GAP_RE = re.compile(r"(?<=[^\s.!?])  +(?=\S)")
_BBOX_LINE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)
_BBOX_WORD = re.compile(r"<word [^>]*>(.*?)</word>", re.S)
_END_MARKS = tuple('.!?;:"\u201d\')]')


def _bbox_pages(path: str) -> list[list[tuple[float, float, float, float, str]]]:
    """Per page, every text line as (xMin, yMin, xMax, yMax, text) from `pdftotext -bbox-layout`."""
    exe = shutil.which("pdftotext")
    if exe is None:
        return []
    try:
        proc = subprocess.run([exe, "-bbox-layout", "-enc", "UTF-8", path, "-"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=TIMEOUT_S, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    html = proc.stdout.decode("utf-8", errors="replace")
    pages = []
    for chunk in html.split("<page ")[1:]:
        rows = []
        for m in _BBOX_LINE.finditer(chunk):
            words = [unescape(w) for w in _BBOX_WORD.findall(m.group(5))]
            rows.append((float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), " ".join(words)))
        pages.append(rows)
    return pages


def formula_gaps_in(rows) -> list[tuple[str, str]]:
    """(end of the line before, start of the line after) for every place where one printed line was cut in two
    side by side with a wide blank between: the picture of an equation stood there. Prose, running heads,
    table cells and page numbers do not look like that (the cut must sit inside a sentence)."""
    out = []
    for a, b in zip(rows, rows[1:]):
        h = a[3] - a[1]
        ta, tb = a[4].strip(), b[4].strip()
        if h <= 0 or not ta or not tb or abs(b[1] - a[1]) >= 0.5 * h or b[0] - a[2] <= 0.5 * h:
            continue
        if ta.endswith(_END_MARKS) or not any(c.isalpha() for c in ta):
            continue
        last = ta.split()[-1]
        if not (last[:1].islower() or tb[:1].islower() or tb[:1] in ",.;)"):
            continue
        out.append((" ".join(ta.split()[-3:]), " ".join(tb.split()[:3])))
    return out


def mark_formula_gaps(page: str, gaps: list[tuple[str, str]]) -> tuple[str, int]:
    """Join each (before, after) line pair found on this page with a double space, so the gap stays visible and
    findable inside the sentence. Only whitespace changes. Returns (page, number of gaps marked)."""
    if not gaps:
        return page, 0
    lines = page.split("\n")
    out: list[str] = []
    n = 0
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            a, b = lines[i].rstrip(), lines[i + 1].strip()
            for before, after in gaps:
                if a and b and " ".join(a.split()).endswith(before) and " ".join(b.split()).startswith(after):
                    out.append(a + GAP_MARK + b)
                    n += 1
                    i += 2
                    break
            else:
                out.append(lines[i])
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), n


def count_formula_gaps(text: str) -> int:
    return sum(len(GAP_RE.findall(ln)) for ln in text.split("\n"))


def formula_gap_note(n: int) -> str | None:
    if n <= 0:
        return None
    place = "place in this PDF has an equation" if n == 1 else "places in this PDF have equations"
    return f"{n} {place} stored as {'a picture' if n == 1 else 'pictures'}; CodeCoach can't read {'it' if n == 1 else 'them'}."


def is_fragment(text: str) -> bool:
    """A PDF block that is not prose: a page number, a map label, a contents line with dot leaders.
    Fragments stay in the document text (the Sources panel shows everything) but are not passages,
    so no lesson or question is built on them."""
    t = text.strip()
    if len(t) < 25 or len(_WORD.findall(t)) < 4:
        return True
    visible = [c for c in t if not c.isspace()]
    letters = sum(c.isalpha() for c in visible)
    return letters < 0.6 * len(visible)


def available() -> bool:
    return shutil.which("pdftotext") is not None


def empty_pages_of(text: str) -> list[int]:
    """1-based pages with no text, from text whose pages are separated by form feeds."""
    pages = text.split("\f")
    if len(pages) < 2:
        return []
    return [i + 1 for i, p in enumerate(pages) if len(p.strip()) < EMPTY_PAGE_CHARS]


def read_pdf(path: str) -> PdfText:
    exe = shutil.which("pdftotext")
    if exe is None:
        raise PdfError("pdf_unavailable", "PDF reading needs pdftotext (poppler-utils). Paste the text instead.")
    try:
        proc = subprocess.run([exe, "-enc", "UTF-8", "-eol", "unix", path, "-"], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise PdfError("pdf_unreadable", "Reading that PDF took too long. Paste the text instead.") from None
    except OSError as e:
        raise PdfError("pdf_unavailable", f"Could not run pdftotext ({type(e).__name__}).") from None
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").lower()
        if "password" in err or "encrypt" in err:
            raise PdfError("pdf_encrypted", "That PDF is password-protected. Remove the password and import it again.")
        raise PdfError("pdf_unreadable", "Could not read that PDF (it may be damaged). Paste the text instead.")
    pages = proc.stdout.decode("utf-8", errors="replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()            # pdftotext ends every page, the last one included, with a form feed
    empty = [i + 1 for i, p in enumerate(pages) if len(p.strip()) < EMPTY_PAGE_CHARS]
    if not pages or len(empty) == len(pages):
        raise PdfError("pdf_no_text", "That PDF has no text layer (it is scanned images). "
                                      "Run it through OCR first, or paste the text instead.")
    gaps = _bbox_pages(path)
    if gaps:
        pages = [mark_formula_gaps(p, formula_gaps_in(gaps[i]))[0] if i < len(gaps) else p
                 for i, p in enumerate(pages)]
    pages = strip_page_chrome([p.replace("\x07", "") for p in pages])
    return PdfText("\f".join(rejoin_page(p) for p in pages), len(pages), empty)


CHROME_MIN_PAGES = 4           # fewer pages than this: too little evidence to call a line "running"
CHROME_EDGE_LINES = 3          # a header/footer sits in the first or last few lines of a page
CHROME_SHORT = 80              # shorter repeated edge lines are removed; longer ones (footnotes) are kept once


def _shape(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def strip_page_chrome(pages: list[str]) -> list[str]:
    """Remove running headers, footers and page numbers: a line (digits ignored, so "-4-" matches "-5-")
    at the top or bottom of at least 40% of the pages. Short ones are removed; a long repeated line
    (a footnote printed on every page) is kept on its first page only. Body lines are never touched."""
    filled = [p for p in pages if p.strip()]
    if len(filled) < CHROME_MIN_PAGES:
        return pages
    edges: list[set[int]] = []                      # per page: indexes of edge lines
    per_shape: dict[str, set[int]] = {}
    split = [p.split("\n") for p in pages]
    for pi, lines in enumerate(split):
        idx = [i for i, ln in enumerate(lines) if ln.strip()]
        keep = set(idx[:CHROME_EDGE_LINES]) | set(idx[-CHROME_EDGE_LINES:])
        edges.append(keep)
        for i in keep:
            per_shape.setdefault(_shape(lines[i]), set()).add(pi)
    need = max(3, int(0.4 * len(filled) + 0.999))
    running = {s for s, where in per_shape.items() if len(where) >= need and s}
    out: list[str] = []
    seen_long: set[str] = set()
    for pi, lines in enumerate(split):
        kept = []
        for i, ln in enumerate(lines):
            s = _shape(ln)
            if i in edges[pi] and s in running:
                if len(ln.strip()) <= CHROME_SHORT:
                    continue
                if s in seen_long:
                    continue
                seen_long.add(s)
            kept.append(ln)
        out.append("\n".join(kept))
    return out


def skipped_pages_note(empty_pages: list[int], total_pages: int | None = None) -> str | None:
    """Plain-English note for the learner, or None when every page had text."""
    if not empty_pages:
        return None
    shown = ", ".join(str(p) for p in empty_pages[:NOTE_MAX_PAGES])
    more = len(empty_pages) - NOTE_MAX_PAGES
    if more > 0:
        shown += f" and {more} more"
    word = "Page" if len(empty_pages) == 1 else "Pages"
    verb = "has" if len(empty_pages) == 1 else "have"
    of = f" of {total_pages}" if total_pages else ""
    return f"{word} {shown}{of} {verb} no text (images or scans), so CodeCoach could not read {'it' if len(empty_pages) == 1 else 'them'}."
