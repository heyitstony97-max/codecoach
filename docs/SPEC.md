# CodeCoach SPEC (FROZEN by a1)

This is the contract every lane codes against. It is **frozen** when card a1 completes. Later changes go
through `requests_for_integration` in a handoff (a5 applies them). The code in `codecoach/core/` is the
source of truth for exact types; this file says what the lanes may rely on. Read `docs/CONSTRAINTS.md`
sections 10 and 11 first.

## 1. Module map and owners

(Copied from CONSTRAINTS 11.2.)

| Path | Owner |
|---|---|
| `requirements-dev.txt`, `codecoach/__init__.py`, `codecoach/core/**`, `tests/core/**`, `tests/conftest.py`, `tests/fixtures/api/**`, `docs/SPEC.md` | a1 |
| `codecoach/importer/**`, `tests/importer/**` | a1b |
| `codecoach/ground/**`, `codecoach/gen/**`, `tests/ground/**`, `tests/gen/**` | a2a |
| `codecoach/check/**`, `tests/check/**` | a2b |
| `codecoach/adapt/**`, `tests/adapt/**` | a3 |
| `tests/redteam/**`, `docs/REDTEAM.md` | r1 |
| root `__init__.py`, `plugin.yaml`, `codecoach/service.py`, `codecoach/hermes_glue.py`, `codecoach/tools.py`, `dashboard/**`, `tests/surface/**` | a4a |
| `desktop/**`, `devtools/desktop-harness/**` | a4b |
| `devtools/e2e/**`, `docs/VERIFICATION.md`, applying `requests_for_integration` | a5 |
| `README.md`, `LICENSE`, `demo/**` (not `demo/materials/`), `docs/PR.md`, `docs/catalog-entry/**` | a6 |
| `docs/CONSTRAINTS.md`, `docs/forge/**`, `demo/materials/**` | orchestrator |

Files in `codecoach/core/` (all Hermes-free, pydantic + stdlib only):

| File | What |
|---|---|
| `models.py` | the data contract (section 2) and the REST response models (section 5) |
| `store.py` | `Store(path)` SQLite persistence (section 3) |
| `text.py` | `normalize`, `quote_in`, `stable_hash`, `slugify` |
| `passages.py` | `split_document` |
| `llm.py` | `LLMPort`, `structured_call`, `FakeLLM`, `LLMError`, `LLMUnavailable`, `LLMBadOutput` |
| `registry.py` | checker and generator registries, `CheckResult`, `GenResult`, `NotRegistered` |
| `public.py` | `public_view`, `reveal`, `source_label` |
| `samples.py` | one valid `Item` per format for lane tests: `sample_item(fmt, **overrides)`, `SAMPLE_PAYLOADS`, `SECRET` |

Rules that bind everyone: nothing under `codecoach/` imports Hermes except `codecoach/hermes_glue.py`; no
`pyproject.toml`; items reach clients only through `public_view`; nothing LLM-made reaches the store
without passing a gate or checker.

## 2. Data conventions

- All models are frozen pydantic v2 (`_M`): `extra="forbid"`, immutable. Change a model with
  `item.model_copy(update={...})`.
- Datetimes are timezone-aware UTC. A naive datetime is read as UTC. JSON form is ISO 8601 with `Z`.
- `Item.payload` is a union discriminated by `format`. `payload.format` must equal `Item.format`.
- IDs: `Course.id` and `Doc.id` match `^[a-z0-9][a-z0-9_-]{0,63}$`. Objective ids are
  `<course_id>:o<NN>` by convention (any non-empty string is accepted). Item ids come from `new_id("it")`.
  Job ids are `job_<hex>`.
- **Passage ids**: `<doc>:p<page>:<para>` for text and pdf_text; `<doc>:<heading-slug>:<para>` for
  markdown (repeated headings get `-2`, `-3`; text before the first heading is `intro`). Paragraph
  numbers count within the page (or section). Ids are stable across runs and line-ending styles.
- **Location labels** (human readable): `Ch. 2, p. 14, para 3`; no chapter -> `p. 14, para 3`; no page
  markers in the document -> `para 3`; markdown -> `<Heading>, para 2`. "p. N" appears on every passage of
  a document or on none.
- **Page markers** in input text: a form-feed character, or a line `--- page 12 ---`
  (regex `^\s*-{2,}\s*page\s+(\d+)\s*-{2,}\s*$`, case-insensitive). Without markers everything is page 1.
- Chapter lines recognised: `Chapter 2`, `CHAPTER IV`, `Ch. 5`.
- Plain-English labels (use them verbatim in the UI): `FROM_MATERIALS_LABEL = "From your materials"`,
  `NOT_FROM_MATERIALS_LABEL = "Not from your materials"`, `NOT_COVERED_LABEL = "Not in your materials"`.

### 2.1 Gate reason codes

`GATE_CODES` (tuple, order is the display order) and `GATE_LABELS` (plain English):

| code | label |
|---|---|
| `quote_missing` | Quote not found in your materials |
| `unsupported_fact` | Answer contains a fact (number, date or name) the quote does not support |
| `ambiguous` | More than one answer was supported by the source |
| `bad_cloze` | The blank was not an exact phrase from the source |
| `framing_fact` | Lesson intro added a fact that is not in the source |
| `key_mismatch` | Answer key disagreed with the computed answer |
| `weak_tests` | Code tests were too weak or did not pass the reference solution |
| `self_check_failed` | The question failed its own consistency check |
| `schema_invalid` | The model's output was malformed |

### 2.2 Code test format (ONE concrete format)

`CodeTest(name, stdin="", expected_stdout=None, harness=None)`: exactly one of `expected_stdout` or `harness`.

- **stdio form**: feed `stdin`, compare stdout to `expected_stdout` after stripping trailing whitespace
  from each line and trailing blank lines. Works for both languages.
- **harness form** (`harness` = source text):
  - Python: a module of `assert` statements. The learner's code is importable as `solution`
    (`from solution import *`). Pass = exit status 0.
  - C: a separate translation unit that defines `main()` and calls the learner's functions. The learner
    file must not define `main`. Pass = exit status 0. The checker compiles both files together.

Python example (harness form):

```json
{"language": "python", "starter": "def digit_sum(n):\n    pass\n",
 "visible_tests": [{"name": "small", "harness": "from solution import *\nassert digit_sum(123) == 6\n"}],
 "hidden_tests": [{"name": "zero", "harness": "from solution import *\nassert digit_sum(0) == 0\n"},
                  {"name": "big", "harness": "from solution import *\nassert digit_sum(99999) == 45\n"}],
 "reference_solution": "def digit_sum(n):\n    return sum(int(c) for c in str(n))\n",
 "time_limit_s": 5.0, "mem_mb": 256}
```

C example (stdio form):

```json
{"language": "c",
 "starter": "#include <stdio.h>\n\nint main(void) {\n    int n;\n    if (scanf(\"%d\", &n) != 1) return 1;\n    /* your code here */\n    return 0;\n}\n",
 "visible_tests": [{"name": "small", "stdin": "123\n", "expected_stdout": "6\n"}],
 "hidden_tests": [{"name": "zero", "stdin": "0\n", "expected_stdout": "0\n"},
                  {"name": "big", "stdin": "99999\n", "expected_stdout": "45\n"}],
 "reference_solution": "#include <stdio.h>\nint main(void){int n,s=0;scanf(\"%d\",&n);while(n){s+=n%10;n/=10;}printf(\"%d\\n\",s);return 0;}\n",
 "time_limit_s": 5.0, "mem_mb": 128}
```

Both forms may be mixed within one item. `reference_solution` must pass every visible and hidden test
(that is the `weak_tests` gate, owned by a2b).

## 3. Models (generated from `codecoach/core/models.py`)

Field tables below are generated from the code. Validators not visible in a table:
`CourseMap` rejects duplicate unit/lesson/objective ids, objectives whose `unit`/`lesson` disagree with where
they sit, dangling ids in edges, self-prerequisites and **cycles** (it derives `edges` from
`Objective.prereqs` when absent). `Objective`: a `reading` objective marked `covered` must cite passages;
a `not_covered` one must list none. `Passage.id` must look like `<doc>:<page-or-slug>:<para>`.
`CodeTest` needs exactly one form. `OrderPayload.correct_order` must be a permutation of event indexes.
`MCPayload.correct_index` must be in range. `ClozePayload.text_with_blank` contains `____` exactly once.
`MatchPayload` rejects duplicate left or right sides. `Item`: `payload.format == format`, and
`origin="materials"` needs at least one citation. `ImportRequest` needs exactly one of `text`/`path`.
`NextResponse` carries `lesson`/`item` exactly when `kind` says so.

**Course**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `title` | `str` | required |
| `mode` | `Literal['strict', 'open']` | 'strict' |
| `mode_reason` | `str` | '' |
| `created_at` | `datetime.datetime` | factory |

**Doc**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `course_id` | `str` | required |
| `name` | `str` | required |
| `kind` | `Literal['text', 'markdown', 'pdf_text']` | 'text' |

**Passage**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `doc_id` | `str` | required |
| `location` | `str` | required |
| `text` | `str` | required |

**Citation**

| field | type | default |
|---|---|---|
| `passage_id` | `str` | required |
| `quote` | `str` | required |

**Objective**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `unit` | `str` | required |
| `lesson` | `str` | required |
| `verb` | `str` | required |
| `statement` | `str` | required |
| `kind` | `Literal['reading', 'code', 'math', 'science']` | 'reading' |
| `prereqs` | `list[str]` | factory |
| `passage_ids` | `list[str]` | factory |
| `coverage` | `Literal['covered', 'not_covered']` | 'covered' |

**Lesson**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `title` | `str` | required |
| `objectives` | `list[Objective]` | factory |

**Unit**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `title` | `str` | required |
| `lessons` | `list[Lesson]` | factory |

**Edge** - prereq must be learned before objective.

| field | type | default |
|---|---|---|
| `prereq` | `str` | required |
| `objective` | `str` | required |

**CourseMap**

| field | type | default |
|---|---|---|
| `course_id` | `str` | required |
| `units` | `list[Unit]` | required |
| `edges` | `list[Edge]` | factory |

**MCPayload**

| field | type | default |
|---|---|---|
| `format` | `Literal['mc']` | 'mc' |
| `options` | `list[Annotated[str, FieldInfo(annotation=NoneType, required=True, metadata=[MinLen(min_length=1)])]]` | required |
| `correct_index` | `int` | required |

**ClozePayload**

| field | type | default |
|---|---|---|
| `format` | `Literal['cloze']` | 'cloze' |
| `text_with_blank` | `str` | required |
| `answer` | `str` | required |
| `aliases` | `list[str]` | factory |

**OrderEvent**

| field | type | default |
|---|---|---|
| `label` | `str` | required |
| `date_text` | `str` | required |
| `passage_id` | `str` | required |

**OrderPayload** - `events` is stored in PRESENTATION order. `correct_order` lists indexes into `events`

| field | type | default |
|---|---|---|
| `format` | `Literal['order']` | 'order' |
| `events` | `list[OrderEvent]` | required |
| `correct_order` | `list[int]` | required |

**MatchPair**

| field | type | default |
|---|---|---|
| `left` | `str` | required |
| `right` | `str` | required |
| `passage_id` | `str | None` | None |

**MatchPayload** - Learner response: dict {left: right}.

| field | type | default |
|---|---|---|
| `format` | `Literal['match']` | 'match' |
| `pairs` | `list[MatchPair]` | required |

**ShortPayload**

| field | type | default |
|---|---|---|
| `format` | `Literal['short']` | 'short' |
| `required_terms` | `list[Annotated[str, FieldInfo(annotation=NoneType, required=True, metadata=[MinLen(min_length=1)])]]` | required |
| `min_terms` | `int | None` | None |

**ExplainPayload** - Self-rated: the source passage is shown after the attempt. Half weight toward mastery.

| field | type | default |
|---|---|---|
| `format` | `Literal['explain']` | 'explain' |

**CodeTest** - ONE concrete test format. Exactly one of the two forms:

| field | type | default |
|---|---|---|
| `name` | `str` | required |
| `stdin` | `str` | '' |
| `expected_stdout` | `str | None` | None |
| `harness` | `str | None` | None |

**CodePayload**

| field | type | default |
|---|---|---|
| `format` | `Literal['code']` | 'code' |
| `language` | `Literal['python', 'c']` | required |
| `starter` | `str` | '' |
| `visible_tests` | `list[CodeTest]` | required |
| `hidden_tests` | `list[CodeTest]` | factory |
| `reference_solution` | `str` | required |
| `time_limit_s` | `float` | 5.0 |
| `mem_mb` | `int` | 256 |

**MathPayload**

| field | type | default |
|---|---|---|
| `format` | `Literal['math']` | 'math' |
| `op` | `Literal['diff', 'integrate', 'solve', 'simplify', 'evaluate']` | required |
| `expr` | `str` | required |
| `var` | `str` | 'x' |
| `given_answer` | `str | None` | None |
| `subs` | `dict[str, str] | None` | None |

**Quantity**

| field | type | default |
|---|---|---|
| `name` | `str` | required |
| `value` | `float` | required |
| `unit` | `str` | '' |

**NumericPayload**

| field | type | default |
|---|---|---|
| `format` | `Literal['numeric']` | 'numeric' |
| `given` | `list[Quantity]` | factory |
| `quantity` | `str` | '' |
| `answer_value` | `float` | required |
| `unit` | `str` | required |
| `rel_tol` | `float` | 0.01 |

**Item**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `objective_id` | `str` | required |
| `format` | `Literal['mc', 'cloze', 'order', 'match', 'short', 'explain', 'code', 'math', 'numeric']` | required |
| `prompt` | `str` | required |
| `payload` | `MCPayload | ClozePayload | OrderPayload | MatchPayload | ShortPayload | ExplainPayload | CodePayload | MathPayload | NumericPayload` | required |
| `citations` | `list[Citation]` | factory |
| `origin` | `Literal['materials', 'model_knowledge']` | 'materials' |
| `checker` | `str` | required |
| `status` | `Literal['active', 'dropped', 'reported']` | 'active' |
| `created_at` | `datetime.datetime` | factory |

**Drop**

| field | type | default |
|---|---|---|
| `id` | `int | None` | None |
| `course_id` | `str | None` | None |
| `objective_id` | `str` | required |
| `format` | `str` | required |
| `gate_code` | `Literal['quote_missing', 'unsupported_fact', 'ambiguous', 'bad_cloze', 'framing_fact', 'key_mismatch', 'weak_tests', 'self_check_failed', 'schema_invalid']` | required |
| `detail` | `str` | '' |
| `draft_hash` | `str` | '' |
| `at` | `datetime.datetime` | factory |

**Attempt**

| field | type | default |
|---|---|---|
| `id` | `int | None` | None |
| `item_id` | `str` | required |
| `response` | `Any` | None |
| `correct` | `bool | None` | None |
| `score` | `float` | required |
| `graded_by` | `str` | required |
| `hints_used` | `int` | 0 |
| `at` | `datetime.datetime` | factory |

**ReviewState**

| field | type | default |
|---|---|---|
| `item_id` | `str` | required |
| `due` | `datetime.datetime` | required |
| `stability` | `float` | required |
| `difficulty` | `float` | required |
| `reps` | `int` | 0 |
| `lapses` | `int` | 0 |
| `last_review` | `datetime.datetime | None` | None |

**Span**

| field | type | default |
|---|---|---|
| `start` | `int` | required |
| `end` | `int` | required |

**Hint**

| field | type | default |
|---|---|---|
| `level` | `int` | required |
| `text` | `str` | required |
| `passage_id` | `str | None` | None |
| `highlight` | `Span | None` | None |

**Job**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `kind` | `str` | required |
| `status` | `Literal['queued', 'running', 'done', 'error']` | 'queued' |
| `pct` | `float` | 0 |
| `stage` | `str` | '' |
| `error` | `str | None` | None |
| `course_id` | `str | None` | None |
| `created_at` | `datetime.datetime` | factory |
| `updated_at` | `datetime.datetime` | factory |

**RevealedCitation**

| field | type | default |
|---|---|---|
| `passage_id` | `str` | required |
| `quote` | `str` | required |
| `location` | `str | None` | None |

**PublicItem** - What a learner may see before answering. Built only by public.public_view().

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `objective_id` | `str` | required |
| `format` | `Literal['mc', 'cloze', 'order', 'match', 'short', 'explain', 'code', 'math', 'numeric']` | required |
| `prompt` | `str` | required |
| `payload` | `dict[str, Any]` | required |
| `origin` | `Literal['materials', 'model_knowledge']` | required |
| `source_label` | `str` | required |
| `status` | `Literal['active', 'dropped', 'reported']` | 'active' |
| `hints_available` | `int` | 3 |

**PerTestResult**

| field | type | default |
|---|---|---|
| `name` | `str` | required |
| `passed` | `bool` | required |
| `visible` | `bool` | True |
| `detail` | `str` | '' |
| `stdout` | `str | None` | None |
| `expected` | `str | None` | None |
| `time_ms` | `int | None` | None |

**CourseSummary**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `title` | `str` | required |
| `mode` | `Literal['strict', 'open']` | required |
| `mode_reason` | `str` | '' |
| `created_at` | `datetime.datetime` | required |
| `objectives` | `int` | 0 |
| `covered` | `int` | 0 |
| `not_covered` | `int` | 0 |
| `items_active` | `int` | 0 |
| `mastery` | `float` | 0 |
| `due_reviews` | `int` | 0 |

**CoursesResponse**

| field | type | default |
|---|---|---|
| `courses` | `list[CourseSummary]` | required |

**ImportRequest**

| field | type | default |
|---|---|---|
| `title` | `str` | required |
| `text` | `str | None` | None |
| `path` | `str | None` | None |
| `mode` | `Literal['strict', 'open']` | 'strict' |

**ImportResponse**

| field | type | default |
|---|---|---|
| `job_id` | `str` | required |

**JobResponse**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `kind` | `str` | required |
| `status` | `Literal['queued', 'running', 'done', 'error']` | 'queued' |
| `pct` | `float` | 0 |
| `stage` | `str` | '' |
| `error` | `str | None` | None |
| `course_id` | `str | None` | None |
| `created_at` | `datetime.datetime` | factory |
| `updated_at` | `datetime.datetime` | factory |

**ObjectiveView**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `unit` | `str` | required |
| `lesson` | `str` | required |
| `verb` | `str` | required |
| `statement` | `str` | required |
| `kind` | `Literal['reading', 'code', 'math', 'science']` | 'reading' |
| `prereqs` | `list[str]` | factory |
| `passage_ids` | `list[str]` | factory |
| `coverage` | `Literal['covered', 'not_covered']` | 'covered' |
| `mastery` | `float` | 0 |
| `items_active` | `int` | 0 |
| `due_reviews` | `int` | 0 |
| `status_label` | `str | None` | None |

**LessonNode**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `title` | `str` | required |
| `objectives` | `list[ObjectiveView]` | required |

**UnitNode**

| field | type | default |
|---|---|---|
| `id` | `str` | required |
| `title` | `str` | required |
| `lessons` | `list[LessonNode]` | required |

**MapSummary**

| field | type | default |
|---|---|---|
| `objectives` | `int` | required |
| `covered` | `int` | required |
| `not_covered` | `int` | required |
| `mastered` | `int` | required |
| `mean_mastery` | `float` | required |

**MapResponse**

| field | type | default |
|---|---|---|
| `course` | `CourseSummary` | required |
| `units` | `list[UnitNode]` | required |
| `edges` | `list[Edge]` | required |
| `summary` | `MapSummary` | required |

**LessonPassage**

| field | type | default |
|---|---|---|
| `passage_id` | `str` | required |
| `location` | `str` | required |
| `text` | `str` | required |

**LessonView**

| field | type | default |
|---|---|---|
| `objective_id` | `str` | required |
| `unit_title` | `str` | required |
| `lesson_title` | `str` | required |
| `statement` | `str` | required |
| `framing` | `str | None` | None |
| `passages` | `list[LessonPassage]` | required |
| `source_label` | `str` | 'From your materials' |

**NextResponse**

| field | type | default |
|---|---|---|
| `kind` | `Literal['lesson', 'item', 'done']` | required |
| `reason` | `Literal['new', 'review', 'interleave', 'placement', 'none']` | 'new' |
| `lesson` | `LessonView | None` | None |
| `item` | `PublicItem | None` | None |
| `message` | `str | None` | None |

**AnswerRequest**

| field | type | default |
|---|---|---|
| `response` | `Any` | required |

**NextReview**

| field | type | default |
|---|---|---|
| `due` | `datetime.datetime` | required |
| `interval_days` | `float` | required |

**AnswerResponse**

| field | type | default |
|---|---|---|
| `item_id` | `str` | required |
| `correct` | `bool | None` | required |
| `score` | `float` | required |
| `feedback` | `str` | required |
| `correct_answer` | `Any` | None |
| `citations` | `list[RevealedCitation]` | factory |
| `source_label` | `str` | 'From your materials' |
| `next_review` | `NextReview | None` | None |
| `mastery` | `float | None` | None |

**RunRequest**

| field | type | default |
|---|---|---|
| `code` | `str` | required |

**RunResponse**

| field | type | default |
|---|---|---|
| `item_id` | `str` | required |
| `ok` | `bool` | required |
| `passed` | `int` | required |
| `total` | `int` | required |
| `results` | `list[PerTestResult]` | required |
| `compile_error` | `str | None` | None |
| `timed_out` | `bool` | False |

**HintRequest**

| field | type | default |
|---|---|---|
| `level` | `int` | required |

**HintResponse**

| field | type | default |
|---|---|---|
| `item_id` | `str` | required |
| `hint` | `Hint` | required |
| `location` | `str | None` | None |
| `passage_text` | `str | None` | None |
| `hints_used` | `int` | required |

**ReportRequest**

| field | type | default |
|---|---|---|
| `reason` | `str` | '' |

**ReportResponse**

| field | type | default |
|---|---|---|
| `item_id` | `str` | required |
| `status` | `Literal['active', 'dropped', 'reported']` | required |
| `reports_total` | `int` | required |

**GateCount**

| field | type | default |
|---|---|---|
| `gate_code` | `str` | required |
| `label` | `str` | required |
| `count` | `int` | required |

**DropsResponse**

| field | type | default |
|---|---|---|
| `course_id` | `str` | required |
| `total` | `int` | required |
| `by_gate` | `list[GateCount]` | required |
| `recent` | `list[Drop]` | factory |

**ProgressResponse**

| field | type | default |
|---|---|---|
| `course_id` | `str` | required |
| `objectives` | `int` | required |
| `covered` | `int` | required |
| `not_covered` | `int` | required |
| `mastered` | `int` | required |
| `mean_mastery` | `float` | required |
| `items_generated` | `int` | required |
| `items_active` | `int` | required |
| `items_dropped` | `int` | required |
| `items_reported` | `int` | required |
| `attempts` | `int` | required |
| `accuracy` | `float | None` | None |
| `due_reviews` | `int` | required |
| `drops_by_gate` | `dict[str, int]` | required |
| `reports` | `int` | required |
| `recent_days` | `list[DayActivity]` | factory |

**DayActivity**

| field | type | default |
|---|---|---|
| `date` | `str` | required |
| `attempts` | `int` | required |
| `correct` | `int` | required |

**TimelineEvent**

| field | type | default |
|---|---|---|
| `label` | `str` | required |
| `date_text` | `str` | required |
| `sort_year` | `int | None` | None |
| `passage_id` | `str` | required |
| `location` | `str` | required |
| `quote` | `str` | required |

**TimelineResponse**

| field | type | default |
|---|---|---|
| `course_id` | `str` | required |
| `events` | `list[TimelineEvent]` | required |

**PlotSeries**

| field | type | default |
|---|---|---|
| `label` | `str` | required |
| `expr` | `str` | required |
| `points` | `list[tuple[float, float | None]]` | required |

**PlotResponse**

| field | type | default |
|---|---|---|
| `item_id` | `str` | required |
| `title` | `str` | required |
| `x_label` | `str` | 'x' |
| `y_label` | `str` | 'y' |
| `x_range` | `tuple[float, float]` | required |
| `series` | `list[PlotSeries]` | required |
| `verified_by` | `Literal['sympy']` | 'sympy' |

**PassageResponse**

| field | type | default |
|---|---|---|
| `passage` | `Passage` | required |
| `doc_name` | `str` | required |
| `doc_kind` | `Literal['text', 'markdown', 'pdf_text']` | required |
| `prev_id` | `str | None` | None |
| `next_id` | `str | None` | None |

**JobProgressEvent**

| field | type | default |
|---|---|---|
| `job_id` | `str` | required |
| `pct` | `float` | required |
| `stage` | `str` | required |

**BankUpdatedEvent**

| field | type | default |
|---|---|---|
| `course_id` | `str` | required |

## 4. Public signatures lanes call

```python
# codecoach/core/text.py   (pure stdlib; a2a's gate imports it)
normalize(s: str) -> str        # NFKC, curly quotes -> straight, en/em/minus dashes -> "-", zero-width and
                                # soft-hyphen removed, whitespace collapsed, casefold. Idempotent.
quote_in(passage_text: str, quote: str) -> bool   # normalize(quote) is a non-empty substring of normalize(passage)
stable_hash(*parts, length=16) -> str
slugify(s: str, default="section") -> str

# codecoach/core/passages.py
split_document(doc_id: str, text: str, kind: DocKind = "text") -> list[Passage]   # ValueError on bad doc_id/kind

# codecoach/core/llm.py
class LLMPort(Protocol):
    def complete_structured(self, *, instructions: str, text: str, schema: dict, purpose: str,
                            max_tokens: int | None = None, timeout: float | None = None) -> dict | None
    def complete(self, *, messages: list[dict], purpose: str, max_tokens: int | None = None,
                 timeout: float | None = None) -> str
structured_call(port, ModelCls, *, instructions, text, purpose, retries=3, max_tokens=None, timeout=None) -> ModelCls
#   retries = total attempts; each retry appends the validation error to `text`;
#   raises LLMBadOutput(purpose, attempts, last_error) or LLMUnavailable (never retried).
FakeLLM(script=None, text_script=None)  # script (structured) / text_script (complete): iterable of replies
#   (dict | model | JSON str | None | exception instance) or a function (purpose, text) -> reply. Records .calls, .structured_calls, .text_calls, .purposes().
#   An exhausted script raises LLMUnavailable. Satisfies LLMPort.

# codecoach/core/registry.py
register_checker(kind: str, fn) / get_checker(kind) / check(item, response, *, mode="submit") -> CheckResult
register_generator(format: str, fn) / get_generator(format)
#   Unknown kind/format raises NotRegistered(LookupError) at lookup time, never at import. Last registration wins.
#   checker: fn(item, response, *, mode="submit") -> CheckResult; mode "visible" runs visible tests only.
#   generator: fn(*, objective, passages, llm, mode, rng, now) -> GenResult(items, drops)
CheckResult(ok, score 0..1, detail="", per_test: list[PerTestResult] | None = None, undecided=False)
#   undecided=True means "could not decide" (e.g. SymPy timeout); it is never counted as wrong.

# codecoach/core/public.py
public_view(item: Item) -> PublicItem             # per-format allow-list; unknown fields stay private
reveal(item, locate: Callable[[str], str | None] | None = None) -> list[RevealedCitation]   # only after an answer
source_label(item) -> str

# codecoach/core/samples.py (tests only)
sample_item(fmt, **overrides) -> Item
```

`public_view` exposes per format: mc `options`; cloze `text_with_blank`; order the shuffled event labels
(no dates); match `lefts` and deterministically shuffled `rights`; short `terms_expected`; explain nothing;
code `language`, `starter`, `visible_tests`, `hidden_test_count`, `time_limit_s`, `mem_mb`; math `op`,
`expr`, `var`, `subs`; numeric `given`, `quantity`, `unit`. It never exposes answer keys, `correct_index`,
`aliases`, `correct_order`, `required_terms`, `hidden_tests`, `reference_solution`, `given_answer`,
`answer_value` or `citations`. Citations come back only from `reveal`, inside `AnswerResponse`.

### Store (`codecoach/core/store.py`)

`Store(path)` (`":memory:"` allowed). One connection per thread; every write is a `BEGIN IMMEDIATE`
transaction; WAL; `busy_timeout=60000`; `user_version` migrations. Three threads writing at once is tested.
Methods (all return pydantic models or plain values):

- courses: `put_course`, `get_course`, `list_courses`, `set_course_mode(id, mode, reason)`
- docs and passages: `put_doc`, `get_doc`, `list_docs(course_id)`, `put_passages(list) -> int` (upsert,
  keeps insertion order), `get_passage`, `list_passages(doc_id=|course_id=)`, `adjacent_passage_ids(id) -> (prev, next)`
- map: `save_map(CourseMap)`, `get_map(course_id)`, `get_objective`, `list_objectives(course_id)`,
  `set_coverage(objective_id, coverage)`, `course_of_objective`
- items: `add_item -> bool` (False if the id exists), `get_item`,
  `list_items(objective_id=, course_id=, format=, status=)`, `count_items(...)`,
  `set_item_status`, `mark_dropped(id)`, `mark_reported(id, reason="")`, `count_reports(course_id=None)`
- drops: `log_drop(Drop) -> Drop` (assigns `id`, `course_id`), `count_drops_by_gate(course_id=None) -> {code: n}`,
  `list_drops(course_id=None, limit=100)`
- attempts: `add_attempt`, `list_attempts(item_id=, objective_id=, course_id=, since=, newest_first=, limit=)`, `count_attempts`
- review: `put_review_state`, `get_review_state`, `list_due(course_id=, now=, limit=)` (active items only, most overdue first),
  `list_review_states(course_id=)`
- jobs: `create_job(kind, course_id=) -> Job` (status `queued`), `update_job(id, status=, pct=, stage=, error=, course_id=)`,
  `get_job`, `list_jobs(status=)`
- meta: `set_meta(key, json_value)`, `get_meta(key, default)`; `schema_version()`, `close()`

### DB schema (version 1; migrations are append-only)

```sql
courses(id PK, title, mode, mode_reason, created_at)
docs(id PK, course_id, name, kind)
passages(seq AUTOINCREMENT PK, id UNIQUE, doc_id, location, text)      -- seq = reading order
course_maps(course_id PK, json)
objectives(id PK, course_id, unit, lesson, kind, coverage, ord, json)
items(id PK, objective_id, course_id, format, status, origin, checker, created_at, json)
drops(id AUTOINCREMENT PK, course_id, objective_id, format, gate_code, detail, draft_hash, at)
attempts(id AUTOINCREMENT PK, item_id, response /*JSON*/, correct, score, graded_by, hints_used, at)
review_state(item_id PK, due, stability, difficulty, reps, lapses, last_review)
reports(id AUTOINCREMENT PK, item_id, course_id, reason, at)
jobs(id PK, kind, status, pct, stage, error, course_id, created_at, updated_at)
meta(key PK, value)
```

Datetimes are stored as fixed-width UTC ISO strings so they sort as text. `Store` is the only code that
touches the file.

## 5. REST contract

Base path `/api/plugins/codecoach/`. JSON in, JSON out. Errors use FastAPI's `{"detail": "..."}` with 404
(unknown id), 422 (bad body) or 409 (item not active). Each response model is in `codecoach/core/models.py`;
one realistic example per endpoint is in `tests/fixtures/api/<name>.json` and is validated against its
model by `tests/core/test_core_fixtures.py`.

| Method and path | Request | Response model | Fixture |
|---|---|---|---|
| `GET /courses` | | `CoursesResponse` | `courses.json` |
| `POST /courses/import` | `ImportRequest {title, text? or path?, mode}` | `ImportResponse {job_id}` | `courses_import.json` |
| `GET /jobs/{id}` | | `JobResponse` (= `Job`) | `job.json` |
| `GET /courses/{id}/map` | | `MapResponse`: units -> lessons -> objectives with `mastery`, `coverage`, `status_label`; `edges`; `summary` | `map.json` (40 objectives, 3 not_covered, mixed mastery) |
| `GET /courses/{id}/next` | | `NextResponse {kind: lesson\|item\|done, reason, lesson?, item?}` | `next_lesson.json`, `next_item_mc.json`, `next_item_code.json` |
| `POST /items/{id}/answer` | `AnswerRequest {response}` | `AnswerResponse {correct, score, feedback, citations (quote + location), next_review, mastery}` | `answer_mc.json` |
| `POST /items/{id}/run` | `RunRequest {code}` | `RunResponse` (visible tests only) | `run.json` |
| `POST /items/{id}/hint` | `HintRequest {level 1..3}` | `HintResponse {hint, location, passage_text (level 3)}` | `hint.json` |
| `POST /items/{id}/report` | `ReportRequest {reason}` | `ReportResponse {status}` | `report.json` |
| `GET /courses/{id}/progress` | | `ProgressResponse` | `progress.json` |
| `GET /courses/{id}/drops` | | `DropsResponse {total, by_gate [{gate_code, label, count}], recent}` | `drops.json` |
| `GET /courses/{id}/timeline` | | `TimelineResponse` (events from gate-passed dates, sorted) | `timeline.json` (8 events) |
| `GET /items/{id}/plot` | | `PlotResponse` (series of `[x, y\|null]`; `null` marks a gap) | `plot.json` |
| `GET /passages/{id}` | | `PassageResponse {passage, doc_name, prev_id, next_id}` | `passage.json` |

`AnswerRequest.response` is untyped JSON. Recommended shapes (a2b's checkers are the authority and must
document any difference in their handoff): mc = chosen index (int); cloze, short, math = string; numeric =
string that may carry a unit (`"6 N"`); order = event indexes in the learner's order; match = list of
`[left_index, right_index]` pairs against the public view; explain = self-rating 0..1 (`correct` is `null`);
code = source string. `Attempt.response` stores whatever was sent.

Rules: `next` and every item field go through `public_view`. `run` returns visible-test results only and
never touches hidden tests. `answer` is the only path that reveals citations and the correct answer.
`mastery` is 0..1 per objective; `coverage="not_covered"` objectives show `NOT_COVERED_LABEL`, get no items,
and are never practised.

### Events (host.onEvent)

- `plugin.codecoach.job.progress` `{job_id, pct 0..100, stage}`: `JobProgressEvent`
- `plugin.codecoach.bank.updated` `{course_id}`: `BankUpdatedEvent`

Both are in `tests/fixtures/api/events.json`. Job `status`: `queued | running | done | error`.

## 6. Dev environment

`uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt`.
Pins: pydantic 2.14.1, sympy 1.14.0, fastapi 0.133.1, httpx, pytest. Run
`env -u PYTHONPATH .venv/bin/python -m pytest tests/<area> -q`. Test file names must be unique across
`tests/` (there are no `__init__.py` files): prefix them by area (`test_core_*`, `test_importer_*`, ...).
`tests/conftest.py` puts the repo root on `sys.path`. Regenerate fixtures with
`env -u PYTHONPATH .venv/bin/python tests/fixtures/api/_generate.py` (a1 only).

## Ask

Chat over the learner's own materials, where code (not the model) decides which sentences are shown. Package
`codecoach/ask/`; routes at the end of `dashboard/plugin_api.py`; asks are stored in `meta` under
`codecoach:asks:<course_id>` (deleted with the course).

Rules (binding):

1. **Strict courses only.** Open-mode course, or a course with no source text: `{"status":"unavailable","reason":...}`, no model call.
2. **Retrieval is deterministic code:** the syllabus BM25 `Index` over the course's passages (section headings count), top 12 (a model `not_covered` is retried with 16).
   No passage sharing at least min(2, question terms) content words: `not_covered` with NO model call, plus the 3
   closest passages (verbatim, location, labeled "closest, not an answer").
3. **The model** gets the question and the candidate passages with ids and returns
   `{"sentences":[{"text","cites":[{"passage_id","quote"}]}],"answerable":bool}`: at most 5 sentences of at most 40 words,
   each with a cite. It is told to answer only from the passages and to set `answerable:false` otherwise.
4. **The code gate, per sentence** (a failing sentence is dropped, never the whole answer; each drop goes to the drop
   log with the sentence): `ask_no_cite`; `ask_too_long` (over 40 words or a 6th sentence); `ask_quote_missing` (passage not
   offered, quote too short, or not verbatim: `text_found`); `ask_unsupported_fact` (a number, date or name not in
   the sentence's own quotes: `missing_facts`; or a content word its quotes lack beyond the framing gate's
   tolerance: `framing_odd_words`, the same test `check_framing` uses); `ask_negation` (`negated_clauses`, and a
   sentence that denies what no quote denies).
5. **Outcomes.** `answered` (>= 1 sentence survived; cites carry `passage_id, doc_id, location, quote, quote_start,
   quote_end` with offsets into the passage text, plus `removed`), `not_covered` (model said `answerable:false`, or
   nothing survived; no prose, the closest passages and `removed`), `error` (model unreachable, or bad JSON twice).
   Bad JSON is retried once. A model `not_covered` is retried once with the top 16 passages.
6. **Never blocks.** `POST /courses/{id}/ask {question 1-300 chars, open_item_id?}` returns a job id at once;
   `GET /courses/{id}/ask/{job}` returns `{job_id, status: queued|running|done, stage, result}` with stage
   `Finding passages | Writing the answer | Checking every sentence`; `GET /courses/{id}/asks` is the history,
   newest first, capped at 50. Cached by (course, normalised question, passages); errors are not cached.
7. **Integrity.** `open_item_id` of an unanswered Practice question puts it back in the queue ungraded
   (the Read-tab `release`); the response carries it as `released_item`.
8. **Quote extension (code).** When a sentence's number, date or name is missing from its quotes, code may look in the
   passages that sentence ALREADY cites for the passage sentence that holds that fact word for word (`text_found`) and add it as
   one more cite marked `extended_by_code: true` (exact `quote_start/quote_end`), at most 2 per sentence; then the whole
   gate runs on the sentence again. It never reads an uncited passage and never edits the answer sentence. The added
   passage sentence must hold EVERY fact of the answer sentence by itself, so code never stitches a fact from one
   sentence onto a claim from another (orchestrator probe 2026-11-06: "Hewes paid for the spike in 1869" borrowed 1869
   from a sentence about the railroad's completion and passed before this rule). Surviving
   sentences are ordered by shared content words with the question (nothing is dropped for low overlap).
9. **Hermes tool** `codecoach_ask(course, question)` returns only checked sentences with cites, or `not_covered`.

## Learn mode

A topic taught in small steps. Module `codecoach/learn.py` (Hermes-free); routes under `/courses/{id}/learn`;
contract in `LEARN-CONTRACT.md`. Progress is stored in `meta` as `learn:progress:<course_id>`, explanations as
`learn:ex:<hash>:<course_id>` (both deleted with the course).

1. **Steps.** The topic's passages in document order; not quotable (heading, under 4 words, chapter label) and
   `formula_missing` passages are skipped (the learner can still open the original text); two consecutive short
   passages of one document merge (under 120 words together); at most 6 steps. A `not_covered` topic is 409.
2. **Explanations go through Ask's gate.** The model gets one step's passages and writes up to 4 plain-word
   sentences, each with quotes. Code runs Ask's per-sentence gate (`AskEngine._gate`: quote verbatim, facts, names,
   framing words, negation, quote extension); a failing sentence is dropped and counted in `removed`. Zero kept (or
   `answerable:false`, no model, model down) is `unavailable`: the screen shows the passage itself. Bad JSON gets one retry.
3. **Never blocks, never builds a course.** `GET .../learn/{objective}` returns at once; missing explanations show
   `pending` (and `building: true`) while one worker builds the opened topic and then the next topic. Builds run one at a time per
   process; model failures are not cached (and not retried for 5 minutes). The cache key is the step's passage ids
   and text plus the prompt version, so edited passages rebuild.
4. **Check** (`POST .../check`) grades one existing bank item whose citations sit inside the step, by code, and
   writes nothing (no attempt, mastery or review state). Item from another topic: 422.
5. **Step** (`POST .../step {index}`) records progress. The last step marks the topic learned and gives every
   active item of the topic that has no review state a first Review due in about a day; existing review state is untouched.
