"""Tier B registry facts: shipped ids keep the contract order, carry tier B, and obey the memory ceiling."""
from __future__ import annotations

from codecoach import lang

TIER_A = ["c", "cpp", "python", "javascript", "java", "go", "rust", "ruby", "bash"]
TIER_B = ["typescript", "php", "lua", "perl", "haskell", "zig", "elixir", "lisp"]


def test_tier_b_ids_follow_tier_a_in_contract_order():
    ids = lang.all_ids()
    assert ids[:9] == TIER_A
    rest = ids[9:]
    assert rest == [i for i in TIER_B if i in rest]


def test_tier_b_specs_are_complete_and_capped():
    for i in lang.all_ids()[9:]:
        s = lang.spec(i)
        assert s.tier == "B"
        assert s.ext.startswith(".") and s.style_note and s.comment and s.hello and s.source_name
        assert s.limits.compile_as_mb <= lang.AS_CEILING_MB == 4096


def test_tier_b_hello_reads_a_line_and_echoes():
    for i in lang.all_ids()[9:]:
        assert "echo: " in lang.spec(i).hello
