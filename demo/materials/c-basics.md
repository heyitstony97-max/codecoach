# C Basics: an eight-unit outline

A beginner outline for learning C on Linux with gcc. Each unit lists what you should be able to do at the end.

## Unit 1: Your first program

- Write a program with a `main` function that prints a line with `printf`.
- Compile with `gcc -Wall -o hello hello.c` and run it with `./hello`.
- Read a compiler error and find the line it points to.
- Use `\n` to end a line of output.

## Unit 2: Variables and types

- Declare and initialize `int`, `double` and `char` variables.
- Print values with the `%d`, `%f` and `%c` format specifiers.
- Do integer and floating-point arithmetic, and predict the result of integer division and `%`.
- Read a number from standard input with `scanf`.

## Unit 3: Making decisions

- Use `if`, `else if` and `else` to choose between actions.
- Combine conditions with `&&`, `||` and `!`.
- Write a program that classifies a number as negative, zero or positive.

## Unit 4: Loops

- Write a `for` loop that counts from 1 to n.
- Write a `while` loop that runs until a condition changes.
- Sum the numbers from 1 to n, and print a multiplication table.
- Find and fix an off-by-one error in a loop.

## Unit 5: Functions

- Write a function that takes parameters and returns a value.
- Declare a function prototype before `main`.
- Split a program into small functions, such as `is_even` and `max_of_three`.
- Explain why changing a parameter inside a function does not change the caller's variable.

## Unit 6: Arrays

- Declare an array, fill it in a loop and print it.
- Find the largest value and the average of an array of integers.
- Pass an array and its length to a function.
- Reverse an array in place.

## Unit 7: Strings

- Store text in a `char` array that ends with `'\0'`.
- Find the length of a string with a loop, then with `strlen`.
- Count the vowels in a string.
- Convert a string to uppercase one character at a time with `toupper`.

## Unit 8: Pointers, a first look

- Print the address of a variable with `&` and `%p`.
- Read and change a value through a pointer with `*`.
- Write a `swap` function that takes two pointers.
- Walk through an array with a pointer instead of an index.
