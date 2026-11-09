"""Grounding: the source gate. Pure functions, no LLM, no Hermes."""

from .gate import (Failure, check_citations, check_framing, check_item, first_failure, quote_long_enough,
                   structural, text_found)

__all__ = ["Failure", "check_citations", "check_framing", "check_item", "first_failure",
           "quote_long_enough", "structural", "text_found"]
