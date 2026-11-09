"""Which programming language a course teaches, guessed from its title and text.

Code-exercise generation is forced to this language, so a C course never gets Python items.
Returns None when the signals are absent or tie: generation then accepts either language.
"""

from __future__ import annotations

import re

# case-insensitive tool/API words
_C_WORDS = re.compile(r"\b(?:gcc|printf|scanf|clang|malloc)\b|#\s*include\b", re.I)
# the letter C as a language name is case-sensitive ("c" in prose is not the language)
_C_NAME = re.compile(r"\bC\s+(?:programming|language|basics|course|compiler|code|programs?|tutorial|primer)\b"
                     r"|\b(?:learn(?:ing)?|in|the|to)\s+C\b(?!\+\+|#)")
_PY = re.compile(r"\bpython\d?\b|(?m:^\s*def\s+\w+\s*\()|\bpip\s+install\b|\bpytest\b", re.I)


def infer_code_language(*texts: str) -> str | None:
    blob = "\n".join(t for t in texts if t)
    c = len(_C_WORDS.findall(blob)) + len(_C_NAME.findall(blob))
    py = len(_PY.findall(blob))
    if c > py:
        return "c"
    if py > c:
        return "python"
    return None
