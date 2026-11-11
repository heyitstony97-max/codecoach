"""Grade book in Ruby."""

_S1 = "score = gets.to_i\nputs \"Score: #{score}\"\n"

_S2 = ("count = gets.to_i\ntotal = 0\ncount.times do\n  score = gets.to_i\n  total = total + score\nend\n"
       'puts "Count: #{count}"\nputs "Total: #{total}"\n')

_S3 = ("count = gets.to_i\ntotal = 0\nbest = 0\ncount.times do\n  score = gets.to_i\n  total = total + score\n"
       "  if score > best\n    best = score\n  end\nend\n"
       'puts "Count: #{count}"\nputs "Total: #{total}"\nputs "Average: #{total / count}"\nputs "Best: #{best}"\n')

_S4 = ("count = gets.to_i\ntotal = 0\nbest = 0\ncount.times do\n  score = gets.to_i\n  total = total + score\n"
       "  if score > best\n    best = score\n  end\n  if score >= 60\n    puts \"#{score} pass\"\n  else\n"
       "    puts \"#{score} fail\"\n  end\nend\n"
       'puts "Count: #{count}"\nputs "Total: #{total}"\nputs "Average: #{total / count}"\nputs "Best: #{best}"\n')

LANG = {"steps": [
    {"lesson": ["In Ruby, gets reads one line of input as text, and to_i turns that text into a whole number.",
                "A line is text even when it holds digits, so without to_i you could not calculate with it.",
                "The name on the left of = is a variable that remembers the value.",
                "puts prints a line, and inside double quotes, #{score} is replaced by the value of the variable score."],
     "example": {"code": 'age = gets.to_i\nputs "Next year: #{age + 1}"\n', "stdin": "12\n"},
     "solution": _S1,
     "hints": ["Two jobs: get the number into a variable, then show it with its label.",
               "Read a line with gets, turn it into a number with to_i, and print with puts.",
               "Write score = gets.___ and then puts with the label and #{} around the variable inside double quotes."]},
    {"lesson": ["The times method repeats a block of lines, once for each count, and the block is closed by the word end.",
                "Inside the block, each pass calls gets once, so the same line reads every score.",
                "To add up, start a variable at 0 before the block and add each score to it inside the block.",
                "Print the count and the total after the block, because they are only final once every score is read."],
     "example": {"code": 'sum = 0\n4.times do\n  sum = sum + 5\nend\nputs sum\n', "stdin": ""},
     "solution": _S2,
     "hints": ["The first number only tells you how many more numbers to read.",
               "Use times to repeat once per score, and keep a running total that starts at 0.",
               "Call times on the count, read with gets inside the block, and in the block write total = ___ + score."]},
    {"lesson": ["Dividing two whole numbers in Ruby gives a whole number and drops the remainder, so 7 / 2 is 3.",
                "To find the highest value, keep a variable called best that starts at 0 and compare every new score against it.",
                "An if statement runs its lines only when its condition is true, and > means greater than.",
                "The summary lines go after the block, because the total and the best are only final once every score is read."],
     "example": {"code": 'a = 7\nb = 2\nputs a / b\nif a > b\n  puts "a is bigger"\nend\n', "stdin": ""},
     "solution": _S3,
     "hints": ["There are two new numbers: one comes from dividing, the other from comparing every score.",
               "Divide two whole numbers to get a whole-number result, and put an if inside the block to update a best-so-far variable.",
               "Start best at 0 before the block. In the block write if ___ > best and set best to the score on the next line, then end."]},
    {"lesson": ["An if statement can have an else part, which runs when the condition is not true.",
                "A comparison like score >= 60 is true when the score is 60 or more.",
                "The pass or fail line belongs inside the block, so it is printed once for every score as it is read.",
                "The summary lines stay after the block, so they come last, as the task asks."],
     "example": {"code": 't = 15\nif t >= 20\n  puts "warm"\nelse\n  puts "cool"\nend\n', "stdin": ""},
     "solution": _S4,
     "hints": ["Each score needs its own verdict, so decide where in the program a single score is in hand.",
               "Inside the block, use if with else to choose between printing pass and printing fail for that score.",
               "Write if ___ >= 60 and print the score and the word pass, then else and print the score and the word fail, then end."]},
]}
