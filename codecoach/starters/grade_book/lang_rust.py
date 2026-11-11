"""Grade book in Rust."""

_HEAD = "use std::io::Read;\n\nfn main() {\n"


def _p(body):
    return _HEAD + "".join("    " + ln + "\n" for ln in body) + "}\n"


_READ = ["let mut text = String::new();", "std::io::stdin().read_to_string(&mut text).unwrap();",
         "let mut words = text.split_whitespace();"]

_S1 = _p(_READ + ["let score: i32 = words.next().unwrap().parse().unwrap();", 'println!("Score: {}", score);'])

_S2 = _p(_READ + ["let count: i32 = words.next().unwrap().parse().unwrap();", "let mut total = 0;",
                  "for _ in 0..count {", "    let score: i32 = words.next().unwrap().parse().unwrap();",
                  "    total = total + score;", "}", 'println!("Count: {}", count);', 'println!("Total: {}", total);'])

_S3 = _p(_READ + ["let count: i32 = words.next().unwrap().parse().unwrap();", "let mut total = 0;", "let mut best = 0;",
                  "for _ in 0..count {", "    let score: i32 = words.next().unwrap().parse().unwrap();",
                  "    total = total + score;", "    if score > best {", "        best = score;", "    }", "}",
                  'println!("Count: {}", count);', 'println!("Total: {}", total);',
                  'println!("Average: {}", total / count);', 'println!("Best: {}", best);'])

_S4 = _p(_READ + ["let count: i32 = words.next().unwrap().parse().unwrap();", "let mut total = 0;", "let mut best = 0;",
                  "for _ in 0..count {", "    let score: i32 = words.next().unwrap().parse().unwrap();",
                  "    total = total + score;", "    if score > best {", "        best = score;", "    }",
                  "    if score >= 60 {", '        println!("{} pass", score);', "    } else {",
                  '        println!("{} fail", score);', "    }", "}",
                  'println!("Count: {}", count);', 'println!("Total: {}", total);',
                  'println!("Average: {}", total / count);', 'println!("Best: {}", best);'])

LANG = {"steps": [
    {"lesson": ["In Rust, the easiest way to read numbers is to read all of the input into a String and then split it into words.",
                "split_whitespace() cuts the text at spaces and line ends, and next() hands you the next word.",
                "A word is still text, so parse() turns it into a number, and the type you write on the variable, such as i32, says which kind.",
                "unwrap() stops the program with a message if there was no word or it was not a number, and println! prints a line, with {} marking where a value goes."],
     "example": {"code": "use std::io::Read;\n\nfn main() {\n    let mut buf = String::new();\n"
                         "    std::io::stdin().read_to_string(&mut buf).unwrap();\n"
                         "    let age: i32 = buf.trim().parse().unwrap();\n"
                         '    println!("Next year: {}", age + 1);\n}\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number from the input, then show it with its label.",
               "Read the input into a String, split it into words, take the first word and parse it into a number.",
               "Write let score: i32 = words.next().unwrap().___; then println! with the label, {} and the variable."]},
    {"lesson": ["A for loop repeats a block, and for _ in 0..count runs it count times without needing the counter.",
                "Inside the loop, each pass takes the next word and parses it, so the same line reads every score.",
                "To add up, start a variable at 0 before the loop with let mut and add each score to it inside the loop.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": "fn main() {\n    let mut sum = 0;\n    for k in 1..5 {\n        sum = sum + k;\n    }\n"
                         '    println!("{}", sum);\n}\n', "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop that runs count times, and keep a running total that starts at 0.",
               "for _ in 0..___ { with a parse inside, and in the loop write total = ___ + score;"]},
    {"lesson": ["Dividing two whole numbers in Rust gives a whole number and drops the remainder, so 7 / 2 is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs its block only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": 'fn main() {\n    let a = 7;\n    let b = 2;\n    println!("{}", a / b);\n    if a > b {\n'
                         '        println!("a is bigger");\n    }\n}\n', "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide two integers to get a whole-number result, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if ___ > best { and set best to the score inside it."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": 'fn main() {\n    let t = 15;\n    if t >= 20 {\n        println!("warm");\n    } else {\n'
                         '        println!("cool");\n    }\n}\n', "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if ___ >= 60 { and print the score and the word pass, then } else { and print the score and the word fail."]},
]}
