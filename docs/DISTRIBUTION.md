# Getting Sym in every language

The **core** is the Python package — host, workers, compiler, everything:
```
pip install sym-lang        # → `sym` CLI  +  `import sym`
```
Every other language ships a THIN CLIENT that auto-discovers the core
(via SYM_HOME → ~/.sym → asking python3 for the pip package path):

| Language | Install | Status |
|---|---|---|
| Python | `pip install sym-lang` | packaging/build_pip.sh — ready to `twine upload` |
| Node | `npm install symlang` | packaging/npm — copy client in, `npm publish` |
| Ruby | `gem install sym-lang` | packaging/gem — `gem build && gem push` |
| Rust | `cargo add sym-bridge` | packaging/cargo — `cargo publish` |
| Go | `go get github.com/RicKanjilal/sym/clients/go` | live the moment the repo is public |
| Java | JitPack: `com.github.RicKanjilal:sym:v0.2.0` | live after first GitHub tag |
| PHP | `composer require ric/sym` | submit repo to Packagist |
| R | `remotes::install_github("RicKanjilal/sym", subdir="clients/r")` | live when public; CRAN later |
| Perl | cpanm from git | CPAN (PAUSE) later |

Publishing order: PyPI first (everything depends on it) → GitHub public
(activates Go + JitPack + R for free) → npm/gem/cargo (10 min each) →
Packagist → CRAN/CPAN when there's demand.

Name notes: `sym` is taken on PyPI/npm — dist names are sym-lang / symlang;
the import name stays `sym` everywhere.
