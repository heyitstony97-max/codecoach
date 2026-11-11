# Demo script for version 1.1 (target 80 s, hard cap 85 s; the rule is under 90)

One continuous take, no cuts, screen recording of Hermes Desktop with the CodeCoach page, voice-over by Anthony. Nothing in it waits on a model: the project is the ready-made one, and the drill and the model check are prepared before recording (see [RESET.md](RESET.md)).

Window 1280x800, dark theme, CodeCoach page full-width. Cursor visible. No emoji, no handles on screen.

| # | Time | Beat |
|---|------|------|
| 1 | 0-8 s | Sixteen languages, pick Rust |
| 2 | 8-20 s | Start: a lesson and an example that really ran |
| 3 | 20-45 s | A wrong program, the real diff, the fix, green |
| 4 | 45-60 s | Practice: predict the output |
| 5 | 60-72 s | The model check |
| 6 | 72-80 s | Close |

## Beat 1 (0-8 s): sixteen languages, pick Rust

- Start on **Build**. The language list is on screen with each compiler's version and a fit badge.
- Click **Rust**. The button on the Grade book card now reads **Start in Rust**, and the line under the list reads "Rust <version>, proven in the sandbox on this machine."
- Narration (about 8 s): "I'm teaching myself to program. CodeCoach is a Hermes plugin that teaches any of sixteen languages my machine can run. Tonight, Rust."

## Beat 2 (8-20 s): Start, a lesson, an example that really ran

- Press **Start in Rust**. The first lesson opens in a second or two. Do not cut the wait: it is the point.
- Pause on **Tutor's explanation** (2 s), then on the example labelled **Ran in CodeCoach's sandbox** with its input and output (2 s).
- Narration (about 11 s): "No waiting. Every step teaches one idea, and the example isn't a model guessing what it prints: CodeCoach compiled it and ran it, and that's the real output."

## Beat 3 (20-45 s): wrong, the real diff, the fix, green

- Scroll to **Tests your code must pass** and **Your code**.
- Replace the code with the cheat from RESET.md (it prints `Score: 85` whatever the input is). Press **Ctrl+Enter**.
- Expected: **1 of 2 passing**. The failing test shows expected `Score: 0` next to got `Score: 85`.
- Paste the real program from RESET.md. Press **Check step**.
- Expected: **4 of 4 passing** (two of them were hidden until now) and **Next step**.
- Narration (about 23 s): "Now me. I'll cheat and hard-code the answer. One test passes, one fails, and it shows exactly what I printed against what it wanted. So I read the input properly. Check step runs hidden tests too. Four of four."

## Beat 4 (45-60 s): Practice

- Click **Practice**, then **Loops** (the drill is already built, see RESET.md). A short Rust program appears with "Type exactly what this program prints".
- Type your prediction and press **Ctrl+Enter**.
- Expected: your lines next to the real ones, and the output labelled **Ran in CodeCoach's sandbox**.
- Narration (about 13 s): "Practice is reading code. I predict what it prints, and the answer key isn't the model's opinion. It's what the program printed when CodeCoach ran it."

## Beat 5 (60-72 s): the model check

- Click **Build**, then click the fit badge on the Rust row (or on the weakest language in your list).
- Expected: five warm-ups with passed or failed, the model's name, when it was measured, and for a Mixed or Weak result the lines on how to point CodeCoach at another model.
- Narration (about 11 s): "And it checks the model too. Five small programs per language, run in the sandbox. If my model is weak in a language, it says so and shows me how to switch."

## Beat 6 (72-80 s): close

- Click **Test** and hold on it.
- Narration (about 7 s): "Build, practice, test. Real code, real output, sixteen languages. CodeCoach, for Hermes."
- No end card is needed in a single take. No handles on screen (they go in the post, not the video).

## Notes

- Spoken text is about 73 s at a calm pace. If a take runs long, drop beat 6's click and say the last line over beat 5.
- Every number said aloud must be on the screen on that take. If your machine has fewer than sixteen languages set up, say the number your list shows.
- Read the badge that is on screen in beat 5; do not promise a result the take does not show.
- The post on X (tagging @NousResearch) and the form are Anthony's.
