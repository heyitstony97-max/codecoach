"""The original text of a document and where each passage sits in it. No LLM, no network.

backfill_doc_text: courses imported before offsets existed. Aligns the stored passages, in order,
  against a file the learner still has (whitespace-insensitive) and writes text + spans only when
  EVERY passage aligns.
reconstruct_doc: a course with no stored text. Rebuilds a readable text from the passages
  (section headings from their locations, paragraphs separated by blank lines) with exact spans.
"""

from .models import Passage
from .passages import normalize_newlines
from .store import Store

SNIPPET = 80


class AlignmentError(ValueError):
    """A passage was not found in the text. Nothing was written."""

    def __init__(self, message: str, passage_id: str = "", aligned: int = 0, total: int = 0):
        super().__init__(message)
        self.passage_id = passage_id
        self.aligned = aligned
        self.total = total


def _squeeze(text: str) -> tuple[str, list[int]]:
    """The text without any whitespace, plus the original index of each kept character."""
    chars: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            where.append(i)
    return "".join(chars), where


def align_passages(passages: list[Passage], text: str) -> dict[str, tuple[int, int]]:
    """Span of every passage inside `text`, in order, ignoring whitespace. Raises AlignmentError naming
    the first passage that cannot be found after the previous one."""
    squeezed, where = _squeeze(text)
    spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for n, p in enumerate(passages):
        needle = "".join(p.text.split())
        at = squeezed.find(needle, cursor) if needle else -1
        if at < 0:
            snippet = " ".join(p.text.split())[:SNIPPET]
            raise AlignmentError(
                f"passage {p.id} ({p.location}) was not found in the text after passage "
                f"{n} of {len(passages)}: {snippet!r}. Nothing was written (aligned {n}/{len(passages)}).",
                passage_id=p.id, aligned=n, total=len(passages))
        spans[p.id] = (where[at], where[at + len(needle) - 1] + 1)
        cursor = at + len(needle)
    return spans


def backfill_doc_text(store: Store, doc_id: str, text: str, *, dry_run: bool = False) -> dict:
    """Save `text` as the document text and the offsets of all its passages, only if 100% align.
    Otherwise writes nothing and raises AlignmentError. Returns {doc_id, aligned, total, chars, written}."""
    doc = store.get_doc(doc_id)
    if doc is None:
        raise AlignmentError(f"no document with id {doc_id!r}")
    passages = store.list_passages(doc_id=doc_id)
    if not passages:
        raise AlignmentError(f"document {doc_id!r} has no passages to align")
    text = normalize_newlines(text)
    spans = align_passages(passages, text)
    if not dry_run:
        store.set_doc_text(doc_id, text, spans)
    return {"doc_id": doc_id, "aligned": len(spans), "total": len(passages), "chars": len(text),
            "written": not dry_run}


def reconstruct_doc(passages: list[Passage], kind: str = "text") -> tuple[str, dict[str, tuple[int, int]]]:
    """Readable text for a doc whose original was never stored. Offsets are exact for the rebuilt text."""
    out: list[str] = []
    size = 0
    spans: dict[str, tuple[int, int]] = {}
    heading: str | None = None
    for p in passages:
        h = p.location.rsplit(", para", 1)[0] if ", para" in p.location else None
        if h and h != heading:
            line = f"## {h}" if kind == "markdown" else h
            out.append(line)
            size += len(line) + 2
        heading = h if h else heading
        spans[p.id] = (size, size + len(p.text))
        out.append(p.text)
        size += len(p.text) + 2
    return "\n\n".join(out), spans
