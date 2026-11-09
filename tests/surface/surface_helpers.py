"""Shared setup for the surface tests: a service over a temp store, with a fake LLM and no threads."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPT = HERE.parent / "adapt"
if str(ADAPT) not in sys.path:
    sys.path.insert(0, str(ADAPT))

from adapt_helpers import GRAPH12, T0, Clock, build_course, make_item, new_store, oid  # noqa: E402,F401

from codecoach.core.llm import FakeLLM  # noqa: E402
from codecoach.core.models import (Citation, CodePayload, CodeTest, Item, MathPayload, OrderEvent,  # noqa: E402,F401
                                OrderPayload)
from codecoach.service import CodeCoachService, ServiceConfig  # noqa: E402


def make_service(*, llm=None, graph=None, items_per=2, **kw):
    store = new_store()
    cid = build_course(store, graph, items_per=items_per, **kw)
    clock = Clock()
    events: list = []
    svc = CodeCoachService(store, llm or FakeLLM(lambda p, t: None), ServiceConfig(), now_fn=clock,
                        emit=lambda e, p: events.append((e, p)), background=False, auto_bank=False)
    svc.events = events
    return svc, store, cid


def secrets_of(item: Item) -> list[str]:
    """Strings that must not be visible before the item is answered."""
    return [c.quote for c in item.citations]
