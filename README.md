<div align="center">

# Sym ⚡

### *Every library. Every language. No walls.*

[![battery](https://github.com/RicKanjilal/sym/actions/workflows/test.yml/badge.svg)](https://github.com/RicKanjilal/sym/actions/workflows/test.yml)
![matrix](https://img.shields.io/badge/matrix-80%2F80_green-22c55e?style=flat-square&labelColor=000)
![ecosystems](https://img.shields.io/badge/ecosystems-10-7c5cff?style=flat-square&labelColor=000)
![license](https://img.shields.io/badge/license-MIT-93c5fd?style=flat-square&labelColor=000)

[![demo](https://asciinema.org/a/QHtQEfsmSTwf8YsX.svg)](https://asciinema.org/a/QHtQEfsmSTwf8YsX)

*Ten languages. One program. Same run.*

</div>

---

Your Python can't use Apache Lucene. Your Ruby can't touch ggplot2. Your Go can't call numpy.

Says who?

```python
import sym

lucene = sym.java.package("org.apache.lucene")   # Java's search engine
np     = sym.imp("numpy")                        # Python's own
ggplot = sym.r("ggplot2")                        # R's finest
gson   = sym.java("com.google.gson.Gson")()      # a LIVE Java object, held in Python

# same file. same breath.
```

Not ports. Not reimplementations. **The actual libraries**, running in their own real runtimes, called from yours.

## The party trick: real React, from Python

```python
import sym

React  = sym.js("react")
Server = sym.js("react-dom/server")

page = React.createElement("h1", None, "rendered by React. called from Python. yes, really.")
print(Server.renderToString(page))
# <h1>rendered by React. called from Python. yes, really.</h1>
```

That's React — the real one, from npm, running in Node — driven from a `.py` file.

## THE MATRIX

Every row can use every column's libraries. 80 out of 80 cells, green in CI on Linux **and** macOS.

```
              java     js  python    php   ruby      r   perl      c
  python        ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  node          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  ruby          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  rust          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  go            ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  java          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  php           ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  r             ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  perl          ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
  sym           ✅     ✅     ✅     ✅     ✅     ✅     ✅     ✅
```

*(rows = who's asking · columns = whose library)*

A Ruby script using numpy. A Go binary holding a live Java object. R calling PHP. Reproduce it yourself:

```bash
python3 tests/test_matrix.py
```

## Install

The Python package is the engine. Every other language is a thin client that finds it.

```bash
pip install sym-lang                    # the core → `import sym` + the `sym` CLI
```

```bash
npm install symlang                     # Node
cargo add sym-bridge                    # Rust
gem install sym-lang                    # Ruby
composer require rickanjilal/sym        # PHP
go get github.com/RicKanjilal/sym/clients/go
# R:    remotes::install_github("RicKanjilal/sym", subdir="clients/r")
# Java: JitPack — com.github.RicKanjilal:sym:v0.2.0
```

Grab libraries from any ecosystem with one command:

```bash
sym add numpy              # pip
sym add js:lodash          # npm
sym add java:lucene        # Maven, with transitive deps
sym add ruby:nokogiri      # gem
sym add r:ggplot2          # CRAN
```

## How it actually works

No magic — a broker and a neutral format.

```
                    ┌──────────────┐
   your program ──► │  SYM  (host) │
                    │  the broker  │
                    └──┬────┬───┬──┘
             Symbol    │    │   │    Symbol
             Objects   │    │   │    Objects
                   ┌───▼─┐┌─▼──┐┌▼────┐
                   │ JVM ││Node││ R … │   ← real runtimes,
                   └─────┘└────┘└─────┘     launched and owned by Sym
```

- Each language runs as a **worker process** Sym launches, owns, and shuts down.
- Values crossing a boundary become **Symbol Objects** — a neutral JSON form every language can read.
- Live objects never move. They stay in their runtime and travel as **handles**; method calls route back to where the object lives. When your variable dies, so does the object (distributed GC).
- One protocol: newline-delimited JSON, small enough to read in an evening → [`docs/BRIDGE.md`](docs/BRIDGE.md)

**Ten consumers × eight providers = 80 combinations, from 18 small programs** — because nobody talks to anybody. Everybody talks to Sym.

### Honest limits (physics, not laziness)

- The **callable surface** of a library bridges: functions, methods, objects, data.
- **Environments** don't — React's browser event loop, a game engine, a framework that wants to own your process. No system crosses that, Sym included.
- No callbacks (foreign code calling back into you) or cross-language inheritance yet.
- Every boundary crossing is a real round trip (~0.1–1 ms). Put the hot loop *inside* the block, not across it.

## The test battery

```
python3 tests/test_compiler.py   # 19  — language core
python3 tests/test_bridge.py     # 21  — every ecosystem, handles, clients
python3 tests/test_deep.py       # 42  — unicode, 2^53 longs, error recovery,
                                 #       stale handles, adversarial parsing
python3 tests/test_matrix.py     # 80  — every consumer × every provider
showcase/run_all.sh              # 10  — the same real program in ten languages
```

172 checks + 10 programs, every push, on Linux and macOS. Workers must survive their own crashes. Bengali and emoji must cross every boundary intact. A `}` inside a string must not break the parser — it did, once, and the deep suite caught it before you could.

---

# Chapter two: `.sym`, the language

Sym started as a language before it became a host, and the language is still here.

Files use `.sym`. The compiler is Python, lives in this repo, and emits **two** things:

1. **Python** for any function that touches the Python ecosystem
2. **C shared libraries** for any function that's *pure* — only math, no Python deps

You don't annotate anything. The compiler walks the AST, tags each function, routes the pure ones through C codegen, and wires them together with `ctypes`. You just run `sym run file.sym`.

### Speed you didn't ask for

| Test | Pure Python | Native C | Speedup |
|------|-------------|----------|---------|
| `fib(35)` | 1778 ms | 29 ms | **62×** |
| `sum_squares(1M)` | 166 ms | <0.1 ms | **>1000×** |
| `count_primes(100K)` | 228 ms | 46 ms | **5×** |
| Combined | 1900 ms | 33 ms | **57×** |

The speedup isn't the interesting part — that's just C being C. The interesting part is that you got it without writing C, choosing a decorator, or knowing it happened.

### Three syntax modes, one AST

Same program, same speed, same generated code. Pick whichever you find readable.

```sym
fn fibonacci(n: int) -> int          # Normal
    if n <= 1
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
```

```sym
#compact
f fib(n: int) -> int                 # Compact
    ? n <= 1
        <- n
    <- fib(n - 1) + fib(n - 2)
```

```sym
#symbol
ƒ fib(n:ℤ) → ℤ                       # Symbol — yes, this compiles
    ? n ≤ 1
        ← n
    ← fib(n-1) + fib(n-2)
```

### Polyglot, natively

```sym
sym.import numpy                     # the registry finds it

java.import math.Calculator          # a live JVM answers
let c = Calculator()                 # a real Java object — Sym holds the handle
let sum = c.add(5, 8)

js> {
  sym.doubled = sym.numbers.map(x => x * 2);      // real Node
}

r> {
  sym$sd <- sd(unlist(sym$doubled))               // real R
}

rust> {
  let m = sym.get("doubled").as_f64s().iter().cloned().fold(f64::MIN, f64::max);
  sym.set("max", J::num(m));                      // compiled once, cached, native
}
```

### CLI

```bash
sym run file.sym              # hybrid C+Python (default)
sym run file.sym -v           # verbose — shows what compiled to C
sym run file.sym --emit       # show generated code
sym build -t native file.sym  # standalone binary
sym check file.sym            # purity analysis report
sym repl                      # interactive REPL
sym add <pkg>                 # install from any ecosystem
```

## Why I built this

I'd wanted to build a real language for years. My first attempt was [Pytson 1.0](https://github.com/RicKanjilal/Pytson1.0), in Class 5 — tutorial-following work that produced a basic interpreter.

Sym is that idea five years later, with the questions I actually wanted to answer:

- *Can a high-level language give native speed without the user thinking about it?* → purity analysis routes pure functions to C.
- *Can syntax be a preference instead of a law?* → three modes, one AST.
- *Why should a library's home language decide who's allowed to use it?* → SymBridge. That question ate the whole project, and became the headline.

## How this was built

**I drove the design.** The three-syntax-mode architecture, hybrid Python/C compilation gated by purity analysis, the Symbol Object model, Sym-as-host-not-language, object handles, the consumer/provider split that turns 80 bridges into 18 programs — those calls are mine, usually after several wrong turns.

**Most of the implementation typing was done with Claude.** I specified what each module should do, reviewed output, pushed back, and debugged. The lexer, parser, codegens, workers, and clients were collaborative. I can read all of it, modify all of it, and explain all of it. If you fork this and ask me why something works the way it does, I'll answer.

The design is mine. The execution was collaborative. The understanding is mine.

## Roadmap

Shipped: ten-ecosystem bridge, Symbol Objects, live handles with distributed GC, overload scoring, universal `sym add`, the 80-cell matrix, nine package registries.

Next: auto-free handles in external clients · Windows support · Java varargs · callbacks · facade libraries (`symxl`-style wrappers so `sheet["A1"]` beats fifteen lines of POI).

Never (physics, not laziness): cross-language inheritance · bridging environments.

## License

MIT. Fork it, extend it, ship something better.

---

<div align="center">
<sub>Built by <a href="https://github.com/RicKanjilal"><b>Ric Kanjilal</b></a> · Class 10 · Don Bosco School, Liluah · Kolkata</sub><br>
<sub><i>A 5-year arc from <a href="https://github.com/RicKanjilal/Pytson1.0">Pytson 1.0</a> to a runtime that hosts ten ecosystems.</i></sub>
</div>
