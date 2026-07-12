// sym — the SymBridge client for Node.js.
//
//     import { sym } from "./sym.mjs";
//
//     const Calc = await sym.java("math.Calculator");
//     console.log(await Calc.add(5, 8));          // 13, from a live JVM
//
//     const lst = await Calc.new();                // or await AnyClass.new(...)
//     await lst.add("hello");                      // live handle
//
//     await sym.block("ruby", 'sym["x"] = 42');
//     console.log(await sym.get("x"));
//
// The client spawns Sym's stdio host (a Python process owning the broker)
// and speaks JSON lines to it. Every call is async — a boundary crossing
// is a real round trip, and the API is honest about that.
//
// Finds Sym via $SYM_HOME, ~/.sym, or walking up from here.

import { spawn } from "node:child_process";
import * as cpModule from "node:child_process";
import { createInterface } from "node:readline";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

function findRoot() {
  const env = process.env.SYM_HOME;
  if (env && fs.existsSync(path.join(env, "bridge"))) return env;
  const home = path.join(os.homedir(), ".sym");
  if (fs.existsSync(path.join(home, "bridge"))) return home;
  let d = path.dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 5; i++) {
    if (fs.existsSync(path.join(d, "bridge"))) return d;
    d = path.dirname(d);
  }
  try {  // pip-installed core (pip install sym-lang)
    const { execSync } = cpModule;
    const core = execSync(`python3 -c "import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))"`,
      { encoding: "utf8" }).trim();
    if (fs.existsSync(path.join(core, "bridge"))) return core;
  } catch (_) {}
  throw new Error("sym: can't find Sym. pip install sym-lang, or set SYM_HOME.");
}

class SymClient {
  constructor() {
    const root = findRoot();
    this.proc = spawn("python3", [path.join(root, "bridge", "stdio_host.py")],
                      { stdio: ["pipe", "pipe", "inherit"] });
    this.rl = createInterface({ input: this.proc.stdout });
    this.pending = new Map();
    this.nextId = 1;
    this.rl.on("line", (line) => {
      let msg;
      try { msg = JSON.parse(line); } catch { return; }
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      msg.ok ? p.resolve(msg.value) : p.reject(new Error(msg.error));
    });
  }

  request(msg) {
    return new Promise((resolve, reject) => {
      msg.id = this.nextId++;
      this.pending.set(msg.id, { resolve, reject });
      this.proc.stdin.write(JSON.stringify(msg) + "\n");
    });
  }

  wrap(value, lang) {
    if (value && value.__sym__ === "handle") return this.handleProxy(value);
    if (value && value.__sym__ === "module") return this.moduleProxy(value);
    return value;
  }

  moduleProxy(mod, chain = "") {
    const client = this;
    const fn = async (...args) => {   // bare call on a class = construct
      const target = chain ? `${mod.target}.${chain}` : mod.target;
      return client.wrap(await client.request(
        { op: "new", lang: mod.lang, target, args }));
    };
    return new Proxy(fn, {
      get(_, prop) {
        if (typeof prop !== "string" || prop === "then") return undefined;
        if (prop === "new") return fn;
        const next = chain ? `${chain}.${prop}` : prop;
        const leaf = async (...args) => client.wrap(await client.request(
          { op: "call", lang: mod.lang, target: `${mod.target}.${next}`, args }));
        return new Proxy(leaf, {
          get: (_, p2) => (p2 === "new"
            ? async (...args) => client.wrap(await client.request(
                { op: "new", lang: mod.lang, target: `${mod.target}.${next}`, args }))
            : client.moduleProxy(mod, next)[p2]),
        });
      },
    });
  }

  handleProxy(tag) {
    const client = this;
    return new Proxy({ __handle: tag }, {
      get(t, prop) {
        if (prop === "__handle" || typeof prop !== "string" || prop === "then")
          return t[prop];
        return async (...args) => client.wrap(await client.request(
          { op: "hcall", lang: tag.runtime, handle: tag.id, method: prop,
            args: args.map(a => a && a.__handle ? a.__handle : a) }));
      },
    });
  }

  java(t)  { return this.request({ op: "import", lang: "java", target: t }).then(v => this.wrap(v)); }
  jsMod(t) { return this.request({ op: "import", lang: "js", target: t }).then(v => this.wrap(v)); }
  php(t)   { return this.request({ op: "import", lang: "php", target: t }).then(v => this.wrap(v)); }
  ruby(t)  { return this.request({ op: "import", lang: "ruby", target: t }).then(v => this.wrap(v)); }
  r(t)     { return this.request({ op: "import", lang: "r", target: t }).then(v => this.wrap(v)); }
  perl(t)  { return this.request({ op: "import", lang: "perl", target: t }).then(v => this.wrap(v)); }

  call(lang, target, args = []) { return this.request({ op: "call", lang, target, args }); }
  block(lang, code) { return this.request({ op: "exec", lang, code }); }
  get(key) { return this.request({ op: "get", key }); }
  set(key, value) { return this.request({ op: "set", key, value }); }
  close() { return this.request({ op: "shutdown" }).finally(() => this.proc.kill()); }
}

export const sym = new SymClient();

// ── selftest: node as consumer against every provider ───────
import { pathToFileURL as _pfu } from "node:url";
if (process.argv[1] && import.meta.url === _pfu(process.argv[1]).href) {
  const ok = (name, passed) =>
    console.log(`  ${passed ? "✅" : "❌"} node → ${name}`);
  ok("java", (await sym.call("java", "java.lang.Math.pow", [2, 5])) === 32);
  ok("js", (await sym.call("js", "Math.max", [3, 9, 2])) === 9);
  ok("python", (await sym.call("python", "math.sqrt", [81])) === 9);
  ok("php", (await sym.call("php", "strtoupper", ["sym"])) === "SYM");
  ok("ruby", (await sym.call("ruby", "Math.sqrt", [144])) === 12);
  ok("r", (await sym.request({ op: "call", lang: "r", target: "mean", args: [[1, 2, 3, 4, 5]] })) === 3);
  await sym.request({ op: "import", lang: "perl", target: "POSIX" });
  ok("perl", (await sym.call("perl", "POSIX.floor", [3.7])) === 3);
  const c = await sym.request({ op: "call", lang: "c", target: "m.sqrt", args: [2.0], ret: "double", argtypes: ["double"] });
  ok("c", Math.abs(c - 1.41421) < 0.001);
  const AL = await sym.java("java.util.ArrayList");
  const lst = await AL.new();
  await lst.add("from node");
  ok("java live object", (await lst.size()) === 1);
  await sym.close();
  console.log("MATRIX_ROW_OK node");
  process.exit(0);
}
