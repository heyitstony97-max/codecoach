"""Grade book in Bash."""

_H = "#!/usr/bin/env bash\n"

_S1 = _H + 'read score\necho "Score: $score"\n'

_S2 = (_H + 'read count\ntotal=0\nfor ((i = 0; i < count; i++)); do\n  read score\n  total=$((total + score))\ndone\n'
       'echo "Count: $count"\necho "Total: $total"\n')

_S3 = (_H + 'read count\ntotal=0\nbest=0\nfor ((i = 0; i < count; i++)); do\n  read score\n  total=$((total + score))\n'
       '  if [ "$score" -gt "$best" ]; then\n    best=$score\n  fi\ndone\n'
       'echo "Count: $count"\necho "Total: $total"\necho "Average: $((total / count))"\necho "Best: $best"\n')

_S4 = (_H + 'read count\ntotal=0\nbest=0\nfor ((i = 0; i < count; i++)); do\n  read score\n  total=$((total + score))\n'
       '  if [ "$score" -gt "$best" ]; then\n    best=$score\n  fi\n'
       '  if [ "$score" -ge 60 ]; then\n    echo "$score pass"\n  else\n    echo "$score fail"\n  fi\ndone\n'
       'echo "Count: $count"\necho "Total: $total"\necho "Average: $((total / count))"\necho "Best: $best"\n')

LANG = {"steps": [
    {"lesson": ["In Bash, the read command takes one line of input and stores it in a variable whose name you give it.",
                "To use the value later, write a dollar sign in front of the name, as in $score.",
                "Bash keeps every value as text, and it only treats it as a number when you do arithmetic on it.",
                "echo prints a line, and a double-quoted string can hold both words and variables."],
     "example": {"code": '#!/usr/bin/env bash\nread age\necho "Next year: $((age + 1))"\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number into a variable, then show it with its label.",
               "Use read to fill a variable, and echo to print text with the variable inside double quotes.",
               "Write read score and then echo with the label and $___ inside double quotes."]},
    {"lesson": ["A for loop repeats its block; the double-parenthesis form sets a counter, says when to stop, and says how to count.",
                "Inside the loop, each pass calls read once, so the same line reads every score.",
                "To add up, start a variable at 0 before the loop and use $(( )) to calculate a new value from the old one.",
                "Print the count and the total after the loop, because they are only final once every score is read."],
     "example": {"code": '#!/usr/bin/env bash\nsum=0\nfor ((k = 1; k <= 4; k++)); do\n  sum=$((sum + k))\ndone\necho "$sum"\n', "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use a for loop that runs count times, and keep a running total that starts at 0.",
               "for ((i = 0; i < ___; i++)); do with a read inside, and in the loop write total=$((total + ___))."]},
    {"lesson": ["Division inside $(( )) gives a whole number and drops the remainder, so $((7 / 2)) is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement with [ ... ] runs its block only when the test is true, and -gt means greater than.",
                "The summary lines go after the loop, because the total and the best are only final once every score is read."],
     "example": {"code": '#!/usr/bin/env bash\na=7\nb=2\necho "$((a / b))"\nif [ "$a" -gt "$b" ]; then\n  echo "a is bigger"\nfi\n', "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide inside $(( )) to get a whole-number result, and put an if inside the loop to update a best-so-far variable.",
               "Start best at 0 before the loop. In the loop write if [ \"$score\" -gt \"$___\" ]; then and set best to the score, then fi."]},
    {"lesson": ["An if statement can have an else part, which runs when the test is not true.",
                "The test -ge means greater than or equal to, so [ \"$score\" -ge 60 ] is true when the score is 60 or more.",
                "The pass or fail line belongs inside the loop, so it is printed once for every score as it is read.",
                "The summary lines stay after the loop, so they come last, as the task asks."],
     "example": {"code": '#!/usr/bin/env bash\nt=15\nif [ "$t" -ge 20 ]; then\n  echo "warm"\nelse\n  echo "cool"\nfi\n', "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the loop, use if with else to choose between printing pass and printing fail for that score.",
               "Test the score with -ge 60 and echo the score and the word pass, then else and echo the score and the word fail, then fi."]},
]}
