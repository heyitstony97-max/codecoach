"""Grade book in Python."""

LANG = {"steps": [
    {"lesson": ["A program gets its input from the keyboard, and in Python the input() function reads one line of it.",
                "What input() gives back is text, even when you typed digits, so wrap it in int() to get a whole number.",
                "Store the number in a variable, which is a name for a value, and then print() can show it.",
                "print() can take several things separated by commas and puts a space between them."],
     "example": {"code": 'age = int(input())\nprint("Next year you will be", age + 1)\n', "stdin": "12\n"},
     "solution": 'score = int(input())\nprint("Score:", score)\n',
     "hints": ["Think about two jobs: getting the number in, and showing it with its label.",
               "input() reads one line as text; int() turns that text into a whole number you can keep in a variable.",
               "Read with ___ = int(input()), then print the label and the variable together."]},
    {"lesson": ["A loop repeats some lines, and for with range(n) repeats them n times.",
                "Inside the loop you read one score each time around, so the same line of code reads every score.",
                "To add things up, start a total at 0 before the loop and add each new value to it inside the loop.",
                "Print the count and the total after the loop, once all the scores have been read."],
     "example": {"code": "price = 0\nfor coin in [5, 10, 25]:\n    price = price + coin\nprint(price)\n", "stdin": ""},
     "solution": 'count = int(input())\ntotal = 0\nfor i in range(count):\n    score = int(input())\n'
                 '    total = total + score\nprint("Count:", count)\nprint("Total:", total)\n',
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop over range(count) so the reading code runs once per score, and keep a running total.",
               "Set the total to 0 first. Inside the loop read a score and add it with total = ___ + ___."]},
    {"lesson": ["Whole-number division in Python is written with two slashes, so 7 // 2 is 3 and the remainder is dropped.",
                "To find the highest value, keep a variable called best that starts at 0 and compare each new score with it.",
                "An if statement runs its indented lines only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": "tallest = 0\nfor height in [150, 182, 170]:\n    if height > tallest:\n"
                         "        tallest = height\nprint(tallest)\nprint(7 // 2)\n", "stdin": ""},
     "solution": 'count = int(input())\ntotal = 0\nbest = 0\nfor i in range(count):\n    score = int(input())\n'
                 '    total = total + score\n    if score > best:\n        best = score\n'
                 'print("Count:", count)\nprint("Total:", total)\nprint("Average:", total // count)\nprint("Best:", best)\n',
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide with // so there is no decimal part, and use an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if ___ > best: and then set best to the score."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "Comparisons give true or false: score >= 60 means the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": 'temperature = 15\nif temperature >= 20:\n    print("warm")\nelse:\n    print("cool")\n', "stdin": ""},
     "solution": 'count = int(input())\ntotal = 0\nbest = 0\nfor i in range(count):\n    score = int(input())\n'
                 '    total = total + score\n    if score > best:\n        best = score\n'
                 '    if score >= 60:\n        print(score, "pass")\n    else:\n        print(score, "fail")\n'
                 'print("Count:", count)\nprint("Total:", total)\nprint("Average:", total // count)\nprint("Best:", best)\n',
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if ___ >= 60: and print the score and the word pass, then else: and print the score and the word fail."]},
]}
