"""Regenerates the *.json files next to this script. Run:
    env -u PYTHONPATH .venv/bin/python tests/fixtures/api/_generate.py
The JSON files are the contract examples (served by a4b's screenshot harness). tests/core
re-validates each file against its response model, so edits to either side are caught.
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from codecoach.core.models import (  # noqa: E402
    GATE_CODES, GATE_LABELS, CodePayload, CodeTest, Citation, Item, MCPayload,
)
from codecoach.core.public import public_view  # noqa: E402

CID = "us-revolution"
DOC = "rev-ch2"
T0 = "2026-11-04T18:02:11.000000Z"


def dump(name: str, obj) -> None:
    (HERE / f"{name}.json").write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


course = {"id": CID, "title": "The American Revolution (NPS readings)", "mode": "strict",
          "mode_reason": "Imported materials: only facts found in them are used.",
          "created_at": T0}

# ------------------------------------------------------------------ map: 4 units x 5 lessons x 2 objectives
UNITS = [
    ("u1", "Roots of the Conflict", [
        ("The French and Indian War", ["Explain why Britain's war debt followed the Seven Years' War.",
                                       "Identify the territory Britain gained in 1763."]),
        ("The Proclamation of 1763", ["Describe what the Proclamation Line forbade.",
                                      "Explain colonial reaction to the Proclamation."]),
        ("The Stamp Act", ["State what the Stamp Act taxed.", "Explain 'no taxation without representation'."]),
        ("The Townshend Acts", ["List the goods taxed by the Townshend duties.",
                                "Describe the colonial boycott response."]),
        ("The Boston Massacre", ["Recount the events of March 5, 1770.",
                                 "Explain how the event was used in print."]),
    ]),
    ("u2", "Organizing Resistance", [
        ("Committees of Correspondence", ["Explain the purpose of the committees.",
                                          "Identify how news travelled between colonies."]),
        ("The Tea Act and Tea Party", ["Describe the Tea Act's effect on tea prices.",
                                       "Recount the December 1773 protest."]),
        ("The Coercive Acts", ["List the measures aimed at Massachusetts.", "Explain why colonists called them Intolerable."]),
        ("The First Continental Congress", ["Identify who attended in 1774.", "Describe the Continental Association."]),
        ("Lexington and Concord", ["Recount the April 1775 march.", "Explain what 'the shot heard round the world' refers to."]),
    ]),
    ("u3", "Declaring Independence", [
        ("The Second Continental Congress", ["Identify the army's commander appointed in 1775.", "Describe the Olive Branch Petition."]),
        ("Common Sense", ["State the main argument of the pamphlet.", "Explain its effect on public opinion."]),
        ("Drafting the Declaration", ["Name the Committee of Five members.", "Identify the principal author."]),
        ("The Declaration's Argument", ["Summarize the list of grievances.", "Explain the natural-rights claim."]),
        ("Adoption and Reaction", ["Give the date of adoption.", "Describe how the text was publicized."]),
    ]),
    ("u4", "Winning the War", [
        ("Early Campaigns", ["Describe the Siege of Boston's outcome.", "Explain the New York campaign."]),
        ("Saratoga and the French Alliance", ["Explain why Saratoga mattered.", "Identify the 1778 treaty terms."]),
        ("The Southern Campaign", ["Describe the shift of British strategy to the South.", "Identify the battle of Cowpens."]),
        ("Yorktown", ["Recount the siege of 1781.", "Identify the role of the French fleet."]),
        ("The Treaty of Paris", ["Give the year the treaty was signed.", "Describe the boundaries it set."]),
    ]),
]
NOT_COVERED = {"u2o4a", "u3o2b", "u4o3a"}   # three objectives the readings do not cover

units, edges, flat = [], [], []
n = 0
page = 12
for ui, (uid, utitle, lessons) in enumerate(UNITS):
    lnodes = []
    for li, (ltitle, stmts) in enumerate(lessons):
        lid = f"{uid}l{li + 1}"
        onodes = []
        for oi, stmt in enumerate(stmts):
            n += 1
            oid = f"{CID}:o{n:02d}"
            key = f"{uid}o{li + 1}{'ab'[oi]}"
            covered = key not in NOT_COVERED
            if oi == 1:
                prereqs = [flat[-1]]          # second objective follows the first in its lesson
            elif li > 0:
                prereqs = [flat[-2]]          # first objective follows the previous lesson's first
            else:
                prereqs = []
            mastery = [0.92, 0.85, 0.78, 0.66, 0.55, 0.43, 0.31, 0.2, 0.08, 0.0][n % 10]
            if not covered:
                mastery = 0.0
            pids = [f"{DOC}:p{page + li}:{1 + oi}", f"{DOC}:p{page + li}:{2 + oi}"] if covered else []
            verb = stmt.split()[0].lower()
            onodes.append({
                "id": oid, "unit": uid, "lesson": lid, "verb": verb, "statement": stmt,
                "kind": "reading", "prereqs": prereqs, "passage_ids": pids,
                "coverage": "covered" if covered else "not_covered",
                "mastery": mastery if covered else 0.0,
                "items_active": 0 if not covered else 3 + (n % 3),
                "due_reviews": (n % 4) if covered and mastery > 0.3 else 0,
                "status_label": None if covered else "Not in your materials",
            })
            for p in prereqs:
                edges.append({"prereq": p, "objective": oid})
            flat.append(oid)
        lnodes.append({"id": lid, "title": ltitle, "objectives": onodes})
    page += 5
    units.append({"id": uid, "title": utitle, "lessons": lnodes})

objs = [o for u in units for l in u["lessons"] for o in l["objectives"]]
cov = [o for o in objs if o["coverage"] == "covered"]
course_summary = {**course, "objectives": 40, "covered": len(cov), "not_covered": 40 - len(cov),
                  "items_active": sum(o["items_active"] for o in objs),
                  "mastery": round(sum(o["mastery"] for o in cov) / len(cov), 3),
                  "due_reviews": sum(o["due_reviews"] for o in objs)}
dump("map", {"course": course_summary, "units": units, "edges": edges,
             "summary": {"objectives": 40, "covered": len(cov), "not_covered": 40 - len(cov),
                         "mastered": sum(1 for o in cov if o["mastery"] >= 0.8),
                         "mean_mastery": course_summary["mastery"]}})

dump("courses", {"courses": [
    course_summary,
    {"id": "c-pointers", "title": "C: pointers and memory", "mode": "open",
     "mode_reason": "You chose open mode; items without a source are labeled.",
     "created_at": "2026-11-02T01:30:00.000000Z", "objectives": 12, "covered": 12, "not_covered": 0,
     "items_active": 36, "mastery": 0.41, "due_reviews": 5}]})
dump("courses_import", {"job_id": "job_5f3a9c1d2e44"})
dump("job", {"id": "job_5f3a9c1d2e44", "kind": "import", "status": "running", "pct": 62.5,
             "stage": "generating questions (objective 25 of 40)", "error": None, "course_id": CID,
             "created_at": T0, "updated_at": "2026-11-04T18:03:40.000000Z"})

# ------------------------------------------------------------------ next: lesson, MC item, code item
q1 = ("The Stamp Act of 1765 was the first direct tax Parliament placed on the American colonies; "
      "it required that printed materials carry a tax stamp.")
dump("next_lesson", {
    "kind": "lesson", "reason": "new",
    "lesson": {
        "objective_id": f"{CID}:o05", "unit_title": "Roots of the Conflict", "lesson_title": "The Stamp Act",
        "statement": "State what the Stamp Act taxed.",
        "framing": "Read this passage, then answer a few questions about it.",
        "passages": [
            {"passage_id": f"{DOC}:p14:3", "location": "Ch. 2, p. 14, para 3", "text": q1},
            {"passage_id": f"{DOC}:p14:4", "location": "Ch. 2, p. 14, para 4",
             "text": "Colonists objected that they had no elected representatives in Parliament, "
                     "and several colonial assemblies passed resolutions against the tax."}],
        "source_label": "From your materials"},
    "item": None, "message": None})

mc = Item(id="it_mc_stamp_01", objective_id=f"{CID}:o05", format="mc",
          prompt="According to the reading, what did the Stamp Act of 1765 require?",
          payload=MCPayload(options=["That printed materials carry a tax stamp",
                                     "That colonists house British soldiers",
                                     "That tea be bought only from the East India Company",
                                     "That colonial ports close to trade"], correct_index=0),
          citations=[Citation(passage_id=f"{DOC}:p14:3",
                              quote="it required that printed materials carry a tax stamp")],
          origin="materials", checker="mc")
dump("next_item_mc", {"kind": "item", "reason": "new", "lesson": None,
                      "item": json.loads(public_view(mc).model_dump_json()), "message": None})

code = Item(
    id="it_code_digits_01", objective_id="c-pointers:o03", format="code",
    prompt="Read one non-negative integer from standard input and print the sum of its decimal digits.",
    payload=CodePayload(
        language="c",
        starter="#include <stdio.h>\n\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 1;\n"
                "    /* your code here */\n    return 0;\n}\n",
        visible_tests=[CodeTest(name="small", stdin="123\n", expected_stdout="6\n"),
                       CodeTest(name="zero", stdin="0\n", expected_stdout="0\n")],
        hidden_tests=[CodeTest(name="large", stdin="2147483647\n", expected_stdout="46\n"),
                      CodeTest(name="power of ten", stdin="1000\n", expected_stdout="1\n"),
                      CodeTest(name="repeated nines", stdin="99999\n", expected_stdout="45\n")],
        reference_solution="#include <stdio.h>\nint main(void){int n,s=0;if(scanf(\"%d\",&n)!=1)return 1;"
                           "while(n>0){s+=n%10;n/=10;}printf(\"%d\\n\",s);return 0;}\n",
        time_limit_s=5, mem_mb=128),
    citations=[], origin="model_knowledge", checker="code_c")
dump("next_item_code", {"kind": "item", "reason": "review", "lesson": None,
                        "item": json.loads(public_view(code).model_dump_json()), "message": None})

dump("answer_mc", {
    "item_id": "it_mc_stamp_01", "correct": True, "score": 1.0,
    "feedback": "Correct. The passage says the Act required printed materials to carry a tax stamp.",
    "correct_answer": "That printed materials carry a tax stamp",
    "citations": [{"passage_id": f"{DOC}:p14:3",
                   "quote": "it required that printed materials carry a tax stamp",
                   "location": "Ch. 2, p. 14, para 3"}],
    "source_label": "From your materials",
    "next_review": {"due": "2026-11-07T18:05:00.000000Z", "interval_days": 3.0},
    "mastery": 0.62})

dump("run", {"item_id": "it_code_digits_01", "ok": False, "passed": 1, "total": 2, "timed_out": False,
             "compile_error": None,
             "results": [
                 {"name": "small", "passed": True, "visible": True, "detail": "", "stdout": "6\n",
                  "expected": "6\n", "time_ms": 4},
                 {"name": "zero", "passed": False, "visible": True,
                  "detail": "Output differs from expected", "stdout": "", "expected": "0\n", "time_ms": 3}]})

dump("hint", {"item_id": "it_mc_stamp_01", "hints_used": 2,
              "hint": {"level": 2, "text": "Look again at paragraph 3 on page 14.",
                       "passage_id": f"{DOC}:p14:3", "highlight": None},
              "location": "Ch. 2, p. 14, para 3", "passage_text": None})
dump("report", {"item_id": "it_mc_stamp_01", "status": "reported", "reports_total": 2})

# ------------------------------------------------------------------ progress / drops
by_gate = {"quote_missing": 6, "unsupported_fact": 4, "ambiguous": 2, "bad_cloze": 1, "framing_fact": 1,
           "key_mismatch": 0, "weak_tests": 0, "self_check_failed": 0, "schema_invalid": 3}
dump("progress", {
    "course_id": CID, "objectives": 40, "covered": 37, "not_covered": 3, "mastered": 9,
    "mean_mastery": course_summary["mastery"], "items_generated": 143, "items_active": 124,
    "items_dropped": 17, "items_reported": 2, "attempts": 88, "accuracy": 0.72, "due_reviews": 6,
    "drops_by_gate": by_gate, "reports": 2,
    "recent_days": [{"date": f"2026-11-0{d}", "attempts": a, "correct": c}
                    for d, a, c in [(1, 12, 8), (2, 20, 14), (3, 0, 0), (4, 31, 24)]]})
recent = [
    {"id": 17, "course_id": CID, "objective_id": f"{CID}:o05", "format": "mc", "gate_code": "unsupported_fact",
     "detail": "year '1766' is not in the cited quote", "draft_hash": "a41c9e07b2d3f5a8", "at": "2026-11-04T18:02:40.000000Z"},
    {"id": 16, "course_id": CID, "objective_id": f"{CID}:o07", "format": "cloze", "gate_code": "bad_cloze",
     "detail": "blank 'Parliament' is not an exact span of the quote", "draft_hash": "77be01c93a6d12ef", "at": "2026-11-04T18:02:31.000000Z"},
    {"id": 15, "course_id": CID, "objective_id": f"{CID}:o11", "format": "mc", "gate_code": "quote_missing",
     "detail": "quote not found in rev-ch2:p15:2", "draft_hash": "0d5f88a1c4e92b36", "at": "2026-11-04T18:02:22.000000Z"}]
dump("drops", {"course_id": CID, "total": sum(by_gate.values()),
               "by_gate": [{"gate_code": g, "label": GATE_LABELS[g], "count": by_gate[g]} for g in GATE_CODES],
               "recent": recent})

# ------------------------------------------------------------------ timeline, plot, passage
ev = [
    ("French and Indian War ends", "1763", 1763, 12, 1, "The Treaty of Paris of 1763 ended the French and Indian War."),
    ("Stamp Act passed", "March 22, 1765", 1765, 14, 3, "Parliament passed the Stamp Act on March 22, 1765."),
    ("Boston Massacre", "March 5, 1770", 1770, 17, 2, "On March 5, 1770, British soldiers fired on a crowd in Boston."),
    ("Boston Tea Party", "December 16, 1773", 1773, 19, 1, "On December 16, 1773, colonists destroyed East India Company tea."),
    ("First Continental Congress", "September 1774", 1774, 21, 2, "The First Continental Congress met in September 1774."),
    ("Lexington and Concord", "April 19, 1775", 1775, 23, 1, "Fighting began at Lexington and Concord on April 19, 1775."),
    ("Declaration adopted", "July 4, 1776", 1776, 27, 4, "Congress adopted the Declaration of Independence on July 4, 1776."),
    ("Siege of Yorktown ends", "October 19, 1781", 1781, 33, 2, "Cornwallis surrendered at Yorktown on October 19, 1781."),
]
dump("timeline", {"course_id": CID, "events": [
    {"label": l, "date_text": d, "sort_year": y, "passage_id": f"{DOC}:p{p}:{k}",
     "location": f"Ch. 2, p. {p}, para {k}", "quote": q} for l, d, y, p, k, q in ev]})


def series(label, expr, fn, lo=-3.0, hi=3.0, steps=40):
    pts = []
    for i in range(steps + 1):
        x = lo + (hi - lo) * i / steps
        try:
            y = fn(x)
            pts.append([round(x, 4), None if y is None or not math.isfinite(y) else round(y, 5)])
        except (ValueError, ZeroDivisionError):
            pts.append([round(x, 4), None])
    return {"label": label, "expr": expr, "points": pts}


dump("plot", {"item_id": "it_math_diff_01", "title": "f(x) = x^2 sin(x) and its derivative",
              "x_label": "x", "y_label": "y", "x_range": [-3.0, 3.0], "verified_by": "sympy",
              "series": [series("f(x)", "x**2*sin(x)", lambda x: x * x * math.sin(x)),
                         series("f'(x)", "2*x*sin(x) + x**2*cos(x)",
                                lambda x: 2 * x * math.sin(x) + x * x * math.cos(x)),
                         series("ln(x)", "log(x)", lambda x: math.log(x))]})

dump("passage", {"passage": {"id": f"{DOC}:p14:3", "doc_id": DOC, "location": "Ch. 2, p. 14, para 3", "text": q1},
                 "doc_name": "NPS: Road to Revolution (chapter 2)", "doc_kind": "pdf_text",
                 "prev_id": f"{DOC}:p14:2", "next_id": f"{DOC}:p14:4"})
dump("events", {"job_progress": {"job_id": "job_5f3a9c1d2e44", "pct": 62.5,
                                 "stage": "generating questions (objective 25 of 40)"},
                "bank_updated": {"course_id": CID}})
print("wrote fixtures to", HERE)
