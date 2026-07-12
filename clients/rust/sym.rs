// sym — the SymBridge client for RUST.
//
//     let mut sym = Sym::new();
//     let v = sym.call("python", "math.sqrt", vec![J::num(81.0)]);
//     let lst = sym.new_obj("java", "java.util.ArrayList", vec![]);
//     sym.hcall(&lst, "add", vec![J::s("from rust")]);
//
// Rust can't be a library PROVIDER (no runtime to host) — but as a
// CONSUMER it gets numpy, POI, ggplot2, all of it. Zero crates:
// ships the same mini-JSON the rust> block template uses.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

#[derive(Clone, Debug, PartialEq)]
pub enum J { Null, B(bool), N(f64), S(String), A(Vec<J>), O(HashMap<String, J>) }

impl J {
    pub fn num(v: f64) -> J { J::N(v) }
    pub fn s(v: &str) -> J { J::S(v.to_string()) }
    pub fn as_f64(&self) -> f64 { if let J::N(n) = self { *n } else { 0.0 } }
    pub fn as_str(&self) -> &str { if let J::S(s) = self { s } else { "" } }
    pub fn get(&self, k: &str) -> J {
        if let J::O(m) = self { m.get(k).cloned().unwrap_or(J::Null) } else { J::Null }
    }
    pub fn is_handle(&self) -> bool { self.get("__sym__").as_str() == "handle" }
}

pub struct Sym {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    id: i64,
}

impl Sym {
    pub fn new() -> Sym {
        let root = std::env::var("SYM_HOME").ok()
            .filter(|p| std::path::Path::new(&format!("{}/bridge", p)).is_dir())
            .or_else(|| {
                let home = std::env::var("HOME").unwrap_or_default();
                let p = format!("{}/.sym", home);
                if std::path::Path::new(&format!("{}/bridge", p)).is_dir() { Some(p) } else { None }
            })
            .or_else(|| {
                let out = std::process::Command::new("python3")
                    .args(["-c", "import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))"])
                    .output().ok()?;
                let core = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if std::path::Path::new(&format!("{}/bridge", core)).is_dir() { Some(core) } else { None }
            })
            .expect("sym: no Sym found (pip install sym-lang, or set SYM_HOME)");
        let mut child = Command::new("python3")
            .arg(format!("{}/bridge/stdio_host.py", root))
            .stdin(Stdio::piped()).stdout(Stdio::piped())
            .spawn().expect("failed to spawn sym host");
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        Sym { _child: child, stdin, stdout, id: 0 }
    }

    pub fn request(&mut self, mut msg: HashMap<String, J>) -> Result<J, String> {
        self.id += 1;
        msg.insert("id".into(), J::N(self.id as f64));
        writeln!(self.stdin, "{}", write(&J::O(msg))).map_err(|e| e.to_string())?;
        self.stdin.flush().ok();
        let mut line = String::new();
        loop {
            line.clear();
            if self.stdout.read_line(&mut line).map_err(|e| e.to_string())? == 0 {
                return Err("sym host died".into());
            }
            if let Some(resp) = parse(line.trim()) {
                if resp.get("id").as_f64() as i64 != self.id { continue; }
                if let J::B(true) = resp.get("ok") {
                    return Ok(resp.get("value"));
                }
                return Err(format!("sym: {}", resp.get("error").as_str()));
            }
        }
    }

    fn base(op: &str, lang: &str) -> HashMap<String, J> {
        let mut m = HashMap::new();
        m.insert("op".into(), J::s(op));
        m.insert("lang".into(), J::s(lang));
        m
    }

    pub fn import(&mut self, lang: &str, target: &str) -> Result<J, String> {
        let mut m = Self::base("import", lang);
        m.insert("target".into(), J::s(target));
        self.request(m)
    }
    pub fn call(&mut self, lang: &str, target: &str, args: Vec<J>) -> Result<J, String> {
        let mut m = Self::base("call", lang);
        m.insert("target".into(), J::s(target));
        m.insert("args".into(), J::A(args));
        self.request(m)
    }
    pub fn new_obj(&mut self, lang: &str, target: &str, args: Vec<J>) -> Result<J, String> {
        let mut m = Self::base("new", lang);
        m.insert("target".into(), J::s(target));
        m.insert("args".into(), J::A(args));
        self.request(m)
    }
    pub fn hcall(&mut self, handle: &J, method: &str, args: Vec<J>) -> Result<J, String> {
        let mut m = Self::base("hcall", handle.get("runtime").as_str());
        m.insert("handle".into(), handle.get("id"));
        m.insert("method".into(), J::s(method));
        m.insert("args".into(), J::A(args));
        self.request(m)
    }
    pub fn c_call(&mut self, lib_fn: &str, ret: &str, argtypes: Vec<&str>, args: Vec<J>)
        -> Result<J, String> {
        let mut m = Self::base("call", "c");
        m.insert("target".into(), J::s(lib_fn));
        m.insert("args".into(), J::A(args));
        m.insert("ret".into(), J::s(ret));
        m.insert("argtypes".into(), J::A(argtypes.iter().map(|t| J::s(t)).collect()));
        self.request(m)
    }
    pub fn close(&mut self) {
        let _ = self.request(Self::base("shutdown", ""));
    }
}

// ── selftest ─────────────────────────────────────────────────
fn main() {
    let mut sym = Sym::new();
    let mut ok = |name: &str, pass: bool|
        println!("  {} rust \u{2192} {}", if pass { "\u{2705}" } else { "\u{274c}" }, name);

    let v = sym.call("java", "java.lang.Math.pow", vec![J::num(2.0), J::num(5.0)]).unwrap();
    ok("java", v.as_f64() == 32.0);
    let v = sym.call("js", "Math.max", vec![J::num(3.0), J::num(9.0), J::num(2.0)]).unwrap();
    ok("js", v.as_f64() == 9.0);
    let v = sym.call("python", "math.sqrt", vec![J::num(81.0)]).unwrap();
    ok("python", v.as_f64() == 9.0);
    let v = sym.call("php", "strtoupper", vec![J::s("sym")]).unwrap();
    ok("php", v.as_str() == "SYM");
    let v = sym.call("ruby", "Math.sqrt", vec![J::num(144.0)]).unwrap();
    ok("ruby", v.as_f64() == 12.0);
    let v = sym.call("r", "mean", vec![J::A(vec![J::num(1.0), J::num(2.0), J::num(3.0), J::num(4.0), J::num(5.0)])]).unwrap();
    ok("r", v.as_f64() == 3.0);
    sym.import("perl", "POSIX").unwrap();
    let v = sym.call("perl", "POSIX.floor", vec![J::num(3.7)]).unwrap();
    ok("perl", v.as_f64() == 3.0);
    let v = sym.c_call("m.sqrt", "double", vec!["double"], vec![J::num(2.0)]).unwrap();
    ok("c", (v.as_f64() - 1.41421).abs() < 0.001);
    let lst = sym.new_obj("java", "java.util.ArrayList", vec![]).unwrap();
    sym.hcall(&lst, "add", vec![J::s("from rust")]).unwrap();
    let sz = sym.hcall(&lst, "size", vec![]).unwrap();
    ok("java live object", sz.as_f64() == 1.0);
    sym.close();
    println!("MATRIX_ROW_OK rust");
}

// ── mini JSON (same as the rust> block template) ─────────────
fn parse(s: &str) -> Option<J> {
    let cs: Vec<char> = s.chars().collect();
    let mut i = 0usize;
    pval(&cs, &mut i)
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
        ws(cs, i); *i += 1;
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
            let items: Vec<String> = m.iter().map(|(k, x)| format!("\"{}\":{}", k, write(x))).collect();
            format!("{{{}}}", items.join(","))
        }
    }
}
