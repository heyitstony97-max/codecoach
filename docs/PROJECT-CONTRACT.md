# Project mode: the contract (engine and screen both build to this)

**Why it exists.** Importing a book and waiting for a question bank is a slow way to learn to code. Project mode: name a small program, CodeCoach splits it into small build steps, teaches each one, and you write the code yourself. Each step is checked by really running your code. Nothing waits on a whole-course build.

**The promise that never bends (programming edition).**
- Every code example shown has really run in the sandbox. The output shown is the real stdout, never model text.
- A step passes only when real tests pass. Before any learner sees a step, CodeCoach's own hidden reference solution
  must pass every one of that step's tests in the sandbox.
- Lesson prose is model text and is labelled as the tutor's explanation. It never claims an output; outputs come from
  runs.
- Hints never contain the answer (a code gate, below). CodeCoach's solution is shown only on request after real effort.
- Projects are CodeCoach's own, never CS50 problem sets (CS50 allows only its Duck there).


## The loop

```
start      idea ("number-guessing game", free text or a suggested idea) + language (c | python)
plan       ONE model call: title, one-line summary, 4-8 small steps (title, goal, concept). Runs in the background.
step i     lesson (2-5 short sentences, labelled tutor) + example (code that RAN, with its real output)
           + task (what to add) + visible tests (input -> expected output) + editor holding YOUR code
run        visible tests only, results per test (expected vs got, compiler errors in plain text)
check      visible + hidden tests. Pass = step done, your code carries into step i+1.
hint       up to 3, one at a time: nudge -> which construct and why -> shape with blanks (never full code)
solution   CodeCoach's reference, only after 3 failed checks on this step AND every hint used; step marked "with help"
done       "You built it": every step passed, download your program (.c / .py), list of concepts you used
```

Step i+1 is built in the background as soon as step i is opened, so the learner codes while the model works. A step
is built from CodeCoach's reference solution of step i-1 (always known), never from the learner's code.

## Engine (`codecoach/project.py`)

`ProjectEngine(svc)` mirrors `LearnEngine` in `codecoach/learn.py` (read it first: storage in store meta, lazy builds,
`svc._spawn` for background work, `structured_call` for every model call). No database migration.

**Storage (store meta, JSON):**
- `projects:index` -> list of project ids, newest first
- `project:<pid>` -> state: id, title, language, idea, status, reason, summary, plan steps, current, code (learner's
  latest code), per-step progress (status, attempts, failed_checks, hints_used, with_help), created_at, updated_at
- `project:<pid>:step:<i>` -> the built step INCLUDING server-only fields (reference_solution, hidden tests, all
  hints). Server-only fields never appear in any API response (test-enforced).
- Delete removes all three kinds of key for that project. Check `codecoach/core/store.py` for the meta API; if there is no
  meta delete, add the smallest one with a test.

**Plan call.** Reply model (pydantic, in project.py): `feasible: bool`, `why_not: str`, `title: str`,
`summary: str`, `steps: list[{title, goal, concept}]`. Rules in the prompt: beginner level; one new idea per step;
deterministic programs only (no random numbers, time, files, network, graphics; take any "secret" from input);
stdin/stdout only. Code checks: 4-8 steps, non-empty fields, else retry (structured_call retries), else project
status `failed` with a plain reason. `feasible: false` -> status `refused` with `why_not` shown.

**Step build call.** Input: idea, plan, step index, CodeCoach's previous reference solution (step 0: the starter, below).
Reply: `lesson: list[str]` (2-5 sentences), `example: {code, stdin}`, `task: str`, `reference_solution: str` (the
FULL program after this step), `visible_tests` and `hidden_tests` (stdio form of `CodeTest`:
`name, stdin, expected_stdout`), `hints: list[str]` (exactly 3, in ladder order).

Starter (step 0's "previous reference" and the learner's first editor content):
- C: `#include <stdio.h>\n\nint main(void)\n{\n    return 0;\n}\n`
- Python: `# Your program starts here\n`

**Gates, all code, in this order** (use `codecoach/check/code_runner.py`: `check_reference`, `run_snippet`, `grade`):
- G1 reference passes every visible and hidden test (`check_reference`).
- G2 the step tests something new: CodeCoach's previous reference FAILS at least one of this step's tests.
- G3 deterministic: the reference run twice gives identical stdout on every test.
- G4 at least 1 visible and 1 hidden test; no hidden test has the same stdin as a visible one.
- G5 small step: the reference adds at most 40 non-blank lines over the previous reference.
- G6 example: runs with exit 0 within limits via `run_snippet`. The step stores the REAL stdout as `example.stdout`.
  If the example fails, one repair attempt; if it fails again the step keeps going with `example: null`.
- G7 hint leak: drop any hint that contains, verbatim after trimming, a reference line of 12+ non-space characters
  that is not already in the previous reference. Fewer than 3 hints left is fine.
- G8: every kind of output line a hidden test expects was shown to the learner:
  a visible test of this or an earlier step, or a quote in the task. Numbers, typed input and words the task names may
  vary. Steps print no input prompts. Steps 0 and 1 start building as soon as the plan is ready.
- G1-G5 or G8 failing: retry the whole step call (at most 2 retries in total), then status `unavailable` with the gate's
  plain reason. A retry endpoint rebuilds it.

Builds: one model build at a time across all projects (a module-level lock), because a local model usually shares one GPU.
`PROJECT_PROMPT_VERSION` is part of each step's stored record; a mismatch rebuilds that step when opened.
Sandbox missing (`sandbox_status() != "bwrap"`): no builds start, and every response says `sandbox: "unavailable"`.

## API (all under `/api/plugins/codecoach`)

Routes go in ONE block appended at the END of `dashboard/plugin_api.py`. Service gets one-line delegations next to the
`learn_*` ones in `codecoach/service.py`, plus the engine's construction where `_learn` is built.

`GET /projects` ->
```json
{"sandbox": "bwrap",
 "ideas": [{"title": "Number-guessing game", "language": "c", "pitch": "You type the secret and the guesses; it says higher or lower."}],
 "projects": [{"id": "p_1a2b", "title": "...", "language": "c", "status": "ready", "steps_total": 6, "steps_passed": 2,
               "updated_at": "2026-11-08T22:00:00Z"}]}
```
`ideas` is a fixed list in project.py (4-6 entries, both languages, all deterministic stdin/stdout programs).

`POST /projects` body `{"idea": "...", "language": "c"}` (idea 3-300 chars after trim; language c | python; 422
otherwise; 503 code `sandbox_unavailable` when there is no sandbox) -> `{"project_id": "p_...", "status": "planning"}`.

`GET /projects/{pid}` -> `Project`:
```json
{"id": "p_1a2b", "title": "Guess the number", "language": "c", "idea": "...", "summary": "...",
 "status": "planning | ready | refused | failed | done", "reason": null, "current": 1, "code": "...",
 "steps": [{"index": 0, "title": "...", "goal": "...", "concept": "printf",
            "status": "passed | ready | building | unavailable | locked | planned", "attempts": 2, "hints_used": 1,
            "with_help": false}]}
```
`planned` = in the plan, not built yet and not unlocked; `locked` = not reachable yet (index > current).

`GET /projects/{pid}/steps/{i}` -> `Step`. Opening step i <= current starts its build if needed and prefetches i+1.
i > current -> 409 code `step_locked`.
```json
{"index": 1, "title": "...", "goal": "...", "concept": "...", "status": "ready",
 "lesson": ["...", "..."],
 "example": {"code": "...", "stdin": "", "stdout": "real output", "ran": true},
 "task": "...",
 "visible_tests": [{"name": "too low", "stdin": "50\n10\n", "expected_stdout": "Too low\n"}],
 "hidden_count": 2, "hints_shown": ["..."], "hints_left": 2, "can_show_solution": false,
 "starting_code": "the learner's code to load if the editor is empty", "reason": null}
```
While `status` is `building`, only index/title/goal/concept/status are filled (the rest empty or null).

`POST /projects/{pid}/steps/{i}/run` body `{"code": "..."}` -> the SAME JSON shape as the existing
`POST /items/{id}/run` (so the screen reuses its result renderer). Saves `code`.
`POST /projects/{pid}/steps/{i}/check` body `{"code": "..."}` -> that shape plus `{"step_passed": bool,
"next_index": int | null, "project_done": bool}`. Hidden test results carry no stdin/expected (as `grade` does).
Pass: step `passed`, current = i+1, prefetch i+2; last step passed -> project `done`.
`POST /projects/{pid}/steps/{i}/hint` -> `{"hint": "...", "hints_left": 1}`; none left -> 409 `no_hints`.
`POST /projects/{pid}/steps/{i}/solution` -> `{"code": "..."}` only when `can_show_solution`; else 409
`solution_locked`. Marks the step `with_help`.
`POST /projects/{pid}/steps/{i}/retry` -> rebuilds an `unavailable` step (409 otherwise).
`DELETE /projects/{pid}?confirm=true` -> `{"deleted": true}`.

Errors use the existing `ServiceError(status, code, message)` path; messages are plain sentences a learner can act on.

## Screen (`desktop/plugin.js`)

- New view `projects` FIRST in `VIEWS` (codicon `tools`), and the default view. Course-based views stay as they are.
- Home: "Build something" with idea box, C / Python toggle, idea chips from `ideas`, Start; your projects with
  "step 2 of 6". Sandbox missing -> a Banner saying what to install, Start disabled.
- Planning: honest wait copy ("Planning your project. A local model can take a couple of minutes; you can leave this
  page and come back."). Poll every 3 s.
- Project: step rail (title + status icon, codicons only) and the open step: Lesson labelled "Tutor's explanation";
  Example card labelled "Ran in CodeCoach's sandbox" with the real output; Task; visible tests as input -> expected;
  the existing code editor (reuse the code-item editor, Ctrl+Enter runs); buttons Run, Check step, Hint (n left),
  Show CodeCoach's solution (only when allowed, with a one-line warning). Results reuse the code-item result renderer.
- Step passed: short "Step done" and Next step (or "Building the next step..." with polling).
- Done: "You built it", Download your program, concepts used.
- Refused / failed / unavailable: the reason plus the one action (Try another idea / Try again).
- **Programming only:** the app talks only about programming. Import copy says programming notes; any subject choice
  in the import UI is removed (subject is Programming); onboarding and empty states speak about code. No view or
  engine code is deleted.
- Theme tokens only (`tests/desktop/test_host_theme_tokens.py`), no emoji, codicons only, light + dark, 1280 and 900.
