<div align="center">

# Sym ⚡

### *The polyglot host. One file, ten ecosystems — none of them know the others exist.*

<br>

<p>
  <img src="https://img.shields.io/badge/Built_in-Class_10-22c55e?style=for-the-badge&labelColor=000" />
  <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white&labelColor=000" />
  <img src="https://img.shields.io/badge/C-A8B9CC?style=for-the-badge&logo=c&logoColor=black&labelColor=000" />
  <img src="https://img.shields.io/badge/lines_of_code-~4k-7c5cff?style=for-the-badge&labelColor=000" />
  <img src="https://img.shields.io/badge/license-MIT-93c5fd?style=for-the-badge&labelColor=000" />
</p>

<br>

</div>

## What this is

Sym is a programming language I designed and built. Files use the `.sym` extension. The compiler is written in Python, lives in this repo, and produces two outputs:

1. **Python code** for any function that touches the Python ecosystem (numpy, pandas, requests, anything `pip`-installable)
2. **C shared libraries** for any function that's *pure* (only does math, no Python deps)

The compiler analyzes every function for purity, then routes the pure ones through C codegen for native speed while keeping Python-dependent ones as Python. They talk via `ctypes`. The user just runs `sym run file.sym` and never thinks about it.

The language has three syntax modes (Normal, Compact, Symbol) that all parse to the same AST. Whichever style you find readable is the one you write. Performance and behavior are identical.

## THE MATRIX

Every language below can **consume** every library ecosystem below. 80/80 cells, tested:

```
              java     js  python    php   ruby      r   perl      c
  sym           ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  python        ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  node          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  ruby          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  php           ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  perl          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  r             ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  java          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  go            ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  rust          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
```

Rows are consumers, columns are providers. A Ruby script using numpy. A Go
binary holding a live Java object. R calling PHP. **Ten consumers, eight
provider ecosystems, one hub, and zero direct language-to-language
connections** — that's why this is 18 small programs instead of 80 bridges.
Reproduce it yourself: `python3 tests/test_matrix.py`

And `showcase/run_all.sh` runs the same real program in **ten languages** —
each one borrowing Python's statistics, R's `sd`, Java's UUID, PHP's
formatting, and C's libm. A Rust binary importing four interpreted
ecosystems. An R script holding a Java object. Ten authors, one library
universe.

Clients: `clients/{python,node,ruby,php,perl,r,java,go,rust}` — each under
~300 lines, each speaking the same newline-JSON protocol to one hub.

## The headline: SymBridge

```
sym.import numpy                      # Python answers

java.import math.Calculator           # a live JVM answers
let c = Calculator()                  # a real Java object — Sym holds handle #1
let sum = c.add(5, 8)

js> {
  sym.doubled = sym.numbers.map(x => x * 2);     // real Node
}

r> {
  sym$sd <- sd(unlist(sym$doubled))              // real R
}

rust> {
  let m = sym.get("doubled").as_f64s().iter().cloned().fold(f64::MIN, f64::max);
  sym.set("max", J::num(m));                     // compiled once, cached, native
}
```

**None of these runtimes know about each other.** Python never sees Java. R never sees Rust. Every runtime is a worker process that Sym launches, owns, and shuts down — and every value that crosses a boundary becomes a **Symbol Object**, a neutral form all of them can read. One shared space (`sym`), one protocol (JSON lines, readable in an evening), ten ecosystems:

| | Import | Blocks | Live objects |
|---|---|---|---|
| Python | `sym.import numpy` (in-host, full fidelity) | `python> {}` | native |
| JavaScript | `js.import lodash` | `js> {}` | ✅ handles |
| Java | `java.import math.Calculator` | `java> {}` | ✅ handles + overload scoring |
| PHP | `php.import mylib` | `php> {}` | — |
| Ruby | `ruby.import mylib` | `ruby> {}` | — |
| R | `r.import ggplot2` | `r> {}` | — |
| Perl | `perl.import POSIX` | `perl> {}` | — |
| Go | — | `go> {}` compiled+cached | — |
| Rust | — | `rust> {}` compiled+cached | — |
| C | `c.import sqlite3` (FFI) | — | — |

And Sym isn't only a language — it's **middleware**. The same broker is available from plain Python or Node:

```python
import sym                                 # any .py file, anywhere
lst = sym.java("java.util.ArrayList")()    # construct a live Java object
lst.add("hello")
sym.block("ruby", 'sym["x"] = "from ruby"')
```

Full architecture, honest limits included: [docs/BRIDGE.md](docs/BRIDGE.md)

## The test battery

```
python3 tests/test_compiler.py   # 19  — language core
python3 tests/test_bridge.py     # 21  — every ecosystem, handles, clients
python3 tests/test_deep.py       # 39  — unicode, 2^53 longs, error recovery,
                                 #       stale handles, adversarial parsing
python3 tests/test_matrix.py     # 80  — every consumer × every provider
showcase/run_all.sh              # 10  — real programs in ten languages
```

159 checks + 10 programs. Workers must survive their own crashes; Bengali
and emoji must cross every boundary intact; a `}` inside a string must not
break the parser (it did — the deep suite caught it before you could).

## Quick start

```bash
./install.sh                     # detects your runtimes, installs to ~/.sym
cd ~/.sym/examples
sym run polyglot_ultimate.sym    # eight ecosystems, one file
python3 ../tests/test_bridge.py  # the suite that keeps it honest
```

## Why I built this

I'd been wanting to build a real programming language for years. My first attempt was [Pytson 1.0](https://github.com/RicKanjilal/Pytson1.0), in Class 5, following David Callanan's interpreter tutorial on YouTube. That was tutorial-following work and produced a basic interpreter.

Sym is the version of that idea built five years later, with the questions I actually wanted to answer:

- *What if writing Python could feel less verbose without being unreadable?* → three syntax modes that all parse to the same code. Pick your style.
- *What if a high-level language could give native speed without making the user think about it?* → automatic purity analysis routes pure functions to C, everything else stays Python.
- *What if importing libraries from the Python ecosystem just worked, without a separate package manager?* → `import py.numpy` triggers an automatic `pip install` if numpy isn't there.

Every design decision in Sym traces back to one of those three questions. They're the questions I started with. Whether they make sense as a real product is a different conversation.

## How this was built

I want to be transparent about my process because it matters for understanding what this project is.

**I drove the design.** The three-syntax-mode architecture, the hybrid Python/C compilation idea, purity analysis as the gating mechanism for C codegen, the `py.` prefix for auto-install Python imports, the AST that all three modes parse to. Every architectural decision came from me, often after multiple iterations. I read about how real compilers work (LLVM tutorials, Crafting Interpreters, the Mojo design docs) and made choices about which patterns fit my goals.

**Most of the implementation typing was done by Claude (Anthropic's coding assistant).** I worked with it across multiple sessions, specifying what each module should do, reviewing the output, requesting changes. The lexer, the recursive-descent parser, the two codegens, the purity analyzer's actual implementation, were collaborative. I can read all of it, modify all of it, explain all of it.

This is how I work on code now. It's how a lot of professional engineers work in 2026. I'm noting it explicitly because I think it matters that you know, both for understanding the project and for understanding me as an engineer. The design is mine. The execution was collaborative. The understanding is mine.

If you fork this and ask me a question about why something works the way it does, I can answer.

## The three syntax modes

All three parse to the same abstract syntax tree. Zero performance difference. Whichever you find readable is the one you write.

### Normal mode

Looks like Python with explicit types and `fn` instead of `def`.

```sym
fn fibonacci(n: int) -> int
    if n <= 1
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
```

### Compact mode (`#compact` directive)

Single-character keywords for the people who like terse code.

```sym
#compact

f fib(n: int) -> int
    ? n <= 1
        <- n
    <- fib(n - 1) + fib(n - 2)
```

Mappings: `f`=fn, `?`=if, `??`=elif, `!`=else, `~`=for, `~~`=while, `>>`=print, `<<`=import, `<-`=return, `@`=let.

### Symbol mode (`#symbol` directive)

Real mathematical notation. For when you want your fibonacci function to look like the one in a textbook.

```sym
#symbol

ƒ fib(n:ℤ) → ℤ
    ? n ≤ 1
        ← n
    ← fib(n-1) + fib(n-2)
```

Mappings: `ƒ`=fn, `←`=return, `→`=arrow, `∀`=for, `⟳`=while, `⊂`=import, `⊕`=print, `≤`=<=, `≥`=>=, `ℤ`=int, `ℝ`=float, `𝔹`=bool, `𝕊`=str. Inherits compact-mode shortcuts.

## The hybrid compilation idea

This is the part of the project I find most interesting.

Most languages force you to pick: either you're fast (C, Rust, Go) or you have a rich ecosystem (Python, JavaScript). The crossover languages (Cython, Mojo, Codon) try to bridge this with various tradeoffs. Sym tries one specific approach: **let the compiler decide on a per-function basis.**

When you write:

```sym
fn calculate_fib(n: int) -> int
    if n <= 1
        return n
    return calculate_fib(n - 1) + calculate_fib(n - 2)

fn fetch_data(url: str)
    import py.requests as req
    return req.get(url).json()
```

The compiler analyzes both functions:

- `calculate_fib` is **pure**: integer math, no Python imports, no string manipulation, no dynamic types. Compile to C.
- `fetch_data` is **impure**: imports a Python library, returns dynamic JSON. Keep as Python.

You don't annotate this. You don't pick. The compiler walks the AST and tags each function. Pure ones become entries in a generated C file, compiled with gcc into `_sym_native.so`. Impure ones become Python functions that look up the C ones via ctypes when they need to call them.

The `.so` is cached. Re-running unchanged code is instant.

### Real benchmarks (Pop!_OS, Python 3.12, gcc 13.3.0)

| Test | What it does | Pure Python | Native C | Speedup |
|------|--------------|-------------|----------|---------|
| `fib(35)` | Naive recursive fibonacci | 1778ms | 29ms | **62x** |
| `sum_squares(1M)` | Sum of squares 1 to 1,000,000 | 166ms | <0.1ms | **>1000x** |
| `count_primes(100K)` | Count primes up to 100,000 | 228ms | 46ms | **5x** |
| **Combined** | All three in sequence | 1900ms | 33ms | **57x** |

The 62x and 1000x numbers aren't surprising. They're what you get any time you replace interpreted Python loops with compiled C. The interesting part isn't the speedup. It's that you got the speedup *without writing any C, choosing any decorator, or knowing this happened*. You ran `sym run file.sym` and the compiler made the routing decision.

## Python interop with auto-install

```sym
import py.numpy as np
import py.pandas as pd
import py.requests as req
```

The `py.` prefix tells the compiler this is a Python library. If the package is missing, the compiler tries:

1. `pip install --user`
2. `pip3 install --user`
3. `sudo apt install python3-<pkg>` (with 30+ name mappings: `cv2` → `opencv-python`, `sklearn` → `scikit-learn`, `bs4` → `beautifulsoup4`, `PIL` → `Pillow`, etc.)
4. Falls back to clear manual install instructions if all three fail

This was built because I kept running into "no module named X" errors when running other people's Python code, and wanted my language to handle that automatically.

## The test battery

```
python3 tests/test_compiler.py   # 19  — language core
python3 tests/test_bridge.py     # 21  — every ecosystem, handles, clients
python3 tests/test_deep.py       # 39  — unicode, 2^53 longs, error recovery,
                                 #       stale handles, adversarial parsing
python3 tests/test_matrix.py     # 80  — every consumer × every provider
showcase/run_all.sh              # 10  — real programs in ten languages
```

159 checks + 10 programs. Workers must survive their own crashes; Bengali
and emoji must cross every boundary intact; a `}` inside a string must not
break the parser (it did — the deep suite caught it before you could).

## Quick start

```bash
git clone https://github.com/RicKanjilal/Symbolcore.git
cd Symbolcore
./install.sh
sym run examples/hello.sym
```

Requirements:
- Python 3.8+ (required)
- gcc or clang (optional, enables C compilation)

Without gcc, Sym falls back to pure-Python execution. You lose the speed but everything still runs.

## CLI

```bash
sym run file.sym              # Run with hybrid C+Python (default)
sym run file.sym -v           # Verbose, shows what compiles to C
sym run file.sym --python     # Force pure Python (debug mode)
sym run file.sym --emit       # Show generated code

sym build -t native file.sym  # Compile to standalone binary
sym build -t c file.sym       # Emit C source
sym build -t hybrid file.sym  # Build .so + Python wrapper

sym check file.sym            # Purity analysis report
sym repl                      # Interactive REPL
```

## Project structure

```
symbolcore/
├── bin/sym.py              CLI entry point
├── compiler/
│   ├── lexer.py            Tokenizer (handles all 3 syntax modes)
│   ├── parser.py           Recursive descent parser → AST
│   ├── ast_nodes.py        AST node dataclasses
│   ├── codegen_python.py   Python transpiler
│   ├── codegen_c.py        C transpiler
│   └── hybrid.py           Purity analyzer + hybrid compiler
├── runtime/
│   └── builtins.py         Built-in functions (smart sort, math, IO)
├── editor/vscode/          VS Code extension (syntax + 30 snippets)
├── examples/               9 working example programs
├── tests/                  19 passing tests
└── docs/                   Full language reference (HTML)
```

## Examples included

- `hello.sym`. Minimal hello world
- `compact_mode.sym`. Demos compact syntax
- `python_interop.sym`. Using numpy and pandas
- `speed_test.sym`. Runs the benchmark suite
- `benchmark.sym`. The `benchmark fn` decorator
- `hybrid_demo.sym`. Pure vs impure functions side by side
- `web_scraper.sym`. Uses requests + BeautifulSoup
- `finance.sym`. 404-line finance tracker app with curses UI, file persistence, charts
- `snake.sym`. Playable Snake game in symbol mode

## Honest assessment

This is the section that's not in most language READMEs because most language authors are trying to get adopted. I'm not.

**Solo programming languages almost never break through.** Mojo has $100M+ in funding. Cython has 15 years of work. Nim, Zig, V are all struggling for adoption. There's no realistic universe where Sym replaces Python in your workflow.

**The interesting bits aren't the language, they're the techniques.** Specifically: the hybrid C+Python compilation gated by purity analysis. That idea, generalized, could work as a Python library, a `@fast` decorator that compiles pure functions to C automatically. People want their Python faster; they don't want a new language.

**Where this could go.** Extracting the hybrid compiler as a Python library is the realistic next step. Sym as a language is a research project. The compiler underneath is potentially a real tool.

## What I'd do differently in v2

- Extract `compiler/hybrid.py` as a standalone Python package (`@fast` decorator approach)
- Add type inference so types don't always need to be explicit
- Better error messages. Current ones are bare-minimum, real languages have rich diagnostics
- Self-hosting: write parts of Sym in Sym itself
- LLVM backend instead of C codegen for better optimization

## License

MIT. Fork it, extend it, ignore it, ship the `@fast` decorator if I haven't gotten to it yet.

---

<div align="center">
<sub>Built by <a href="https://github.com/RicKanjilal"><b>Ric Kanjilal</b></a> · Class 10 · Don Bosco School, Liluah · Kolkata</sub><br>
<sub><i>5-year arc from <a href="https://github.com/RicKanjilal/Pytson1.0">Pytson 1.0</a> (Class 5, tutorial-followed) to here.</i></sub>
</div>
