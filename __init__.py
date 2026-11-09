"""CodeCoach: a grounded course tutor for Hermes. register(ctx) wires tools, the /codecoach command and the
auxiliary LLM task. All logic lives under codecoach/; only codecoach/hermes_glue.py touches Hermes."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:  # the loader imports this file as a package; make `import codecoach` work
    sys.path.insert(0, _here)


def register(ctx) -> None:
    from codecoach import hermes_glue, tools

    hermes_glue.register_aux_task(ctx)
    get_svc = lambda: hermes_glue.build_service_for_ctx(ctx)  # noqa: E731  (lazy: opens the store on first use)

    handlers = tools.make_handlers(get_svc)
    for name in tools.TOOL_NAMES:
        ctx.register_tool(name=name, toolset="codecoach", schema=tools.SCHEMAS[name], handler=handlers[name],
                          description=tools.SCHEMAS[name]["description"])

    ctx.register_command("codecoach", tools.make_command(get_svc),
                         description="CodeCoach tutor: courses, import, next, answer, hint, progress, map",
                         args_hint="[courses|import <path>|next|answer <text>|hint|progress|map]")
