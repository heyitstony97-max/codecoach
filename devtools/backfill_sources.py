"""Backfill the original text and passage offsets of a course imported before they were kept.

  python devtools/backfill_sources.py --store <codecoach.db> --doc <doc_id> --file <path> [--dry-run]

Prints aligned/total. Writes only when every passage aligns; a wrong file is refused (exit 2) and
nothing is written. Run it with the repo .venv:  env -u PYTHONPATH .venv/bin/python devtools/backfill_sources.py ...
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codecoach.core.sources import AlignmentError, backfill_doc_text  # noqa: E402
from codecoach.core.store import Store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", required=True, help="path to codecoach.db")
    ap.add_argument("--doc", required=True, help="doc id, e.g. history-md")
    ap.add_argument("--file", required=True, help="the original file the course was imported from")
    ap.add_argument("--dry-run", action="store_true", help="align and report, write nothing")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.store):
        print(f"error: no store at {a.store}", file=sys.stderr)
        return 1
    try:
        with open(os.path.expanduser(a.file), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()      # same reader behaviour as the importer: universal newlines
    except OSError as e:
        print(f"error: cannot read {a.file}: {e}", file=sys.stderr)
        return 1
    store = Store(a.store)
    try:
        r = backfill_doc_text(store, a.doc, text, dry_run=a.dry_run)
    except AlignmentError as e:
        print(f"REFUSED {a.doc}: aligned {e.aligned}/{e.total}\n{e}", file=sys.stderr)
        return 2
    finally:
        store.close()
    print(f"{a.doc}: aligned {r['aligned']}/{r['total']} passages, {r['chars']} chars, "
          + ("dry run, nothing written" if a.dry_run else "written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
