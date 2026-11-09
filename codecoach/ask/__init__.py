"""Ask your materials: a question answered only with sentences that code has checked against a quote.

The model proposes sentences with quotes; codecoach/ask/check.py keeps a sentence only if every check passes.
See docs/SPEC.md, section "Ask"."""

from codecoach.ask.engine import (ASK_STAGES, AskEngine, AskRequest, normalise_question,  # noqa: F401
                               MAX_HISTORY, MAX_QUESTION_CHARS)
