"""The desktop view may only paint with theme variables the Hermes desktop app actually defines.

An undefined variable silently falls back, and the fallback looked right only in our own rig: the Sources panel
used `var(--ui-surface-primary, var(--ui-base))`, the host has no --ui-surface-primary, and the host's --ui-base is
the theme's FOREGROUND seed, so on the light theme the panel was painted near-black (Anthony, 2026-11-06).

HOST_TOKENS is the list of --ui-* variables defined in the desktop app's src/styles.css (Hermes desktop, Nov 2026).
If the host adds a token you need, add it here after checking it exists there.
"""

import re
from pathlib import Path

PLUGIN_JS = Path(__file__).resolve().parents[2] / "desktop" / "plugin.js"

HOST_TOKENS = set("""
--ui-accent --ui-accent-secondary --ui-base --ui-bg-card --ui-bg-chrome --ui-bg-editor --ui-bg-elevated --ui-bg-input
--ui-bg-primary --ui-bg-quaternary --ui-bg-quinary --ui-bg-secondary --ui-bg-sidebar --ui-bg-tertiary --ui-blue
--ui-chat-bubble-background --ui-chat-bubble-opaque-background --ui-chat-surface-background
--ui-control-active-background --ui-control-hover-background --ui-cyan --ui-diff-add-background --ui-diff-add-border
--ui-diff-add-foreground --ui-diff-remove-background --ui-diff-remove-border --ui-diff-remove-foreground
--ui-editor-surface-background --ui-green --ui-inline-code-background --ui-inline-code-foreground --ui-orange
--ui-purple --ui-red --ui-row-active-background --ui-row-hover-background --ui-row-open-background
--ui-sash-hover-background --ui-sash-hover-border --ui-selection-background --ui-sidebar-surface-background
--ui-stroke-primary --ui-stroke-quaternary --ui-stroke-secondary --ui-stroke-tertiary --ui-surface-background
--ui-tab-hover-darken --ui-terminal-surface-background --ui-text-primary --ui-text-quaternary --ui-text-secondary
--ui-text-tertiary --ui-warm --ui-widget-surface-background --ui-yellow
""".split())


def _code() -> str:
    # comments may name a token to explain it; only code counts
    src = PLUGIN_JS.read_text()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split("//", 1)[0] if "://" not in line else line for line in src.splitlines())


def test_every_theme_variable_is_defined_by_the_host():
    used = set(re.findall(r"--ui-[a-z0-9-]+", _code()))
    assert used - HOST_TOKENS == set(), f"not defined by the desktop app: {sorted(used - HOST_TOKENS)}"


def test_the_foreground_seed_is_never_used_directly():
    # --ui-base is the text colour seed in the host; text colours come from --ui-text-*, fills from surface tokens
    assert "--ui-base" not in _code()
