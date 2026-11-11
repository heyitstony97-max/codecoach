"""Grade book in JavaScript (Node.js)."""

_READ = "const lines = require('fs').readFileSync(0, 'utf8').split('\\n');\n"

_S1 = _READ + "const score = Number(lines[0]);\nconsole.log('Score: ' + score);\n"

_S2 = (_READ + "const count = Number(lines[0]);\nlet total = 0;\nfor (let i = 1; i <= count; i++) {\n"
       "  const score = Number(lines[i]);\n  total = total + score;\n}\n"
       "console.log('Count: ' + count);\nconsole.log('Total: ' + total);\n")

_S3 = (_READ + "const count = Number(lines[0]);\nlet total = 0;\nlet best = 0;\nfor (let i = 1; i <= count; i++) {\n"
       "  const score = Number(lines[i]);\n  total = total + score;\n  if (score > best) {\n    best = score;\n  }\n}\n"
       "console.log('Count: ' + count);\nconsole.log('Total: ' + total);\n"
       "console.log('Average: ' + Math.floor(total / count));\nconsole.log('Best: ' + best);\n")

_S4 = (_READ + "const count = Number(lines[0]);\nlet total = 0;\nlet best = 0;\nfor (let i = 1; i <= count; i++) {\n"
       "  const score = Number(lines[i]);\n  total = total + score;\n  if (score > best) {\n    best = score;\n  }\n"
       "  if (score >= 60) {\n    console.log(score + ' pass');\n  } else {\n    console.log(score + ' fail');\n  }\n}\n"
       "console.log('Count: ' + count);\nconsole.log('Total: ' + total);\n"
       "console.log('Average: ' + Math.floor(total / count));\nconsole.log('Best: ' + best);\n")

LANG = {"steps": [
    {"lesson": ["In Node.js, the whole input can be read at once with the fs module, and split('\\n') cuts that text into an array of lines.",
                "The first line is lines[0], because counting in an array starts at 0.",
                "A line is text, even when it holds digits, so wrap it in Number() to get a number you can calculate with.",
                "A const is a name for a value that you do not change, and console.log prints a line."],
     "example": {"code": "const text = require('fs').readFileSync(0, 'utf8').split('\\n')[0];\n"
                         "console.log('Double is ' + Number(text) * 2);\n", "stdin": "21\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number in from the first line, then show it with its label.",
               "Read the lines once, take the first one, and turn that text into a number with Number().",
               "Keep Number(lines[___]) in a const, then call console.log with the label joined to it by +."]},
    {"lesson": ["A for loop repeats a block; its parentheses set a counter, say when to stop, and say how to count.",
                "Here the scores sit in lines 1 to count, so the counter starts at 1 and the loop runs while it is at most count.",
                "To add up, start a variable at 0 before the loop with let, and add each score to it inside the loop.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": "let sum = 0;\nfor (let k = 1; k <= 4; k++) {\n  sum = sum + k;\n}\nconsole.log(sum);\n", "stdin": ""},
     "solution": _S2,
     "hints": ["The first line only tells you how many lines of scores come after it.",
               "Use a for loop that visits line 1 up to line count, and keep a running total that starts at 0.",
               "for (let i = 1; i <= ___; i++) and inside it total = ___ + score."]},
    {"lesson": ["Dividing in JavaScript gives a decimal, so Math.floor() is used to drop the remainder: Math.floor(7 / 2) is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs its block only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": "console.log(Math.floor(7 / 2));\nif (7 > 2) {\n  console.log('seven is bigger');\n}\n", "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide and round down with Math.floor, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if (___ > best) { and set best to the score inside it."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": "const t = 15;\nif (t >= 20) {\n  console.log('warm');\n} else {\n  console.log('cool');\n}\n", "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if (___ >= 60) { and print the score and the word pass, then } else { and print the score and the word fail."]},
]}
