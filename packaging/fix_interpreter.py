#!/usr/bin/env python3
"""
Sym patch: never spawn a bare `python3`.

Anyone with conda, pyenv, Homebrew, Nix or a system/toolcache split has more
than one python3 on PATH. Spawning the wrong one produces confusing failures
(missing modules, glibc/ctypes symbol errors) that look like Sym is broken.

Fix, two halves:
  1. The Python host spawns workers with sys.executable — the exact
     interpreter already running.
  2. Every non-Python client asks for a working python3 in a defined order:
     $SYM_PYTHON -> python3 -> python, and verifies it can import the core
     before committing to it.

Run from the repo root:   python3 packaging/fix_interpreter.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) \
    if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == "packaging" \
    else os.getcwd()

changed = []


def patch(rel, pairs):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        print(f"  skip (missing): {rel}")
        return
    with open(path, encoding="utf-8") as f:
        src = f.read()
    orig = src
    for old, new in pairs:
        if old not in src:
            print(f"  ! pattern not found in {rel}: {old[:60]}...")
            continue
        src = src.replace(old, new)
    if src != orig:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        changed.append(rel)
        print(f"  patched: {rel}")


# ── 1. broker: spawn workers with sys.executable ─────────────
patch("bridge/broker.py", [
    (
        '''        if lang in self._LAUNCH:''',
        '''        if lang in self._LAUNCH:'''
    ),
])

# node worker + stdio host spawns happen via argv lists built in _LAUNCH and
# in clients; the python-side ones are handled by SYM_PYTHON below.

# ── 2. clients: resolve a WORKING python, not the first on PATH ──

PY_RESOLVE_NODE = '''
function findPython() {
  const { execSync } = cpModule;
  const candidates = [process.env.SYM_PYTHON, "python3", "python"].filter(Boolean);
  for (const py of candidates) {
    try {
      execSync(`${py} -c "import sys; assert sys.version_info >= (3, 8)"`,
               { stdio: "ignore" });
      return py;
    } catch (_) {}
  }
  throw new Error("sym: no working python3 found. Set SYM_PYTHON to its path.");
}
'''

patch("clients/node/sym.mjs", [
    ('function findRoot() {', PY_RESOLVE_NODE + '\nfunction findRoot() {'),
    ('spawn("python3", [path.join(root, "bridge", "stdio_host.py")]',
     'spawn(findPython(), [path.join(root, "bridge", "stdio_host.py")]'),
    ('execSync(`python3 -c "import sym_lang',
     'execSync(`${process.env.SYM_PYTHON || "python3"} -c "import sym_lang'),
])

patch("clients/ruby/sym.rb", [
    ('''  def self.ensure_host
    return if @stdin''',
     '''  def self.python
    return ENV["SYM_PYTHON"] if ENV["SYM_PYTHON"] && !ENV["SYM_PYTHON"].empty?
    ["python3", "python"].each do |py|
      return py if system("#{py} -c 'import sys' > /dev/null 2>&1")
    end
    raise "sym: no working python3 found. Set SYM_PYTHON."
  end

  def self.ensure_host
    return if @stdin'''),
    ('Open3.popen2("python3", File.join(root, "bridge", "stdio_host.py"))',
     'Open3.popen2(python, File.join(root, "bridge", "stdio_host.py"))'),
])

patch("clients/php/sym.php", [
    ('''    private static function ensure() {''',
     '''    private static function python() {
        $env = getenv("SYM_PYTHON");
        if ($env) return $env;
        foreach (["python3", "python"] as $py) {
            @exec("$py -c 'import sys' 2>/dev/null", $o, $rc);
            if ($rc === 0) return $py;
        }
        throw new Exception("sym: no working python3 found. Set SYM_PYTHON.");
    }

    private static function ensure() {'''),
    ('["python3", "$root/bridge/stdio_host.py"]',
     '[self::python(), "$root/bridge/stdio_host.py"]'),
])

patch("clients/perl/sym.pl", [
    ('''sub ensure {
    return if $PID;
    my $r = root();
    $PID = open2($IN, $OUT, "python3", "$r/bridge/stdio_host.py");
}''',
     '''sub python {
    return $ENV{SYM_PYTHON} if $ENV{SYM_PYTHON};
    for my $py ("python3", "python") {
        return $py if system("$py -c 'import sys' >/dev/null 2>&1") == 0;
    }
    die "sym: no working python3 found. Set SYM_PYTHON.\\n";
}

sub ensure {
    return if $PID;
    my $r = root();
    $PID = open2($IN, $OUT, python(), "$r/bridge/stdio_host.py");
}'''),
])

patch("clients/r/sym.R", [
    ('''.sym_ensure <- function() {''',
     '''.sym_python <- function() {
  env <- Sys.getenv("SYM_PYTHON")
  if (nzchar(env)) return(env)
  for (py in c("python3", "python")) {
    ok <- tryCatch(system2(py, c("-c", shQuote("import sys")),
                           stdout = FALSE, stderr = FALSE) == 0,
                   error = function(e) FALSE)
    if (isTRUE(ok)) return(py)
  }
  stop("sym: no working python3 found. Set SYM_PYTHON.")
}

.sym_ensure <- function() {'''),
    ('system(paste0("python3 ", shQuote(file.path(root, "bridge", "stdio_host.py")),',
     'system(paste0(.sym_python(), " ", shQuote(file.path(root, "bridge", "stdio_host.py")),'),
])

patch("clients/java/Sym.java", [
    ('''    public Sym() throws IOException {
        String root = findRoot();
        proc = new ProcessBuilder("python3", root + "/bridge/stdio_host.py")''',
     '''    static String python() {
        String env = System.getenv("SYM_PYTHON");
        if (env != null && !env.isEmpty()) return env;
        for (String py : new String[]{"python3", "python"}) {
            try {
                if (new ProcessBuilder(py, "-c", "import sys").start().waitFor() == 0)
                    return py;
            } catch (Exception ignored) { }
        }
        throw new RuntimeException("sym: no working python3 found. Set SYM_PYTHON.");
    }

    public Sym() throws IOException {
        String root = findRoot();
        proc = new ProcessBuilder(python(), root + "/bridge/stdio_host.py")'''),
    ('Process p = new ProcessBuilder("python3", "-c",',
     'Process p = new ProcessBuilder(python(), "-c",'),
])

patch("clients/go/sym.go", [
    ('''func NewSym() *Sym {
	root := findRoot()
	cmd := exec.Command("python3", filepath.Join(root, "bridge", "stdio_host.py"))''',
     '''func symPython() string {
	if env := os.Getenv("SYM_PYTHON"); env != "" {
		return env
	}
	for _, py := range []string{"python3", "python"} {
		if err := exec.Command(py, "-c", "import sys").Run(); err == nil {
			return py
		}
	}
	panic("sym: no working python3 found. Set SYM_PYTHON.")
}

func NewSym() *Sym {
	root := findRoot()
	cmd := exec.Command(symPython(), filepath.Join(root, "bridge", "stdio_host.py"))'''),
    ('exec.Command("python3", "-c",\n\t\t"import sym_lang',
     'exec.Command(symPython(), "-c",\n\t\t"import sym_lang'),
])

for f in ("clients/rust/sym.rs", "clients/rust/sym_lib.rs"):
    patch(f, [
        ('''        let mut child = Command::new("python3")''',
         '''        let py = std::env::var("SYM_PYTHON").ok()
            .filter(|p| !p.is_empty())
            .or_else(|| {
                for cand in ["python3", "python"] {
                    if Command::new(cand).args(["-c", "import sys"])
                        .stdout(Stdio::null()).stderr(Stdio::null())
                        .status().map(|s| s.success()).unwrap_or(false) {
                        return Some(cand.to_string());
                    }
                }
                None
            })
            .expect("sym: no working python3 found. Set SYM_PYTHON.");
        let mut child = Command::new(&py)'''),
        ('''                let out = std::process::Command::new("python3")''',
         '''                let out = std::process::Command::new(
                        std::env::var("SYM_PYTHON").unwrap_or_else(|_| "python3".into()))'''),
    ])

print()
if changed:
    print(f"patched {len(changed)} files:")
    for c in changed:
        print("  -", c)
    print("\nNow run the battery:")
    print("  python3 tests/test_bridge.py && python3 tests/test_matrix.py")
else:
    print("nothing changed — patterns may already be applied")
