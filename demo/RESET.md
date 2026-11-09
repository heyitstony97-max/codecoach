# Preparing and resetting the demo

The video never waits on the model. On a local model a project plan takes about a minute and each step one to three minutes, so build everything before you record.

## One-time pre-build

Record on a Hermes install where CodeCoach is enabled and routed to the model you will name in the video (README, Install and "Route CodeCoach to a local model"). Only the Projects tab is shown, so other courses in your Library stay off screen; do not click Library during a take.

1. **The demo project (beats 1-3).** In **Projects**, start **Number-guessing game** in **C**. Wait until step 1 is open and the step rail shows step 2 as built (it builds in the background). Do not run or check any code yet.
2. **The finished project (beat 5).** Start a second project (for example **Word counter** in C) and work through every step yourself until it says **You built it**. The narration says "written by me", so it has to be true: do not use Show CodeCoach's solution on this one.
3. Rehearse beat 2 once on a throwaway project to see the kind of task the model writes. Then write the mistake you will make on camera below.

## Checks before recording

1. Projects lists the demo project (step 1 of N, nothing passed) and the finished project (all steps passed).
2. The demo project's step 1 shows *Tutor's explanation*, an example labelled *Ran in CodeCoach's sandbox* with an output, a task, and at least one visible test.
3. Step 2 of the demo project shows as built in the step rail (not building).
4. The finished project shows **You built it** and **Download your program** works.
5. Nothing private on screen: no project title or code mentions anything personal, and the terminal prompt hides your home directory.

## Reset between takes

Takes change state (a step passes, hints are used). Keep a pristine copy of the data directory once the checks pass, and restore it before each take:

```
cp -r <hermes home>/plugin-data/codecoach ~/codecoach-demo-pristine      # once, after the checks pass
```

Before each take, close Hermes Desktop and stop the gateway, then:

```
cp -r ~/codecoach-demo-pristine/. <hermes home>/plugin-data/codecoach/
```

Start the gateway and Desktop again and open the CodeCoach page. Copy over the folder; do not delete it while Hermes is running.

## The mistake to type (beat 2)

The task differs per build. After the pre-build, open step 1 of the demo project, read the task, and write here the program you will type with one visible mistake (wrong wording or a missing newline) and the fix:

```
(fill in after the pre-build: the step 1 task, the program with the mistake, and the one-line fix)
```

## Recording settings

1280x800, dark theme, 30 fps, system audio off, voice recorded separately. Hide the terminal prompt path (it shows the home directory) and any base URL.
