"""Keeps docs/REDTEAM.md honest: it is generated from the attack tests, and these checks fail when it is
stale, when an attack record is incomplete, or when an xfail is not a strict HOLE marker."""

from redteam_doc import DOC, KINDS, SEVERITIES, collect, render, summary

REGEN = "env -u PYTHONPATH .venv/bin/python tests/redteam/redteam_doc.py"


def test_there_are_at_least_twenty_attacks():
    assert summary(collect())["attacks_total"] >= 20


def test_every_attack_has_a_complete_record():
    for r in collect():
        assert r["title"], r["name"]
        assert r.get("severity") in SEVERITIES, f"{r['id']}: severity must be one of {SEVERITIES}"
        assert r.get("kind") in KINDS, f"{r['id']}: kind must be one of {KINDS}"
        assert r.get("fix"), f"{r['id']}: a one-line fix suggestion is required"
        assert len(r["fix"]) < 400, f"{r['id']}: keep the fix to one line"


def test_every_xfail_is_a_strict_hole_marker():
    for r in collect():
        if r["result"] == "HOLE":
            assert r["strict"], f"{r['id']}: an expected failure must be xfail(strict=True)"
            assert r["reason"].startswith("HOLE: "), f"{r['id']}: xfail reason must start with 'HOLE: '"


def test_report_is_in_sync_with_the_tests():
    assert DOC.exists(), f"docs/REDTEAM.md is missing: run {REGEN}"
    assert DOC.read_text(encoding="utf-8") == render(), f"docs/REDTEAM.md is stale: run {REGEN}"
