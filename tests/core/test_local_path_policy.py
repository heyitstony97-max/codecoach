"""Local path policy (B5): a prompt-injected agent calling codecoach_import on a secret must get a refusal.

Canaries only: every file here is planted under pytest's tmp_path. Nothing reads a real secret.
"""

import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

from codecoach.service import MAX_FILE_BYTES, ServiceError, read_local_path


def _refused(path, code=None):
    with pytest.raises(ServiceError) as ei:
        read_local_path(str(path))
    assert 400 <= ei.value.status < 500
    if code:
        assert ei.value.code == code, ei.value.code
    assert "CANARY" not in ei.value.message
    return ei.value


def test_plain_text_and_markdown_still_import(tmp_path):
    (tmp_path / "a.md").write_text("# Heading\nBody text.\n")
    (tmp_path / "b.txt").write_text("plain")
    assert read_local_path(str(tmp_path / "a.md")).startswith("# Heading")
    assert read_local_path(str(tmp_path / "b.txt")) == "plain"


def test_canary_secrets_by_name_are_refused(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("CANARY-SSH")
    (tmp_path / ".env").write_text("CANARY_KEY=abc")
    _refused(ssh / "id_rsa")
    _refused(tmp_path / ".env")


def test_symlink_named_md_pointing_at_a_secret_is_refused(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("CANARY-SSH")
    link = tmp_path / "notes.md"
    link.symlink_to(ssh / "id_rsa")
    _refused(link)


def test_symlink_named_md_pointing_into_a_secret_dir_is_refused(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "readme.md").write_text("CANARY-SSH")
    link = tmp_path / "notes.md"
    link.symlink_to(ssh / "readme.md")
    _refused(link)


def test_hidden_file_even_with_good_extension_is_refused(tmp_path):
    (tmp_path / ".notes.md").write_text("CANARY hidden")
    _refused(tmp_path / ".notes.md")


def test_secret_dir_with_good_extension_is_refused(tmp_path):
    d = tmp_path / ".aws"
    d.mkdir()
    (d / "credentials.txt").write_text("CANARY")
    _refused(d / "credentials.txt")


def test_symlink_to_an_ordinary_note_is_fine(tmp_path):
    (tmp_path / "real.md").write_text("ok text")
    (tmp_path / "alias.md").symlink_to(tmp_path / "real.md")
    assert read_local_path(str(tmp_path / "alias.md")) == "ok text"


def test_directory_is_refused(tmp_path):
    (tmp_path / "folder.md").mkdir()
    _refused(tmp_path / "folder.md")
    _refused(tmp_path)


@pytest.mark.parametrize("p", ["/dev/zero", "/dev/urandom", "/dev/stdin", "/proc/self/environ",
                               "/proc/self/cmdline", "/proc/self/status", "/sys/kernel/notes",
                               "/proc/self/cwd/x.md", "/dev/shm/x.md", "/proc/self/root/etc/hostname"])
def test_dev_proc_sys_are_refused_by_policy_without_reading(p):
    t0 = time.time()
    _refused(p)
    assert time.time() - t0 < 2


def test_dev_zero_through_a_symlink_with_a_good_name(tmp_path):
    link = tmp_path / "zero.txt"
    link.symlink_to("/dev/zero")
    t0 = time.time()
    _refused(link)
    assert time.time() - t0 < 2


def test_fifo_is_refused_without_hanging(tmp_path):
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)
    t0 = time.time()
    e = _refused(fifo)
    assert time.time() - t0 < 2
    assert "regular file" in e.message


def test_socket_file_is_refused():
    import tempfile
    p = Path(tempfile.mkdtemp(prefix="sk", dir="/tmp")) / "sock.txt"      # AF_UNIX paths are short
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(p))
    try:
        assert stat.S_ISSOCK(os.stat(p).st_mode)
        _refused(p)
    finally:
        s.close()


def test_file_swapped_for_a_fifo_between_check_and_open_does_not_hang(tmp_path, monkeypatch):
    """TOCTOU: the check saw a file; by open time it is a FIFO. open() must not block on it."""
    import codecoach.service as svc
    fifo = tmp_path / "late.txt"
    os.mkfifo(fifo)
    real_isfile = os.path.isfile
    monkeypatch.setattr(svc.os.path, "isfile", lambda p: True if str(p) == str(fifo) else real_isfile(p))
    done = []
    t = threading.Thread(target=lambda: done.append(_try(read_local_path, str(fifo))), daemon=True)
    t.start()
    t.join(3)
    assert done, "read_local_path hung on a FIFO"


def _try(fn, *a):
    try:
        return fn(*a)
    except ServiceError as e:
        return e


def test_oversize_file_is_refused_by_size_not_read(tmp_path):
    big = tmp_path / "big.md"
    with open(big, "wb") as f:
        f.truncate(MAX_FILE_BYTES + 1)          # sparse: no disk use
    e = _refused(big, "too_large")
    assert "20 MB" in e.message


def test_file_that_grows_past_the_cap_after_the_size_check_is_still_bounded(tmp_path, monkeypatch):
    import codecoach.service as svc
    p = tmp_path / "grow.md"
    p.write_text("x" * 100)
    monkeypatch.setattr(svc, "MAX_FILE_BYTES", 50)
    monkeypatch.setattr(svc.os.path, "getsize", lambda _p: 10)       # the size check is fooled
    _refused(p, "too_large")


def test_binary_renamed_md_is_refused(tmp_path):
    p = tmp_path / "pic.md"
    p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + os.urandom(64))
    e = _refused(p, "not_text")
    assert "text" in e.message.lower()


def test_latin1_or_utf8_text_with_odd_bytes_still_reads(tmp_path):
    p = tmp_path / "ok.txt"
    p.write_bytes("caf\u00e9 \u2014 na\u00efve\n".encode("utf-8") + b"\xff stray\n")
    assert "caf\u00e9" in read_local_path(str(p))


@pytest.mark.parametrize("name", ["a.docx", "a.exe", "a.json", "a.py", "a.sh", "a", "id_rsa", "a.pem", "a.key"])
def test_unsupported_extensions_get_a_friendly_refusal(tmp_path, name):
    (tmp_path / name).write_text("CANARY")
    e = _refused(tmp_path / name, "unsupported_file")
    assert ".md" in e.message and ".pdf" in e.message


def test_missing_file_and_bad_input(tmp_path):
    _refused(tmp_path / "nope.md", "no_file")
    for bad in ("", "   ", None, 5, "a\x00b.md"):
        with pytest.raises(ServiceError):
            read_local_path(bad)


def test_the_import_route_and_tool_use_the_same_policy(tmp_path):
    """start_import (HTTP and codecoach_import) goes through the service's path reader."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "importer"))
    from importer_helpers import new_store, scripted
    from codecoach.service import CodeCoachService
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("CANARY-SSH")
    link = tmp_path / "x.md"
    link.symlink_to(ssh / "id_rsa")
    svc = CodeCoachService(new_store(), scripted(), background=False)
    with pytest.raises(ServiceError):
        svc.start_import(title="T", path=str(link))
    with pytest.raises(ServiceError):
        svc.start_import(title="T", text="some text", syllabus_path=str(link))


def test_pdf_reader_through_hermes_also_applies_the_policy(tmp_path, monkeypatch):
    """With no pdftotext the glue reads PDFs through Hermes read_file; the policy must come first."""
    from codecoach import hermes_glue, pdf
    monkeypatch.setattr(pdf, "available", lambda: False)
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "k.pdf").write_text("CANARY")
    called = []

    class Ctx:
        def dispatch_tool(self, *a, **k):
            called.append(a)
            return "{}"
    reader = hermes_glue.make_ctx_reader(Ctx())
    with pytest.raises(ServiceError):
        reader(str(ssh / "k.pdf"))
    with pytest.raises(ServiceError):
        reader("/proc/self/environ.pdf")
    assert not called
