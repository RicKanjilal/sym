#!/bin/bash
# Sym installer — one command, then `sym run anything.sym`.
#   ./install.sh
# Installs to ~/.sym, puts `sym` on your PATH, wires the Python client.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYM_HOME="$HOME/.sym"
BIN_DIR="$HOME/.local/bin"

echo ""
echo "  ⚡ Sym — the polyglot host"
echo "  ─────────────────────────────────────────────"
echo "  One file. Ten ecosystems. Nobody talks to anybody."
echo "  Everybody talks to Sym."
echo ""

# ── required ─────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "  ❌ python3 is required. sudo apt install python3"
    exit 1
fi
echo "  ✅ python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')  (host)"

# ── ecosystem detection ──────────────────────────────────
check() { # name, command, unlocks, hint
    if command -v "$2" &>/dev/null; then
        printf "  ✅ %-10s %s\n" "$1" "$3"
        return 0
    else
        printf "  ⬚  %-10s %s  →  %s\n" "$1" "$3" "$4"
        return 1
    fi
}

echo ""
echo "  Ecosystems (each optional — install any time, Sym picks them up):"
check "node"    node    "js.import, js> blocks, node client" "sudo apt install nodejs" || true
if command -v javac &>/dev/null; then
    printf "  ✅ %-10s %s\n" "java(jdk)" "java.import, java> blocks, live objects"
elif command -v java &>/dev/null; then
    printf "  ⚠️  %-10s %s\n" "java(jre)" "JRE found but javac missing → sudo apt install openjdk-21-jdk"
else
    printf "  ⬚  %-10s %s  →  %s\n" "java" "java.import, java> blocks" "sudo apt install openjdk-21-jdk"
fi
check "php"     php     "php.import, php> blocks"   "sudo apt install php-cli" || true
check "ruby"    ruby    "ruby.import, ruby> blocks" "sudo apt install ruby" || true
if command -v Rscript &>/dev/null; then
    if Rscript -e 'library(jsonlite)' &>/dev/null; then
        printf "  ✅ %-10s %s\n" "r" "r.import, r> blocks"
    else
        printf "  ⚠️  %-10s %s\n" "r" "found, but needs: sudo Rscript -e 'install.packages(\"jsonlite\", repos=\"https://cloud.r-project.org\")'"
    fi
else
    printf "  ⬚  %-10s %s  →  %s\n" "r" "r.import, r> blocks" "sudo apt install r-base-core"
fi
check "perl"    perl    "perl.import, perl> blocks" "sudo apt install perl" || true
check "go"      go      "go> compiled blocks"       "sudo apt install golang-go" || true
check "rustc"   rustc   "rust> compiled blocks"     "sudo apt install rustc" || true
check "gcc"     gcc     "c.import via FFI"          "sudo apt install gcc" || true

# ── install ──────────────────────────────────────────────
echo ""
# migrate from the old install dir if present
if [ -d "$HOME/.symbolcore/jars" ] && [ ! -d "$SYM_HOME/jars" ]; then
    mkdir -p "$SYM_HOME"
    mv "$HOME/.symbolcore/jars" "$SYM_HOME/jars"
    echo "  ↪ migrated jars from ~/.symbolcore"
fi
[ -d "$HOME/.symbolcore" ] && rm -rf "$HOME/.symbolcore"

echo "  Installing to $SYM_HOME ..."
# downloaded jars survive reinstalls
if [ -d "$SYM_HOME/jars" ]; then
    mv "$SYM_HOME/jars" /tmp/.sym-jars-keep
fi
rm -rf "$SYM_HOME"
mkdir -p "$SYM_HOME"
if [ -d /tmp/.sym-jars-keep ]; then
    mv /tmp/.sym-jars-keep "$SYM_HOME/jars"
fi
cp -r "$SCRIPT_DIR"/bin "$SCRIPT_DIR"/compiler "$SCRIPT_DIR"/runtime \
      "$SCRIPT_DIR"/bridge "$SCRIPT_DIR"/clients "$SCRIPT_DIR"/docs \
      "$SCRIPT_DIR"/examples "$SYM_HOME"/ 2>/dev/null
find "$SYM_HOME" -name "*.class" -delete 2>/dev/null
find "$SYM_HOME" -name ".symworker.srchash" -delete 2>/dev/null
find "$SYM_HOME" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

# launcher
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/sym" << 'LAUNCHER'
#!/bin/bash
export SYM_HOME="${SYM_HOME:-$HOME/.sym}"
exec python3 "$SYM_HOME/bin/sym.py" "$@"
LAUNCHER
chmod +x "$BIN_DIR/sym"
echo "  ✅ launcher: $BIN_DIR/sym"

# python client → user site-packages, so `import sym` works anywhere
SITE=$(python3 -m site --user-site)
mkdir -p "$SITE"
cp "$SCRIPT_DIR/clients/python/sym.py" "$SITE/sym.py"
echo "  ✅ python client: import sym  (→ $SITE)"

# node client stays in SYM_HOME; point people at it
echo "  ✅ node client: import { sym } from \"$SYM_HOME/clients/node/sym.mjs\""

# PATH check
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo ""
       echo "  ⚠️  $BIN_DIR is not on your PATH. Add to ~/.bashrc:"
       echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ""
echo "  ─────────────────────────────────────────────"
echo "  Try it:"
echo "      cd $SYM_HOME/examples"
echo "      sym run polyglot_ultimate.sym"
echo ""
echo "  Or from plain Python, anywhere:"
echo "      python3 -c 'import sym; print(sym.java(\"java.lang.Math\").pow(2,10))'"
echo ""
