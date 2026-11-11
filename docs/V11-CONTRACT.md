# CodeCoach 1.1: the contract

What version 1.1 adds to CodeCoach and what the code must guarantee. The step checks of Build are in
[PROJECT-CONTRACT.md](PROJECT-CONTRACT.md) and are unchanged. Section numbers are stable: code comments cite them.

## 1. What 1.1 is

**CodeCoach teaches you a programming language by having you build, predict and test, in any language your machine
can run, and it proves each claim by really running code.**

| In 1.1 | Proof by code |
|---|---|
| Many languages | A language is offered only after a hello-world compiled and ran inside the sandbox on this machine |
| Projects in every ready language | The step checks G1-G8 of version 1.0, unchanged, on the new runner |
| Ready-made project | Shipped lessons that need no model and pass the same checks G1-G8 on this machine |
| Model fit check | The model solves 5 fixed warm-up tasks per language; the sandbox grades them against hand-written keys |
| Practice | Predict-the-output drills; the answer key is the program's real stdout, never model text |
| Test | A no-hints checkpoint made of drills, with a score and weak topics |
| Coding only | The notes and course screens of 1.0 are hidden unless the setting `show_notes` turns them on |

Not in 1.1: removing the 1.0 notes engine, more languages than the sixteen in section 3, a per-project model picker,
new agent tools, fix-the-bug drills, any new dependency.

## 2. Rules that bind all of it

1. **Version 1.0 behaviour stays.** `PROJECT_PROMPT_VERSION` is not bumped, and the prompt text a C or Python
   project sends is byte-identical to 1.0 (a test holds a copy), so a project started in 1.0 opens in 1.1 without
   being rebuilt. Stored 1.0 data loads unchanged.
2. **Limits are always finite.** Every sandboxed process has a wall clock. `RLIMIT_AS` is never unlimited and never
   above 4 GiB for any language.
3. **In Build, Practice and Test the learner's code and answers never go to the model.** Lessons, drills and
   warm-ups are built from fixed prompts and CodeCoach's own solutions; what the learner writes only goes to the
   sandbox.
4. **Nothing the learner is graded on is model text.** Test verdicts, example output and drill answer keys are
   captured from real runs.
5. **Plain words** in every learner-facing string, no emoji.
6. **Tests need no model and no network.** They use a scripted stand-in for the model and the real sandbox.

## 3. Languages (`codecoach/lang/`, new package)

Fixed ids, in display order. Tier A is required; Tier B ships only where every check below is green.

- Tier A: `c`, `cpp`, `python`, `javascript`, `java`, `go`, `rust`, `ruby`, `bash`
- Tier B: `php`, `lua`, `perl`, `haskell`, `zig`, `elixir`, `lisp` (as built; `typescript` was dropped: its
  compiler lives in a folder the bind rules refuse, and the system Node cannot strip types)

Public surface (import only from `codecoach.lang`):

```python
all_ids() -> list[str]                           # every id above that has a spec, display order
spec(lang_id) -> LangSpec                        # raises UnknownLanguage (a ValueError)
status(lang_id, *, refresh=False) -> LangStatus
statuses(*, refresh=False) -> list[LangStatus]   # one per id, display order
ready_ids() -> list[str]
configure(cache_dir: Path | None) -> None        # where the probe cache lives; None = memory only
```

`LangSpec` fields other code may read: `id`, `name` ("C++"), `ext` (".cpp"), `starter` (a program that compiles,
runs and prints nothing), `style_note` (one line for model prompts, e.g. "Plain C11, standard library only." or
"One file. public class Main with a main method. Read input with a Scanner on System.in."), `comment` (line-comment
prefix), `live_run` (bool: cheap enough to run the visible tests while the learner types).

`LangStatus` is also the API shape:

```json
{"id": "rust", "name": "Rust", "status": "ready", "version": "1.99.1", "reason": null,
 "hello_ms": 412, "live_run": false, "ext": ".rs", "tier": "A"}
```

- `status`: `ready` only after a hello-world (reads one line from stdin, prints it back with a prefix) was compiled
  and run INSIDE the sandbox on this machine and printed exactly the expected line. `missing` = toolchain not
  found. `failed` = found but the probe failed; `reason` is one plain sentence ("Found Go at /usr/local/go, but it
  could not run inside the sandbox.").
- Probe results are cached (toolchain path + version + mtime) so pages never wait on a probe twice. `refresh=True`
  re-probes. A probe has a wall clock; all probes together finish in under 60 s on this box.
- `live_run` is false when a warm compile-and-run of hello takes over 1.5 s.

Sandbox rules for toolchains (the new attack surface, so tests are required):

- The sandbox keeps `--unshare-all` (no network), read-only system dirs, tmpfs `/tmp`, the work dir, `--clearenv`.
- **Process limit (changed Nov 10):** set INSIDE the sandbox with `prlimit --nproc=K:K` where the kernel counts it
  per user namespace (Linux 5.14+, proven once per process by `code_runner.nproc_inside()`); v1.0's outside limit
  (the user's task count plus headroom) is the fallback. Outside, a busy desktop or a few sandboxes side by side
  made correct programs fail to start (measured: 5 of 120 at 16 at a time; 0 of 720 after).
- A toolchain outside `/usr` gets ONLY its own resolved root bound read-only (examples: `rustc --print sysroot`,
  `go env GOROOT`, the real prefix of the `node` binary). Call the real binary, not a wrapper in `~/.cargo/bin`.
- Never bind `$HOME` or any parent of it, `/`, `~/.cargo`, `~/.npm`, `~/.config`, `~/.ssh`, `~/.gnupg`, `~/.aws`,
  or a root that contains any of those. Such a toolchain is `failed` with a plain reason.
- Per language, tests prove: hello; stdin echo; a compile/syntax error is reported in plain text with no host
  paths; an infinite loop is stopped at the time limit; a memory hog is stopped (rule 2.5 caps apply); a fork or
  thread bomb is stopped; no network (a connect attempt fails); a canary file planted under a temp HOME-like dir
  outside the sandbox is not readable; output is capped. Use the existing C/Python red-team tests as the model.
- Caches a compiler needs (Go build cache and similar) live in the sandbox's tmpfs or a per-language dir under the
  configured cache dir. If a persistent cache is used it is bound read-write ONLY at that one dir.

Runner (`codecoach/check/code_runner.py`): `run_snippet`, `run_visible`, `grade`, `check_reference` and
`check_code` accept every id in `all_ids()`. `CodePayload.language` becomes a `str` validated against `all_ids()`.
Stored v1.0 payloads (`"c"`, `"python"`) load unchanged.

API module `codecoach/lang/api.py`: `register(router, get_service)` adds

- `GET /languages` -> `{"sandbox": "bwrap", "languages": [LangStatus, ...]}`
- `POST /languages/refresh` -> the same shape after a re-probe (may take up to 60 s; runs in the request).

## 4. Projects in every ready language (`codecoach/project.py`)

- `POST /projects` accepts any id in `ready_ids()`. Otherwise 422 `bad_language` with "That language is not set up
  on this machine yet." The other project routes and shapes are unchanged.
- Starter, display name, download file extension and the prompt's style line come from the `LangSpec`.
- `GET /projects` `ideas` keeps its shape. Add at least one idea per Tier A language (deterministic stdin/stdout
  programs only). The screen may ignore an idea's `language` and pair the idea with the picked language.
- A step whose build fails all retries stores `reason` as before. Nothing else changes in the step shape.
- **Ready-made projects** (`codecoach/starters/`, added Nov 10): shipped data, no model call, the same gates
  G1-G8. `GET /projects` gains `"starters": [{"id": "grade-book", "title", "pitch", "steps": 4,
  "languages": [ids with data that are ready]}]`; every project row and `GET /projects/{id}` gain `"starter":
  "grade-book" | null`. `POST /projects` accepts `{"starter": "grade-book", "language": "rust"}` with no idea;
  an unknown starter or a language without data is 422 `bad_starter`.

## 5. Model fit (`codecoach/fit/`, new package)

Why: the model writes every lesson's reference solution. A model that is weak in a language fails builds or is
slow there. v1.1 measures that and says so.

- **Warm-ups:** five fixed tasks, the same for every language, each with three stdin -> stdout tests whose keys are
  hand-written data in the repo (a test recomputes them with a Python reference): `sum2` (two integers on a line,
  print the sum), `count_vowels` (a line, print how many of aeiou, any case), `fizzbuzz` (n, then 1..n with
  Fizz/Buzz/FizzBuzz), `reverse_words` (a line, words in reverse order, single spaces), `max_of_list` (n, then n
  integers, print the largest).
- **One check** = for each task one `structured_call` (reply `{code: str}`; the prompt gives the task, the
  language name and its `style_note`), then the sandbox runs the code on the three tests. A task passes only when
  all three match after the runner's normal stdout normalization. Calls run one at a time under
  `codecoach.project._BUILD_LOCK` (import it, do not copy it). No retries: a retry would hide weakness.
- **Verdict:** 5 or 4 passed `good`; 3 `mixed`; 2 or fewer `poor`; model unreachable `unknown`.
- **Which model:** the LLM port reports the provider and model that answered. Add `last_route() -> dict | None`
  (`{"provider": ..., "model": ...}`) to the port in `codecoach/core/llm.py` (FakeLLM too) and fill it in
  `codecoach/hermes_glue.py` from the host result's `provider` and `model` fields. A fit record is stored per
  language AND model, in store meta: `fit:<language>:<provider>/<model>`, plus `fit:latest:<language>`.
- `engine_for(svc) -> FitEngine` caches the engine on the service object (no edit to service.py).
  `FitEngine.ensure(language)` starts a check if there is no record for the current model; it never blocks.
- **It starts on its own** (as built): `CodeCoachService.language_used(language)` calls `ensure` after the model's
  first stored project step or drill in a language, never for a ready-made project, never ahead of the first
  lesson. Setting `auto_fit_check` (plugin default true) turns it off. The screen re-reads `GET /fit` every 20 s.

API module `codecoach/fit/api.py`: `register(router, get_service)` adds

- `GET /fit` -> `{"model": {"provider": "custom", "model": "qwen3.8-27b"} | null, "languages": {"rust": Fit, ...},
  "switch": {"commands": ["hermes config set auxiliary.codecoach.provider <provider>",
  "hermes config set auxiliary.codecoach.model <model>"], "note": "Then press Check again."}}`
  (`languages` holds one entry per language that has any record; `model` is the last model that answered.)
- `POST /fit/{language}` -> 202 `{"status": "running"}`; 409 `fit_running` if one is running; 422 `bad_language`.
- `GET /fit/{language}` -> `Fit`:

```json
{"language": "rust", "status": "none | running | done | error", "verdict": "good | mixed | poor | unknown",
 "passed": 4, "total": 5, "done_tasks": 5,
 "tasks": [{"id": "sum2", "title": "Add two numbers", "ok": true, "reason": null},
           {"id": "fizzbuzz", "title": "FizzBuzz", "ok": false, "reason": "Its program did not compile."}],
 "model": {"provider": "custom", "model": "qwen3.8-27b"}, "median_ms": 8200,
 "measured_at": "2026-11-11T05:00:00Z", "stale": false,
 "advice": "This model passed 4 of 5 Rust warm-ups. It should teach Rust well."}
```

- `reason` values are plain sentences for: did not compile, wrong output, too slow, no answer from the model.
- `stale` is true when the last model that answered differs from the record's model.
- `advice` for `poor`: "This model passed 1 of 5 Zig warm-ups. Lessons in Zig may fail to build or take long.
  You can point CodeCoach at another model and check again." Never names a specific other model.
- The learner's own code is never sent to the model here or anywhere in v1.1.

## 6. Practice and Test (`codecoach/practice/`, new package)

**Drill (kind `predict`).** The model writes a short program for (language, topic); the learner predicts its
output; the key is the real stdout.

- Topics (fixed ids and titles): `variables` Variables, `arithmetic` Arithmetic, `strings` Strings,
  `conditionals` If and else, `loops` Loops, `functions` Functions, `lists` Lists and arrays, `tracing` Mixed
  tracing.
- Build call reply: `{code: str, stdin: str, explanation: list[str]}` (2-5 short sentences, shown only after an
  answer, labelled as the tutor's explanation).
- Gates, all code, in order. Fail -> one rebuild, then the drill is `unavailable` with a plain reason:
  - P1 runs in the sandbox, exit 0, inside the limits.
  - P2 deterministic: two runs give identical stdout.
  - P3 output is 1 to 8 lines, each at most 80 characters, not all blank.
  - P4 at most 25 non-blank lines of code.
  - P5 at least one output line is not written out whole as a string literal in the source (there must be
    something to work out; a bare substring test wrongly dropped drills whose output `6` matched `x = 16`).
  - P6 not a copy of a stored drill for the same language and topic (hash of whitespace-normalized code).
  - P7 the view sent before an answer carries no stdout and no explanation (test-enforced, like the project
    leak tests).
  - P8 no comment in the code carries a line of the output (`print(x * 6)  # 42`); the prompt asks for no
    comments at all.
- Grading is code: the prediction equals the real stdout after stripping trailing spaces per line and trailing
  blank lines. Nothing else is forgiven.
- Lazy and bounded: `next` serves an unseen stored drill for (language, topic) or builds one in the background,
  then prefetches ONE more. Builds take `codecoach.project._BUILD_LOCK`. Never build a whole bank.
- Storage in store meta (JSON), like projects: `practice:index`, `drill:<id>` (server-only fields included),
  `practice:attempts`. `engine_for(svc) -> PracticeEngine` caches on the service object.
- Readiness seam: `ready_ids()` from `codecoach.lang` when that package exists, else `("c", "python")`.

API module `codecoach/practice/api.py`: `register(router, get_service)` adds

- `GET /practice?language=rust` -> `{"sandbox": "bwrap", "language": "rust",
  "topics": [{"id": "loops", "title": "Loops", "seen": 3, "correct": 2, "ready": 1}]}`
- `POST /practice/next` body `{"language": "rust", "topic": "loops"}` -> `{"drill_id": "d_1a2b", "status": "ready | building"}`
- `GET /practice/drills/{id}` -> `{"id": "d_1a2b", "language": "rust", "topic": "loops", "kind": "predict",
  "status": "ready | building | unavailable", "code": "...", "stdin": "", "line_count": 3, "reason": null,
  "answered": false}` (no stdout, no explanation before an answer; test-enforced)
- `POST /practice/drills/{id}/answer` body `{"prediction": "..."}` -> `{"correct": false, "expected_stdout": "1\n3\n6\n",
  "lines": [{"n": 1, "expected": "1", "got": "1", "ok": true}, {"n": 2, "expected": "3", "got": "2", "ok": false}],
  "explanation": ["..."]}`

**Test (checkpoint).** A fixed set of drills, no feedback until the end.

- `POST /tests` body `{"language": "rust", "size": 5}` (size 3-10, default 5; topics spread evenly, unseen drills
  first) -> `{"test_id": "t_9f", "status": "building | ready"}`
- `GET /tests/{id}` -> `{"id": "t_9f", "language": "rust", "status": "building | ready | done", "ready_count": 3,
  "size": 5, "questions": [{"n": 1, "drill_id": "d_1a2b", "topic": "loops", "answered": true}], "result": null}`
  (`result` is the finish shape once done)
- `POST /tests/{id}/answer` body `{"n": 1, "prediction": "..."}` -> `{"recorded": true}` (may be changed until finish;
  no correctness is revealed; a drill opened through a test never reveals its key through the practice routes
  until the test is finished)
- `POST /tests/{id}/finish` -> `{"score": 4, "total": 5, "by_topic": [{"topic": "loops", "title": "Loops",
  "correct": 1, "total": 2}], "weak_topics": ["loops"], "review": [{"n": 1, "topic": "loops", "correct": false,
  "code": "...", "your_answer": "...", "expected_stdout": "...", "explanation": ["..."]}]}`
- `GET /tests` -> `{"tests": [{"id", "language", "status", "score", "total", "created_at"}]}`

## 7. Screen (`desktop/plugin.js`) and `GET /meta`

`GET /meta` -> `{"version": "1.1.0", "features": {"notes": false}}`.

- Views, in order: **Build** (the Projects view, default), **Practice**, **Test**. Every course/notes view (Learn,
  Review, Ask, Sources, map, timeline, progress, import) shows only when `features.notes` is true. With it false no
  screen, empty state, tooltip or palette entry mentions notes, materials, courses, history, math or science.
- **Language picker** (Build home, Practice, Test) from `GET /languages`: ready languages as pickable rows with
  name, version and a fit badge; below, a collapsed "Not set up on this machine" list with each `reason`. The
  picked language is remembered. "Look again" calls the refresh route.
- **Fit badge** from `GET /fit`: `Good fit 5/5`, `Mixed 3/5`, `Weak 1/5`, `Checking 2 of 5`, `Not checked`.
  Opening it shows the five warm-ups with pass or fail and reason, the model name, when it was measured,
  `advice`, a "Check again" button, and for `mixed`/`poor`/`stale` the `switch.commands` each with a copy button.
  A `poor` verdict also shows one line under the Start button before a project starts. It never blocks.
- A step that is `unavailable` shows its reason plus the language's fit verdict and the same switch block.
- **Persistent proof line** on Build, Practice and Test: "Rust 1.99.1, proven in the sandbox on this machine.
  Model fit 4/5 on <model>." (text only, tertiary colour).
- **Practice:** topic list with seen/correct; a drill shows the code (read-only, same code font), the stdin if any,
  "Type exactly what this program prints" with a plain textarea and `line_count` as a hint; after Check: per-line
  expected vs yours, the real output labelled "Ran in CodeCoach's sandbox", the tutor's explanation, Next drill.
  Building -> honest wait copy and 3 s polling.
- **Test:** pick size, Start; one question at a time with a progress count, no feedback; Finish -> score, by topic,
  weak topics each with "Practice this", and the review list.
- Typing in a project runs the visible tests live only when the language's `live_run` is true; otherwise Run only.
- Rules that already bind this file: no JSX, SDK imports only, theme tokens only
  (`tests/desktop/test_host_theme_tokens.py`), codicons only, no emoji, light and dark, 1280 and 900 wide,
  timers through `ctx.*`, every backend call can fail or time out at 30 s and says so plainly.
