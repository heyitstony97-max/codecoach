"""Bind rules inside the home folder, on a real temp tree standing in for HOME.

Found by the orchestrator's probe of lang-a: a toolchain whose binary sat at ~/.local/bin/<tool> would have had
all of ~/.local (keyrings, app data) shared read-only with the sandbox, and ~/.hermes, ~/.cache, ~/Documents
and ~/projects were shareable the same way. Inside home a root must look like a toolchain and nothing else.
"""
from __future__ import annotations

import os

import pytest

from codecoach.lang import toolchain as tc


def mk(home, *parts):
    p = os.path.join(home, *parts)
    os.makedirs(p, exist_ok=True)
    return p


@pytest.fixture
def home(tmp_path):
    h = str(tmp_path / "home" / "learner")
    os.makedirs(h)
    return h


@pytest.mark.parametrize("name", [".local", ".hermes", ".cache", ".mozilla", ".docker", ".kube",
                                  ".password-store", ".bun"])
def test_a_hidden_folder_directly_in_home_is_never_shared(home, name):
    root = mk(home, name)
    mk(root, "bin")
    why = tc.bind_refusal(root, home=home)
    assert why, f"{name} would be shared"
    assert why[0].islower() and "\n" not in why        # it completes "its folder (...) <why>, so CodeCoach ..."


@pytest.mark.parametrize("parts", [(".local", "share"), (".local", "share", "keyrings"), (".local", "state")])
def test_local_share_and_state_are_never_shared(home, parts):
    assert tc.bind_refusal(mk(home, *parts), home=home)


@pytest.mark.parametrize("extra", ["Invoices", "client-site", "state", ".git"])
def test_a_folder_holding_more_than_a_toolchain_is_refused(home, extra):
    root = mk(home, "projects")
    mk(root, "bin")
    mk(root, extra)
    why = tc.bind_refusal(root, home=home)
    assert why and "besides the toolchain" in why and extra in why


@pytest.mark.parametrize("name", [".env", "auth.json", ".netrc", ".npmrc", "keyrings"])
def test_a_root_with_a_credential_file_at_the_top_is_refused(home, name):
    root = mk(home, "tools", "node")
    mk(root, "bin")
    with open(os.path.join(root, name), "w") as f:
        f.write("x")
    why = tc.bind_refusal(root, home=home)
    assert why and name in why


@pytest.mark.parametrize("parts,dirs,files", [
    (("go",), ("api", "bin", "doc", "lib", "misc", "pkg", "src", "test"), ("VERSION", "LICENSE", "go.env")),
    ((".rustup", "toolchains", "stable-x86_64-unknown-linux-gnu"), ("bin", "etc", "lib", "libexec", "share"), ()),
    ((".hermes", "node"), ("bin", "include", "lib", "share"), ("LICENSE", "README.md", "CHANGELOG.md")),
    ((".nvm", "versions", "node", "v22.1.0"), ("bin", "include", "lib", "share"), ()),
    ((".sdkman", "candidates", "java", "21"), ("bin", "conf", "include", "jmods", "legal", "lib", "man"), ("release",)),
    ((".local", "zig"), ("lib", "doc"), ("zig", "LICENSE")),
    ((".local", "share", "uv", "python", "cpython-3.12.4-linux-x86_64-gnu"), ("bin", "include", "lib", "share"), ()),
    ((".local", "share", "mise", "installs", "node", "22.1.0"), ("bin", "include", "lib", "share"), ()),
    ((".asdf", "installs", "erlang", "27"), ("bin", "erts-15.0", "lib", "releases", "usr", "misc"), ("Install",)),
])
def test_real_toolchain_layouts_are_still_shared(home, parts, dirs, files):
    root = mk(home, *parts)
    for d in dirs:
        mk(root, d)
    for name in files:
        with open(os.path.join(root, name), "w") as f:
            f.write("x")
    assert tc.bind_refusal(root, home=home) is None


def test_outside_home_the_layout_rule_does_not_apply(home, tmp_path):
    root = str(tmp_path / "opt" / "go")
    os.makedirs(os.path.join(root, "bin"))
    os.makedirs(os.path.join(root, "anything-else"))
    assert tc.bind_refusal(root, home=home) is None


def test_this_machines_rust_toolchain_is_still_found():
    d = tc.discover("rust")
    if d.status == "missing":
        pytest.skip("no Rust here")
    assert d.status == "found", d.reason
