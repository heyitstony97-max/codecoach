"""Builds docs/REDTEAM.md from the attack tests themselves, so the report cannot drift from the suite.

    env -u PYTHONPATH .venv/bin/python tests/redteam/redteam_doc.py        # rewrite docs/REDTEAM.md
    env -u PYTHONPATH .venv/bin/python tests/redteam/redteam_doc.py --json # print the handoff numbers

Each attack is a test function `test_rtNN_<slug>`; its docstring carries title, severity, fix and kind
(see redteam_kit.py) and an `xfail(strict=True, reason="HOLE: ...")` mark means the gate lets it through.
tests/redteam/test_redteam_doc.py fails when docs/REDTEAM.md no longer matches what this prints.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DOC = ROOT / "docs" / "REDTEAM.md"
MODULES = ("test_redteam_quotes", "test_redteam_facts", "test_redteam_items", "test_redteam_leaks")
SEVERITIES = ("high", "medium", "low")
KINDS = ("fixable", "honest-limit")
_NAME = re.compile(r"^test_rt(\d\d)_")

# Open holes grouped by where one code change would close them. Only ids that are still HOLE are shown.
BATCHES = [
    ("Quote boundary (`ground/gate.py` `text_found`)",
     "Treat `,` `.` `-` `'` between two alphanumerics as part of the word, on both edges of the quote.",
     ["RT12", "RT13", "RT14", "RT15"]),
    ("Normalization must agree everywhere (`core/text.py`, `ground/facts.py` `prep`)",
     "`prep` must strip the same invisible set as `normalize` (all Cf characters). Superscripts and subscripts "
     "must not turn into plain digits.",
     ["RT17", "RT21"]),
    ("Number and date facts (`ground/facts.py`)",
     "Add quantity words (dozen, score, couple). Read teen+tens as one spoken year. Keep a BC marker (stop "
     "`expand` turning a BC year into a bare number). Keep the sign of a negative number. Do not let a "
     "percentage support a bare count. Read month names case-insensitively. Compare d/m/y, y-m-d and a:b "
     "as ordered sequences, not sets.",
     ["RT23", "RT24", "RT29", "RT31", "RT32", "RT39", "RT42", "RT43", "RT90"]),
    ("Name extraction (`ground/facts.py` `extract_names`)",
     "Keep a Roman numeral `I` that follows a name. Do not skip a stop word (Will, Mark) that is followed by "
     "another capitalized word.",
     ["RT26", "RT27", "RT47"]),
    ("Multiple-choice key must be fully supported (`ground/gate.py` `_gate_mc`)",
     "Require every content word of the correct option to occur in the quote(s), and compare a small "
     "quantifier lexicon (all, every, none, only, some).",
     ["RT38", "RT46", "RT48"]),
    ("Negation (`ground/gate.py`)",
     "If the quote clause that carries the key contains not, never, no, n't, without, failed to or refused, "
     "drop unless the prompt carries it too. Applies to MC and short-answer terms.",
     ["RT40", "RT41"]),
    ("Bind prompt, key, label and date to the SAME quote sentence (`ground/gate.py`)",
     "The prompt's content words must occur in the cited quote(s). For order events, match pairs and the MC "
     "key, require the two halves to occur in one sentence of one quote, not in the pool of all quotes. Cap "
     "quote length so a quote cannot be the whole passage. Require cloze aliases to fill the blank.",
     ["RT10", "RT11", "RT52", "RT57", "RT61", "RT63", "RT72"]),
    ("Order dates (`ground/gate.py` `_gate_order`, `ground/facts.py` `date_sort_key`)",
     "Sort by the quoted span including its era. Treat a year-only date and a dated event in the same year, "
     "or a decade and a year inside it, as ambiguous. Reject dates the quote qualifies (before, after, "
     "circa). Reject correct_order == identity and labels that contain their own date.",
     ["RT53", "RT54", "RT55", "RT56", "RT79", "RT80"]),
    ("Distractors and required terms (`ground/gate.py`)",
     "Check each distractor against all passages, flag a distractor that contains every fact of the key, "
     "and reject required terms that are stop words.",
     ["RT68", "RT70", "RT71"]),
    ("Prompts and non-reading formats (`ground/gate.py` `check_item`, `gen/generate.py` `verify`)",
     "Check ordinals in prompts, check prompts against the passages only (not the LLM-written objective "
     "statement), reject prompts that contain the answer, give `check_item` a `mode` that refuses "
     "model_knowledge in strict courses, and run the prompt fact check on code/math/numeric items that "
     "carry a citation.",
     ["RT74", "RT75", "RT76", "RT77", "RT78", "RT84"]),
    ("Quote content (`ground/gate.py` `quote_long_enough`)",
     "Require at least 3 alphabetic words in a quote.",
     ["RT16"]),
    ("`core/public.py`",
     "`_shuffled` must never return the original order.",
     ["RT83"]),
    ("Framing line (`ground/gate.py` `check_framing`)",
     "Require every content word of the framing line to appear in the shown passage or in a short list of "
     "instruction words, and read spelled-out decades and spoken years.",
     ["RT87", "RT88", "RT89"]),
]


def _parse(fn) -> dict:
    doc = inspect.getdoc(fn) or ""
    lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
    head = lines[0]
    rid, _, title = head.partition(":")
    rec = {"id": rid.strip(), "title": title.strip(), "name": fn.__name__}
    for ln in lines[1:]:
        key, _, val = ln.partition(":")
        assert key.strip() in ("severity", "fix", "kind"), (
            f"{fn.__name__}: docstring line {ln!r} is not 'severity:', 'fix:' or 'kind:' (keep the title on one line)")
        rec[key.strip()] = val.strip()
    xf = [m for m in getattr(fn, "pytestmark", []) if m.name == "xfail"]
    rec["result"] = "HOLE" if xf else "blocked"
    rec["strict"] = all(m.kwargs.get("strict") is True for m in xf)
    rec["reason"] = (xf[0].kwargs.get("reason", "") if xf else "")
    return rec


def collect() -> list[dict]:
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(ROOT))
    out = []
    for name in MODULES:
        mod = importlib.import_module(name)
        for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ == mod.__name__ and _NAME.match(fn_name):
                out.append(_parse(fn))
    out.sort(key=lambda r: r["id"])
    ids = [r["id"] for r in out]
    assert len(ids) == len(set(ids)), f"duplicate attack ids: {sorted(i for i in ids if ids.count(i) > 1)}"
    for r in out:
        assert r["name"].startswith("test_" + r["id"].lower() + "_"), f"{r['name']} does not match its id {r['id']}"
    return out


def summary(recs: list[dict]) -> dict:
    holes = [r for r in recs if r["result"] == "HOLE"]
    by_sev: dict[str, dict] = {}
    for sev in SEVERITIES:
        hs = [r for r in holes if r["severity"] == sev]
        by_sev[sev] = {"count": len(hs),
                       "fixable": [r["id"] for r in hs if r["kind"] == "fixable"],
                       "honest_limit": [r["id"] for r in hs if r["kind"] == "honest-limit"]}
    return {"attacks_total": len(recs), "blocked": len(recs) - len(holes), "holes": len(holes),
            "holes_by_severity": by_sev}


def _cell(s: str) -> str:
    return s.replace("|", "/").replace("\n", " ")


def render() -> str:
    recs = collect()
    s = summary(recs)
    holes = {r["id"]: r for r in recs if r["result"] == "HOLE"}
    out = [
        "# Red team: the source gate (R1)",
        "",
        "_Generated by `tests/redteam/redteam_doc.py` from the attack tests. Do not edit by hand: change the test "
        "docstring or the `xfail` mark, then re-run `env -u PYTHONPATH .venv/bin/python tests/redteam/redteam_doc.py`. "
        "`tests/redteam/test_redteam_doc.py` fails when this file is stale._",
        "",
        "**The promise under attack** (CONSTRAINTS section 10): every fact CodeCoach shows is a word-for-word quote from "
        "the learner's materials, and every answer key is backed by one of those quotes. **Target:** `codecoach/ground` "
        "(gates a to d, the prompt check, the framing gate) and `core.public_view`. **Goal:** get a false fact past "
        "the gate. **Method:** each attack is a pytest test that builds a draft item plus passages and asserts the gate "
        "drops it. A test that exposes a real hole is `xfail(strict=True, reason=\"HOLE: ...\")`, so the suite stays "
        "green, every hole stays visible, and fixing one turns its test red until the marker is removed.",
        "",
        "Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/redteam -q`",
        "",
        "## Scoreboard",
        "",
        f"- Attacks: **{s['attacks_total']}**. Blocked: **{s['blocked']}**. Holes: **{s['holes']}**.",
    ]
    for sev in SEVERITIES:
        d = s["holes_by_severity"][sev]
        out.append(f"- Holes, {sev}: **{d['count']}** ({len(d['fixable'])} fixable, {len(d['honest_limit'])} honest-limit)")
    out += [
        "",
        "**Severity** answers \"would a learner be told something false?\":",
        "",
        "- `high`: a false fact is shown as true, or the answer key is false.",
        "- `medium`: a right answer is graded wrong, or a wrong answer graded right, but no false fact is stated.",
        "- `low`: nothing false is said; the item leaks its own answer, or the shown proof proves nothing.",
        "",
        "For a *blocked* row, severity is the harm the attack would have done had it got through.",
        "",
        "**Kind**: `fixable` means a deterministic code change closes the demonstrated attack. `honest-limit` means the "
        "gate would have to understand meaning (which fact answers the question, what a premise claims, whether two "
        "phrases mean the same), so only heuristics could shrink it. These belong in the README's honest-limit text "
        "(CONSTRAINTS section 10), next to \"a question that reads the quote differently from the author\". "
        "For a blocked row Kind is shown only when the row also suggests an optional improvement (an over-strict drop).",
        "",
        "## Every attack",
        "",
        "| ID | Attack | Result | Severity | What gets through | Fix suggestion | Kind |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in recs:
        hole = r["result"] == "HOLE"
        through = _cell(r["reason"].removeprefix("HOLE: ")) if hole else "-"
        fix = _cell(r["fix"])
        kind = r["kind"] if (hole or not r["fix"].lower().startswith("none needed")) else "-"
        out.append(f"| {r['id']} | {_cell(r['title'])} | {r['result']} | {r['severity']} | {through} | {fix} | {kind} |")
    out += ["", "## Open holes grouped by the change that closes them (for a5)", "",
            "Only holes that are still open are listed. `honest-limit` ids are marked and need a README line, not a patch.",
            ""]
    grouped: set[str] = set()
    for title, how, ids in BATCHES:
        live = [i for i in ids if i in holes]
        grouped.update(live)
        if not live:
            continue
        def tag(i):
            r = holes[i]
            return f"{i} ({r['severity']}{', honest-limit' if r['kind'] == 'honest-limit' else ''})"
        out += [f"### {title}", "", how, "", "Closes: " + ", ".join(tag(i) for i in live), ""]
    ungrouped = sorted(i for i in set(holes) - grouped if holes[i]["kind"] != "honest-limit")
    out += ["### Honest limits (no code change closes these)", ""]
    lim = [i for i in sorted(holes) if holes[i]["kind"] == "honest-limit"]
    if lim:
        out += [f"- {i}: {holes[i]['title']} (severity {holes[i]['severity']})" for i in lim]
    else:
        out.append("- none open")
    out.append("")
    if ungrouped:
        out += ["### Open holes not yet assigned to a batch", ""] + [
            f"- {i}: {holes[i]['title']}" for i in ungrouped] + [""]
    out += [
        "## What was not attacked",
        "",
        "Grading (`codecoach/check`), the code sandbox, the importer's passage splitting, the store and the REST layer, "
        "and the hint ladder. This report covers what a learner could be told before and after answering, up to the "
        "gate and `public_view`. `reveal()` was read but not attacked: it only runs after an answer.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if "--json" in argv:
        print(json.dumps(summary(collect()), indent=2))
        return 0
    DOC.write_text(render(), encoding="utf-8")
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
