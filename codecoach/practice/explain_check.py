"""Is a drill's explanation safe to show? Pure text checks: no model, no sandbox.

A drill's answer key is the program's real output. Its explanation is the model's prose, written BEFORE the program
ran, so it can name a different number than the program printed. Seen on real drills from a small free model:
"two positive numbers are read before the 0, so the output is 2" under a real output of 1.

`explanation_problem` finds what can be decided from the text alone:
  E1  a number the explanation says is printed ("the output is 2", "prints 98", "8, which is printed",
      "prints the total, which is 8") is not in the real output;
  E2  the last number of a sentence about printing occurs nowhere: not in the real output, the program or its input
      ("Adding those gives 98, so the program prints exactly that number.");
  E3  it says the output is spread over lines ("one per line") and the real output is a single line;
  E4  it lists, in a row, exactly as many numbers as the program printed (four or more), and they are other numbers
      ("produces the sequence 1, 3, 6, 9, 4.5, 5.5, 16.5, 12.5" under a real 1 3 6 3 1.5 2.5 7.5 3.5).
It is a filter for plain contradictions, NOT a proof that an explanation is right. Known to pass: a wrong value
inside a chain of equations when the sentence ends on a right one, wrong reasoning that ends on the right number,
a wrong number written as a word. An explanation with a problem is left out (`shown_explanation`); the drill
itself stays, because its program and its answer key are real.

`real_line_breaks` repairs an input the model escaped twice (the two characters backslash and n where a line break
was meant), so the learner is not asked to predict what a stray backslash does.
"""

from __future__ import annotations

import re

LEFT_OUT = ("CodeCoach left the model's explanation out of this drill, because it did not agree with what the program "
            "really printed. The output shown above is the real one.")

_NUMP = r"-?\d+(?:\.\d+)?(?![A-Za-z_\d]|\.\d)"                  # a whole number token, never the front of a longer one
_NUM = re.compile(r"(?<![A-Za-z_\d.])" + _NUMP)                 # numbers in prose: `i32`, `v1.2` and `2nd` are not numbers
_LOOSE = re.compile(r"\d+(?:\.\d+)?")                           # every digit run: what the program, input and output hold
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_PRINT = re.compile(r"\b(?:print\w*|output\w*|display\w*|echo\w*|puts)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# "prints 3 lines" counts things; it does not say that 3 is printed.
_COUNTED = (r"(?!\s*(?:more\s+|positive\s+|negative\s+|non-?\w+\s+|different\s+)?(?:lines?|times?|rows?|columns?|"
            r"characters?|chars?|numbers?|values?|items?|words?|elements?|digits?|letters?|stars?|spaces?|strings?|"
            r"entries|iterations?|things?)\b)")
_SAID_PRINTED = (
    re.compile(r"\boutput\s+(?:is|was|will\s+be|would\s+be|should\s+be)\s*:?\s*[`'\"]?(" + _NUMP + ")" + _COUNTED, re.I),
    re.compile(r"\b(?:prints?|printed|printing|outputs?|outputting|displays?|displayed|echo(?:es)?)\s+(?:out\s+)?"
               r"(?:exactly\s+|only\s+|just\s+)?(?:the\s+(?:number|value|total|sum|count|result|answer)\s+)?[`'\"]?("
               + _NUMP + ")" + _COUNTED, re.I),
    re.compile(r"(?<![A-Za-z_\d.])(" + _NUMP + r")[`'\"]?,?\s+(?:which\s+)?(?:is|gets|are)\s+(?:then\s+|finally\s+)?"
               r"(?:printed|output|displayed|shown)\b", re.I),
)
_WHICH_IS = re.compile(r"\bwhich\s+is\s+[`'\"]?(" + _NUMP + r")[`'\"]?\s*[.!]?\s*$", re.I)   # only in a printing sentence
_NOT_A_VALUE = re.compile(r"\b(?:status|code)\s+(?:of\s+)?-?\d+", re.I)        # "exits with status 0" prints nothing
_LISTED = re.compile(r"(?<![A-Za-z_\d.])" + _NUMP + r"(?:\s*,\s*(?:and\s+)?" + _NUMP + r"|\s+and\s+" + _NUMP + r"){3,}")  # 4 or more in a row
_OVER_LINES = re.compile(r"\b(?:one\s+per\s+line|each\s+on\s+(?:its|their|a)\s+(?:own\s+|new\s+|separate\s+)?line|"
                         r"on\s+(?:separate|different)\s+lines)\b", re.I)


def _canon(tok: str) -> str:
    """'-08' -> '8', '3.50' -> '3.5', '2.0' -> '2': the sign and padding never decide a mismatch."""
    t = tok.lstrip("-")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    t = t.lstrip("0") or "0"
    return "0" + t if t.startswith(".") else t


def _held(text: str) -> set[str]:
    """Every number `text` holds, generously (digit runs inside words too, and 1,000 as 1000)."""
    text = text or ""
    return {_canon(m) for m in _LOOSE.findall(text)} | {_canon(m) for m in _LOOSE.findall(_THOUSANDS.sub("", text))}


def _lines(stdout: str) -> list[str]:
    lines = [ln.rstrip(" ") for ln in (stdout or "").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def explanation_problem(code: str, stdin: str, stdout: str, explanation: list[str]) -> str | None:
    """A short reason (for logs and tests, never shown to the learner), or None when nothing is provably off."""
    printed = _held(stdout)
    lines = _lines(stdout)
    # Counts a true sentence may name without the number being written anywhere: lines, items, items or characters per line.
    counts = {len(lines), len((stdout or "").split())} | {len(ln) for ln in lines} | {len(ln.split()) for ln in lines}
    anywhere = printed | _held(code) | _held(stdin) | {str(c) for c in counts}
    in_order = [_canon(t) for t in _NUM.findall(stdout or "")]           # the numbers the program printed, in order
    text = " ".join(s.strip() for s in explanation if isinstance(s, str))
    for sentence in _SENTENCE.split(_THOUSANDS.sub("", text)):
        about_printing = bool(_PRINT.search(sentence))
        claims = [m.group(1) for rx in _SAID_PRINTED for m in rx.finditer(sentence)]
        if about_printing:
            claims += [m.group(1) for m in _WHICH_IS.finditer(sentence)]
        for tok in claims:
            if _canon(tok) not in printed:
                return f"E1: says {tok} is printed; the real output does not hold it"
        if about_printing:
            nums = _NUM.findall(_NOT_A_VALUE.sub(" ", sentence))
            if nums and _canon(nums[-1]) not in anywhere:
                return f"E2: {nums[-1]} is in neither the real output, the program nor its input"
        for run in _LISTED.finditer(sentence):
            listed = [_canon(t) for t in _NUM.findall(run.group(0))]
            if len(listed) == len(in_order) and listed != in_order:
                return "E4: lists as many values as the program printed, and they are not the printed ones"
    if len(lines) == 1 and _OVER_LINES.search(text):
        return "E3: says the output is spread over lines; the real output is one line"
    return None


def shown_explanation(rec: dict) -> list[str]:
    """The explanation to hand to the learner for drill record `rec`: the model's own, or the left-out note."""
    expl = [s for s in (rec.get("explanation") or []) if isinstance(s, str) and s.strip()]
    if not expl:
        return []
    try:
        problem = explanation_problem(rec.get("code") or "", rec.get("stdin") or "", rec.get("stdout") or "", expl)
    except Exception:                                    # a check that cannot run leaves the prose as it was before
        return expl
    return [LEFT_OUT] if problem else expl


def real_line_breaks(stdin: str) -> str:
    """Input with the two characters backslash-n (or -t, -r) turned into the real ones. Anything else is untouched."""
    if "\\" not in stdin:
        return stdin
    return stdin.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "").replace("\\t", "\t")
