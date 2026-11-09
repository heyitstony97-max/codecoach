# Verification (A5)

Status: suite, validate, the cloud e2e and the local (Qwen) e2e are done and printed below. The five served items
and the SymPy check ran through `CodeCoachService` (a driver script), not through `hermes chat`. Outline mode was not
re-run. See UNVERIFIED.

## 1. Test suite and validate

```
env -u PYTHONPATH .venv/bin/python -m pytest -q
461 passed, 11 xfailed, 1 warning in 16.44s

hermes plugins validate .          (no --install-deps)
Validation passed.   0 failures
warnings: security scan "caution" (curl_pipe_shell in docs/CONSTRAINTS.md, dump_all_env in a release script,
path_traversal_deep in the vendored harness bundle under devtools/desktop-harness/vendor/);
isolation: plugin host, dashboard API buffered.
```

Before A5: 417 passed, 55 xfailed (r1 baseline). After: 461 passed, 11 xfailed.

## 2. Red-team scoreboard

| | attacks | blocked | holes |
|---|---|---|---|
| before (r1) | 90 | 35 | 55 |
| after (A5) | 90 | 79 | 11 |

The 11 open holes: 5 honest limits (RT44, RT45, RT66, RT69, RT73; need README text, not code) and 6 fixable
medium/low (RT63, RT68, RT70, RT77, RT83, RT84), left for a follow-up card. The one remaining high-severity
fixable hole, RT87 (lowercase false claim in a framing line), is closed: every content word of a framing line must
come from the passage or from a short list of instruction words.

## 3. Real defects the end-to-end run found (fixed, committed)

- Strict drafts left `citations` optional. A real model omitted it, every draft was dropped as `schema_invalid`.
  Strict reading formats now require it (`draft_model` in `codecoach/gen/drafts.py`).
- `outline` was not reachable from the tools or the command. Added `outline` to `codecoach_import` and
  `/codecoach import outline <path>`.
- Hermes refuses models with a context window under 64K, so the scratch config sets 131072.

## 4. End to end, cloud at $0 (real Hermes, scratch home)

Home: a scratch directory under the system temp dir (not under `~/.hermes`: Hermes 0.22.7 attaches any home below
`~/.hermes` to the live gateway). `plugins/codecoach` is a symlink to the repo, `config.yaml` written directly by
`devtools/e2e/e2e_home.py`, no `hermes plugins install/enable`. Cloud model `meituan/longcat-2.5:free` via
a local OpenAI-compatible proxy; any id not ending `:free` is refused in code before a call (`guard_cloud_model`).
(`poolside/laguna-s-2.1:free` was the card's default; longcat answered, so it was used.)

Commands: `env -i HOME=$HOME PATH=$PATH TERM=dumb HERMES_HOME=<home> hermes chat -Q -q "<prompt>"` from `~`,
through `devtools/e2e/hold.sh`; the prompt tells the agent to call `codecoach_import` / `codecoach_next` and then
`sleep`, because CodeCoach's import and bank run on background threads that a one-shot CLI would kill.
Numbers come from `devtools/e2e/e2e_report.py <codecoach.db>`.

| | history.md (strict) |
|---|---|
| objectives | 76 (75 reading, 1 math), all covered |
| prerequisite cycles | 0 |
| map schema-valid | yes |
| import wall time | 655 s |
| bank size (active) | 40 (25 mc, 15 cloze), bank build stopped at that size |
| drops | 37: unsupported_fact 20, ambiguous 8, self_check_failed 7, bad_cloze 1, quote_missing 1 |
| served items re-gated | 40 of 40 pass (100%) |

The outline imports of that first run were stored as `strict` because the scratch gateway still ran pre-patch
code; they are not used as evidence. Outline mode is proven in section 7 (c-basics, open mode).

Served-items query: `SELECT id, format, origin FROM items WHERE course_id = ? AND status = 'active'`, then every
reading item re-run through the gate against its cited passages.

## 5. End to end, local Qwen (real Hermes, scratch home)

Model `qwen3.8-27b` served by llama.cpp's OpenAI-compatible server on the same machine. Home built with
`env -u PYTHONPATH python3 devtools/e2e/e2e_home.py local --model qwen3.8-27b`, plus `auxiliary.codecoach.timeout: 900`
in its `config.yaml`: the default 90 s auxiliary timeout made the first bank run stop ("the model was unreachable").

Commands, run from `~` with `HERMES_HOME` set to the scratch home:

```
env -u PYTHONPATH python3 devtools/e2e/e2e_session.py <home> <queue> <out>    # long-lived `hermes chat`
echo "/codecoach import <repo>/demo/materials/history.md" >> <queue>   # strict import
devtools/e2e/hold.sh <home> "<prompt>"   # `hermes chat -Q -q`: codecoach_next, then sleep + codecoach_progress polling
```

A second `codecoach_next` restarted the bank, which ran to `done`. Numbers from `devtools/e2e/e2e_report.py`:

| | history.md (strict, Qwen) |
|---|---|
| objectives | 69 (68 reading, 1 math), all covered |
| prerequisite cycles | 0 |
| map schema-valid | yes |
| import wall time | 868 s |
| bank build wall time | about 71 min (second run) |
| bank size (active) | 61 (45 mc, 13 cloze, 3 short) |
| drops | 48: unsupported_fact 21, bad_cloze 9, self_check_failed 9, ambiguous 8, framing_fact 1 |
| served items re-gated | 61 of 61 pass (100%) |

Served-items query: `SELECT id, format, origin FROM items WHERE course_id = ? AND status = 'active'`, then each
reading item re-run through `check_item` against its cited passages.

## 6. Serving five items and the SymPy check

`devtools/e2e/serve_five.py <codecoach.db> <copy.db>` copies a store, adds one hand-authored C code item and two math
items, and serves through `CodeCoachService`, using the Python of the scratch home's own venv. Run on both the Qwen
and the cloud store with the same outcome. Qwen run, abridged:

```
sympy 1.14.0 from <scratch home>/installs/.../venv/lib/python3.14/site-packages/sympy/__init__.py
sandbox_status: bwrap
math key verified by SymPy: ok=True
1a mc WRONG answer        correct=false  "Not correct."  mastery 0.1758
1b mc RIGHT answer        correct=true   mastery 0.5662  next review in 1.0 day
2a hint level 1           "Look in the section "The Golden Spike" of history.md."  hints_used=1
2b answer after hint      correct=true   mastery 0.5152  next review in 3.0 days
3a C run, bad code        ok=false  compile_error: solution.c:3:13: error: expected expression before ';' token
3b C run, good code       ok=true   1/1 visible tests
3c C graded               correct=true  "3/3 tests passed" (visible + 2 hidden)  next review in 3.0 days
4a math, equivalent form  correct=true   (x**2*cos(x) + 2*x*sin(x) against key 2*x*sin(x) + x**2*cos(x))
4b math, wrong answer     correct=false  "That is not the derivative."
5  cloze                  correct=true   citation "The Golden Spike, para 1"
reviews scheduled: 6 (one per answered item); attempts: 7
```

- The C source was really compiled and run in `bwrap`; the compile error text is gcc's.
- Math ran through SymPy 1.14.0 / mpmath 1.3.0 in the scratch home's venv (`import sympy` works there). The numeric
  fallback only runs on a SymPy timeout; the feedback has no "checked numerically" suffix, so it did not trigger.
- The hinted answer (2b) got a lower mastery than the unhinted one (1b), as designed.

## UNVERIFIED

- The five served items ran through a driver over `CodeCoachService`, not through tool calls in `hermes chat`. The C
  and math items in section 6 are hand-authored. Section 7 has model-written C items (cloud and Qwen) graded in the sandbox.
- SymPy was present in the scratch home's venv on 2026-11-05; a later Hermes update may drop it again.
- Qwen's first bank run failed on the default 90 s timeout. Since card A5b the plugin default is `llm_timeout_s` 600
  (section 7.2), set per scratch home in the later runs.
- The cloud banks are partial (40 and 17 items) because the bank threads died when the agent sessions ended.
- The PluginLlm call from the REST path and PDF import through `dispatch_tool` stay unverified live.
- Cloud-model quality: only free, small-budget calls with one question per objective.

---

## 7. Outline mode, the model timeout, and live C exercise generation (card A5b)

Scratch files live under `/tmp/codecoach-e2e-code/`; no live Hermes home or user file was touched. The outline source
is the in-repo `demo/materials/c-basics.md` (the old tests read a personal file; that is gone, see section 1).

### 7.1 Test suite and validate

```
$ env -u PYTHONPATH .venv/bin/python -m pytest -q
479 passed, 5 xfailed, 1 warning in 19.14s        # before this card: 471 passed, 5 xfailed
$ hermes plugins validate .
Validation passed.
```

New tests: `tests/surface/test_surface_outline.py` (4: `/codecoach import outline <path>`, `codecoach_import` with
`outline: true`, `POST /courses/import` with `"outline": true`, and the same file without the flag stays strict) and
`tests/surface/test_surface_timeout.py` (4: plugin.yaml key, parse and clamp, per-call `timeout=`, ctx wiring).
Those tests use a scripted model, so they prove the plumbing and the rules (mode, `kind=code` for write-code and
program-tracing objectives, 20+ objectives, 10+ code); the live numbers are below.

### 7.2 Timeout

`plugin.yaml` has `config_schema.llm_timeout_s` (int, default 600). `ServiceConfig.llm_timeout_s` reads it
(clamped 10..7200); `hermes_glue` passes it as the default `timeout=` of every `ctx.llm` and `PluginLlm` call
(`default_timeout`). Before, the default was 90 s, which is what killed Qwen's first bank run. The scratch homes are
built with `devtools/e2e/e2e_home.py local|cloud --timeout-s N` (writes `llm_timeout_s` into the home's config).

### 7.3 Outline mode, real Hermes

Command path: `/codecoach import outline <repo>/demo/materials/c-basics.md` typed into a live
`hermes chat` (`devtools/e2e/e2e_session.py`), scratch home, cloud `meituan/longcat-2.5:free`. Read back with
`devtools/e2e/e2e_report.py`: course mode `open`, mode_reason `no source materials attached`, 46 objectives
(42 code, 3 reading, 1 math), all covered, 0 prerequisite cycles, map schema-valid, import wall 288 s.
The one-shot `codecoach_import` tool call with `outline: true` (via `hold.sh`) produced the same course in that run.
The REST route is covered by the test only (no live gateway call).

### 7.4 Live code-exercise generation

Driver: `devtools/e2e/live_codegen.py` (imports the outline through `import_outline`, then runs the real
generator and the real bubblewrap self-check for each code objective, 1 item wanted, up to 2 drafts per objective).
It calls the model endpoints directly with a small OpenAI-compatible port, not through `ctx.llm`: a one-shot
`hermes chat` quits as soon as the agent stops sleeping, which killed the bank thread twice, so the bank could not be
held open that way. The Hermes-run bank (7.3) did run partway (28 of 42 objectives, 23 code items, ended when the
session was killed).

```
$ env -u PYTHONPATH .venv/bin/python devtools/e2e/live_codegen.py cloud --model meituan/longcat-2.5:free --timeout 600 --store /tmp/codecoach-e2e-code/cloud2.db
$ env -u PYTHONPATH .venv/bin/python devtools/e2e/live_codegen.py local --model qwen3.8-27b --timeout 900 --limit 12 --store /tmp/codecoach-e2e-code/local2.db
```

| | cloud `meituan/longcat-2.5:free` | local `qwen3.8-27b` (llm timeout 900 s) |
|---|---|---|
| course | open, "no source materials attached", 49 objectives, 35 code | open, same reason, 37 objectives, 30 code |
| import wall | 473 s | 352 s |
| code objectives attempted | 32 of 35 (run stopped at the 33rd, see below) | 12 (evenly spaced sample of 30) |
| draft attempts | 32 | 12 |
| code items kept | 30 | 12 |
| drops by reason | weak_tests 1 (reference passed 3 of 4 own tests) | none |
| keep rate | 30 of 35 code objectives (target was 1 of 2) | 12 of 12 attempted |
| codegen wall | 2885 s | 688 s |

On the cloud run a model call raised `LLMUnavailable` on objective 33 (a proxy error or a timeout; the driver does
not record which), and `fill_objective` stops the whole bank on that. The remaining 3 code objectives were not
tried. No generator change was needed: the target was met without one. Fail-closed is unchanged.

The Hermes-run bank also dropped three `math` drafts for `self_check_failed`: "No module named 'sympy'" in the scratch
home's venv (the same gap as noted in section 6). Not a code-item problem; math items are refused rather than served.

### 7.5 Sandbox grading through `codecoach.service`

`devtools/e2e/grade_code_items.py <copy of the store>`: for each item, `CodeCoachService.run` (visible tests only) and
`CodeCoachService.answer` (visible + hidden) with the starter, a wrong attempt and the reference. Real bubblewrap + gcc.

| item (model) | submission | `run` (visible) | `answer` (hidden included) |
|---|---|---|---|
| print one line with printf (cloud) | starter | 0/1 fail | wrong, 0/2 |
| | wrong attempt | 0/1 fail | wrong, 0/2 |
| | reference | 1/1 pass | correct, 2/2 |
| read int, double, char (cloud) | starter | 1/1 pass | wrong, 2/4 |
| | wrong attempt | 0/1 fail | wrong, 0/4 |
| | reference | 1/1 pass | correct, 4/4 |
| fix `average` (cloud) | starter | compile error (implicit declaration) | wrong, compile error |
| | wrong attempt | 0/2 fail | wrong, 0/7 |
| | reference | 2/2 pass | correct, 7/7 |
| hello world (Qwen) | starter / wrong / reference | 0/1, 0/1, 1/1 | 0/3, 0/3, correct 3/3 |
| `add` from stdin (Qwen) | starter / wrong / reference | 0/2, 0/2, 2/2 | 1/5, 0/5, correct 5/5 |
| `to_upper` in place (Qwen) | starter / wrong / reference | 0/2, compile error (own `main()` clashes with the harness), 2/2 | 2/6, compile error, correct 6/6 |

The second cloud item shows what `run` hides: the starter passes its single visible test but fails the hidden ones,
so a learner who only presses Run would think it was done; only `answer` is decisive.

### 7.6 Not verified

- The REST outline route and the tool route were tested with a scripted model, not live.
- The generator was driven through a direct HTTP port, not Hermes' `ctx.llm`, so `llm_timeout_s` reaching a live
  `ctx.llm` call is covered by the unit test only; the live runs passed the timeout to the HTTP port themselves.
- One cloud model and one local model, one course. Python code items were not exercised.
- The local run is a 12-objective sample, not all 30.

## 8. Code exercise quality gates and course language (card A5c)

Re-grading the 42 items from section 7 found three gaps: the starter already green on every visible test (12 of 30
cloud items, 1 of 12 local), hidden tests that copy the visible ones (1 local item), and Python items in a C course.
All three are now code-checked.

### 8.1 What changed

| Gap | Fix | Gate code |
|---|---|---|
| Starter passes every visible test | self-check: the starter must fail at least one visible test; the generation instructions say so | `weak_tests` ("starter already passes the visible tests") |
| Hidden tests copy visible ones | self-check: at least one hidden test differs from every visible test (stdin + expected + harness, whitespace-normalised); instructions say so | `weak_tests` ("every hidden test is a copy of a visible test") |
| Wrong language | `Course.code_language` (`python`/`c`/None). The importer infers it from the title and text (gcc, printf, scanf, `#include`, "C programming" -> c; python, `def`, pip -> python; tie or nothing -> None) and `import_outline` / `import_materials` take `code_language=` to override. Generation restricts the draft schema to that language (the model sees the validation error and retries), and a draft in another language is dropped | `schema_invalid` |
| Objective about the outline itself | no code draft is requested when the statement has no programming verb or code word and mentions the outline/course/units | none (skipped, listed in `BankReport.skipped_meta`) |

Store: migration 2 (`ALTER TABLE courses ADD COLUMN code_language TEXT`), run once under `user_version`; a version-1
database opens, loads `code_language=None` and migrates (test `test_old_database_gets_code_language_column`).

### 8.2 Tests

```
$ env -u PYTHONPATH .venv/bin/python -m pytest -q
501 passed, 5 xfailed, 1 warning in 19.34s       (before: 479 passed, 5 xfailed)
$ hermes plugins validate .
Validation passed.
```

New: `tests/gen/test_gen_quality.py` (green starter, copied hidden, harness comparison, wrong language, meta objective,
bank passes the language), importer inference and override, store migration from a version-1 file.

### 8.3 Live cloud run (meituan/longcat-2.5:free, `:free` guard on)

```
$ env -u PYTHONPATH .venv/bin/python devtools/e2e/live_codegen.py cloud --model meituan/longcat-2.5:free \
    --store /tmp/codecoach-e2e-code/cloud3.db
course c-basics, mode open ("no source materials attached"), code_language c, 52 objectives (45 code)
code objectives reached 31 of 45; draft attempts 25; kept 24; dropped 0; llm_unavailable true
import_wall_s 1887.3  codegen_wall_s 4232.2
```

The proxy returned HTTP 502 for many large calls; the run backs off five times per call and stopped at objective o31
when five retries in a row failed, so 14 code objectives were never tried. Zero drops means the model passed the new
rules on its own after the instructions changed, not that the rules are inert (the unit tests drop each case).

Re-grade of every kept item with the orchestrator's script (real bubblewrap + gcc):

```
$ env -u PYTHONPATH .venv/bin/python <scratch>/regrade_code.py /tmp/codecoach-e2e-code/cloud3.db
  {'n': 24, 'ref_ok': 24, 'starter_fails': 24, 'starter_green_visible': 0, 'dup_hidden': 0, 'no_hidden': 0}
```

All 24 items are language `c`. Against the target: `starter_green_visible` 0 (was 12 of 30), `dup_hidden` 0, language c
24 of 24, kept 24 for 45 code objectives (target 23). Not shown: Qwen was not re-run (being turned off).

## 9. Syllabus coverage check (demo beat 1)

`import_with_syllabus` builds the passage index from the materials and makes one objective per syllabus line
(statement verbatim). For each line: the top 6 passages by stemmed content-word BM25 are retrieved
deterministically; the model sees only those and proposes `{passage_id, quote}`. The line is `covered` only if the
quote passes the source gate in that exact passage AND shares at least 2 stemmed content words with the line.
Otherwise it is stored `not_covered` ("no passage in your materials covers this"); a model error gives
"could not check". When the first answer yields no verified quote the line gets one second look (the gates are
deterministic, so a second sample cannot admit a fake quote). Not-covered objectives never get items.

Entry points: `/codecoach import <materials> --syllabus <file>`, `codecoach_import` arg `syllabus_path`, REST
`POST /courses/import` `syllabus_text` / `syllabus_path`, and the desktop import form. Tests:
`tests/importer/test_importer_syllabus.py`, `tests/surface/test_surface_syllabus.py`.

### 9.1 Live cloud runs (meituan/longcat-2.5:free, `:free` guard on)

```
$ env -u PYTHONPATH .venv/bin/python devtools/e2e/live_syllabus.py --model meituan/longcat-2.5:free --store /tmp/x.db
```

Both final runs: lines 1-6 covered with a real supporting quote, lines 7 (Pony Express) and 8 (Native American
nations) not_covered (run 1 wall 288.7 s, 19 calls; run 2 wall 243.1 s, 20 calls).

A first run, before the second look was added, marked lines 1 and 3 not_covered: the model answered with nothing
on that sample, though a direct re-ask returned verified quotes for both. That is the reason for the second look.

## 9b. Syllabus coverage on local models, and delete course (A1d)

Bug: on local Qwen (`qwen3.8-27b`), line 1 (the four special spikes) came back not_covered although the
`## Four Special Spikes` section exists. One empty model answer was enough to grey out a covered topic.

Fix, with the promise unchanged (a line is covered only with a verbatim quote that passes the quote gate AND shares
>= 2 content words with the line; LLM errors still fail closed as "could not check"):

- Retrieval scores each passage on its text plus its heading path ("Four Special Spikes > Nevada's Silver Spike";
  the book title is left out). `syllabus.heading_paths` re-walks the headings the way `split_document` does.
- If the first call yields no quote passing both rules, one retry asks over the top 3 candidates for the single
  sentence that best supports the objective, or none.
- A deterministic fallback was NOT added: heading paths plus the retry were enough in every live run.

Live proof (`devtools/e2e/live_syllabus.py`): Qwen x3 and the free cloud model x1 all gave lines 1-6 covered with a
quote and lines 7 and 8 not_covered (Qwen walls 299.8 / 326.6 / 351.2 s; cloud `meituan/longcat-2.5:free` 487.4 s).
Tests: `tests/importer/test_importer_syllabus_robust.py` (heading path, empty-then-good retry, empty twice, error on
retry, real demo files). Known limit, kept as a strict xfail: a model that ALWAYS quotes a sentence can pass line 8
on the shared words "railroad" and "nation"; real models answer none.

Delete: `Store.delete_course` removes docs, passages, map, objectives, items, attempts, review state, reports, drops,
jobs and the course's meta in one transaction; other courses are untouched. `/codecoach delete <course-id>` only
previews; `/codecoach delete <course-id> confirm` deletes. Tests: `tests/core/test_core_delete.py`,
`tests/surface/test_surface_delete.py`.

History bank yield (read-only, on a copy of the live store): the `self_check_failed` drops are all ORDER items.
Qwen writes event dates without a year ("May 10", "Before noon on May 10", "150th anniversary of the Golden Spike"),
which the gate cannot sort, so the item is dropped ("an event date could not be read"). That is the check working,
not misfiring on a good item: a year-less date cannot be placed safely. Other probes dropped for two events sharing
a date (ambiguous) or an event and date not in one quoted sentence (unsupported_fact). Possible later fix: require
a year in the order prompt. No code changed.
