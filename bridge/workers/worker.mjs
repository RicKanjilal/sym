// SymBridge JS worker — a Node runtime conducted by Sym.
// Speaks Symbol Objects (JSON lines) over stdin/stdout. Never talks to
// other languages — only to Sym.

import readline from "node:readline";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require_ = createRequire(path.join(process.cwd(), "package.json"));
const modules = {};   // imported modules by name
const HANDLES = new Map(); let handleCounter = 0;

function handleFor(obj) {
  const id = ++handleCounter;
  HANDLES.set(id, obj);
  return { __sym__: "handle", runtime: "js", id,
           type: obj?.constructor?.name ?? typeof obj };
}
function deref(args) {
  return (args || []).map(a =>
    (a && typeof a === "object" && a.__sym__ === "handle")
      ? (HANDLES.has(a.id) ? HANDLES.get(a.id) : (() => { throw new Error(`stale handle #${a.id}`); })())
      : a);
}
const rl = readline.createInterface({ input: process.stdin, terminal: false });

function send(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }
function ok(id, value, exports) { send({ id, ok: true, value: value ?? null, exports: exports ?? null }); }
function fail(id, e) { send({ id, ok: false, error: String(e && e.message || e), trace: String(e && e.stack || "") }); }

async function importModule(target) {
  // try: registered name → node_modules → local file → builtin
  try { return require_(target); } catch (_) {}
  const candidates = [target, `./${target}.mjs`, `./${target}.js`, `./jslib/${target}.mjs`, `./jslib/${target}.js`];
  for (const c of candidates) {
    try { return (await import(c.startsWith(".") ? pathToFileURL(path.resolve(c)).href : c)); } catch (_) {}
  }
  throw new Error(`cannot import '${target}' (tried npm, ./${target}.js, ./jslib/)`);
}

// ── describe: introspection for editors (Velnor's cross-runtime completion) ──
function jsSignature(name, fn) {
  const src = Function.prototype.toString.call(fn);
  if (/\{\s*\[native code\]\s*\}/.test(src)) return `${name}(${Array.from({ length: fn.length }, (_, i) => "arg" + i).join(", ")})`;
  const m = src.match(/^[^(]*\(([^)]*)\)/) || src.match(/^\s*([\w$]+)\s*=>/);
  const params = m ? m[1].replace(/\s+/g, " ").trim() : "";
  return `${name}(${params})`;
}
function describeValue(name, value) {
  const members = [];
  const seen = new Set();
  const add = (key, v, isStatic) => {
    if (seen.has(key) || key === "constructor") return; seen.add(key);
    const t = typeof v;
    if (t === "function") {
      const isClass = /^class[\s{]/.test(Function.prototype.toString.call(v));
      members.push({ name: key, kind: isClass ? "class" : "function", signature: jsSignature(key, v), static: isStatic });
    } else members.push({ name: key, kind: t === "object" && v !== null ? "namespace" : "constant",
                          signature: `${key}: ${v === null ? "null" : Array.isArray(v) ? "array" : t}`, static: isStatic });
  };
  const own = (obj, isStatic) => {
    for (const key of Object.getOwnPropertyNames(obj)) {
      if (/^(length|name|prototype|caller|arguments)$/.test(key) && typeof obj === "function") continue;
      let v; try { v = obj[key]; } catch { continue; }
      add(key, v, isStatic);
    }
  };
  if (value && (typeof value === "object" || typeof value === "function")) {
    own(value, true);
    if (typeof value === "function" && value.prototype) {
      let proto = value.prototype;
      while (proto && proto !== Object.prototype) { own(proto, false); proto = Object.getPrototypeOf(proto); }
    }
    if (value.default && typeof value.default === "object") own(value.default, true);
  }
  const kind = typeof value === "function" ? (/^class[\s{]/.test(Function.prototype.toString.call(value)) ? "class" : "function") : "module";
  return { kind, name, members: members.slice(0, 600) };
}
async function describe(target) {
  const parts = target.split(".");
  for (let i = parts.length; i > 0; i--) {
    const head = parts.slice(0, i).join(".");
    let root = modules[head];
    if (!root) { try { root = await importModule(head); modules[head] = root; } catch { continue; } }
    const rest = parts.slice(i).join(".");
    return describeValue(target, rest ? resolvePath(root, rest) : root);
  }
  if (target in globalThis) return describeValue(target, globalThis[target]);
  throw new Error(`cannot describe '${target}'`);
}

function resolvePath(root, dotted) {
  let obj = root;
  for (const part of dotted.split(".")) {
    if (obj == null) throw new Error(`'${dotted}': '${part}' not found`);
    obj = obj[part];
  }
  return obj;
}

rl.on("line", async (line) => {
  line = line.trim();
  if (!line) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  const { id, op } = msg;
  try {
    if (op === "ping") return ok(id, "pong");
    if (op === "shutdown") { ok(id, "bye"); process.exit(0); }

    if (op === "describe") return ok(id, await describe(msg.target));

    if (op === "import") {
      const mod = await importModule(msg.target);
      modules[msg.target] = mod;
      return ok(id, { exports: Object.keys(mod).slice(0, 200) });
    }

    if (op === "new") {
      const parts = msg.target.split(".");
      let mod = null, rest = null;
      for (let i = parts.length; i > 0; i--) {
        const name = parts.slice(0, i).join(".");
        if (modules[name]) { mod = modules[name]; rest = parts.slice(i).join("."); break; }
      }
      if (mod === null) { mod = globalThis; rest = msg.target; }
      const Cls = rest ? resolvePath(mod, rest) : (mod.default ?? mod);
      if (typeof Cls !== "function") throw new Error(`'${msg.target}' is not constructable`);
      const obj = new Cls(...deref(msg.args));
      return ok(id, handleFor(obj));
    }

    if (op === "hcall") {
      const self = HANDLES.get(msg.handle);
      if (self === undefined) throw new Error(`stale handle #${msg.handle}`);
      const fn = self[msg.method];
      if (typeof fn !== "function") throw new Error(`no method '${msg.method}' on handle`);
      let result = fn.apply(self, deref(msg.args));
      if (result && typeof result.then === "function") result = await result;
      return ok(id, toSymbol(result));
    }

    if (op === "free") { HANDLES.delete(msg.handle); return ok(id, true); }

    if (op === "call") {
      // target: "moduleName.path.to.fn" — longest registered module prefix wins
      const parts = msg.target.split(".");
      let mod = null, rest = null;
      for (let i = parts.length; i > 0; i--) {
        const name = parts.slice(0, i).join(".");
        if (modules[name]) { mod = modules[name]; rest = parts.slice(i).join("."); break; }
      }
      if (mod === null) { // maybe a global (Math.max, JSON.parse …)
        mod = globalThis; rest = msg.target;
      }
      const fnParent = rest.includes(".") ? resolvePath(mod, rest.split(".").slice(0, -1).join(".")) : mod;
      const fn = rest ? resolvePath(mod, rest) : (mod.default ?? mod);
      if (typeof fn !== "function") throw new Error(`'${msg.target}' is not a function`);
      let result = fn.apply(fnParent, deref(msg.args));
      if (result && typeof result.then === "function") result = await result;
      return ok(id, toSymbol(result));
    }

    if (op === "exec") {
      // js> { ... } block. `sym` = shared data object; mutations flow back.
      const sym = msg.env || {};
      const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
      const fn = new AsyncFunction("sym", "require", msg.code);
      const value = await fn(sym, require_);
      return ok(id, toSymbol(value), toSymbol(sym));
    }

    throw new Error(`unknown op '${op}'`);
  } catch (e) { fail(id, e); }
});

function toSymbol(v) {
  if (v === undefined) return null;
  if (v === null || ["boolean", "number", "string"].includes(typeof v)) return v;
  if (Array.isArray(v)) return v.map(toSymbol);
  if (v instanceof Uint8Array || Buffer.isBuffer(v))
    return { __sym__: "bytes", b64: Buffer.from(v).toString("base64") };
  if (typeof v === "object") {
    const proto = Object.getPrototypeOf(v);
    if (proto !== null && proto !== Object.prototype) return handleFor(v); // class instance
    // plain object BUT carrying Symbol values (React elements' $$typeof etc.)
    // — JSON can't carry a Symbol; a handle can carry the whole object
    for (const k of Object.keys(v))
      if (typeof v[k] === "symbol") return handleFor(v);
    const out = {};
    for (const k of Object.keys(v)) out[k] = toSymbol(v[k]);
    return out;
  }
  if (typeof v === "function") return handleFor(v);
  return { __sym__: "opaque", repr: String(v), type: typeof v };
}
