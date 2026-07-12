# Sym for VS Code

## Install (30 seconds)

```bash
# 1. Remove old version if any
rm -rf ~/.vscode/extensions/symbolcore*

# 2. Copy extension
cp -r symbolcore-vscode ~/.vscode/extensions/symbolcore-lang

# 3. Copy tasks to your project (for F5 run support)
mkdir -p ~/your-project/.vscode
cp symbolcore-vscode/.vscode/tasks.json ~/your-project/.vscode/

# 4. Restart VS Code
# File → Close Window, then reopen
```

## What You Get

- **Syntax highlighting** for all 3 modes (Normal, Compact, Symbol)
  - Keywords: pink/magenta
  - Strings: yellow
  - Numbers: purple
  - Functions: green
  - Types: orange
  - Python imports (py.*): blue
  - Unicode symbols (ƒ ∂ ← → ∀ ⟳ ⊂ ⊕ ≠ ≤ ≥ ℤ ℝ): colored by role
  - Comments: gray italic

- **Snippets** — type prefix + Tab:
  fn, fne, let, const, if, ife, for, fore, while, pr,
  struct, impy, match, try, lam, bench, #symbol, #compact

- **Run with Ctrl+Shift+B** or set up F5 via tasks.json

- **Auto-indent** after fn, if, for, while, struct, etc.

- **Bracket matching** and **auto-close** for () [] {} "" ''

## Running .sym Files

Option 1: Terminal
```
sym run file.sym
```

Option 2: VS Code task (Ctrl+Shift+B)
- Runs `sym run` on the current file
- Errors link to line numbers in editor

Option 3: Custom keybinding
- Copy keybindings.json content to your VS Code keybindings
- F5 = run, F6 = check
