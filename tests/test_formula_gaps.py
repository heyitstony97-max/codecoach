"""PDF equations stored as pictures: found, marked on the passage, noted, and no false marks on prose."""
import glob, os
import pytest
from codecoach import pdf
from codecoach.core.passages import split_document
from codecoach.core.store import Store

SUBJECTS = os.path.join(os.path.dirname(__file__), "..", "devtools", "e2e", "subjects")


def _pdfs(*names):
    out = []
    for n in names:
        out += glob.glob(os.path.join(SUBJECTS, "*", n))
    return out


@pytest.mark.parametrize("name", ["plato-apology.pdf", "dmv-ca-excerpt.pdf", "usgs-this-dynamic-earth-excerpt.pdf",
                                  "openstax-biology-2e-ch8-photosynthesis-excerpt.pdf",
                                  "openstax-chemistry-2e-ch4-stoichiometry-excerpt.pdf"])
def test_prose_pdfs_get_no_marks(name):
    files = _pdfs(name)
    if not files:
        pytest.skip("fixture pdf not fetched")
    assert pdf.count_formula_gaps(pdf.read_pdf(files[0]).text) == 0


def test_physics_pdf_has_marks():
    files = _pdfs("openstax-college-physics-2e-ch2-kinematics-excerpt.pdf")
    if not files:
        pytest.skip("fixture pdf not fetched")
    assert pdf.count_formula_gaps(pdf.read_pdf(files[0]).text) > 5


def test_mark_joins_pair_with_double_space():
    page = "Linear equations may take the form\nand are solved using algebra.\n"
    out, n = pdf.mark_formula_gaps(page, [("take the form", "and are solved")])
    assert n == 1 and "form  and are solved" in out


def test_formula_gaps_in_detects_side_by_side_cut_only():
    # (xMin, yMin, xMax, yMax, text)
    cut = [(10, 100, 200, 112, "equations may take the form"), (260, 100, 400, 112, "and are solved here")]
    assert pdf.formula_gaps_in(cut) == [("take the form", "and are solved")]
    prose = [(10, 100, 200, 112, "a line that ends the sentence."), (260, 100, 400, 112, "and another")]
    assert pdf.formula_gaps_in(prose) == []
    below = [(10, 100, 200, 112, "equations may take the form"), (10, 114, 200, 126, "and are solved here")]
    assert pdf.formula_gaps_in(below) == []


def test_note_text():
    assert "3 places in this PDF have equations stored as pictures; CodeCoach can't read them." == pdf.formula_gap_note(3)
    assert pdf.formula_gap_note(0) is None


def test_passage_flag_roundtrips_store(tmp_path):
    from codecoach.core.models import Doc, Course
    ps = split_document("d", "May take the form  and are solved.\n\nPlain paragraph here.", "pdf_text")
    ps = [p.model_copy(update={"formula_missing": bool(pdf.GAP_RE.search(p.text))}) for p in ps]
    assert [p.formula_missing for p in ps] == [True, False]
    st = Store(str(tmp_path / "s.db"))
    st.put_passages(ps)
    assert [st.get_passage(p.id).formula_missing for p in ps] == [True, False]
