"""Text normalization and stable hashing. Pure stdlib; the grounding gate imports this."""

import hashlib
import json
import re
import unicodedata

_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u0060": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u00ab": '"', "\u00bb": '"',
}
_DASHES = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2015": "-", "\u2212": "-", "\ufe58": "-", "\ufe63": "-", "\uff0d": "-",
}
# zero-width and invisible formatting characters
_ZERO_WIDTH = (
    "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad"
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u180e"
)
_TABLE: dict[int, str | None] = {ord(k): v for k, v in {**_QUOTES, **_DASHES}.items()}
_TABLE.update({ord(c): None for c in _ZERO_WIDTH})
_WS = re.compile(r"\s+")

# Superscript digits/signs: NFKC would flatten 10\u00b2\u00b3 into 1023. They become an explicit exponent instead.
_SUP = {"\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2074": "4", "\u2075": "5",
        "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9", "\u207a": "+", "\u207b": "-",
        "\u207f": "n"}


def sup_piece(text: str, i: int) -> str | None:
    """Exponent replacement for the raw char text[i] when it is a superscript, else None. The first char of a run
    carries the '^' (unless a '^' already precedes it), so a char-by-char offset mapper agrees with
    fold_superscripts: 10\u00b2\u00b3 -> '10', '^2', '3' -> '10^23'."""
    d = _SUP.get(text[i])
    if d is None:
        return None
    if i > 0 and (text[i - 1] in _SUP or text[i - 1] == "^"):
        return d
    return "^" + d


def fold_superscripts(s: str) -> str:
    if not any(c in _SUP for c in s):
        return s
    return "".join(sup_piece(s, i) or ch for i, ch in enumerate(s))


def normalize(s: str) -> str:
    """NFKC, curly quotes -> straight, en/em dashes -> '-', zero-width chars removed,
    whitespace collapsed to single spaces and trimmed, casefolded.

    Idempotent: normalize(normalize(x)) == normalize(x).
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", fold_superscripts(s))
    s = s.translate(_TABLE)
    s = _WS.sub(" ", s).strip()
    s = s.casefold()
    # casefold can emit characters that are not NFKC-stable; settle once more.
    s = unicodedata.normalize("NFKC", s).translate(_TABLE)
    return _WS.sub(" ", s).strip()


def quote_in(passage_text: str, quote: str) -> bool:
    """True when `quote` appears verbatim in `passage_text` after normalize() on both.
    An empty (after normalizing) quote is never found."""
    q = normalize(quote)
    return bool(q) and q in normalize(passage_text)


def stable_hash(*parts: object, length: int = 16) -> str:
    """Deterministic short hash of JSON-able parts (used for ids and draft hashes)."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def slugify(s: str, default: str = "section") -> str:
    """ASCII, lowercase, hyphen-separated slug; never empty, never contains ':'."""
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or default
