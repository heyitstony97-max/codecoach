"""Grade book in C++."""

_HEAD = "#include <iostream>\n\nint main()\n{\n"
_TAIL = "    return 0;\n}\n"


def _p(body):
    return _HEAD + "".join("    " + ln + "\n" for ln in body) + _TAIL


_S1 = _p(["int score;", "std::cin >> score;", 'std::cout << "Score: " << score << "\\n";'])

_S2 = _p(["int count;", "int total = 0;", "std::cin >> count;", "for (int i = 0; i < count; i++) {",
          "    int score;", "    std::cin >> score;", "    total = total + score;", "}",
          'std::cout << "Count: " << count << "\\n";', 'std::cout << "Total: " << total << "\\n";'])

_S3 = _p(["int count;", "int total = 0;", "int best = 0;", "std::cin >> count;", "for (int i = 0; i < count; i++) {",
          "    int score;", "    std::cin >> score;", "    total = total + score;", "    if (score > best) {",
          "        best = score;", "    }", "}",
          'std::cout << "Count: " << count << "\\n";', 'std::cout << "Total: " << total << "\\n";',
          'std::cout << "Average: " << total / count << "\\n";', 'std::cout << "Best: " << best << "\\n";'])

_S4 = _p(["int count;", "int total = 0;", "int best = 0;", "std::cin >> count;", "for (int i = 0; i < count; i++) {",
          "    int score;", "    std::cin >> score;", "    total = total + score;", "    if (score > best) {",
          "        best = score;", "    }", "    if (score >= 60) {", '        std::cout << score << " pass\\n";',
          "    } else {", '        std::cout << score << " fail\\n";', "    }", "}",
          'std::cout << "Count: " << count << "\\n";', 'std::cout << "Total: " << total << "\\n";',
          'std::cout << "Average: " << total / count << "\\n";', 'std::cout << "Best: " << best << "\\n";'])

LANG = {"steps": [
    {"lesson": ["In C++, std::cin >> score reads the next whole number from the keyboard and stores it in the variable score.",
                "The variable has to be declared first, for example int score; so the program knows it holds a whole number.",
                "std::cout << sends text and values to the screen, and you can chain several pieces with more << signs.",
                "The text \\n ends a line."],
     "example": {"code": '#include <iostream>\n\nint main()\n{\n    int age;\n    std::cin >> age;\n'
                         '    std::cout << "Next year: " << age + 1 << "\\n";\n    return 0;\n}\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number into a variable, then show it with its label.",
               "Declare an int, read it with std::cin >>, and print with std::cout << using the label then the variable.",
               "Write int score; then std::cin >> ___; then std::cout << the label << score << a newline."]},
    {"lesson": ["A for loop repeats a block; the three parts in the parentheses set a counter, say when to stop, and say how to count.",
                "Inside the loop, each pass reads one number with std::cin, so the same line reads every score.",
                "To add up, start a variable at 0 before the loop and add each score to it inside the loop.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": "#include <iostream>\n\nint main()\n{\n    int sum = 0;\n    for (int k = 1; k <= 4; k++) {\n"
                         '        sum = sum + k;\n    }\n    std::cout << sum << "\\n";\n    return 0;\n}\n', "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop that runs count times, and keep a running total that starts at 0.",
               "for (int i = 0; i < ___; i++) with a std::cin inside, and in the loop write total = ___ + score."]},
    {"lesson": ["Dividing two ints in C++ gives a whole number and drops the remainder, so 7 / 2 is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs its block only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": '#include <iostream>\n\nint main()\n{\n    int a = 7;\n    int b = 2;\n    std::cout << a / b << "\\n";\n'
                         '    if (a > b) {\n        std::cout << "a is bigger\\n";\n    }\n    return 0;\n}\n', "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide two ints to get a whole-number result, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if (___ > best) { and set best to the score inside it."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": '#include <iostream>\n\nint main()\n{\n    int t = 15;\n    if (t >= 20) {\n        std::cout << "warm\\n";\n'
                         '    } else {\n        std::cout << "cool\\n";\n    }\n    return 0;\n}\n', "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if (___ >= 60) { and print the score and the word pass, then } else { and print the score and the word fail."]},
]}
