"""Capture REAL REST responses for the screenshot harness from copies of the A5 e2e stores.

    .venv/bin/python devtools/desktop-harness/make_real.py [history.db]

Defaults are the scratch Hermes homes of the A5 run (Qwen "local" store, cloud store).
Each store is COPIED with sqlite's backup API (never `cp` a live db), opened with codecoach.service
(Hermes-free: read endpoints need no LLM; answer/run use the real checkers) and driven through the
real FastAPI router in dashboard/plugin_api.py. One JSON per endpoint goes to real/. Course titles are cosmetic edits on the COPY only (the e2e stores name courses
by file stem). The C item is hand-authored (the bank generator makes reading items), same as A5.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codecoach.core.models import Citation, CodePayload, CodeTest, Item, OrderEvent, OrderPayload  # noqa: E402
from codecoach.core.public import public_view  # noqa: E402
from codecoach.ground.gate import check_item  # noqa: E402
from codecoach.core.store import Store  # noqa: E402
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402
from dashboard import plugin_api  # noqa: E402

HOME = Path("/tmp/codecoach-e2e-home")
SRC_HIST = Path(sys.argv[1]) if len(sys.argv) > 1 else HOME / "local/plugin-data/codecoach/codecoach.db"
TITLES = {"history": "The First Transcontinental Railroad"}

DIGITS_C = (
    "#include <stdio.h>\n\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 1;\n"
    "    int sum = 0;\n    while (n > 0) {\n        sum += n % 10;\n        n /= 10;\n    }\n    printf(\"%d\\n\", sum);\n    return 0;\n}\n")
TWO_DIGIT_C = (
    "#include <stdio.h>\n\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 1;\n"
    "    int sum = n % 10 + n / 10;\n    printf(\"%d\\n\", sum);\n    return 0;\n}\n")
BROKEN_C = "#include <stdio.h>\n\nint main(void) {\n    int n;\n    int sum = ;\n    return 0;\n}\n"


def backup(src: Path, name: str) -> Path:
    dst = Path(tempfile.mkdtemp(prefix="codecoach-real-")) / name
    a, b = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    b.close()
    return dst


def retitle(db: Path) -> None:
    c = sqlite3.connect(db)
    for cid, title in TITLES.items():
        c.execute("UPDATE courses SET title = ? WHERE id = ?", (title, cid))
    c.commit()
    c.close()


def client_for(db: Path):
    store = Store(str(db))
    svc = CodeCoachService(store, llm=None, config=ServiceConfig(), background=False, auto_bank=False)
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app), store, svc


def dump(out: Path, name: str, data) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"  wrote {out.name}/{name}.json")


def get(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def post(c, path, body):
    r = c.post(path, json=body)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def objectives_of(m):
    return [o for u in m["units"] for l in u["lessons"] for o in l["objectives"]]


def serve_item(c, course, objective, item):
    """GET /next until it serves `item` (capturing a lesson on the way); else its public view."""
    lesson = None
    for _ in range(4):
        nxt = get(c, f"/courses/{course}/next?objective_id={objective}")
        if nxt["kind"] == "lesson":
            lesson = nxt
        if nxt["kind"] == "item" and nxt["item"]["id"] == item.id:
            return nxt, lesson
    nxt = {"kind": "item", "reason": "new", "lesson": None, "message": None,
           "item": json.loads(public_view(item).model_dump_json())}
    return nxt, lesson


def main() -> int:
    out_h = HERE / "real"
    hist_db = backup(SRC_HIST, "history.db")
    retitle(hist_db)
    hc, hstore, hsvc = client_for(hist_db)
    plugin_api.set_service(hsvc)
    hist_courses = get(hc, "/courses")
    # Only the Golden Spike course is exported (the store may hold other, private courses).
    dump(out_h, "courses", {**hist_courses, "courses": [c for c in hist_courses["courses"] if c["id"] == "history"]})

    # ---- history course (Golden Spike)
    plugin_api.set_service(hsvc)
    cid = "history"
    hmap = get(hc, f"/courses/{cid}/map")
    dump(out_h, "map", hmap)
    spike_objs = {f"history:o0{i}" for i in range(1, 7)}
    mcs = [i for i in hstore.list_items(course_id=cid, status="active")
           if i.format == "mc" and i.objective_id in spike_objs and i.citations]
    assert mcs, "no Golden Spike multiple-choice item in the store"
    pick = mcs[0]
    print("  MC", pick.id, pick.objective_id, "correct_index", pick.payload.correct_index)
    nxt, lesson = serve_item(hc, cid, pick.objective_id, pick)
    dump(out_h, "next_item_mc", nxt)
    if lesson:
        dump(out_h, "next_lesson", lesson)
    dump(out_h, "hint", post(hc, f"/items/{pick.id}/hint", {"level": 1}))
    wrong = next(i for i in range(len(pick.payload.options)) if i != pick.payload.correct_index)
    dump(out_h, "answer_wrong", post(hc, f"/items/{pick.id}/answer", {"response": wrong}))
    dump(out_h, "answer_right", post(hc, f"/items/{pick.id}/answer", {"response": pick.payload.correct_index}))
    dump(out_h, "meta", {"mc_item": pick.id, "mc_correct_index": pick.payload.correct_index, "mc_wrong_index": wrong,
                         "objective_id": pick.objective_id, "course_id": cid})
    # real mastery for the progress page: answer a few other bank items correctly
    for it in [i for i in hstore.list_items(course_id=cid, status="active") if i.format == "mc" and i.id != pick.id][:6]:
        post(hc, f"/items/{it.id}/answer", {"response": it.payload.correct_index})
    dump(out_h, "progress", get(hc, f"/courses/{cid}/progress"))
    dump(out_h, "drops", get(hc, f"/courses/{cid}/drops"))
    # The E2E bank has no order items (the generator made mc/cloze/short), and the timeline is built only
    # from answered order items. Hand-author one from real passages, run it through the real gate, answer it.
    pid = {k: f"history-md:{k}" for k in ("nevada-s-silver-spike:1", "the-ceremony:4", "a-scattering-of-history:1", "a-scattering-of-history:3")}
    events = [("Christies Auction House to sell the Arizona spike", "January 2023", "a-scattering-of-history:3", "In January 2023, Christies Auction House was commissioned to sell the Arizona Gold and Silver Spike for the Museum of the city of New York."),
              ("Spike ordered for Nevada", "May 5, 1869", "nevada-s-silver-spike:1", "The spike was ordered on May 5, 1869 by Railroad Commissioner and candidate for Governor of the new State of Nevada, Mr. F.A. Tritle."),
              ("Hewes donates the Golden Spike to Stanford", "1892", "a-scattering-of-history:1", "In 1892, Hewes donated his extensive rare art collection, including the Golden Spike, to the museum of newly built Leland Stanford Junior University in Palo Alto, California."),
              ("Monday, 12:47 p.m.", "May 10, 1869", "the-ceremony:4", "The time was 12:47 p.m., Monday, May 10, 1869.")]
    order_item = Item(
        id="real-order-spikes", objective_id="history:o29", format="order", origin="materials", checker="order",
        prompt="Put these Golden Spike events in the order they happened, earliest first.",
        payload=OrderPayload(events=[OrderEvent(label=l, date_text=d, passage_id=pid[p]) for l, d, p, _ in events],
                             correct_order=[1, 3, 2, 0]),
        citations=[Citation(passage_id=pid[p], quote=q) for _, _, p, q in events])
    gate = check_item(order_item, hstore.list_passages(course_id=cid), mode="strict")
    print("  order item gate failures:", gate)
    assert not gate and hstore.add_item(order_item)
    post(hc, f"/items/{order_item.id}/answer", {"response": [1, 3, 2, 0]})
    tl = get(hc, f"/courses/{cid}/timeline")
    dump(out_h, "timeline", tl)

    ids = {pid for o in objectives_of(hmap) for pid in o.get("passage_ids", [])}
    ids |= {e["passage_id"] for e in tl.get("events", []) if e.get("passage_id")}
    passages = {}
    for pid in sorted(ids):
        r = hc.get(f"/passages/{pid}")
        if r.status_code == 200:
            passages[pid] = r.json()
    dump(out_h, "passages", passages)
    # Hand-authored C item (as in the A5 five-item serve), attached to a Golden Spike objective.
    c_item = Item(
        id="real-c-digit-sum", objective_id=pick.objective_id, format="code", origin="model_knowledge", checker="code",
        prompt="Write a C program that reads one non-negative integer from standard input and prints the sum of its decimal digits.",
        payload=CodePayload(
            language="c",
            starter="#include <stdio.h>\n\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 1;\n    /* your code here */\n    return 0;\n}\n",
            visible_tests=[CodeTest(name="small", stdin="123\n", expected_stdout="6\n"),
                           CodeTest(name="zero", stdin="0\n", expected_stdout="0\n")],
            hidden_tests=[CodeTest(name="long", stdin="99999\n", expected_stdout="45\n"),
                          CodeTest(name="big", stdin="1000000007\n", expected_stdout="8\n"),
                          CodeTest(name="mid", stdin="4096\n", expected_stdout="19\n")],
            reference_solution=DIGITS_C))
    assert hstore.add_item(c_item)
    nxt, _ = serve_item(hc, cid, pick.objective_id, c_item)
    dump(out_h, "next_item_code", nxt)
    runs = []
    for label, code in (("compile error", BROKEN_C), ("partly passing", TWO_DIGIT_C), ("all passing", DIGITS_C)):
        res = post(hc, f"/items/{c_item.id}/run", {"code": code})
        print(f"  run {label}: ok={res['ok']} {res['passed']}/{res['total']} compile_error={bool(res.get('compile_error'))}")
        runs.append({"label": label, "code": code, "result": res})
    dump(out_h, "runs", runs)
    dump(out_h, "answer_code", post(hc, f"/items/{c_item.id}/answer", {"response": DIGITS_C}))
    print("done; copy in", hist_db.parent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
