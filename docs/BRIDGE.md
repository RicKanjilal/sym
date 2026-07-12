# SymBridge — Sym as the Polyglot Host

> Python never talks to Java. JS never talks to Python.
> Everything talks to Sym. Sym brokers **Symbol Objects**.

## The architecture

```
                    ┌──────────────┐
   .sym program ──► │  SYM (host)  │
                    │  the broker  │
                    └──┬───────┬───┘
              Symbol   │       │   Symbol
              Objects  │       │   Objects
                   ┌───▼──┐ ┌──▼───┐
                   │ JVM  │ │ Node │   ← real runtimes,
                   │worker│ │worker│     launched & conducted by Sym
                   └──────┘ └──────┘
```

- Each foreign runtime is a **worker subprocess** that Sym launches, owns, and shuts down.
- All values crossing a boundary become **Symbol Objects** — a neutral JSON
  representation (numbers, strings, bools, lists, maps, tagged bytes).
  No language ever sees another language's raw objects.
- Wire protocol: newline-delimited JSON over stdin/stdout. Dead simple, debuggable.

## Syntax

### Foreign imports
```
java.import math.Calculator          # finds math/Calculator.java or .class,
let x = Calculator.add(5, 8)         # compiles on the fly if needed

java.import java.lang.Math as JMath  # full JDK stdlib available
let p = JMath.pow(2, 16)

js.import textkit                    # npm module, ./textkit.js, or ./jslib/
let s = textkit.shout("hello")
```

### Foreign blocks — code runs in its real runtime
```
sym_set("numbers", [4, 8, 15, 16, 23, 42])

js> {
  sym.doubled = sym.numbers.map(x => x * 2);      // real Node
}

java> {
  java.util.List<Object> xs =                      // real JVM, compiled
      (java.util.List<Object>) sym.get("doubled"); // by javax.tools
  long t = 0;
  for (Object x : xs) t += ((Number) x).longValue();
  sym.put("total", t);
}

let total = sym_get("total")
```

`sym` is the shared Symbol Object space. In JS blocks it's an object
(`sym.name`), in Java blocks a `Map<String,Object>` (`sym.get/put`),
in Sym `sym_get()/sym_set()`.

## Java class resolution order
1. Already on classpath (JDK stdlib, precompiled `.class` in cwd or `./javalib`,
   `$SYM_JAVA_CLASSPATH`)
2. Source at `./<pkg>/<Class>.java` or `./javalib/<pkg>/<Class>.java`
   → compiled automatically by the worker (needs a JDK, not just a JRE)

## JS module resolution order
1. `node_modules` (anything `npm install`-ed)
2. `./<name>.mjs` / `./<name>.js`
3. `./jslib/<name>.mjs` / `./jslib/<name>.js`

## Live objects — handles

```
java.import java.util.ArrayList as AL
let lst = AL()              # constructs in the JVM; Sym holds only a ticket:
lst.add("hello")            # {"__sym__": "handle", "runtime": "java", "id": 1}
lst.size()                  # every call routes back to where the object lives
```

- Bare call on an imported class constructs: `Calculator()`. Explicit form
  works everywhere: `Cls.new(...)` (required in the JS client).
- Handles can be passed back as arguments (`l2.addAll(lst)`) — the worker
  dereferences its own tickets.
- Java overloads are resolved by **conversion-cost scoring** (exact type 0,
  widening 1–4, Object 5) across both methods and constructors.
- Handles are per-runtime: a Java handle means nothing to Ruby. Passing one
  across raises an honest error instead of pretending.
- Go/Rust blocks are one-shot processes — no handles there by design.

## Clients — Sym as middleware

The broker is a service, not just a language feature. `bridge/stdio_host.py`
speaks the same JSON protocol outward, so any language can consume Sym:

```python
import sym                                   # plain Python, anywhere
calc = sym.java("math.Calculator")
lst  = sym.java("java.util.ArrayList")()     # live handle
sym.block("ruby", 'sym["x"] = 1')
np   = sym.imp("numpy")                      # registry
```

```js
import { sym } from "~/.sym/clients/node/sym.mjs";
const Calc = await sym.java("math.Calculator");
await Calc.add(5, 8);                        // every crossing is async — honestly
const lst = await (await sym.java("java.util.ArrayList")).new();
```

## The consumer matrix

Any language with a client can consume any provider. Clients speak the same
JSON-line protocol to `bridge/stdio_host.py` (the hub). Provider handles
work identically from every consumer — a Java ArrayList behaves the same
held from Ruby, Go, or R.

Universal installs: `sym add numpy` (pip) · `sym add js:lodash` (npm) ·
`sym add java:poi` (Maven + transitive deps) · `sym add ruby:nokogiri` (gem)
· `sym add r:ggplot2` (CRAN) · `sym add php:monolog` (composer) ·
`sym add perl:JSON::XS` (cpan) · `sym add rust:serde` (cargo)

## Honest limits (read this)
- **Data and handles cross. Raw memory doesn't.** You can pass numbers, strings, lists,
  maps, bytes. You cannot hold a Java object in Python memory — that's the
  GraalVM problem, and SymBridge deliberately sidesteps it. Anything
  non-serializable comes back tagged `opaque` with its `repr`.
- **Static methods only** for `java.import` calls right now. Instance
  lifecycles across the boundary = live object handles = later, maybe.
- One worker per language per program. Worker cwd is fixed at launch.
- Every boundary crossing costs a JSON round-trip (~0.1–1 ms). Fine for
  glue code and pipelines; don't put it inside a hot loop — move the loop
  *into* the block instead.

## All supported ecosystems

| Ecosystem | Mechanism | Import | Blocks |
|---|---|---|---|
| Python | in-host (native tongue, full fidelity, auto-pip) | `sym.import numpy` | `python> {}` |
| JavaScript | Node worker | `js.import lodash` | `js> {}` |
| Java | JVM worker + on-the-fly javac | `java.import math.Calculator` | `java> {}` |
| PHP | php worker | `php.import mylib` | `php> {}` |
| Ruby | ruby worker | `ruby.import mylib` | `ruby> {}` |
| R | Rscript worker (jsonlite) | `r.import ggplot2` | `r> {}` |
| Perl | perl worker (JSON::PP, core) | `perl.import POSIX` | `perl> {}` |
| Go | compiled block, cached by hash | — | `go> {}` |
| Rust | compiled block, cached by hash (embedded mini-JSON) | — | `rust> {}` |
| C | FFI via ctypes — the OS loader is the bridge | `c.import sqlite3` | — |

### The registry — `sym.import X`
Don't care where a library lives? `sym.import tensorflow` looks the name up
in `bridge/registry.json` (numpy→python, lodash→js, ggplot2→r, ...), falls
back to probing, and routes to the home ecosystem. Per-project overrides in
`./symbridge.json`:
```json
{ "mylib": "ruby" }
```

### C FFI — explicit types, honest types
```
c.import sqlite3 as sq
let v = sq.call("sqlite3_libversion", [], "str")   # ret: int|double|str|void|ptr
```
Return types are explicit because C headers aren't available at runtime —
guessing would be lying.

### Compiled blocks (go/rust)
First run compiles and caches (`~/.symbridge/cache`, keyed by code hash) —
seconds once, native speed forever after. Helpers inside blocks:
- go: `symGet(k)`, `symSet(k, v)`, `symNums(k) []float64`
- rust: `sym.get("k")`, `sym.set("k", J::num(x))`, `.as_f64s()`, `.as_str()`

### What does NOT bridge (by design, not by accident)
Frameworks that want to own the whole program — React, Spring, Unity — are
not importable functions in ANY system, including GraalVM. Function-shaped
libraries (numpy, lodash, ggplot2, ffmpeg, sqlite) bridge beautifully.
That's the honest line, and it's a good line.

## Run the demo
```
cd examples
sym run polyglot_demo.sym       # 3 runtimes
sym run polyglot_ultimate.sym   # 8 ecosystems, one shared space
```

## Tests
```
python3 tests/test_bridge.py     # 21 tests: ecosystems, handles, overloads, clients
```
