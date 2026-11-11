# Preparing and resetting the demo (version 1.1)

The take is continuous and never waits on a model. Three things are prepared first; each takes a minute or two.

## Before the first take

1. **Rust shows a model check.** On **Build**, click the fit badge on the Rust row and press **Check**. It makes five short model calls and ends as Good fit, Mixed or Weak. Whatever it says is what you will read out in beat 5.
2. **A Loops drill is waiting.** Click **Practice**, pick **Rust**, click **Loops**. When the program appears, do not answer it: press **All topics**. A drill you have not answered stays waiting, so the next click on Loops opens it at once. Rehearse the click once; if it shows "building" on a take, wait a minute and start the take again.
3. **Rust is the picked language** and the page is on **Build**, scrolled to the top.

## The two programs for beat 3

Keep these in a text editor next to the recording so you can paste them.

The cheat (passes one visible test, fails the other):

```
fn main() {
    println!("Score: 85");
}
```

The real program for step 1:

```
use std::io::Read;

fn main() {
    let mut text = String::new();
    std::io::stdin().read_to_string(&mut text).unwrap();
    let mut words = text.split_whitespace();
    let score: i32 = words.next().unwrap().parse().unwrap();
    println!("Score: {}", score);
}
```

## Checks before recording

1. The Grade book card's button reads **Start in Rust** and is enabled.
2. The Rust row shows a version number and a badge that is not "Not checked".
3. Practice, Rust, Loops opens a program within a second.
4. Nothing private on screen: your project list is below the fold on Build; do not scroll to it during the take.

## Between takes

A take leaves a new Grade book project in your list (harmless: each press of Start makes a fresh one) and answers the waiting drill. Before the next take, repeat step 2 above so an unanswered drill is waiting again, and scroll Build back to the top.

## Recording settings

1280x800, dark theme, 30 fps, system audio off, voice recorded separately or live. Hide any terminal and any base URL.
