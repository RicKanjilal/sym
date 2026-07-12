#!/bin/bash
# Assemble the pip package: sym-lang (dist) providing `import sym` + `sym` CLI.
# The whole core (compiler, bridge, runtime, workers) ships INSIDE the wheel.
set -e
cd "$(dirname "$0")/.."
STAGE=/tmp/sym-pip-stage
rm -rf "$STAGE"
mkdir -p "$STAGE/sym_lang/core"

# core payload
cp -r compiler bridge runtime bin clients examples docs "$STAGE/sym_lang/core/"
find "$STAGE" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name "*.class" -delete
touch "$STAGE/sym_lang/__init__.py"

# the `sym` import module = the python client, core-aware
cp clients/python/sym.py "$STAGE/sym.py"

# CLI entry point
cat > "$STAGE/sym_lang/cli.py" << 'PY'
import os, sys

def main():
    core = os.path.join(os.path.dirname(__file__), "core")
    sys.path.insert(0, core)
    os.environ.setdefault("SYM_HOME", core)
    sys.argv[0] = "sym"
    runpy_path = os.path.join(core, "bin", "sym.py")
    import runpy
    sys.path.insert(0, os.path.join(core, "bin"))
    runpy.run_path(runpy_path, run_name="__main__")
PY

cat > "$STAGE/pyproject.toml" << 'TOML'
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "sym-lang"
version = "0.2.0"
description = "Sym - the polyglot host. Use any language's libraries from any language."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
authors = [{ name = "Ric Kanjilal" }]
keywords = ["polyglot", "interop", "bridge", "jvm", "ffi"]

[project.urls]
Homepage = "https://github.com/RicKanjilal/sym"

[project.scripts]
sym = "sym_lang.cli:main"

[tool.setuptools]
packages = ["sym_lang"]
py-modules = ["sym"]
include-package-data = true

[tool.setuptools.package-data]
sym_lang = ["core/**/*"]
TOML
head -40 README.md > "$STAGE/README.md"

echo "staged at $STAGE"
cd "$STAGE" && python3 -m pip install . --break-system-packages --quiet 2>/dev/null || python3 -m pip install . --quiet
echo "installed: pip package sym-lang"
