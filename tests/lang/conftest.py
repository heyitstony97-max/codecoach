"""Shared pieces for the language tests: skip a language when its toolchain is not ready on this machine."""
from __future__ import annotations

import pytest

from codecoach import lang
from codecoach.check import code_runner as cr


def _need(lang_id: str) -> None:
    if cr.sandbox_status() != "bwrap":
        pytest.skip("no bubblewrap sandbox on this machine")
    st = lang.status(lang_id)
    if st.status != "ready":
        pytest.skip(f"{lang_id}: {st.status} ({st.reason})")


@pytest.fixture(params=lang.all_ids())
def lid(request) -> str:
    _need(request.param)
    return request.param


@pytest.fixture
def canary(tmp_path_factory):
    d = tmp_path_factory.mktemp("home-like", numbered=True)
    f = d / "secret.txt"
    f.write_text("CANARY-7f3a91\n")
    return str(f)
