"""Registry, probe cache and payload validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from codecoach import lang
from codecoach.core.models import CodePayload, CodeTest

TIER_A = ["c", "cpp", "python", "javascript", "java", "go", "rust", "ruby", "bash"]


def test_tier_a_registered_in_order():
    assert lang.all_ids()[:9] == TIER_A


def test_unknown_language_is_a_value_error():
    with pytest.raises(lang.UnknownLanguage):
        lang.spec("cobol")
    assert issubclass(lang.UnknownLanguage, ValueError)


def test_payload_language_validated():
    kw = dict(starter="", visible_tests=[CodeTest(name="t", stdin="", expected_stdout="")], reference_solution="x")
    assert CodePayload(language="rust", **kw).language == "rust"
    with pytest.raises(ValidationError):
        CodePayload(language="cobol", **kw)


def test_specs_have_the_public_fields():
    for i in TIER_A:
        s = lang.spec(i)
        assert s.ext.startswith(".") and s.starter is not None and s.style_note and s.comment


def test_rlimit_as_never_above_4gib():
    for i in TIER_A:
        lim = lang.spec(i).limits
        assert lim.compile_as_mb <= lang.AS_CEILING_MB == 4096


def test_probe_cache_roundtrip(tmp_path):
    lang.configure(tmp_path)
    try:
        first = lang.status("bash", refresh=True)
        if first.status != "ready":
            pytest.skip("bash not ready")
        assert (tmp_path / "lang-probe.json").exists()
        lang.configure(tmp_path)            # forget memory; the disk cache answers
        again = lang.status("bash")
        assert (again.status, again.version) == (first.status, first.version)
    finally:
        lang.configure(None)
