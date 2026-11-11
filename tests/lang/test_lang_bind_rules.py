"""Toolchain bind rules with fake paths (contract section 3): nothing real is touched."""
from __future__ import annotations

import pytest

from codecoach.lang import toolchain as tc

HOME = "/home/fakeuser"


@pytest.mark.parametrize("root,word", [
    (HOME, "home folder"),
    ("/home", "home folder"),
    ("/", "whole disk"),
    (HOME + "/.ssh", ".ssh"),
    (HOME + "/.ssh/keys", ".ssh"),
    (HOME + "/.cargo", ".cargo"),
    (HOME + "/.cargo/bin", ".cargo"),
    (HOME + "/.config/foo", ".config"),
    (HOME + "/.npm", ".npm"),
    (HOME + "/.gnupg", ".gnupg"),
    (HOME + "/.aws/x", ".aws"),
    ("/etc", "system folder"),
])
def test_dangerous_roots_are_refused_with_a_plain_reason(root, word):
    why = tc.bind_refusal(root, home=HOME)
    assert why and word in why


def test_a_trailing_slash_or_dotdot_does_not_sneak_past():
    assert tc.bind_refusal(HOME + "/", home=HOME)
    assert tc.bind_refusal(HOME + "/tools/../.ssh", home=HOME)


@pytest.mark.parametrize("root", [HOME + "/.local/go", HOME + "/.hermes/node", "/opt/toolchains/rust"])
def test_a_plain_toolchain_root_is_allowed(root):
    assert tc.bind_refusal(root, home=HOME) is None


def test_usr_needs_no_bind_and_others_do():
    assert tc.needs_bind("/usr/bin/gcc") is False
    assert tc.needs_bind("/opt/go/bin/go") is True
