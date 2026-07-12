# Contributing to Sym

Thanks for your interest! Here's how to contribute.

## Setup

```bash
git clone https://github.com/RicKanjilal/Symbolcore.git
cd symbolcore
./install.sh
python tests/test_compiler.py    # Should pass 19 tests
```

## Project Layout

The compiler is ~3900 lines split across:
- `compiler/lexer.py` — turns source code into tokens (3 syntax modes)
- `compiler/parser.py` — builds the AST
- `compiler/ast_nodes.py` — AST node definitions
- `compiler/codegen_python.py` — emits Python source
- `compiler/codegen_c.py` — emits C source for native compilation
- `compiler/hybrid.py` — purity analyzer + hybrid C+Python compiler
- `runtime/builtins.py` — built-in functions called from generated code
- `bin/sym.py` — CLI

Read in that order to understand the full pipeline.

## Adding a New Feature

1. **Add tokens** to the lexer if needed (`compiler/lexer.py`)
2. **Add AST nodes** if needed (`compiler/ast_nodes.py`)
3. **Update the parser** to recognize the new syntax (`compiler/parser.py`)
4. **Update both codegens** (`codegen_python.py` and `codegen_c.py`)
5. **Add tests** in `tests/test_compiler.py`
6. **Add an example** in `examples/`

## Running Tests

```bash
python tests/test_compiler.py
```

## Style

- Match existing code style (no new style rules)
- Tests required for new language features
- Update README and docs/index.html for user-facing changes

## Submitting

1. Fork the repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Commit changes
4. Push and open a Pull Request
