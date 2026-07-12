"""
SymBridge compiled blocks — go> { } and rust> { }.

Systems languages have no interpreter for Sym to host, so Sym does the
next honest thing: it wraps the block in a template, compiles it ONCE
(cached by content hash), then runs the binary as a short-lived worker.

Contract:
  stdin  : the shared `sym` space as one JSON object
  stdout : user prints pass through; final line "__SYM_EXPORTS__{json}"
           carries the mutated sym space back to the host.

First run of a block pays the compile (~0.5–2 s). After that it's a
cached native binary — instant.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

CACHE = os.path.join(os.path.expanduser("~"), ".symbridge", "cache")
MARKER = "__SYM_EXPORTS__"


class CompileError(RuntimeError):
    pass


def run_compiled_block(lang: str, code: str, env: dict):
    binary = _ensure_built(lang, code)
    proc = subprocess.run(
        [binary], input=json.dumps(env), capture_output=True, text=True)
    if proc.returncode != 0:
        raise CompileError(f"[{lang} block] runtime error:\n{proc.stderr}")
    exports, value = {}, None
    for line in proc.stdout.splitlines():
        if line.startswith(MARKER):
            exports = json.loads(line[len(MARKER):])
        else:
            print(line)  # user output passes through
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr, end="")
    return value, exports


def _ensure_built(lang: str, code: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    h = hashlib.sha256((lang + code).encode()).hexdigest()[:16]
    binary = os.path.join(CACHE, f"{lang}-{h}")
    if os.path.exists(binary):
        return binary
    src = _wrap(lang, code)
    if lang == "go":
        srcfile = binary + ".go"
        with open(srcfile, "w") as f:
            f.write(src)
        r = subprocess.run(["go", "build", "-o", binary, srcfile],
                           capture_output=True, text=True,
                           env={**os.environ, "GOCACHE": os.path.join(CACHE, "gocache"),
                                "GO111MODULE": "off"})
    elif lang == "rust":
        srcfile = binary + ".rs"
        with open(srcfile, "w") as f:
            f.write(src)
        r = subprocess.run(["rustc", "-O", "-o", binary, srcfile,
                            "--edition", "2021"],
                           capture_output=True, text=True)
    else:
        raise CompileError(f"no compiled-block support for '{lang}'")
    if r.returncode != 0:
        raise CompileError(f"[{lang} block] compile failed:\n{r.stderr}")
    return binary


# ── templates ────────────────────────────────────────────────

def _wrap(lang: str, code: str) -> str:
    if lang == "go":
        return _wrap_go(code)
    return _wrap_rust(code)


def _wrap_go(code: str) -> str:
    # hoist user `import "x"` / import ( ... ) lines into the file header
    user_imports = []
    body_lines = []
    in_import_block = False
    for line in code.splitlines():
        s = line.strip()
        if in_import_block:
            user_imports.append(line)
            if s == ")":
                in_import_block = False
            continue
        if s.startswith("import ("):
            in_import_block = True
            user_imports.append(line)
        elif s.startswith("import "):
            user_imports.append(line)
        else:
            body_lines.append(line)
    return GO_TEMPLATE.replace("//__USER_IMPORTS__", "\n".join(user_imports)) \
                      .replace("//__USER_CODE__", "\n".join(body_lines))


def _wrap_rust(code: str) -> str:
    user_uses, body_lines = [], []
    for line in code.splitlines():
        (user_uses if line.strip().startswith("use ") else body_lines).append(line)
    return RUST_TEMPLATE.replace("//__USER_USES__", "\n".join(user_uses)) \
                        .replace("//__USER_CODE__", "\n".join(body_lines))


GO_TEMPLATE = '''package main

import (
    "bufio"
    "encoding/json"
    "fmt"
    "os"
)

//__USER_IMPORTS__

var sym map[string]interface{}

func symGet(k string) interface{}      { return sym[k] }
func symSet(k string, v interface{})   { sym[k] = v }
func symNums(k string) []float64 {
    out := []float64{}
    if xs, ok := sym[k].([]interface{}); ok {
        for _, x := range xs {
            if f, ok := x.(float64); ok { out = append(out, f) }
        }
    }
    return out
}

func userMain() {
//__USER_CODE__
}

func main() {
    dec := json.NewDecoder(bufio.NewReader(os.Stdin))
    if err := dec.Decode(&sym); err != nil || sym == nil {
        sym = map[string]interface{}{}
    }
    userMain()
    b, _ := json.Marshal(sym)
    fmt.Println("__SYM_EXPORTS__" + string(b))
    _ = fmt.Sprintf // keep fmt used even if user code doesn't print
}
'''

RUST_TEMPLATE = r'''// SymBridge rust block — compiled once, cached by hash.
// Symbol Objects via the embedded mini-JSON below (no crates needed).
use std::collections::HashMap;
use std::io::Read;
//__USER_USES__

#[derive(Clone, Debug)]
pub enum J { Null, B(bool), N(f64), S(String), A(Vec<J>), O(HashMap<String, J>) }

impl J {
    pub fn num(v: f64) -> J { J::N(v) }
    pub fn s(v: &str) -> J { J::S(v.to_string()) }
    pub fn arr(v: Vec<J>) -> J { J::A(v) }
    pub fn as_f64(&self) -> f64 { if let J::N(n) = self { *n } else { 0.0 } }
    pub fn as_str(&self) -> &str { if let J::S(s) = self { s } else { "" } }
    pub fn as_vec(&self) -> Vec<J> { if let J::A(a) = self { a.clone() } else { vec![] } }
    pub fn as_f64s(&self) -> Vec<f64> { self.as_vec().iter().map(|x| x.as_f64()).collect() }
}

pub struct Sym { map: HashMap<String, J> }
impl Sym {
    pub fn get(&self, k: &str) -> J { self.map.get(k).cloned().unwrap_or(J::Null) }
    pub fn set(&mut self, k: &str, v: J) { self.map.insert(k.to_string(), v); }
}

fn user_main(sym: &mut Sym) {
//__USER_CODE__
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).ok();
    let map = match parse(&input.trim()) { Some(J::O(m)) => m, _ => HashMap::new() };
    let mut sym = Sym { map };
    user_main(&mut sym);
    println!("__SYM_EXPORTS__{}", write(&J::O(sym.map)));
}

// ── mini JSON ────────────────────────────────────────────────
fn parse(s: &str) -> Option<J> {
    let cs: Vec<char> = s.chars().collect();
    let mut i = 0usize;
    let v = pval(&cs, &mut i)?;
    Some(v)
}
fn ws(cs: &[char], i: &mut usize) { while *i < cs.len() && cs[*i].is_whitespace() { *i += 1 } }
fn pval(cs: &[char], i: &mut usize) -> Option<J> {
    ws(cs, i);
    match cs.get(*i)? {
        '{' => pobj(cs, i), '[' => parr(cs, i), '"' => Some(J::S(pstr(cs, i)?)),
        't' => { *i += 4; Some(J::B(true)) },
        'f' => { *i += 5; Some(J::B(false)) },
        'n' => { *i += 4; Some(J::Null) },
        _ => pnum(cs, i),
    }
}
fn pobj(cs: &[char], i: &mut usize) -> Option<J> {
    let mut m = HashMap::new();
    *i += 1; ws(cs, i);
    if cs.get(*i) == Some(&'}') { *i += 1; return Some(J::O(m)); }
    loop {
        ws(cs, i);
        let k = pstr(cs, i)?;
        ws(cs, i); *i += 1; // ':'
        m.insert(k, pval(cs, i)?);
        ws(cs, i);
        match cs.get(*i)? { ',' => { *i += 1; }, _ => { *i += 1; return Some(J::O(m)); } }
    }
}
fn parr(cs: &[char], i: &mut usize) -> Option<J> {
    let mut a = Vec::new();
    *i += 1; ws(cs, i);
    if cs.get(*i) == Some(&']') { *i += 1; return Some(J::A(a)); }
    loop {
        a.push(pval(cs, i)?);
        ws(cs, i);
        match cs.get(*i)? { ',' => { *i += 1; }, _ => { *i += 1; return Some(J::A(a)); } }
    }
}
fn pstr(cs: &[char], i: &mut usize) -> Option<String> {
    let mut b = String::new();
    *i += 1;
    while cs.get(*i)? != &'"' {
        let c = cs[*i]; *i += 1;
        if c == '\\' {
            let e = cs[*i]; *i += 1;
            b.push(match e {
                'n' => '\n', 't' => '\t', 'r' => '\r',
                'u' => {
                    let hex: String = cs[*i..*i + 4].iter().collect();
                    *i += 4;
                    char::from_u32(u32::from_str_radix(&hex, 16).ok()?)?
                }
                other => other,
            });
        } else { b.push(c); }
    }
    *i += 1;
    Some(b)
}
fn pnum(cs: &[char], i: &mut usize) -> Option<J> {
    let start = *i;
    while *i < cs.len() && "-+.eE0123456789".contains(cs[*i]) { *i += 1 }
    let s: String = cs[start..*i].iter().collect();
    Some(J::N(s.parse().ok()?))
}
fn write(v: &J) -> String {
    match v {
        J::Null => "null".into(),
        J::B(b) => b.to_string(),
        J::N(n) => if n.fract() == 0.0 && n.abs() < 9e15 { format!("{}", *n as i64) } else { n.to_string() },
        J::S(s) => format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n")),
        J::A(a) => format!("[{}]", a.iter().map(write).collect::<Vec<_>>().join(",")),
        J::O(m) => {
            let items: Vec<String> = m.iter()
                .map(|(k, x)| format!("\"{}\":{}", k, write(x))).collect();
            format!("{{{}}}", items.join(","))
        }
    }
}
'''
