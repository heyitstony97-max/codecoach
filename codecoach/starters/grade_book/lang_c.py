"""Grade book in C."""

_HEAD = "#include <stdio.h>\n\nint main(void)\n{\n"
_TAIL = "    return 0;\n}\n"

_S1 = _HEAD + '    int score;\n    if (scanf("%d", &score) != 1)\n        return 1;\n    printf("Score: %d\\n", score);\n' + _TAIL

_S2 = (_HEAD + '    int count, total = 0;\n    if (scanf("%d", &count) != 1)\n        return 1;\n'
       '    for (int i = 0; i < count; i++) {\n        int score;\n        if (scanf("%d", &score) != 1)\n'
       '            return 1;\n        total = total + score;\n    }\n'
       '    printf("Count: %d\\n", count);\n    printf("Total: %d\\n", total);\n' + _TAIL)

_S3 = (_HEAD + '    int count, total = 0, best = 0;\n    if (scanf("%d", &count) != 1)\n        return 1;\n'
       '    for (int i = 0; i < count; i++) {\n        int score;\n        if (scanf("%d", &score) != 1)\n'
       '            return 1;\n        total = total + score;\n        if (score > best)\n            best = score;\n    }\n'
       '    printf("Count: %d\\n", count);\n    printf("Total: %d\\n", total);\n'
       '    printf("Average: %d\\n", total / count);\n    printf("Best: %d\\n", best);\n' + _TAIL)

_S4 = (_HEAD + '    int count, total = 0, best = 0;\n    if (scanf("%d", &count) != 1)\n        return 1;\n'
       '    for (int i = 0; i < count; i++) {\n        int score;\n        if (scanf("%d", &score) != 1)\n'
       '            return 1;\n        total = total + score;\n        if (score > best)\n            best = score;\n'
       '        if (score >= 60)\n            printf("%d pass\\n", score);\n        else\n'
       '            printf("%d fail\\n", score);\n    }\n'
       '    printf("Count: %d\\n", count);\n    printf("Total: %d\\n", total);\n'
       '    printf("Average: %d\\n", total / count);\n    printf("Best: %d\\n", best);\n' + _TAIL)

LANG = {"steps": [
    {"lesson": ["In C, scanf reads from the keyboard and stores what it finds in a variable, and %d in its first argument means a whole number.",
                "You pass the variable with & in front of it, so scanf knows where to put the number.",
                "scanf answers with how many values it managed to read, so comparing that answer with 1 tells you the input was bad.",
                "printf shows text, and %d inside it is replaced by the next whole number you give it; \\n ends the line."],
     "example": {"code": '#include <stdio.h>\n\nint main(void)\n{\n    int age;\n    if (scanf("%d", &age) != 1)\n'
                         '        return 1;\n    printf("Next year: %d\\n", age + 1);\n    return 0;\n}\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number into a variable, then show it with its label.",
               "scanf with %d reads a whole number into an int variable, and printf with %d prints one.",
               "Declare an int, give scanf the format and &___, then call printf with the label, %d, and the variable."]},
    {"lesson": ["A for loop repeats its block; the three parts in the parentheses set a counter, say when to stop, and say how to count.",
                "Inside the loop, each pass calls scanf once, so the same line reads every score.",
                "To add up, start a variable at 0 before the loop and add each score to it inside the loop.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": '#include <stdio.h>\n\nint main(void)\n{\n    int sum = 0;\n    for (int k = 1; k <= 4; k++)\n'
                         '        sum = sum + k;\n    printf("%d\\n", sum);\n    return 0;\n}\n', "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop that runs count times, and keep a running total that starts at 0.",
               "for (int i = 0; i < ___; i++) with a scanf inside, and in the loop write total = ___ + score."]},
    {"lesson": ["Dividing two ints in C gives a whole number and drops the remainder, so 7 / 2 is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs the next statement only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": '#include <stdio.h>\n\nint main(void)\n{\n    int a = 7, b = 2;\n    printf("%d\\n", a / b);\n'
                         '    if (a > b)\n        printf("a is bigger\\n");\n    return 0;\n}\n', "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide two ints to get a whole-number result, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if (___ > best) and then set best to the score."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": '#include <stdio.h>\n\nint main(void)\n{\n    int t = 15;\n    if (t >= 20)\n        printf("warm\\n");\n'
                         '    else\n        printf("cool\\n");\n    return 0;\n}\n', "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if (___ >= 60) and print the score and the word pass, then else and print the score and the word fail."]},
]}
