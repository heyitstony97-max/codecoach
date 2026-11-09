"""The fixed, checkable objective verbs and the item formats each one may be tested with.

a2a (generators) imports VERBS, VERB_FORMATS and allowed_formats. Every objective's `verb` is one of
VERBS: a free-form verb ("appreciate", "understand") cannot be checked, so the importer rejects it.
"""

VERBS: tuple[str, ...] = (
    "identify", "define", "order", "compare", "explain",
    "compute", "trace", "write-code", "solve", "convert",
)

# verb -> item formats that can test it (core.models Item.format values), most natural first
VERB_FORMATS: dict[str, tuple[str, ...]] = {
    "identify": ("mc", "cloze", "match"),
    "define": ("cloze", "short", "mc", "match"),
    "order": ("order",),
    "compare": ("mc", "match", "short"),
    "explain": ("explain", "short"),
    "compute": ("math", "numeric"),
    "trace": ("mc", "short", "code"),
    "write-code": ("code",),
    "solve": ("math", "numeric"),
    "convert": ("numeric", "math"),
}

# objective kind -> formats that make sense for it
KIND_FORMATS: dict[str, tuple[str, ...]] = {
    "reading": ("mc", "cloze", "order", "match", "short", "explain"),
    "code": ("code", "mc", "short"),
    "math": ("math", "numeric", "mc"),
    "science": ("numeric", "math", "mc", "short"),
}

# verbs whose answer is computed by code: a "reading" objective using one becomes kind "math"
COMPUTED_VERBS = ("compute", "solve", "convert")


def normalize_verb(raw: str) -> str:
    """'Write code', 'write_code', ' IDENTIFY ' -> canonical form (not validated)."""
    return raw.strip().lower().replace("_", "-").replace(" ", "-")


def allowed_formats(verb: str, kind: str = "reading") -> tuple[str, ...]:
    """Formats allowed for this verb on this kind of objective, in preference order.
    Raises KeyError for a verb outside VERBS."""
    by_verb = VERB_FORMATS[normalize_verb(verb)]
    ok = tuple(f for f in by_verb if f in KIND_FORMATS.get(kind, ()))
    if ok:
        return ok
    return ("explain",) if kind == "reading" else by_verb


# words that make a "trace" objective a program-tracing one rather than a process in a history text
_CODE_HINTS = ("`", "printf", "scanf", "gcc", "compile", "program", "function", "loop", "variable", "pointer",
               "array", "string", "integer", "code", "main(", "int ", "char ", "return value", "output of")


def looks_like_code(*texts: str) -> bool:
    """True when the text talks about programs: used to turn a model's \"trace\" objective into a code one."""
    blob = " ".join(texts).lower()
    return any(h in blob for h in _CODE_HINTS)
