"""The hint ladder.

Level 1 is a nudge, level 2 a stronger nudge, level 3 the last step:
- items grounded in the learner's materials: level 1 names the section, level 2 the paragraph, level 3 shows
  the paragraph with the key sentence's span to highlight
- every other item: level 3 is a worked example of a DIFFERENT item for the same objective

The current item's answer is never given before 2 real attempts. Every hint text is run through a leak
guard (string containment of every answer-key string); a leaking text is replaced by a plainer one.
Level 3 of a material-grounded item is locked until 2 attempts, because the paragraph holds the answer.
"""

import re
from dataclasses import dataclass

from codecoach.core.models import (ClozePayload, CodePayload, Hint, Item, MathPayload, MatchPayload, MCPayload,
                                NumericPayload, OrderPayload, ShortPayload, Span)
from codecoach.core.store import Store
from codecoach.core.text import normalize

REAL_ATTEMPTS_FOR_ANSWER = 2
_READING_FORMATS = {"mc", "cloze", "order", "match", "short", "explain"}
_PARA = re.compile(r",?\s*para \d+\s*$")
LAST_RESORT = "Take another careful look at the question."


@dataclass(frozen=True)
class HintResult:
    hint: Hint
    location: str | None = None
    passage_text: str | None = None     # level 3 of a grounded item, once unlocked
    locked: bool = False                # level 3 asked before 2 real attempts
    attempts: int = 0


# ------------------------------------------------------------------ answer keys

def _nums(x: float) -> list[str]:
    out = {repr(x), f"{x:g}", f"{x:.6g}"}
    if float(x).is_integer():
        out.add(str(int(x)))
    return sorted(out)


def answer_keys(item: Item) -> list[str]:
    """Every string that would give the answer away."""
    p = item.payload
    if isinstance(p, MCPayload):
        return [p.options[p.correct_index]]
    if isinstance(p, ClozePayload):
        return [p.answer, *p.aliases]
    if isinstance(p, OrderPayload):
        labels = [p.events[i].label for i in p.correct_order]
        return [e.date_text for e in p.events] + [sep.join(labels) for sep in (", ", " -> ", " then ", " ")]
    if isinstance(p, MatchPayload):
        return [f"{m.left}{sep}{m.right}" for m in p.pairs for sep in (": ", " - ", " -> ", " = ", " ")]
    if isinstance(p, ShortPayload):
        return list(p.required_terms)
    if isinstance(p, CodePayload):
        keys = [p.reference_solution.strip()]
        keys += [ln.strip() for ln in p.reference_solution.splitlines() if len(ln.strip()) >= 12]
        keys += [t.expected_stdout.strip() for t in p.hidden_tests if t.expected_stdout and len(t.expected_stdout.strip()) >= 3]
        return keys
    if isinstance(p, MathPayload):
        return [p.given_answer] if p.given_answer else []
    if isinstance(p, NumericPayload):
        return _nums(p.answer_value)
    return []


def leaks(text: str | None, keys: list[str]) -> bool:
    if not text:
        return False
    t = normalize(text)
    for k in keys:
        k = normalize(k)
        if k and re.search(r"(?<![\w.])" + re.escape(k) + r"(?![\w])", t):
            return True
    return False


def answer_text(item: Item) -> str:
    """A readable answer, used only for a worked example on a DIFFERENT item."""
    p = item.payload
    if isinstance(p, MCPayload):
        return p.options[p.correct_index]
    if isinstance(p, ClozePayload):
        return p.answer
    if isinstance(p, OrderPayload):
        return ", ".join(f"{p.events[i].label} ({p.events[i].date_text})" for i in p.correct_order)
    if isinstance(p, MatchPayload):
        return "; ".join(f"{m.left} -> {m.right}" for m in p.pairs)
    if isinstance(p, ShortPayload):
        return "A good answer mentions: " + ", ".join(p.required_terms)
    if isinstance(p, CodePayload):
        return p.reference_solution.strip()
    if isinstance(p, MathPayload):
        return p.given_answer or ""
    if isinstance(p, NumericPayload):
        return f"{p.answer_value:g} {p.unit}".strip()
    return ""


def real_attempts(store: Store, item_id: str) -> int:
    def real(r) -> bool:
        return r not in (None, "", [], {})
    return sum(1 for a in store.list_attempts(item_id=item_id) if real(a.response))


# ------------------------------------------------------------------ math: the op and the rule, from the item's data

_FUN = re.compile(r"\b(?:sin|cos|tan|cot|sec|csc|exp|log|ln|sqrt|asin|acos|atan)\s*\(")


def _terms(expr: str) -> int:
    depth, n = 0, 1
    for i, ch in enumerate(expr):
        depth += ch in "(" and 1 or ch in ")" and -1 or 0
        if depth == 0 and ch in "+-" and i > 0 and expr[i - 1] not in "*/^(eE" and expr[:i].strip():
            n += 1
    return n


def math_rule(p: MathPayload) -> tuple[str, str]:
    """(level-1 hint, level-2 hint) naming the op and the rule for THIS expression. Never states the answer."""
    e, v = p.expr, p.var or "x"
    if "=" in e and p.op == "solve":
        e = e.replace("=", "-(") + ")"
    div = bool(re.search(rf"/\s*\(?[^)]*\b{re.escape(v)}\b", e))
    prod = bool(re.search(rf"\b{re.escape(v)}\b[^+\-]*\*[^+\-]*(?:\b{re.escape(v)}\b|{_FUN.pattern})|{_FUN.pattern}[^+\-]*\*\s*{re.escape(v)}", e))
    nested = bool(re.search(rf"{_FUN.pattern}[^)]*[*^/+\-][^)]*\)|\([^)]*\b{re.escape(v)}\b[^)]*\)\s*(?:\*\*|\^)", e))
    many = _terms(e) > 1
    if p.op == "diff":
        if div:
            return ("Differentiate: use the quotient rule (bottom times the derivative of the top, minus top times the derivative of the bottom, over the bottom squared).",
                    "Name the top and the bottom, differentiate each, then put them into the quotient rule.")
        if nested:
            return ("Differentiate: use the chain rule, the derivative of the outer function times the derivative of the inside.",
                    "Find the inside function, differentiate the outside first, then multiply by the derivative of the inside.")
        if prod and not many:
            return ("Differentiate: use the product rule, first times the derivative of the second plus second times the derivative of the first.",
                    "Name the two factors, differentiate each, then put them into the product rule.")
        if _FUN.search(e):
            return ("Differentiate: recall the derivative of the function inside, then handle the rest of the expression term by term.",
                    "Write down the derivative of the basic function, then combine the terms.")
        if many:
            return ("Differentiate: use the power rule on each term (bring the exponent down, then lower it by one).",
                    f"Take the terms one at a time. A constant term has derivative 0, and a number times {v} keeps its number.")
        return ("Differentiate: use the power rule (bring the exponent down, then lower it by one).",
                "Check the exponent on your answer is one lower than the one you started with.")
    if p.op == "integrate":
        if _FUN.search(e):
            return ("Integrate: ask which function has this one as its derivative.",
                    "Differentiate your guess; you should get the original expression back.")
        return ("Integrate: use the power rule for integrals on each term (raise the exponent by one, then divide by the new exponent).",
                "Differentiate your answer; you should get the original expression back.")
    if p.op == "solve":
        if div:
            return ("Solve: multiply both sides by the common denominator to clear the fractions, then solve what is left.",
                    "After clearing fractions, check each answer does not make a denominator zero.")
        if re.search(rf"\b{re.escape(v)}\s*(?:\*\*|\^)\s*2|\b{re.escape(v)}\s*\*\s*{re.escape(v)}", e):
            return ("Solve: move everything to one side, then factor or use the quadratic formula.",
                    "Set each factor equal to zero and solve each one.")
        return (f"Solve: get {v} alone by doing the same operation to both sides.",
                f"Collect the {v} terms on one side and the numbers on the other.")
    if p.op == "simplify":
        return ("Simplify: combine like terms and cancel common factors.",
                "Look for a factor that appears in every term, then factor it out or cancel it.")
    if p.op == "evaluate":
        return ("Evaluate: substitute the given value, then follow the order of operations (brackets, powers, multiply and divide, add and subtract).",
                "Do the brackets first, then powers, then multiplication and division, then addition and subtraction.")
    return ("Name the rule you need.", "Apply the rule one step at a time.")


# ------------------------------------------------------------------ nudges per format

def _nudges(item: Item, objective_statement: str) -> tuple[list[str], list[str]]:
    """(level-1 candidates, level-2 candidates), most specific first."""
    p = item.payload
    goal = f'The goal is: "{objective_statement.strip().rstrip(".")}".'
    if isinstance(p, MCPayload):
        return ([f"{goal} Cross out any option that contradicts the question.",
                 "Cross out the options you are sure are wrong."],
                ["Narrow it down to two options, then compare each one against the question word by word.",
                 "Compare the remaining options word by word."])
    if isinstance(p, ClozePayload):
        n = len(p.answer.split())
        return ([f"{goal} Read the whole sentence and ask what kind of word fits.",
                 "Read the whole sentence and ask what kind of word fits."],
                [f"The blank is {n} word{'s' if n != 1 else ''} long. Say the sentence aloud with your guess in it.",
                 "Say the sentence aloud with your guess in it."])
    if isinstance(p, OrderPayload):
        return ([f"{goal} Find the event you are surest about and put it first or last.",
                 "Find the event you are surest about and put it first or last."],
                ["Place the events two at a time: for each pair, decide which came first.",
                 "Place the events two at a time."])
    if isinstance(p, MatchPayload):
        return ([f"{goal} Start with the pairs you are certain of.",
                 "Start with the pairs you are certain of."],
                ["Remove the pairs you have settled; the leftovers are easier to match.",
                 "Settle the easy pairs first."])
    if isinstance(p, ShortPayload):
        k = len(p.required_terms) if p.min_terms is None else p.min_terms
        return ([f"{goal} Decide what the question is really asking before you write.",
                 "Decide what the question is really asking before you write."],
                [f"A full answer needs {k} key idea{'s' if k != 1 else ''}. List them, then write one sentence.",
                 "List your key ideas, then write one sentence."])
    if isinstance(p, CodePayload):
        return ([f"{goal} Run the visible tests and read the first failure.",
                 "Run the visible tests and read the first failure."],
                ["Trace your code by hand on the first visible test: write each variable after each line, "
                 "and find the line where it first differs from the expected output.",
                 "Trace your code by hand on the first visible test."])
    if isinstance(p, MathPayload):
        rule, step = math_rule(p)
        return ([rule, f"{goal} Pick the rule that matches the shape of the expression."],
                [step, f"Work in terms of {p.var}, one step at a time, and check each step."])
    if isinstance(p, NumericPayload):
        names = ", ".join(q.name for q in p.given) or "the given values"
        return ([f"{goal} Write down {names} with their units.",
                 "Write down the given values with their units."],
                [f"Find the formula that links {names} to {p.quantity or 'the quantity asked for'}, and "
                 f"make sure your answer ends in {p.unit or 'the right unit'}.",
                 "Find the formula that links the given values to the unknown, and check the unit."])
    return ([f"{goal} Say it in your own words first.", "Say it in your own words first."],
            ["Write down the two or three points you would make, then compare with the source.",
             "Write down your main points, then compare with the source."])


def _safe(candidates: list[str], keys: list[str]) -> str:
    for c in candidates:
        if not leaks(c, keys):
            return c
    return LAST_RESORT if not leaks(LAST_RESORT, keys) else "Hint withheld for this question."


# ------------------------------------------------------------------ grounded pointers

def _split_location(location: str) -> str:
    """'Ch. 2, p. 14, para 3' -> 'Ch. 2, p. 14'; 'Stamp Act, para 2' -> 'Stamp Act'; 'para 3' -> ''."""
    return _PARA.sub("", location).strip()


def _sentence_span(text: str, quote: str) -> Span | None:
    """Span (character offsets into `text`) of the whole sentence holding the quote."""
    start = text.find(quote)
    if start < 0:
        start = text.casefold().find(quote.casefold())
    if start < 0:
        q = set(normalize(quote).split())
        best, bs = 0, None
        for m in re.finditer(r"[^.!?\n]+[.!?]?", text):
            overlap = len(q & set(normalize(m.group()).split()))
            if overlap > best:
                best, bs = overlap, m
        if bs is None or best < max(1, len(q) // 2):
            return None
        return Span(start=bs.start(), end=bs.end())
    end = start + len(quote)
    s = max((text.rfind(c, 0, start) for c in ".!?\n"), default=-1) + 1
    nxt = [i for i in (text.find(c, end) for c in ".!?\n") if i >= 0]
    e = min(nxt) + 1 if nxt else len(text)
    while s < len(text) and text[s].isspace():
        s += 1
    return Span(start=s, end=e)


def _grounded(store: Store, item: Item):
    """(passage, doc) when the item points into the learner's materials, else None."""
    if item.origin != "materials" or item.format not in _READING_FORMATS:
        return None
    for c in item.citations:
        ps = store.get_passage(c.passage_id)
        if ps:
            return c, ps, store.get_doc(ps.doc_id)
    return None


def _worked_example(store: Store, item: Item, keys: list[str]) -> str | None:
    others = [o for o in store.list_items(objective_id=item.objective_id, status="active") if o.id != item.id]
    for o in sorted(others, key=lambda i: i.id):
        text = f"Worked example (a different question on this objective): {o.prompt}\nAnswer: {answer_text(o)}"
        if answer_text(o) and not leaks(text, keys):
            return text
    return None


def get_hint(store: Store, item_id: str, level: int) -> HintResult:
    """The hint at `level` (1..3) for the item. Raises KeyError for an unknown item, ValueError for a bad level."""
    if level not in (1, 2, 3):
        raise ValueError("hint level must be 1, 2 or 3")
    item = store.get_item(item_id)
    if item is None:
        raise KeyError(item_id)
    obj = store.get_objective(item.objective_id)
    statement = obj.statement if obj else ""
    keys = answer_keys(item)
    n = real_attempts(store, item_id)
    g = _grounded(store, item)
    n1, n2 = _nudges(item, statement)

    if g is not None and not (level == 1 and isinstance(item.payload, MathPayload)):
        cite, passage, doc = g
        section = _split_location(passage.location)
        where = doc.name if doc else "your materials"
        if level == 1:
            cands = ([f'Look in the section "{section}" of {where}.'] if section else []) + \
                    [f"Look in {where}.", f"{n1[-1]}"]
            return HintResult(Hint(level=1, text=_safe(cands, keys)), attempts=n)
        if level == 2 and not leaks(passage.location, keys) and not leaks(passage.id, keys):
            return HintResult(Hint(level=2, text=_safe([f"The answer is in {passage.location} of {where}."], keys),
                                   passage_id=passage.id), location=passage.location, attempts=n)
        if level == 2:
            return HintResult(Hint(level=2, text=_safe(n2, keys)), attempts=n)
        if n < REAL_ATTEMPTS_FOR_ANSWER:
            return HintResult(Hint(level=3, text="Make two real attempts first. Then I will show the paragraph "
                                                 "with the key sentence marked."),
                              location=passage.location, locked=True, attempts=n)
        span = _sentence_span(passage.text, cite.quote)
        return HintResult(Hint(level=3, text=f"Read the marked sentence in {passage.location}.",
                               passage_id=passage.id, highlight=span),
                          location=passage.location, passage_text=passage.text, attempts=n)

    if level == 1:
        return HintResult(Hint(level=1, text=_safe(n1, keys)), attempts=n)
    if level == 2:
        return HintResult(Hint(level=2, text=_safe(n2, keys)), attempts=n)
    ex = _worked_example(store, item, keys)
    if ex is None:
        text = _safe(["There is no other worked example for this objective yet. " + n2[0].rstrip(".") + ".",
                      "There is no other worked example for this objective yet."], keys)
        return HintResult(Hint(level=3, text=text), attempts=n)
    return HintResult(Hint(level=3, text=ex), attempts=n)
