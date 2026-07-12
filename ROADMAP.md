# Sym Roadmap

Shipped (v0.2): ten-ecosystem bridge, Symbol Object protocol, live object
handles with distributed GC, overload scoring, Maven/npm/pip/gem/CRAN
installs, the 80-cell matrix, 172-check test battery, pip packaging.

## Next
- [ ] Auto-free handles in external clients (Node/Ruby/Go/... finalizers —
      Python host already does this; clients currently pin until close)
- [ ] `sym add` companion hints (react → suggests react-dom)
- [ ] Worker error messages suggest `sym add <eco>:<pkg>` on import failure
- [ ] Java: varargs, instance-field writes, `null`-arg constructor scoring
- [ ] Windows support (workers use portable IPC already; needs testing + installer)
- [ ] Registry publishing: npm, gem, cargo, Packagist (skeletons in packaging/)

## Someday
- [ ] C# worker (.NET) — same worker pattern
- [ ] WASM worker
- [ ] async/await in the .sym language
- [ ] Callback support (foreign code calling back into the consumer)
- [ ] Facade library collection (symxl-style wrappers: symlucene, symplot, ...)

## Never (physics, not laziness)
- Cross-language inheritance (subclass a Java class from Ruby)
- Bridging environments (React's browser loop, Unity, Rails-as-server) —
  their *callable surfaces* bridge; the environments don't, in any system.
