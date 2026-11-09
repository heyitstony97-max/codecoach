"""A5c quality gates: green starters, copied hidden tests, course language, outline-only objectives."""

import random

import pytest

from codecoach.core import registry
from codecoach.core.llm import FakeLLM
from codecoach.core.models import Course, CourseMap, Lesson, Objective, PerTestResult, Unit
from codecoach.core.registry import CheckResult, register_checker
from codecoach.core.store import Store
from codecoach.gen import build_bank, generate_one
from codecoach.gen.generate import is_course_meta

OBJ = Objective(id="oc", unit="u1", lesson="l1", verb="write-code", kind="code",
                statement="Write a program that prints the sum of two numbers with printf.")
REF = "int main(void){return 0;}"


@pytest.fixture(autouse=True)
def clean_registry():
    registry._clear_for_tests()
    yield
    registry._clear_for_tests()


def draft(**kw):
    d = {"prompt": "Sum two ints.", "language": "c", "starter": "int main(void){return 1;}",
         "reference_solution": REF,
         "visible_tests": [{"name": "v1", "stdin": "1 2\n", "expected_stdout": "3\n"}],
         "hidden_tests": [{"name": "h1", "stdin": "5 5\n", "expected_stdout": "10\n"}]}
    d.update(kw)
    return d


def checker(visible_fail=True, hidden_fail=True):
    def fn(item, response, *, mode="submit"):
        if response == item.payload.reference_solution:
            return CheckResult(ok=True, score=1.0)
        pt = [PerTestResult(name="v1", passed=not visible_fail, visible=True),
              PerTestResult(name="h1", passed=not hidden_fail, visible=False)]
        return CheckResult(ok=False, score=0.0, per_test=pt)
    return fn


def gen(d, *, obj=OBJ, code_language=None):
    llm = FakeLLM(lambda purpose, text: d)
    r = generate_one(fmt="code", objective=obj, passages=[], llm=llm, mode="open", rng=random.Random(1),
                     code_language=code_language)
    return r, llm


def test_starter_passing_every_visible_test_is_dropped():
    register_checker("code", checker(visible_fail=False))
    r, _ = gen(draft())
    assert r.drops[0].gate_code == "weak_tests"
    assert "starter already passes the visible tests" in r.drops[0].detail


def test_starter_failing_a_visible_test_is_kept():
    register_checker("code", checker())
    r, _ = gen(draft())
    assert r.items and not r.drops


def test_hidden_tests_that_copy_the_visible_ones_are_dropped():
    register_checker("code", checker())
    v = [{"name": "v1", "stdin": "1 2\n", "expected_stdout": "3\n"}]
    r, _ = gen(draft(hidden_tests=[{"name": "h1", "stdin": "1 2", "expected_stdout": "3"}]))
    assert r.drops[0].gate_code == "weak_tests" and "copy" in r.drops[0].detail
    r, _ = gen(draft(visible_tests=v, hidden_tests=[v[0], {"name": "h2", "stdin": "2 2\n", "expected_stdout": "4\n"}]))
    assert r.items, r.drops          # one fresh hidden test is enough


def test_harness_tests_compared_too():
    register_checker("code", checker())
    h = "int main(void){return f(1)==1?0:1;}"
    r, _ = gen(draft(visible_tests=[{"name": "v", "harness": h}], hidden_tests=[{"name": "h", "harness": h}]))
    assert r.drops[0].gate_code == "weak_tests"
    r, _ = gen(draft(visible_tests=[{"name": "v", "harness": h}],
                     hidden_tests=[{"name": "h", "harness": h.replace("f(1)==1", "f(2)==2")}]))
    assert r.items


def test_wrong_language_draft_is_schema_invalid():
    register_checker("code", checker())
    r, llm = gen(draft(language="python"), code_language="c")
    assert not r.items and r.drops[0].gate_code == "schema_invalid"
    assert 'language to "c"' in llm.calls[0].instructions


def test_right_language_passes_and_instructions_name_it():
    register_checker("code", checker())
    r, _ = gen(draft(), code_language="c")
    assert r.items


@pytest.mark.parametrize("stmt,meta", [
    ("The C Basics outline is organized into eight units.", True),
    ("Know that this course has eight units", True),
    ("Write a program that prints a line with printf.", False),
    ("Compile with gcc -Wall and run the course example.", False),
    ("Explain the outline of a for loop.", False),
])
def test_course_meta_detection(stmt, meta):
    assert is_course_meta(stmt) is meta


def test_meta_objective_gets_no_code_item():
    register_checker("code", checker())
    o = OBJ.model_copy(update={"statement": "The C Basics outline is organized into eight units."})
    r, llm = gen(draft(), obj=o, code_language="c")
    assert not r.items and not r.drops and llm.calls == []


def test_bank_passes_the_course_language_and_skips_meta(tmp_path):
    register_checker("code", checker())
    s = Store(tmp_path / "a.db")
    s.put_course(Course(id="c", title="C", mode="open", code_language="c"))
    meta = OBJ.model_copy(update={"id": "om", "statement": "The outline is organized into eight units."})
    s.save_map(CourseMap(course_id="c", units=[Unit(id="u1", title="U", lessons=[
        Lesson(id="l1", title="L", objectives=[OBJ, meta])])]))
    seen = []

    def fn(purpose, text):
        seen.append(text)
        return draft(language="python")
    rep = build_bank(s, FakeLLM(fn), "c", per_objective=1, formats=["code"])
    assert rep.kept == 0 and rep.drops_by_code["schema_invalid"] >= 1
    assert "om" in " ".join(rep.skipped_meta) and all("eight units" not in t for t in seen)
