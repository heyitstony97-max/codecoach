"""Grade book in Go."""

_HEAD = 'package main\n\nimport "fmt"\n\nfunc main() {\n'


def _p(body):
    return _HEAD + "".join("\t" + ln + "\n" for ln in body) + "}\n"


_S1 = _p(["var score int", "fmt.Scan(&score)", 'fmt.Println("Score:", score)'])

_S2 = _p(["var count int", "fmt.Scan(&count)", "total := 0", "for i := 0; i < count; i++ {",
          "\tvar score int", "\tfmt.Scan(&score)", "\ttotal = total + score", "}",
          'fmt.Println("Count:", count)', 'fmt.Println("Total:", total)'])

_S3 = _p(["var count int", "fmt.Scan(&count)", "total := 0", "best := 0", "for i := 0; i < count; i++ {",
          "\tvar score int", "\tfmt.Scan(&score)", "\ttotal = total + score", "\tif score > best {",
          "\t\tbest = score", "\t}", "}",
          'fmt.Println("Count:", count)', 'fmt.Println("Total:", total)',
          'fmt.Println("Average:", total/count)', 'fmt.Println("Best:", best)'])

_S4 = _p(["var count int", "fmt.Scan(&count)", "total := 0", "best := 0", "for i := 0; i < count; i++ {",
          "\tvar score int", "\tfmt.Scan(&score)", "\ttotal = total + score", "\tif score > best {",
          "\t\tbest = score", "\t}", "\tif score >= 60 {", '\t\tfmt.Println(score, "pass")',
          "\t} else {", '\t\tfmt.Println(score, "fail")', "\t}", "}",
          'fmt.Println("Count:", count)', 'fmt.Println("Total:", total)',
          'fmt.Println("Average:", total/count)', 'fmt.Println("Best:", best)'])

LANG = {"steps": [
    {"lesson": ["In Go, the Scan function from the fmt package reads the next whole number from the keyboard and stores it in a variable.",
                "The variable is declared first with var and a type such as int, and the & in front of it tells Scan where to put the value.",
                "fmt.Println prints its arguments separated by spaces and ends the line.",
                "Go refuses to compile a program that imports a package it does not use, so keep the fmt import only while you use fmt."],
     "example": {"code": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tvar age int\n\tfmt.Scan(&age)\n'
                         '\tfmt.Println("Next year:", age+1)\n}\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number into a variable, then show it with its label.",
               "Declare an int variable, read into it with fmt.Scan, and print with fmt.Println.",
               "Write var score int, then fmt.Scan(&___), then fmt.Println with the label and the variable."]},
    {"lesson": ["A for loop repeats a block; the three parts after the word for set a counter, say when to stop, and say how to count.",
                "Inside the loop, each pass calls fmt.Scan once, so the same line reads every score.",
                "To add up, start a variable at 0 before the loop with := and add each score to it inside the loop.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tsum := 0\n\tfor k := 1; k <= 4; k++ {\n'
                         "\t\tsum = sum + k\n\t}\n\tfmt.Println(sum)\n}\n", "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop that runs count times, and keep a running total that starts at 0.",
               "for i := 0; i < ___; i++ with a Scan inside, and in the loop write total = ___ + score."]},
    {"lesson": ["Dividing two ints in Go gives a whole number and drops the remainder, so 7 / 2 is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs its block only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": 'package main\n\nimport "fmt"\n\nfunc main() {\n\ta := 7\n\tb := 2\n\tfmt.Println(a / b)\n'
                         '\tif a > b {\n\t\tfmt.Println("a is bigger")\n\t}\n}\n', "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide two ints to get a whole-number result, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if ___ > best { and set best to the score inside it."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tt := 15\n\tif t >= 20 {\n\t\tfmt.Println("warm")\n'
                         '\t} else {\n\t\tfmt.Println("cool")\n\t}\n}\n', "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if ___ >= 60 { and print the score and the word pass, then } else { and print the score and the word fail."]},
]}
