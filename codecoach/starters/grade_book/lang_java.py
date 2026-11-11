"""Grade book in Java."""

_HEAD = "import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner in = new Scanner(System.in);\n"
_TAIL = "    }\n}\n"


def _p(body):
    return _HEAD + "".join("        " + ln + "\n" if ln else "\n" for ln in body) + _TAIL


_S1 = _p(["int score = in.nextInt();", 'System.out.println("Score: " + score);'])

_S2 = _p(["int count = in.nextInt();", "int total = 0;", "for (int i = 0; i < count; i++) {",
          "    int score = in.nextInt();", "    total = total + score;", "}",
          'System.out.println("Count: " + count);', 'System.out.println("Total: " + total);'])

_S3 = _p(["int count = in.nextInt();", "int total = 0;", "int best = 0;", "for (int i = 0; i < count; i++) {",
          "    int score = in.nextInt();", "    total = total + score;", "    if (score > best) {",
          "        best = score;", "    }", "}",
          'System.out.println("Count: " + count);', 'System.out.println("Total: " + total);',
          'System.out.println("Average: " + total / count);', 'System.out.println("Best: " + best);'])

_S4 = _p(["int count = in.nextInt();", "int total = 0;", "int best = 0;", "for (int i = 0; i < count; i++) {",
          "    int score = in.nextInt();", "    total = total + score;", "    if (score > best) {",
          "        best = score;", "    }", "    if (score >= 60) {", '        System.out.println(score + " pass");',
          "    } else {", '        System.out.println(score + " fail");', "    }", "}",
          'System.out.println("Count: " + count);', 'System.out.println("Total: " + total);',
          'System.out.println("Average: " + total / count);', 'System.out.println("Best: " + best);'])

LANG = {"steps": [
    {"lesson": ["In Java, a Scanner reads from the keyboard, and the line new Scanner(System.in) at the top of main creates one.",
                "Its method nextInt() reads the next whole number, so you can put the answer straight into an int variable.",
                "The import line at the top is what lets the program use Scanner, so leave it where it is.",
                "System.out.println prints text followed by a new line, and + joins text and a number together."],
     "example": {"code": "import java.util.Scanner;\n\npublic class Main {\n    public static void main(String[] args) {\n"
                         "        Scanner keys = new Scanner(System.in);\n        int age = keys.nextInt();\n"
                         '        System.out.println("Next year: " + (age + 1));\n    }\n}\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number into a variable, then show it with its label.",
               "Call nextInt() on the Scanner to read a whole number, and println to show text joined with it.",
               "Write int score = in.___(); and then System.out.println with the label, a +, and the variable."]},
    {"lesson": ["A for loop repeats a block; the three parts in the parentheses set a counter, say when to stop, and say how to count.",
                "Inside the loop, each pass calls nextInt() once, so the same line reads every score.",
                "To add up, start a variable at 0 before the loop and add each score to it inside the loop.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": "public class Main {\n    public static void main(String[] args) {\n        int sum = 0;\n"
                         "        for (int k = 1; k <= 4; k++) {\n            sum = sum + k;\n        }\n"
                         "        System.out.println(sum);\n    }\n}\n", "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop that runs count times, and keep a running total that starts at 0.",
               "for (int i = 0; i < ___; i++) with a nextInt() inside, and in the loop write total = ___ + score."]},
    {"lesson": ["Dividing two ints in Java gives a whole number and drops the remainder, so 7 / 2 is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs its block only when its condition is true, and > means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": "public class Main {\n    public static void main(String[] args) {\n        int a = 7;\n        int b = 2;\n"
                         "        System.out.println(a / b);\n        if (a > b) {\n            System.out.println(\"a is bigger\");\n"
                         "        }\n    }\n}\n", "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide two ints to get a whole-number result, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if (___ > best) { and set best to the score inside it."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": "public class Main {\n    public static void main(String[] args) {\n        int t = 15;\n        if (t >= 20) {\n"
                         '            System.out.println("warm");\n        } else {\n            System.out.println("cool");\n        }\n    }\n}\n',
                 "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Write if (___ >= 60) { and print the score and the word pass, then } else { and print the score and the word fail."]},
]}
