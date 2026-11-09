"""Which subject a course belongs to (History, Programming, ...). Cheap and fail-safe.

Order: an explicit subject wins; else a course with a code language is Programming; else ONE small
structured LLM call picks from SUBJECTS. Any error, timeout or off-list answer gives None (the UI
shows "Uncategorized"). Nothing here may fail an import.
"""

from __future__ import annotations

from pydantic import BaseModel

from codecoach.core.llm import LLMPort, structured_call

SUBJECTS = ["History", "Programming", "Math", "Science", "Language & Writing", "Business", "Other"]
MAX_SUBJECT_CHARS = 40
SAMPLE_CHARS = 1500
SUBJECT_TIMEOUT_S = 180
SUBJECT_MAX_TOKENS = 400   # a reasoning model spends its first tokens thinking; 60 left no room for the answer


class _Pick(BaseModel):
    subject: str


def clean_subject(value: object) -> str | None:
    """Trim, collapse inner whitespace, cap at 40 chars; empty -> None."""
    if not isinstance(value, str):
        return None
    s = " ".join(value.split())[:MAX_SUBJECT_CHARS].strip()
    return s or None


def _on_list(answer: str) -> str | None:
    key = " ".join((answer or "").split()).casefold().rstrip(".")
    for s in SUBJECTS:
        if s.casefold() == key:
            return s
    return None


def infer_subject(llm: LLMPort | None, *, title: str, text: str, explicit: str | None = None,
                  code_language: str | None = None) -> str | None:
    chosen = clean_subject(explicit)
    if chosen:
        return chosen
    if code_language:
        return "Programming"
    if llm is None:
        return None
    try:
        pick = structured_call(
            llm, _Pick,
            instructions=("Pick the one subject that best describes a course. Answer with a JSON object "
                          '{"subject": "<one of: ' + ", ".join(SUBJECTS) + '>"}. Use exactly one of those '
                          "words; use Other if none fits."),
            text=f"Title: {title}\n\nStart of the materials:\n{(text or '')[:SAMPLE_CHARS]}",
            purpose="codecoach.subject", retries=1, max_tokens=SUBJECT_MAX_TOKENS, timeout=SUBJECT_TIMEOUT_S)
        return _on_list(pick.subject)
    except Exception:
        return None
