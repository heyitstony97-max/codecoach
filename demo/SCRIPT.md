# Demo script (target 80 s, hard cap 85 s)

Five beats, screen recording of Hermes Desktop with the CodeCoach page, voice-over by Anthony. Do the pre-build in [RESET.md](RESET.md) first: on a local model a plan takes about a minute and each step one to three minutes, so the video never waits on the model. Start the demo project before recording and record the planning wait as a short separate clip; say "I started this a minute ago" rather than pretending it is instant.

Window 1280x800, dark theme, CodeCoach page full-width. Cursor visible. No emoji, no handles on screen.

| # | Time | Beat |
|---|------|------|
| 1 | 0-10 s | The idea |
| 2 | 10-35 s | Step 1: a lesson, an example that really ran, a wrong answer |
| 3 | 35-55 s | A hint, the fix, Check step, the next step is already built |
| 4 | 55-70 s | Why you can trust it |
| 5 | 70-80 s | You built it |

## Beat 1 (0-10 s): the idea

- Start on **Projects** (the first tab). Click the idea chip **Number-guessing game** (or type it), keep **C**, press **Start**. Cut to the project already planned (RESET.md built it).
- Expected: the step rail on the left with the plan's steps (4 to 8), step 1 open.
- Narration (about 9 s): "I'm learning C at night. So I built a tutor that teaches me by building a real program with me, one small step at a time."

## Beat 2 (10-35 s): lesson, a real example, a wrong answer

- Step 1 open. Pause on **Tutor's explanation** (2 s), then on the example card labelled **Ran in CodeCoach's sandbox** with its output (2 s).
- Scroll to **Your task** and **Tests your code must pass** (input -> expected output). The task quotes the exact output.
- In **Your code**, type a version with one deliberate mistake (it is in RESET.md: wrong wording or a missing newline). Press **Ctrl+Enter**.
- Expected: the run result shows the failing test with expected next to what the program printed.
- Narration (about 22 s): "Every step teaches one idea. The example isn't the model guessing what it prints: CodeCoach actually ran it, and that's the real output. Then it's my turn. The tests come first, and when I get it wrong, it shows me exactly what my program printed against what it should."

## Beat 3 (35-55 s): hint, fix, Check step

- Click **Hint (3 left)**. Expected: a nudge that does not contain the answer.
- Fix the mistake, press **Check step**. Expected: hidden tests run, **Step done**, and **Next step** (already built while you worked; if it still says "Building the next step...", wait off camera and cut).
- Click **Next step** once to show step 2 opening with your own code carried over.
- Narration (about 18 s): "Hints nudge, they never hand me the answer. Check step runs hidden tests too. Pass, and the next step is already waiting: CodeCoach built it in the background while I was coding."

## Beat 4 (55-70 s): why you can trust it

- Cut to the README's table **The checks every step must pass** (scroll slowly) or hold on the step screen.
- Narration (about 14 s): "Before I see any step, CodeCoach's own hidden solution has to pass every test in a sandbox, the step has to ask for something new, and any hint that leaks the answer is thrown out. Code checks it, not the model."

## Beat 5 (70-80 s): you built it

- Cut to the finished project from RESET.md: **You built it**, the concepts you used, **Download your program**. Click download, then show the file running in a terminal for 3 s (`gcc` it and run it with an input).
- Narration (about 9 s): "At the end, it's my program, written by me, and it runs. CodeCoach is a Hermes plugin, and it runs on a local model on my desk."
- End card (2 s): "CodeCoach, a Hermes plugin", plain text. No handles on screen (they go in the post, not the video).

## Notes

- Total spoken text is about 72 s at a calm pace; trim beat 4 first if a take runs long.
- Every number said aloud must be read off the screen on that take.
- The step texts differ from build to build (the model writes them). Rehearse on the project you will record, and read what is on screen rather than a fixed line.
- The final post (X, tagging @NousResearch) and the Typeform are Anthony's.
