# Publishing Sym — no QR codes involved

## 1. GitHub (tonight, ~10 min)
Create the repo at github.com/new — name it exactly `sym`, public, no README
(we have one). Then:

```bash
cd ~/Downloads/sym
git init
git add -A
git commit -m "Sym v0.2.0 — the polyglot host. Ten ecosystems, one protocol."
git branch -M main
git remote add origin https://github.com/RicKanjilal/sym.git
git push -u origin main
```

Auth: when prompted, GitHub uses browser login or a Personal Access Token
(Settings → Developer settings → Tokens) — no authenticator app required.

## 2. Watch CI go green
The Actions tab runs the full battery (172 checks + matrix + showcase) on
GitHub's fresh Ubuntu + a macOS core job. First run may surface environment
bugs our machines hid — that's the point. Fix, push, repeat until green.

## 3. Tag the release
```bash
git tag v0.2.0
git push origin v0.2.0
```
Then GitHub → Releases → "Draft new release" → choose the tag → attach the
zip. THIS single tag activates three ecosystems instantly:
- Go:      go get github.com/RicKanjilal/sym/clients/go
- Java:    JitPack coordinate com.github.RicKanjilal:sym:v0.2.0
- R:       remotes::install_github("RicKanjilal/sym", subdir="clients/r")

## 4. Registries needing 2FA (whenever you're ready — with dad)
PyPI (the keystone), npm, RubyGems, crates.io. The wheel is already built
at /tmp/sym-pip-stage/dist/. See docs/DISTRIBUTION.md.
