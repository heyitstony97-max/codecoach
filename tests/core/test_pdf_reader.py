"""PDF import through pdftotext: every page with a text layer is read, page numbers are kept for
citations, pages with no text layer are reported (never invented), and the old all-or-nothing failure
on mixed PDFs is gone. Fixtures are tiny hand-built PDFs (no library needed)."""

import pytest

from codecoach import pdf
from codecoach.core.passages import split_document
from codecoach.service import ServiceError, read_local_path

pytestmark = pytest.mark.skipif(not pdf.available(), reason="pdftotext not installed")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """pages: each a list of paragraphs; an empty list is a page with no text layer (like a scan)."""
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, paras in enumerate(pages):
        ops, y = [], 720
        for para in paras:
            for line in para.split("\n"):
                ops.append(f"BT /F1 12 Tf 72 {y} Td ({_esc(line)}) Tj ET")
                y -= 16
            y -= 30
        stream = "\n".join(ops).encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


P1 = ["The last spike was made of 17.6 karat gold.", "It was dropped into a pre-drilled hole."]
P3 = ["The ceremony took place at Promontory Summit."]


@pytest.fixture
def mixed_pdf(tmp_path):
    p = tmp_path / "mixed.pdf"
    p.write_bytes(make_pdf([P1, [], P3]))      # page 2 is image-only (no text layer)
    return str(p)


def test_mixed_pdf_reads_every_text_page_and_reports_the_blank_one(mixed_pdf):
    r = pdf.read_pdf(mixed_pdf)
    assert r.pages == 3 and r.empty_pages == [2]
    pages = r.text.split("\f")
    assert "17.6 karat gold" in pages[0] and pages[1].strip() == "" and "Promontory Summit" in pages[2]
    assert pdf.skipped_pages_note(r.empty_pages, r.pages) == \
        "Page 2 of 3 has no text (images or scans), so CodeCoach could not read it."


def test_passages_cite_their_page(mixed_pdf):
    ps = split_document("mixed-pdf", pdf.read_pdf(mixed_pdf).text, "text")
    locs = {p.location for p in ps}
    assert any("p. 1" in loc for loc in locs) and any("p. 3" in loc for loc in locs)
    assert not any("p. 2" in loc for loc in locs), "a page with no text must yield no passage"
    assert any("17.6 karat gold" in p.text for p in ps)


def test_all_scanned_pdf_says_so(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(make_pdf([[], []]))
    with pytest.raises(pdf.PdfError) as e:
        pdf.read_pdf(str(p))
    assert e.value.code == "pdf_no_text"
    with pytest.raises(ServiceError) as se:
        read_local_path(str(p))
    assert se.value.code == "pdf_no_text" and se.value.status == 422


def test_damaged_pdf_is_a_clear_error(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4\nthis is not a pdf\n")
    with pytest.raises(pdf.PdfError) as e:
        pdf.read_pdf(str(p))
    assert e.value.code in ("pdf_unreadable", "pdf_no_text")


def test_read_local_path_reads_pdfs_now(mixed_pdf):
    text = read_local_path(mixed_pdf)
    assert "17.6 karat gold" in text and "\f" in text


def test_rejoin_joins_a_split_sentence_and_keeps_every_word():
    page = "Everyone is trying to\n\nshow his zeal in worshiping this great road.\n\nA new paragraph starts here."
    out = pdf.rejoin_page(page)
    assert out == "Everyone is trying to\nshow his zeal in worshiping this great road.\n\nA new paragraph starts here."
    assert out.split() == page.split()
    assert pdf.rejoin_page("It ended.\n\nthen lower case") == "It ended.\n\nthen lower case"


def test_fragments_are_not_passages_but_stay_in_the_text():
    for frag in ("39", "North n lde", "Stanford University", "Contents Mission . . . . . . . . . . . . . . 1 2 3",
                 "TABLE OF CONTENTS INTRODUCTION ...................................."):
        assert pdf.is_fragment(frag), frag
    for prose in ("Chinese at work on the Central Pacific", "Three percent are disabled.",
                  "When the telegrapher's three dots flashed coast to coast from Promontory Summit"):
        assert not pdf.is_fragment(prose), prose


def test_note_lists_pages_and_caps():
    assert pdf.skipped_pages_note([]) is None
    assert pdf.skipped_pages_note([3, 17]) == "Pages 3, 17 have no text (images or scans), so CodeCoach could not read them."
    many = pdf.skipped_pages_note(list(range(1, 20)), 40) or ""
    assert many.startswith("Pages 1, 2, 3") and "and 7 more of 40" in many


def test_glue_prefers_pdftotext_over_hermes_read_file(mixed_pdf):
    from codecoach.hermes_glue import make_ctx_reader

    class Ctx:
        def dispatch_tool(self, *a, **k):
            raise AssertionError("Hermes read_file must not be used when pdftotext is installed")

    assert "Promontory Summit" in make_ctx_reader(Ctx())(mixed_pdf)


def test_import_records_the_skipped_pages_note(mixed_pdf, tmp_path):
    import sys
    from pathlib import Path

    from codecoach.core.store import Store
    from codecoach.service import CodeCoachService

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "importer"))
    from importer_helpers import scripted  # noqa: E402

    svc = CodeCoachService(Store(tmp_path / "a.db"), scripted(), background=False, auto_bank=False)
    job = svc.start_import(title="Mixed", path=mixed_pdf, mode="strict")
    j = svc.store.get_job(job)
    assert j is not None and j.status == "done", j
    assert j.course_id
    docs = svc.sources(j.course_id)
    assert len(docs) == 1 and docs[0].note == "Page 2 of 3 has no text (images or scans), so CodeCoach could not read it."
