# Sym — Distribution Guide

## How to Ship It

You have 3 distribution channels. Do them in this order.

---

## 1. PyPI (pip install symbolcore) — THE BIG ONE

This is how 90% of people will install it. Works on Windows, Mac, Linux automatically.

### What users will type:
```bash
pip install symbolcore
sym run hello.sym
```

### Setup:

**Step 1: Restructure for PyPI**

Your current structure needs a small reorganization:

```
symbolcore/
├── pyproject.toml          ← NEW (replaces setup.py)
├── README.md
├── LICENSE
├── symbolcore/             ← Python package (rename from compiler/)
│   ├── __init__.py
│   ├── __main__.py         ← NEW (allows: python -m symbolcore)
│   ├── cli.py              ← renamed from bin/sym.py
│   ├── lexer.py
│   ├── parser.py
│   ├── ast_nodes.py
│   ├── codegen_python.py
│   ├── codegen_c.py
│   ├── hybrid.py
│   └── runtime/
│       ├── __init__.py
│       └── builtins.py
├── examples/
│   ├── hello.sym
│   ├── compact_mode.sym
│   ├── snake.sym
│   └── ...
└── tests/
    └── test_compiler.py
```

**Step 2: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "symbolcore"
version = "0.2.0"
description = "A compiled language with Python's ecosystem and C's speed"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.8"
authors = [{name = "Abir"}]
keywords = ["programming-language", "compiler", "transpiler", "python", "c"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Topic :: Software Development :: Compilers",
]

[project.scripts]
sym = "symbolcore.cli:main"

[project.urls]
Homepage = "https://testwallah.in/symbolcore"
Repository = "https://github.com/RicKanjilal/Symbolcore"
```

**Step 3: Create __main__.py**

```python
from .cli import main
main()
```

**Step 4: Build and upload**

```bash
# Install build tools
pip install build twine

# Build
python -m build

# Upload to PyPI (need account at pypi.org)
twine upload dist/*

# Test it
pip install symbolcore
sym run examples/hello.sym
```

---

## 2. GitHub Releases (Download binary/zip)

For people who want to download and run without pip.

### Create the repo:

```bash
cd ~/Downloads/symbolcore
git init
git add -A
git commit -m "Sym v0.2.0 — initial release"
git branch -M main
git remote add origin https://github.com/RicKanjilal/Symbolcore.git
git push -u origin main
```

### Create a release:

1. Go to github.com/RicKanjilal/Symbolcore
2. Click "Releases" → "Create new release"
3. Tag: v0.2.0
4. Title: Sym v0.2.0
5. Upload these files:
   - symbolcore.tar.gz (Linux/Mac)
   - symbolcore-vscode.tar.gz (VS Code extension)
6. In description, paste install instructions

### GitHub Actions — Auto-build releases

Create `.github/workflows/release.yml`:

```yaml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install build
      - run: python -m build
      - uses: actions/upload-artifact@v4
        with:
          name: dist-${{ matrix.os }}
          path: dist/

  publish:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN }}
```

---

## 3. VS Code Marketplace

### Publish the extension:

```bash
# Install vsce
npm install -g @vscode/vsce

# Package
cd symbolcore-vscode
vsce package
# Creates symbolcore-lang-0.2.0.vsix

# Publish (need Azure DevOps account + Personal Access Token)
vsce publish
```

Users then install with:
- VS Code → Extensions → Search "Sym" → Install
- Or: `code --install-extension symbolcore-lang-0.2.0.vsix`

---

## Quick Checklist

### Before shipping:

- [ ] Create GitHub account / repo
- [ ] Create PyPI account at pypi.org
- [ ] Create LICENSE file (MIT)
- [ ] Restructure folders for PyPI
- [ ] Test `pip install .` locally
- [ ] Test on a fresh Ubuntu VM / Docker
- [ ] Test on Mac (borrow someone's or use GitHub Actions)
- [ ] Windows: test in WSL at minimum
- [ ] Write pyproject.toml
- [ ] Build with `python -m build`
- [ ] Upload to PyPI with `twine upload dist/*`
- [ ] Create GitHub release with tar.gz
- [ ] Publish VS Code extension
- [ ] Update testwallah.in docs page

### Platform-specific notes:

**Windows:**
- Python works fine
- gcc via MinGW or MSYS2: `pacman -S mingw-w64-x86_64-gcc`
- Or skip C compilation — Python mode works everywhere
- sym.exe → use pip install, it auto-creates the entry point

**Mac:**
- Python via brew: `brew install python`
- gcc via Xcode: `xcode-select --install` (gives clang which works)
- Everything else works as-is

**Linux:**
- Already works on your machine
- `sudo apt install gcc python3` covers all deps
