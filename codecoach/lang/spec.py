"""Language specs: everything the runner needs to know about one language, as plain data.

A command is a list of template strings. Placeholders (str.format style):
    {gcc} {python3} {node} ...  real path of that tool (a key of the toolchain's `exes`)
    {src}      the learner's source file, as seen where the command runs
    {sources}  (alone in a list entry) every source file of this build: the learner's, plus a harness if any
    {out}      the output binary for this build
    {dir}      the work directory as seen where the command runs
    {mem_mb}   the memory budget of the run (the first cap for runtimes that have their own heap flag)
    {cache}    the language's build-cache dir (only compile commands; read-write, nothing else is)
    {flags}    (alone in a list entry) the optional flags of the spec, when they work on this machine

Nothing here touches the machine; discovery is `toolchain.py`, probing is `probe.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class UnknownLanguage(ValueError):
    """The id is not one of the languages CodeCoach knows."""


AS_CEILING_MB = 4096                 # contract rule 2.5: RLIMIT_AS is never above 4 GiB, never unlimited


@dataclass(frozen=True)
class Limits:
    """Resource caps for one language. RLIMIT_AS is always finite and never above 4 GiB."""

    compile_as_mb: int = 3072        # address space while compiling (today's C and Python value)
    overhead_mb: int = 0             # address space a run needs on top of its heap budget (runtime reservations)
    run_nproc: int = 96              # processes/threads a run may add on top of what the user already has
    compile_nproc: int = 160
    cpu_extra_s: int = 0             # added to the CPU-time backstop (runtimes burn CPU on several threads)
    compile_wall_s: float = 30.0




@dataclass(frozen=True)
class LangSpec:
    id: str
    name: str
    ext: str
    starter: str
    style_note: str
    comment: str
    live_run: bool                   # static hint; `status()` reports the measured value
    tier: str = "A"
    hello: str = ""                  # reads one line on stdin, prints "echo: <line>"
    source_name: str = ""            # file the learner's code is written to
    tool: str = ""                   # key of the tool that runs/compiles (for discovery)
    tools: tuple[str, ...] = ()      # every executable the language needs, by name
    compile: tuple[str, ...] | None = None    # None = interpreted
    check: tuple[str, ...] | None = None      # syntax check of an interpreted language
    run: tuple[str, ...] = ()
    optional_flags: tuple[str, ...] = ()      # added to compile when `optional_flags_work()` is true
    harness: str | None = None       # None | "link" (extra source compiled with the learner's) | "script"
    harness_ext: str = ""
    version_args: tuple[str, ...] = ("--version",)
    env: tuple[tuple[str, str], ...] = ()          # extra env for compile and run
    compile_env: tuple[tuple[str, str], ...] = ()  # extra env for compile only
    run_env: tuple[tuple[str, str], ...] = ()      # extra env for run only
    cache_name: str = ""             # non-empty: compile gets a persistent rw build cache named like this
    limits: Limits = field(default_factory=Limits)

    @property
    def compiled(self) -> bool:
        return self.compile is not None

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "ext": self.ext, "starter": self.starter,
                "style_note": self.style_note, "comment": self.comment, "live_run": self.live_run}


C_FLAGS = ("-std=c11", "-Wall", "-Wextra")
UBSAN_FLAGS = ("-fsanitize=undefined", "-fno-sanitize-recover=undefined")

_JVM_RUN = ("-Xmx{mem_mb}m", "-Xms8m", "-Xss1m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
            "-XX:CICompilerCount=1", "-XX:ReservedCodeCacheSize=48m", "-XX:CompressedClassSpaceSize=64m",
            "-XX:MaxMetaspaceSize=96m", "-XX:-UsePerfData", "-XX:-CreateCoredumpOnCrash",
            "-XX:ErrorFile=/tmp/hs_err.log", "-Djava.io.tmpdir=/tmp")
_JVM_COMPILE = ("-J-Xmx384m", "-J-XX:+UseSerialGC", "-J-XX:TieredStopAtLevel=1", "-J-XX:CICompilerCount=1",
                "-J-XX:ReservedCodeCacheSize=48m", "-J-XX:CompressedClassSpaceSize=64m",
                "-J-XX:MaxMetaspaceSize=128m", "-J-XX:-UsePerfData", "-J-XX:-CreateCoredumpOnCrash",
                "-J-XX:ErrorFile=/tmp/hs_err.log")


def _specs() -> list[LangSpec]:
    return [
        LangSpec(
            id="c", name="C", ext=".c", comment="//", live_run=True,
            starter="#include <stdio.h>\n\nint main(void)\n{\n    return 0;\n}\n",
            style_note="Plain C11, standard library only.",
            hello=('#include <stdio.h>\n#include <string.h>\n'
                   'int main(void) {\n    char b[256];\n    if (!fgets(b, sizeof b, stdin)) return 1;\n'
                   '    b[strcspn(b, "\\n")] = 0;\n    printf("echo: %s\\n", b);\n    return 0;\n}\n'),
            source_name="solution.c", tool="gcc", tools=("gcc",), version_args=("-dumpfullversion",),
            compile=("{gcc}", *C_FLAGS, "{flags}", "{sources}", "-o", "{out}", "-lm"),
            optional_flags=UBSAN_FLAGS, run=("{out}",), harness="link", harness_ext=".c"),
        LangSpec(
            id="cpp", name="C++", ext=".cpp", comment="//", live_run=True,
            starter="#include <iostream>\n\nint main()\n{\n    return 0;\n}\n",
            style_note="Plain C++17, standard library only.",
            hello=('#include <iostream>\n#include <string>\n'
                   'int main() {\n    std::string line;\n    std::getline(std::cin, line);\n'
                   '    std::cout << "echo: " << line << "\\n";\n    return 0;\n}\n'),
            source_name="solution.cpp", tool="g++", tools=("g++",), version_args=("-dumpfullversion",),
            compile=("{g++}", "-std=c++17", "-Wall", "-Wextra", "{sources}", "-o", "{out}"),
            run=("{out}",)),
        LangSpec(
            id="python", name="Python", ext=".py", comment="#", live_run=True,
            starter="# Your program starts here\n",
            style_note="Python 3, standard library only.",
            hello='import sys\nline = sys.stdin.readline().rstrip("\\n")\nprint("echo: " + line)\n',
            source_name="solution.py", tool="python3", tools=("python3",),
            check=("{python3}", "-B", "-c",
                   "import sys;compile(open(sys.argv[1],encoding='utf-8').read(),sys.argv[1],'exec')", "{src}"),
            run=("{python3}", "-B", "{src}"), harness="script", harness_ext=".py"),
        LangSpec(
            id="javascript", name="JavaScript", ext=".js", comment="//", live_run=True,
            starter="// Your program starts here\n",
            style_note="Plain JavaScript for Node.js. One file. Read input with require('fs').readFileSync(0, 'utf8').",
            hello=("const lines = require('fs').readFileSync(0, 'utf8').split('\\n');\n"
                   "console.log('echo: ' + lines[0]);\n"),
            source_name="solution.js", tool="node", tools=("node",),
            check=("{node}", "--check", "{src}"),
            run=("{node}", "--max-old-space-size={mem_mb}", "{src}"),
            limits=Limits(overhead_mb=2800, cpu_extra_s=2)),
        LangSpec(
            id="java", name="Java", ext=".java", comment="//", live_run=False,
            starter="public class Main {\n    public static void main(String[] args) {\n    }\n}\n",
            style_note="One file. public class Main with a main method. Read input with a Scanner on System.in.",
            hello=('import java.util.Scanner;\npublic class Main {\n    public static void main(String[] args) {\n'
                   '        Scanner s = new Scanner(System.in);\n        String line = s.hasNextLine() ? s.nextLine() : "";\n'
                   '        System.out.println("echo: " + line);\n    }\n}\n'),
            source_name="Main.java", tool="java", tools=("java", "javac"),
            compile=("{javac}", *_JVM_COMPILE, "-encoding", "UTF-8", "-d", "{dir}", "{src}"),
            run=("{java}", *_JVM_RUN, "-cp", "{dir}", "Main"),
            limits=Limits(compile_as_mb=4096, overhead_mb=1800, cpu_extra_s=4, compile_nproc=200)),
        LangSpec(
            id="go", name="Go", ext=".go", comment="//", live_run=False,
            starter="package main\n\nfunc main() {\n}\n",
            style_note="One file, package main, standard library only. Read input with bufio and os.Stdin.",
            hello=('package main\n\nimport (\n\t"bufio"\n\t"fmt"\n\t"os"\n\t"strings"\n)\n\n'
                   'func main() {\n\tr := bufio.NewReader(os.Stdin)\n\tline, _ := r.ReadString(\'\\n\')\n'
                   '\tfmt.Println("echo: " + strings.TrimRight(line, "\\n"))\n}\n'),
            source_name="solution.go", tool="go", tools=("go",), version_args=("version",),
            compile=("{go}", "build", "-o", "{out}", "{src}"), run=("{out}",),
            compile_env=(("GOCACHE", "{cache}"), ("GOPATH", "/tmp/gopath"), ("GOFLAGS", "-p=4"),
                         ("GOMAXPROCS", "4"), ("GOTELEMETRY", "off")),
            env=(("CGO_ENABLED", "0"), ("GOTOOLCHAIN", "local"), ("GOPROXY", "off")),
            run_env=(("GOMAXPROCS", "4"), ("GOMEMLIMIT", "{mem_mb}MiB")),
            cache_name="go",
            limits=Limits(compile_as_mb=4096, overhead_mb=1800, cpu_extra_s=2, compile_nproc=240,
                          run_nproc=160)),
        LangSpec(
            id="rust", name="Rust", ext=".rs", comment="//", live_run=False,
            starter="fn main() {\n}\n",
            style_note="One file, fn main, standard library only (no crates). Read input with std::io::stdin().",
            hello=('use std::io::{self, BufRead};\nfn main() {\n    let mut line = String::new();\n'
                   '    io::stdin().lock().read_line(&mut line).unwrap();\n'
                   '    println!("echo: {}", line.trim_end_matches(\'\\n\'));\n}\n'),
            source_name="solution.rs", tool="rustc", tools=("rustc",),
            compile=("{rustc}", "--edition", "2021", "-C", "opt-level=1", "-C", "debuginfo=0",
                     "-C", "codegen-units=4", "{src}", "-o", "{out}"),
            run=("{out}",),
            limits=Limits(compile_as_mb=4096, compile_nproc=240, cpu_extra_s=2)),
        LangSpec(
            id="ruby", name="Ruby", ext=".rb", comment="#", live_run=True,
            starter="# Your program starts here\n",
            style_note="Plain Ruby, standard library only. Read input with gets or $stdin.",
            hello='line = $stdin.gets.to_s.chomp\nputs "echo: #{line}"\n',
            source_name="solution.rb", tool="ruby", tools=("ruby",),
            check=("{ruby}", "-c", "{src}"), run=("{ruby}", "{src}")),
        LangSpec(
            id="bash", name="Bash", ext=".sh", comment="#", live_run=True,
            starter="#!/usr/bin/env bash\n# Your program starts here\n",
            style_note="Bash 5 script, builtins and core utilities only. Read input with read.",
            hello='read -r line\necho "echo: $line"\n',
            source_name="solution.sh", tool="bash", tools=("bash",),
            check=("{bash}", "-n", "{src}"), run=("{bash}", "{src}")),
        LangSpec(
            id="php", name="PHP", ext=".php", comment="//", live_run=True, tier="B",
            starter="<?php\n\n// Your program starts here\n",
            style_note=("PHP 8 command-line script that starts with <?php. One file, standard library only. "
                        "Read a line of input with fgets(STDIN)."),
            hello=('<?php\n$line = fgets(STDIN);\n$line = $line === false ? "" : rtrim($line, "\\n");\n'
                   'echo "echo: " . $line . "\\n";\n'),
            source_name="solution.php", tool="php", tools=("php",),
            check=("{php}", "-n", "-l", "{src}"),
            run=("{php}", "-n", "-d", "display_errors=stderr", "-d", "memory_limit={mem_mb}M", "{src}")),
        LangSpec(
            id="lua", name="Lua", ext=".lua", comment="--", live_run=True, tier="B",
            starter="-- Your program starts here\n",
            style_note="Plain Lua 5.3, standard library only. One file. Read a line of input with io.read(\"l\").",
            hello='local line = io.read("l") or ""\nprint("echo: " .. line)\n',
            source_name="solution.lua", tool="lua", tools=("lua", "luac"), version_args=("-v",),
            check=("{luac}", "-p", "{src}"), run=("{lua}", "{src}")),
        LangSpec(
            id="perl", name="Perl", ext=".pl", comment="#", live_run=True, tier="B",
            starter="use strict;\nuse warnings;\n\n# Your program starts here\n",
            style_note=("Perl 5 with use strict and use warnings, core modules only. One file. "
                        "Read a line of input with my $line = <STDIN>; and chomp it."),
            hello='use strict;\nuse warnings;\nmy $line = <STDIN>;\n$line = "" unless defined $line;\nchomp $line;\nprint "echo: $line\\n";\n',
            source_name="solution.pl", tool="perl", tools=("perl",),
            check=("{perl}", "-c", "{src}"), run=("{perl}", "{src}")),
        LangSpec(
            id="haskell", name="Haskell", ext=".hs", comment="--", live_run=True, tier="B",
            starter="main :: IO ()\nmain = return ()\n",
            style_note=("One file, module Main with a main :: IO (), base library only (no packages). "
                        "Read a line of input with getLine."),
            hello='main :: IO ()\nmain = do\n  line <- getLine\n  putStrLn ("echo: " ++ line)\n',
            source_name="solution.hs", tool="ghc", tools=("ghc",), version_args=("--numeric-version",),
            compile=("{ghc}", "-O0", "-v0", "-package-env", "-", "-outputdir", "{dir}/o", "-rtsopts",
                     "{src}", "-o", "{out}"),
            run=("{out}", "+RTS", "-M{mem_mb}m", "-RTS"),
            limits=Limits(compile_as_mb=4096, compile_nproc=240, cpu_extra_s=2, overhead_mb=1000)),
        LangSpec(
            id="zig", name="Zig", ext=".zig", comment="//", live_run=True, tier="B",
            starter="const std = @import(\"std\");\n\npub fn main() void {\n}\n",
            style_note=("One file, standard library only. Zig 0.17: declare pub fn main(init: std.process.Init) !void, "
                        "read a line with std.Io.File.stdin().reader(init.io, &buf) and its interface.takeDelimiter(10)."),
            hello=('const std = @import("std");\n\npub fn main(init: std.process.Init) !void {\n'
                   '    const io = init.io;\n    var in_buf: [4096]u8 = undefined;\n'
                   '    var reader = std.Io.File.stdin().reader(io, &in_buf);\n'
                   '    const line = reader.interface.takeDelimiter(0x0a) catch null orelse "";\n'
                   '    var out_buf: [4096]u8 = undefined;\n'
                   '    var writer = std.Io.File.stdout().writer(io, &out_buf);\n'
                   '    try writer.interface.print("echo: {s}\\n", .{line});\n'
                   '    try writer.interface.flush();\n}\n'),
            source_name="solution.zig", tool="zig", tools=("zig",), version_args=("version",),
            compile=("{zig}", "build-exe", "{src}", "-O", "Debug", "-femit-bin={out}"), run=("{out}",),
            compile_env=(("ZIG_GLOBAL_CACHE_DIR", "{cache}"), ("ZIG_LOCAL_CACHE_DIR", "{cache}/local")),
            cache_name="zig",
            limits=Limits(compile_as_mb=4096, compile_nproc=240, cpu_extra_s=2)),
        LangSpec(
            id="lisp", name="Lisp", ext=".lisp", comment=";;", live_run=True, tier="B",
            starter=";; Your program starts here\n",
            style_note=("Common Lisp run as a script by SBCL, standard library only. One file of top-level forms. "
                        "Read a line of input with (read-line *standard-input* nil \"\")."),
            hello=('(let ((line (read-line *standard-input* nil "")))\n'
                   '  (format t "echo: ~a~%" line))\n'),
            source_name="solution.lisp", tool="sbcl", tools=("sbcl",),
            run=("{sbcl}", "--dynamic-space-size", "{mem_mb}MB", "--control-stack-size", "2MB", "--script", "{src}"),
            limits=Limits(overhead_mb=1200, cpu_extra_s=1)),
        LangSpec(
            id="elixir", name="Elixir", ext=".exs", comment="#", live_run=False, tier="B",
            starter="# Your program starts here\n",
            style_note=("Elixir script, standard library only. One file of top-level code, no Mix project. "
                        "Read a line of input with IO.gets(\"\")."),
            hello=('case IO.gets("") do\n  :eof -> IO.puts("echo: ")\n'
                   '  line -> IO.puts("echo: " <> String.trim_trailing(line, "\\n"))\nend\n'),
            source_name="solution.exs", tool="elixir", tools=("elixir",), version_args=("-e", "IO.puts(System.version())"),
            check=("{elixir}", "-e", "Code.string_to_quoted!(File.read!(hd(System.argv())))", "{src}"),
            run=("{elixir}", "{src}"),
            env=(("ERL_FLAGS", "+S 2:2 +A 2 +SDio 2 +sbwt none +sbwtdcpu none +sbwtdio none +JMsingle true"),
                 ("ELIXIR_ERL_OPTIONS", "+S 2:2 +A 2 +SDio 2 +sbwt none +sbwtdcpu none +sbwtdio none +JMsingle true"),
                 ("ERL_CRASH_DUMP", "/dev/null"), ("ERL_CRASH_DUMP_SECONDS", "0")),
            limits=Limits(overhead_mb=2800, cpu_extra_s=3)),
    ]


_SPECS: dict[str, LangSpec] = {s.id: s for s in _specs()}
TIER_A = ("c", "cpp", "python", "javascript", "java", "go", "rust", "ruby", "bash")
TIER_B = ("typescript", "php", "lua", "perl", "haskell", "zig", "elixir", "lisp")


def all_ids() -> list[str]:
    """Every id that has a spec, in display order."""
    return [i for i in TIER_A + TIER_B if i in _SPECS]


def spec(lang_id: str) -> LangSpec:
    try:
        return _SPECS[lang_id]
    except (KeyError, TypeError):
        raise UnknownLanguage(f"Unknown language: {lang_id!r}") from None
