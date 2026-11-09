"""Break-it (world): a Latin-1 / Windows-1252 text file must import with its accents, not U+FFFD garbage."""

from codecoach.service import read_local_path


def test_latin1_file_keeps_accents(tmp_path):
    p = tmp_path / "es.txt"
    p.write_bytes("El ni\u00f1o est\u00e1 en M\u00e9xico. \u00bfD\u00f3nde est\u00e1?".encode("latin-1"))
    t = read_local_path(str(p))
    assert t == "El ni\u00f1o est\u00e1 en M\u00e9xico. \u00bfD\u00f3nde est\u00e1?"
    assert "\ufffd" not in t


def test_cp1252_curly_quotes_survive(tmp_path):
    p = tmp_path / "q.txt"
    p.write_bytes("\u201cHello\u201d \u2014 it\u2019s fine".encode("cp1252"))
    assert read_local_path(str(p)) == "\u201cHello\u201d \u2014 it\u2019s fine"


def test_utf8_bom_and_utf16_and_plain_utf8(tmp_path):
    s = "Caf\u00e9 \u00f1"
    for name, raw in [("a", b"\xef\xbb\xbf" + s.encode()), ("b", s.encode("utf-16")), ("c", s.encode())]:
        p = tmp_path / f"{name}.txt"
        p.write_bytes(raw)
        assert read_local_path(str(p)) == s


def test_crlf_file_is_read_whole(tmp_path):
    p = tmp_path / "w.txt"
    p.write_bytes(b"Line one.\r\n\r\nLine two.\r\n")
    assert "Line two." in read_local_path(str(p))
