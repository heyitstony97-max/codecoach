"""Shared test helpers: fixture loading and a scripted fake model that reads the real prompt."""

import re
import tempfile
from pathlib import Path

from codecoach.core.llm import FakeLLM
from codecoach.core.store import Store

FIX = Path(__file__).parent / "fixtures"
HISTORY = (FIX / "kingdom_history.txt").read_text()
OUTLINE = (FIX / "outline_c_basics.md").read_text()

_ID = re.compile(r"^\[([^\]]+)\] ", re.M)
_VERBS = ["identify", "define", "explain", "order", "compare"]


def passage_ids(prompt: str) -> list[str]:
    return _ID.findall(prompt)


def section_title(prompt: str) -> str:
    m = re.search(r"^Section: (.*)$", prompt, re.M)
    return m.group(1) if m else "?"


def objective_reply(prompt: str, n: int = 2, kind: str = "reading") -> dict:
    """n objectives, each citing a real passage of the section shown in the prompt."""
    ids = passage_ids(prompt)
    title = section_title(prompt)
    return {"objectives": [
        {"verb": _VERBS[i % len(_VERBS)], "statement": f"Objective {i + 1} for {title}", "kind": kind,
         "passage_ids": [ids[i % len(ids)]]} for i in range(n)]}


def scripted(*, per_section=objective_reply, prereqs=None) -> FakeLLM:
    """FakeLLM whose section replies come from per_section(prompt) and prerequisite reply from
    prereqs(prompt) (default: no edges, which triggers the in-order fallback)."""
    def fn(purpose: str, text: str):
        if purpose == "codecoach.import.prereqs":
            return prereqs(text) if prereqs else {"edges": []}
        return per_section(text)
    return FakeLLM(fn)


_DIRS: list = []   # keep temp dirs alive for the test session


def new_store() -> Store:
    d = tempfile.TemporaryDirectory(prefix="codecoach-importer-")
    _DIRS.append(d)
    return Store(Path(d.name) / "codecoach.db")
