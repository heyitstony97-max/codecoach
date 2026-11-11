"""Languages CodeCoach can run: specs, toolchain discovery, and the hello-world proof.

Import only from here:

    all_ids() -> list[str]                           every id that has a spec, display order
    spec(lang_id) -> LangSpec                        raises UnknownLanguage (a ValueError)
    status(lang_id, *, refresh=False) -> LangStatus
    statuses(*, refresh=False) -> list[LangStatus]
    ready_ids() -> list[str]
    configure(cache_dir) -> None                     where the probe cache lives; None = memory only
"""

from __future__ import annotations

from codecoach.lang.spec import AS_CEILING_MB, LangSpec, Limits, UnknownLanguage, all_ids
from codecoach.lang.probe import (LangStatus, build_cache_dir, configure, not_ready_reason, ready_ids,
                                   spec_with_measure, statuses, toolchain_for)
from codecoach.lang.probe import status as status  # noqa: F401


def spec(lang_id: str) -> LangSpec:
    """The spec of one language; `live_run` is the measured value once the language has been probed."""
    return spec_with_measure(lang_id)


__all__ = ["AS_CEILING_MB", "LangSpec", "LangStatus", "Limits", "UnknownLanguage", "all_ids", "spec", "status", "statuses",
           "ready_ids", "configure", "toolchain_for", "build_cache_dir", "not_ready_reason"]
