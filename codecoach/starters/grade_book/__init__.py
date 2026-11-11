"""Grade book: four steps, plain input and output, the same tests in every language."""

_THREE = "3\n80\n90\n70\n"

SHARED = {
    "id": "grade-book",
    "title": "Grade book",
    "pitch": "Type some scores and get the count, total, average, best score and a pass or fail line for each.",
    "summary": "A small grade book that reads a list of scores and reports on them.",
    "steps": [
        {"title": "Read a score",
         "goal": "The program reads one whole number and prints it with a label.",
         "concept": "reading input",
         "task": 'Read one whole number (a score from 0 to 100) from the input and print it as "Score: N", '
                 "where N is the score.",
         "visible": [("eighty-five", "85\n", "Score: 85\n"), ("zero", "0\n", "Score: 0\n")],
         "hidden": [("seven", "7\n", "Score: 7\n"), ("one hundred", "100\n", "Score: 100\n")]},
        {"title": "Add up the scores",
         "goal": "The program reads a count and then that many scores, and reports how many there were and their total.",
         "concept": "loops",
         "task": "The first number is how many scores follow, and then each score is on its own line. "
                 'Print "Count: N" with the number of scores, then "Total: N" with the sum of all the scores.',
         "visible": [("three scores", _THREE, "Count: 3\nTotal: 240\n"),
                     ("one score", "1\n55\n", "Count: 1\nTotal: 55\n")],
         "hidden": [("four scores", "4\n10\n20\n30\n40\n", "Count: 4\nTotal: 100\n"),
                    ("two high scores", "2\n100\n99\n", "Count: 2\nTotal: 199\n")]},
        {"title": "Average and best",
         "goal": "The program also reports the average score and the highest score.",
         "concept": "arithmetic and comparing",
         "task": 'After the count and the total, print "Average: N" with the total divided by the count, using '
                 'whole-number division (drop the remainder), and then "Best: N" with the highest score.',
         "visible": [("three scores", _THREE, "Count: 3\nTotal: 240\nAverage: 80\nBest: 90\n"),
                     ("a remainder", "2\n51\n60\n", "Count: 2\nTotal: 111\nAverage: 55\nBest: 60\n")],
         "hidden": [("four scores", "4\n10\n20\n30\n45\n", "Count: 4\nTotal: 105\nAverage: 26\nBest: 45\n"),
                    ("one score", "1\n77\n", "Count: 1\nTotal: 77\nAverage: 77\nBest: 77\n")]},
        {"title": "Pass or fail",
         "goal": "The program also tells you, for each score, whether it passes.",
         "concept": "if and else",
         "task": 'Before the four summary lines, print one line for each score, in the order they were typed: '
                 '"N pass" when the score is 60 or more, otherwise "N fail", where N is the score. '
                 "Then print the same summary lines as before.",
         "visible": [("a mix", "3\n85\n42\n60\n",
                      "85 pass\n42 fail\n60 pass\nCount: 3\nTotal: 187\nAverage: 62\nBest: 85\n"),
                     ("one fail", "1\n59\n", "59 fail\nCount: 1\nTotal: 59\nAverage: 59\nBest: 59\n")],
         "hidden": [("edges", "4\n100\n0\n61\n59\n",
                     "100 pass\n0 fail\n61 pass\n59 fail\nCount: 4\nTotal: 220\nAverage: 55\nBest: 100\n"),
                    ("all pass", "2\n60\n75\n", "60 pass\n75 pass\nCount: 2\nTotal: 135\nAverage: 67\nBest: 75\n")]},
    ],
}
