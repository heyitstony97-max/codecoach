"""Fact extraction for the grounding gate. Pure stdlib, no LLM.

A "fact" is something a learner could be misled by if it were invented:
  ("num", "1500")        a number (1,000 == 1000, 1.50 == 1.5, "1.5 million" == 1500000, "fourteen" == 14)
  ("pct", "40")          a percentage (40%, 40 percent)
  ("ord", 3)             an ordinal (3rd, third)
  ("date", y, m, d)      a full date (March 4, 1861 == 4 March 1861); y is negative for BC
  ("my", y, m)           month and year
  ("md", m, d)           month and day
  ("year", y)            a year with an era (500 BC, 500 BCE, AD 476)
  ("decade", 1860)       1860s
  ("word", "half")       a few fraction words
Names (capitalized words, multi-word runs, possessives) are handled separately as token tuples.

Everything here errs toward MORE facts: a fact we over-extract can only cause a dropped item,
a fact we miss could let an invented claim through.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..core.text import normalize

Fact = tuple

# ------------------------------------------------------------------ text prep (case preserved)

_PREP: dict[int, str | None] = {}
for _c in "\u2018\u2019\u201a\u201b\u2032\u0060\u00b4\u02bc":
    _PREP[ord(_c)] = "'"
for _c in "\u201c\u201d\u201e\u201f\u2033\u00ab\u00bb":
    _PREP[ord(_c)] = '"'
for _c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d":
    _PREP[ord(_c)] = "-"
for _c in "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad\u180e":
    _PREP[ord(_c)] = None
for _i, _c in enumerate("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"):
    _PREP[ord(_c)] = f"^{_i}"        # NFKC would flatten 10\u2076 into 106; keep it an exponent
for _i, _c in enumerate("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"):
    _PREP[ord(_c)] = f"_{_i}"
_WS = re.compile(r"\s+")


def prep(s: str) -> str:
    """Superscripts kept apart, NFKC, quotes and dashes straightened, every invisible format character
    (Unicode category Cf: zero-width, bidi marks, invisible operators) removed, whitespace collapsed.
    Keeps case."""
    s = unicodedata.normalize("NFKC", (s or "").translate(_PREP)).translate(_PREP)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return _WS.sub(" ", s).strip()


# ------------------------------------------------------------------ regexes

_NUM = r"(?:\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?)"
_MON = (r"(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?![A-Za-z])\.?")
_D = r"(?P<d>\d{1,2})(?:st|nd|rd|th)?(?!\d)"
_Y = r"(?P<y>\d{3,4})(?!\d)"
_FLAGS = re.I
_P_MDY = re.compile(rf"(?<![A-Za-z]){_MON}\s+{_D},?\s+{_Y}", _FLAGS)
_P_DMY = re.compile(rf"(?<!\d){_D}\s+(?:of\s+)?{_MON},?\s+{_Y}", _FLAGS)
_P_MY = re.compile(rf"(?<![A-Za-z]){_MON},?\s+{_Y}", _FLAGS)
_P_MD = re.compile(rf"(?<![A-Za-z]){_MON}\s+{_D}(?!,\d{{3}})", _FLAGS)
_P_DM = re.compile(rf"(?<![\d.,]){_D}\s+(?:of\s+)?{_MON}", _FLAGS)
_ERA = re.compile(r"\s*(?P<era>B\.?C\.?(?:E\.?)?|A\.?D\.?|C\.?E\.?)(?![A-Za-z])")
_P_YEAR_ERA = re.compile(r"(?<![\w.])(?P<y>\d{1,4})\s*(?P<era>B\.?C\.?(?:E\.?)?|A\.?D\.?|C\.?E\.?)(?![A-Za-z])")
_P_AD_YEAR = re.compile(r"(?<![A-Za-z])(?P<era>A\.?D\.?)\s*(?P<y>\d{1,4})(?!\d)")
_P_DECADE = re.compile(r"(?<![\d,.])(?P<y>\d{3}0)'?s(?![A-Za-z])")
_DECADE_WORDS = {"twenties": 20, "thirties": 30, "forties": 40, "fifties": 50, "sixties": 60,
                 "seventies": 70, "eighties": 80, "nineties": 90}
_P_DECADE_WORD = re.compile(rf"(?<![\w-])(?P<w>{'|'.join(_DECADE_WORDS)})(?![A-Za-z])", re.I)
_SIGN = r"(?P<sign>(?<![\w.,)\]])-(?=\d))?"      # a minus, not a range hyphen (1861-65) or a glued one
_P_PCT = re.compile(rf"{_SIGN}(?<!\d)(?P<n>{_NUM})\s*(?:%|(?:percent|per\s?cent)(?![A-Za-z]))", re.I)
_P_NUM = re.compile(rf"{_SIGN}(?<!\d)(?P<n>{_NUM})(?:\s*(?P<scale>thousand|million|billion|trillion)(?![A-Za-z])"
                    rf"|(?P<ord>st|nd|rd|th)(?![A-Za-z]))?", re.I)
# ordered numeric forms: the ORDER is part of the fact (3/4/1861 is not 4/3/1861, 3:1 is not 1:3)
_P_ISO = re.compile(r"(?<![\d./:-])(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?![\d-])")
_P_SLASH_DATE = re.compile(r"(?<![\d./:-])(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4}|\d{2})(?![\d/])")
_P_RATIO = re.compile(r"(?<![\d./:-])(?P<a>\d+)\s?(?P<op>[:/])\s?(?P<b>\d+)(?!\d|[./:]\d)")
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9, "trillion": 10**12}

# ------------------------------------------------------------------ number words

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_ORD_UNITS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
              "seventh": 7, "eighth": 8, "ninth": 9}
_ORD_TEENS = {"tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
              "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19}
_ORD_TENS = {"twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60,
             "seventieth": 70, "eightieth": 80, "ninetieth": 90}
_FRACTION_WORDS = {"half", "halves", "quarter", "quarters", "twice", "thrice", "double", "triple",
                   "dozen", "dozens", "hundreds", "thousands", "millions", "billions"}


def _classify(w: str):
    """-> (kind, value, is_ordinal) or None."""
    if w in _UNITS:
        return ("unit", _UNITS[w], False)
    if w in _TEENS:
        return ("teen", _TEENS[w], False)
    if w in _TENS:
        return ("tens", _TENS[w], False)
    if w == "hundred":
        return ("hundred", 100, False)
    if w in _SCALE:
        return ("scale", _SCALE[w], False)
    if w in _ORD_UNITS:
        return ("unit", _ORD_UNITS[w], True)
    if w in _ORD_TEENS:
        return ("teen", _ORD_TEENS[w], True)
    if w in _ORD_TENS:
        return ("tens", _ORD_TENS[w], True)
    if w == "hundredth":
        return ("hundred", 100, True)
    if w == "thousandth":
        return ("scale", 1000, True)
    if w == "millionth":
        return ("scale", 10**6, True)
    return None


def _can_follow(last: str | None, kind: str, val: int, last_scale: int | None) -> bool:
    if last is None:
        return True
    if last in ("unit", "teen"):
        return kind in ("hundred", "scale")
    if last == "tens":
        return (kind == "unit" and val > 0) or kind == "scale"
    if last == "hundred":
        return kind in ("unit", "teen", "tens", "scale") and not (kind == "unit" and val == 0)
    if last == "scale":
        if kind == "scale":
            return last_scale is not None and val < last_scale
        return not (kind == "unit" and val == 0)
    return False


def _number_runs(words: list[str]) -> list[tuple[int, int, int, bool]]:
    """Greedy parse of a cluster of lowercase words into (start, end, value, is_ordinal)."""
    out = []
    i = 0
    while i < len(words):
        c0 = _classify(words[i])
        if c0 is None:
            i += 1
            continue
        # a spoken year: "eighteen sixty-four" is 1864, not the two numbers 18 and 64
        c1 = _classify(words[i + 1]) if i + 1 < len(words) else None
        if (c1 and not c0[2] and not c1[2] and c1[0] == "tens"
                and (c0[0] == "teen" or (c0[0] == "tens" and c0[1] == 20))):
            j = i + 2
            value = c0[1] * 100 + c1[1]
            c2 = _classify(words[j]) if j < len(words) else None
            if c2 and c2[0] == "unit" and not c2[2] and c2[1] > 0:
                value += c2[1]
                j += 1
            out.append((i, j, value, False))
            i = j
            continue
        total = current = 0
        last: str | None = None
        last_scale: int | None = None
        ordinal = False
        j = i
        while j < len(words):
            c = _classify(words[j])
            if c is None:
                nxt = _classify(words[j + 1]) if j + 1 < len(words) else None
                if (words[j] == "and" and last in ("hundred", "scale") and nxt
                        and nxt[0] in ("unit", "teen", "tens")):
                    j += 1
                    continue
                break
            kind, val, is_ord = c
            if not _can_follow(last, kind, val, last_scale):
                break
            if kind == "hundred":
                current = (current or 1) * 100
            elif kind == "scale":
                total += (current or 1) * val
                current = 0
                last_scale = val
            else:
                current += val
            last = kind
            j += 1
            if is_ord:
                ordinal = True
                break
        out.append((i, j, total + current, ordinal))
        i = max(j, i + 1)
    return out


# ------------------------------------------------------------------ scanning


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    fact: Fact
    from_word: bool = False


def _canon(s: str) -> str:
    try:
        d = Decimal(s.replace(",", ""))
    except InvalidOperation:
        return s
    return format(d.normalize(), "f")


def _month(name: str) -> int:
    return _MONTHS[name.lower()[:3]]


def _era_after(text: str, pos: int):
    m = _ERA.match(text, pos)
    if not m:
        return None, pos
    era = m.group("era").replace(".", "")
    return ("bc" if era.startswith("B") else "ad"), m.end()


def scan(raw: str, *, lower_months: bool = False) -> tuple[str, list[Span]]:
    """-> (prepped text, non-overlapping spans in text order). `lower_months` also reads a month name
    written in lowercase ("april 4, 1861"); used only for the text under test, never for a source
    (a lowercase "march 5 miles" in a source must not support a date). A lowercase "may" counts only
    inside a full month-day-year date, because "may" is a common word."""
    text = prep(raw)
    spans: list[Span] = []

    def free(s: int, e: int) -> bool:
        return all(e <= a.start or s >= a.end for a in spans)

    def add(s: int, e: int, fact: Fact, from_word: bool = False) -> bool:
        if free(s, e):
            spans.append(Span(s, e, fact, from_word))
            return True
        return False

    # ordered numeric forms first: 1861-03-04, 3/4/1861, 3:1
    for m in _P_ISO.finditer(text):
        y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
        if y > 0 and 1 <= mo <= 12 and 1 <= d <= 31:
            add(m.start(), m.end(), ("date", y, mo, d))
    for m in _P_SLASH_DATE.finditer(text):
        a, b, y = int(m.group("a")), int(m.group("b")), int(m.group("y"))
        full = len(m.group("y")) == 4 and y > 0
        if full and 12 < a <= 31 and 1 <= b <= 12:
            add(m.start(), m.end(), ("date", y, b, a))           # can only be day/month/year
        elif full and 12 < b <= 31 and 1 <= a <= 12:
            add(m.start(), m.end(), ("date", y, a, b))           # can only be month/day/year
        else:
            add(m.start(), m.end(), ("sdate", a, b, y))          # ambiguous: keep the order
    for m in _P_RATIO.finditer(text):
        add(m.start(), m.end(), ("ratio" if m.group("op") == ":" else "frac", int(m.group("a")), int(m.group("b"))))

    # dates, most specific first
    for kind, pat in (("date", _P_MDY), ("date", _P_DMY), ("my", _P_MY), ("md", _P_MD), ("md", _P_DM)):
        for m in pat.finditer(text):
            if not m.group("mon")[0].isupper():
                if not lower_months or (m.group("mon").lower() == "may" and pat is not _P_MDY):
                    continue
            mon = _month(m.group("mon"))
            d = int(m.group("d")) if "d" in pat.groupindex else 0
            if "d" in pat.groupindex and not 1 <= d <= 31:
                continue
            end = m.end()
            if "y" in pat.groupindex:
                y = int(m.group("y"))
                if y <= 0:
                    continue
                era, end = _era_after(text, end)
                if era == "bc":
                    y = -y
                fact = ("date", y, mon, d) if kind == "date" else ("my", y, mon)
            else:
                fact = ("md", mon, d)
            add(m.start(), end, fact)

    for m in _P_YEAR_ERA.finditer(text):
        era = m.group("era").replace(".", "")
        y = int(m.group("y"))
        add(m.start(), m.end(), ("year", -y if era.startswith("B") else y))
    for m in _P_AD_YEAR.finditer(text):
        add(m.start(), m.end(), ("year", int(m.group("y"))))
    for m in _P_DECADE.finditer(text):
        add(m.start(), m.end(), ("decade", int(m.group("y"))))
    for m in _P_DECADE_WORD.finditer(text):          # "the eighties": the century is not stated
        add(m.start(), m.end(), ("wdecade", _DECADE_WORDS[m.group("w").lower()]))
    for m in _P_PCT.finditer(text):
        add(m.start(), m.end(), ("pct", ("-" if m.group("sign") else "") + _canon(m.group("n"))))
    for m in _P_NUM.finditer(text):
        n = ("-" if m.group("sign") else "") + _canon(m.group("n"))
        if m.group("scale"):
            fact: Fact = ("num", _canon(str(Decimal(n) * _SCALE[m.group("scale").lower()])))
        elif m.group("ord"):
            fact = ("ord", int(Decimal(n))) if Decimal(n) == Decimal(n).to_integral_value() else ("num", n)
        else:
            fact = ("num", n)
        add(m.start(), m.end(), fact)

    # number words: cluster tokens joined by a single space or hyphen
    toks = [(m.start(), m.end(), m.group(0).lower()) for m in _WORD.finditer(text)]
    toks = [t for t in toks if free(t[0], t[1])]
    k = 0
    while k < len(toks):
        cluster = [toks[k]]
        while (k + len(cluster) < len(toks)
               and text[cluster[-1][1]:toks[k + len(cluster)][0]] in (" ", "-")
               and "'" not in toks[k + len(cluster)][2]):
            cluster.append(toks[k + len(cluster)])
        words = [t[2] for t in cluster]
        for a, b, value, ordinal in _number_runs(words):
            if b - a == 1 and words[a] == "one":
                continue            # "one" alone is a pronoun far more often than a quantity
            fact = ("ord", value) if ordinal else ("num", str(value))
            add(cluster[a][0], cluster[b - 1][1], fact, from_word=True)
        for t in cluster:
            if t[2] in _FRACTION_WORDS:
                add(t[0], t[1], ("word", t[2]), from_word=True)
        k += len(cluster)

    spans.sort(key=lambda s: s.start)
    return text, spans


def extract_facts(raw: str, *, ignore_small_words: bool = False, lower_months: bool = False) -> list[Fact]:
    """Non-name facts, deduplicated, in text order. `ignore_small_words` skips number WORDS up to
    ten ("name two causes"): used only for question prompts, never for answer keys."""
    _, spans = scan(raw, lower_months=lower_months)
    out: list[Fact] = []
    for s in spans:
        if ignore_small_words and s.from_word:
            v = s.fact[1]
            # cardinals up to ten are instructions ("name two causes"), and "first" is a connective;
            # any other ordinal ("the fifth battle") is a claim and is checked
            if (s.fact[0] == "num" and int(v) <= 10) or (s.fact[0] == "ord" and int(v) == 1):
                continue
        if s.fact not in out:
            out.append(s.fact)
    return out


def expand(fact: Fact) -> set[Fact]:
    """What a fact in the SOURCE also establishes."""
    out = {fact}
    k = fact[0]
    if k == "date":
        _, y, m, d = fact
        out |= {("my", y, m), ("md", m, d), ("num", str(d)), ("year", y)}
        if y > 0:                  # a BC year never supports the bare number: 500 BC is not "500"
            out.add(("num", str(y)))
    elif k == "my":
        if fact[1] > 0:
            out.add(("num", str(fact[1])))
    elif k == "md":
        out |= {("num", str(fact[2]))}
    elif k == "year":
        if fact[1] > 0:
            out.add(("num", str(fact[1])))
    elif k == "decade":
        out.add(("wdecade", fact[1] % 100))      # a passage's "1860s" supports "the sixties"
    elif k == "sdate":
        out |= {("num", str(fact[1])), ("num", str(fact[2])), ("num", str(fact[3]))}
    elif k in ("ratio", "frac"):
        out |= {("num", str(fact[1])), ("num", str(fact[2]))}
    # a percentage does NOT support the bare count: 40% of the colonists is not "40 colonists"
    return out


def available_facts(texts: list[str]) -> set[Fact]:
    out: set[Fact] = set()
    for t in texts:
        for f in extract_facts(t):
            out |= expand(f)
    return out


# ------------------------------------------------------------------ names

_STOP_INITIAL = frozenset("""
the a an in on at by for of to from with without within and but or nor so yet as if when while where
which who whom whose what why how that this these those there here it its he she they we you his her
their our your my him them us me some any all each every both either neither no not none many much
most more few several other another such same also then thus hence therefore however moreover
furthermore meanwhile although though because since after before during until unless whereas whether
restore finish supply insert replace provide indicate determine specify calculate rearrange sort label
decide write type enter mark circle underline paraphrase restate rewrite predict infer justify support
illustrate trace outline sequence rank rate assess argue relate connect locate spot recognize
recognise differentiate distinguish decide imagine suppose assume
once now today yesterday tomorrow later earlier soon according based given using use explain describe
identify name list state define compare contrast summarize summarise discuss analyze analyse evaluate
select choose pick complete fill match order arrange put read notice note look pay focus consider
think remember recall review answer question true false correct incorrect following above below is
are was were be been being am do does did have has had can could will would shall should may might
must let see find tell give show make take get go yes among between through into about against over
under per via upon across around toward towards chapter section passage paragraph page text reading
source quote according finally first next last often usually sometimes always never only even still
just very too really perhaps maybe indeed instead rather quite whatever whenever whichever
""".split())
_ALWAYS_SKIP = frozenset({"i", "i'm", "i'll", "i'd", "i've"})
_CONNECT = frozenset({"of", "the", "de", "la", "le", "von", "van", "der", "den", "du", "del", "da", "di", "al", "bin"})


# stop words that are also given names / month-like names: "Will Rogers", "Mark Twain", "May Day"
_NAME_LIKE_STOP = frozenset({"will", "may", "mark", "page"})


def _roman_i(text: str, prev, tok) -> bool:
    """A bare "I" right after a capitalized name token is the numeral (Napoleon I, World War I),
    not the pronoun."""
    return tok[2] == "I" and text[prev[1]:tok[0]] == " "


def _strip_poss(tok: str) -> str:
    low = tok.lower()
    if low.endswith("'s"):
        return tok[:-2]
    return tok


def _sentence_initial(text: str, start: int) -> bool:
    j = start
    while j > 0 and text[j - 1] in " \"'([{":
        j -= 1
    return j == 0 or text[j - 1] in ".!?:-*\u2022"


def name_tokens(raw: str) -> list[str]:
    """All word tokens, normalized and possessive-stripped (for contiguous-phrase checks)."""
    text = prep(raw)
    return [normalize(_strip_poss(m.group(0))) for m in _WORD.finditer(text)]


def extract_names(raw: str) -> list[tuple[str, ...]]:
    """Capitalized names as tuples of normalized tokens. Multi-word runs (also across of/the/de) stay
    together. Sentence-initial common words are skipped via a stopword list; any other
    sentence-initial capitalized word is treated as a name (fail closed)."""
    text, spans = scan(raw)
    toks = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text)]
    toks = [t for t in toks if all(t[1] <= s.start or t[0] >= s.end for s in spans)]

    def capital(t) -> bool:
        return t[2][0].isupper()

    def gap_ok(a, b) -> bool:
        return all(ch in " -" for ch in text[a[1]:b[0]])

    out: list[tuple[str, ...]] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        low = t[2].lower()
        if not capital(t) or low in _ALWAYS_SKIP:
            i += 1
            continue
        if low in _STOP_INITIAL and _sentence_initial(text, t[0]):
            nxt = toks[i + 1] if i + 1 < n else None
            first_name = (low in _NAME_LIKE_STOP and nxt is not None and capital(nxt) and gap_ok(t, nxt)
                          and nxt[2].lower() not in _STOP_INITIAL and nxt[2].lower() not in _ALWAYS_SKIP)
            if not first_name:        # "Will Rogers", "Mark Twain": a given name, not a stop word
                i += 1
                continue
        j = i
        while True:
            if j + 1 < n and capital(toks[j + 1]) and gap_ok(toks[j], toks[j + 1]) \
                    and (toks[j + 1][2].lower() not in _ALWAYS_SKIP
                         or _roman_i(text, toks[j], toks[j + 1])):
                j += 1
                continue
            m = j + 1
            conns = 0
            while (m < n and conns < 2 and toks[m][2].lower() in _CONNECT and not capital(toks[m])
                   and gap_ok(toks[m - 1], toks[m])):
                m += 1
                conns += 1
            if conns and m < n and capital(toks[m]) and gap_ok(toks[m - 1], toks[m]):
                j = m
                continue
            break
        phrase = tuple(normalize(_strip_poss(x[2])) for x in toks[i:j + 1])
        if phrase not in out:
            out.append(phrase)
        i = j + 1
    return out


def contains_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    if n == 0:
        return True
    return any(tuple(tokens[i:i + n]) == phrase for i in range(len(tokens) - n + 1))


# ------------------------------------------------------------------ the support check


def _fmt_fact(f: Fact) -> str:
    k = f[0]
    if k == "num":
        return f[1]
    if k == "pct":
        return f"{f[1]}%"
    if k == "ord":
        return f"ordinal {f[1]}"
    if k == "date":
        return f"date {abs(f[1])}{' BC' if f[1] < 0 else ''}-{f[2]:02d}-{f[3]:02d}"
    if k == "my":
        return f"month-year {abs(f[1])}-{f[2]:02d}"
    if k == "md":
        return f"month-day {f[1]:02d}-{f[2]:02d}"
    if k == "year":
        return f"year {abs(f[1])}{' BC' if f[1] < 0 else ' AD'}"
    if k == "sdate":
        return f"date {f[1]}/{f[2]}/{f[3]}"
    if k == "ratio":
        return f"{f[1]}:{f[2]}"
    if k == "frac":
        return f"{f[1]}/{f[2]}"
    if k == "decade":
        return f"the {f[1]}s"
    if k == "wdecade":
        return f"the '{f[1]}s"
    return str(f[1])


_DATE_KINDS = frozenset({"date", "my", "md", "year", "decade", "wdecade", "sdate"})


def missing_facts(text: str, source_texts: list[str], *, ignore_small_words: bool = False,
                  dates_only: bool = False) -> list[str]:
    """Facts (numbers, years, dates, names) in `text` that the source texts do not contain.
    Empty list = every fact is supported. `dates_only` checks dates, years and decades and nothing
    else: for numeric, math and code prompts, whose numbers and names are problem parameters."""
    have = available_facts(source_texts)
    src_tokens: list[str] = []
    for s in source_texts:
        src_tokens.extend(name_tokens(s))
        src_tokens.append("\x00")        # phrases never span two sources
    missing = [_fmt_fact(f) for f in extract_facts(text, ignore_small_words=ignore_small_words,
                                                   lower_months=True)
               if f not in have and (not dates_only or f[0] in _DATE_KINDS
                                     or (f[0] == "num" and re.fullmatch(r"[12]\d{3}", f[1])))]
    if dates_only:
        return missing
    for phrase in extract_names(text):
        if not contains_phrase(src_tokens, phrase):
            missing.append(" ".join(phrase))
    return missing


# ------------------------------------------------------------------ key tokens (gate c)

_KEYSTOP = frozenset("""
the a an of in on at by for to from with and or but is are was were be been it its this that these
those as his her their he she they them which who whom what when where not no also than then so such
into over under after before during about between through up down out off had has have did does do
can could would should will may might shall must more most other any all some each both own very
just only
""".split())


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words_outside(text: str, spans: list[Span]) -> list[str]:
    out = []
    for m in _WORD.finditer(text):
        if all(m.end() <= s.start or m.start() >= s.end for s in spans):
            out.append(_stem(normalize(_strip_poss(m.group(0)))))
    return out


# Quantifiers and negators change what a sentence claims. A KEY that uses one must find it in the quote.
_QUANTIFIERS = frozenset("""
all every each everyone everybody everything everywhere none no nobody nothing nowhere neither nor
only always never some someone somebody something somewhere sometimes most mostly many few several
any anyone anything both either often usually entire whole not without
""".split())


def _variants(w: str) -> set[str]:
    """Cheap inflection folding so 'ratified' finds 'ratify' and 'freed' finds 'free'."""
    out = {w}
    for suf, add in (("ies", "y"), ("ied", "y"), ("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e"),
                     ("es", ""), ("ly", ""), ("s", ""), ("d", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            out.add(w[:-len(suf)] + add)
    return out


def unsupported_words(raw: str, source_texts: list[str]) -> list[str]:
    """Content words of `raw` (not numbers or dates, not function words) that no source text contains,
    after folding plurals and verb endings; plus any quantifier or negator (all, only, never, no...)
    of `raw` that the sources do not use. Used for the correct multiple-choice option: a key may not
    add words the quote never says."""
    text, spans = scan(raw)
    plain: set[str] = set()
    support_vars: set[str] = set()
    for src in source_texts:
        st, sp = scan(src)
        for m in _WORD.finditer(st):
            if all(m.end() <= s.start or m.start() >= s.end for s in sp):
                support_vars |= _variants(normalize(_strip_poss(m.group(0))))
            plain.add(normalize(_strip_poss(m.group(0))))
    out: list[str] = []
    for m in _WORD.finditer(text):
        w = normalize(_strip_poss(m.group(0)))
        if not w or w in out:
            continue
        if w in _QUANTIFIERS:
            if w not in plain:
                out.append(w)
            continue
        if w in _KEYSTOP or not all(m.end() <= s.start or m.start() >= s.end for s in spans):
            continue
        if not (_variants(w) & support_vars):
            out.append(w)
    return out


# ------------------------------------------------------------------ negation

_NEG_WORDS = frozenset("""
not never no nor neither without cannot nobody nothing none unable fail fails failed failing
refuse refuses refused refusing
""".split())
_CLAUSE_SPLIT = re.compile(r"[;:!?]|\.(?=\s|$)|,(?=\s|$)|\s(?:and|but|while|although|though|whereas|yet|because"
                           r"|which|who|whom|whose)\s", re.I)


def has_negation(raw: str) -> bool:
    """True when `raw` contains not, never, no, nor, neither, without, n't, fail(ed), refus(ed) ..."""
    for m in _WORD.finditer(prep(raw)):
        w = normalize(m.group(0))
        if w in _NEG_WORDS or w.endswith("n't"):
            return True
    return False


def _content(raw: str) -> tuple[set[str], set[str]]:
    """(inflection-folded content words, fact reprs) of a short text, negators and function words out."""
    text, spans = scan(raw)
    words: set[str] = set()
    for m in _WORD.finditer(text):
        if not all(m.end() <= s.start or m.start() >= s.end for s in spans):
            continue
        w = normalize(_strip_poss(m.group(0)))
        if w and w not in _KEYSTOP and w not in _NEG_WORDS and not w.endswith("n't"):
            words |= _variants(w)
    return words, {repr(s.fact) for s in spans}


def negated_clauses(probes: list[str], quotes: list[str]) -> list[str]:
    """Clauses of the quotes whose NEGATED part (the words after not / never / no / n't / without / failed
    / refused) contains a content word or fact of one of the `probes` (the answer key and the question).
    Such a key asserts what the quote denies. The subject before the negator is not in scope:
    "The Stamp Act wasn't popular" does not negate "Stamp Act"."""
    pw: set[str] = set()
    pf: set[str] = set()
    for t in probes:
        w, f = _content(t)
        pw |= w
        pf |= f
    hits: list[str] = []
    for q in quotes:
        for clause in _CLAUSE_SPLIT.split(prep(q)):
            if not clause:
                continue
            for m in _WORD.finditer(clause):
                w = normalize(m.group(0))
                if w in _NEG_WORDS or w.endswith("n't"):
                    sw, sf = _content(clause[m.end():])
                    if (pw & sw) or (pf & sf):
                        hits.append(clause.strip()[:60])
                    break
    return hits


def key_tokens(raw: str) -> set[str]:
    """Content tokens of a short text (stemmed words minus function words, plus fact tokens)."""
    text, spans = scan(raw)
    toks = {w for w in _words_outside(text, spans) if w and w not in _KEYSTOP}
    toks |= {"\x01" + repr(s.fact) for s in spans}
    return toks


def support_tokens(texts: list[str]) -> set[str]:
    out: set[str] = set()
    for raw in texts:
        text, spans = scan(raw)
        out |= set(_words_outside(text, spans))
        for s in spans:
            for f in expand(s.fact):
                out.add("\x01" + repr(f))
    return out


# ------------------------------------------------------------------ dates for ordering


def date_span(raw: str) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
    """(earliest, latest) (year, month, day) the date-like thing in `raw` can mean: a year covers all its
    months, a decade covers ten years. None when unsure. Used to tell whether two dates overlap."""
    _, spans = scan(raw)
    for s in spans:
        f = s.fact
        if f[0] == "date":
            return (f[1], f[2], f[3]), (f[1], f[2], f[3])
        if f[0] == "my":
            return (f[1], f[2], 0), (f[1], f[2], 99)
        if f[0] == "year":
            return (f[1], 0, 0), (f[1], 99, 99)
        if f[0] == "decade":
            lo, hi = sorted((f[1], f[1] + 9))
            return (lo, 0, 0), (hi, 99, 99)
        if f[0] == "num" and "." not in f[1] and 1 <= int(f[1]) <= 2999:
            return (int(f[1]), 0, 0), (int(f[1]), 99, 99)
    return None


def date_sort_key(raw: str) -> tuple[int, int, int] | None:
    """(year, month, day) of the first date-like thing in `raw`, BC years negative. None when unsure."""
    _, spans = scan(raw)
    for s in spans:
        f = s.fact
        if f[0] == "date":
            return (f[1], f[2], f[3])
        if f[0] == "my":
            return (f[1], f[2], 0)
        if f[0] in ("year", "decade"):
            return (f[1], 0, 0)
        if f[0] == "num" and "." not in f[1] and 1 <= int(f[1]) <= 2999:
            return (int(f[1]), 0, 0)
    return None
