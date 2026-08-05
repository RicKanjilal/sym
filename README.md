# Sym

### Every library. Every language. No walls.

Your Python can't use Apache Lucene. Your Ruby can't touch numpy's cousins in R. Says who?

```python
import sym

lucene = sym.java.package("org.apache.lucene")   # Java's search engine, in Python
np     = sym.imp("numpy")                          # Python's own
ggplot = sym.r("ggplot2")                          # R's finest
gson   = sym.java("com.google.gson.Gson")()        # a LIVE Java object, in Python
```

**The party trick — real React, from Python:**

```python
React  = sym.js("react")
html   = sym.js("react-dom/server").renderToString(
    React.createElement("h1", None, "rendered by React. called from Python. yes, really."))
```

Not a port. Not a reimplementation. The **actual** libraries — Lucene, React, numpy, ggplot2, Apache POI — running in their own runtimes, called from yours. Ten languages, every package ecosystem on Earth, one host. None of them know the others exist. They all just talk to Sym.

**Install:**

```bash
pip install sym-lang     # then: import sym
```

Node, Rust, Ruby, PHP, Go, R, Java clients too → github.com/RicKanjilal/sym

Built by a 15-year-old and a very stubborn test suite.