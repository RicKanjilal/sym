// SymBridge Java worker — a JVM conducted by Sym.
// Reads Symbol Object requests (JSON lines) on stdin, answers on stdout.
// Zero dependencies: ships its own minimal JSON codec.
//
//   import : load a class (compiling .java source on the fly if needed)
//   call   : invoke a static method via reflection
//   exec   : compile & run a  java> { ... }  block (javax.tools)

import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import javax.tools.*;

public class SymWorker {

    static final Map<String, Class<?>> LOADED = new HashMap<>();
    static final Map<Long, Object> HANDLES = new HashMap<>();
    static long handleCounter = 0;
    static int execCounter = 0;

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(
                new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintStream out = new PrintStream(System.out, true, "UTF-8");
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) continue;
            Object idObj = null;
            try {
                Map<String, Object> msg = asMap(Json.parse(line));
                idObj = msg.get("id");
                String op = (String) msg.get("op");
                switch (op == null ? "" : op) {
                    case "ping":     out.println(ok(idObj, "pong", null)); break;
                    case "shutdown": out.println(ok(idObj, "bye", null)); return;
                    case "import":   out.println(ok(idObj, doImport((String) msg.get("target")), null)); break;
                    case "new":      out.println(ok(idObj, doNew((String) msg.get("target"),
                                        asList(msg.get("args"))), null)); break;
                    case "call":
                        if (msg.containsKey("handle"))
                            out.println(ok(idObj, doHandleCall(((Number) msg.get("handle")).longValue(),
                                (String) msg.get("method"), asList(msg.get("args"))), null));
                        else
                            out.println(ok(idObj, doCall((String) msg.get("target"),
                                asList(msg.get("args"))), null));
                        break;
                    case "index": {
                        Object self = HANDLES.get(((Number) msg.get("handle")).longValue());
                        if (self == null) throw new RuntimeException("stale handle");
                        Object key = derefArgs(asList(msg.get("args"))).get(0);
                        Object val;
                        if (self instanceof Map) val = ((Map<?, ?>) self).get(key);
                        else if (self instanceof List) val = ((List<?>) self).get(((Number) key).intValue());
                        else if (self.getClass().isArray()) val = java.lang.reflect.Array.get(self, ((Number) key).intValue());
                        else throw new RuntimeException("not indexable: " + self.getClass().getName());
                        out.println(ok(idObj, toSymbol(val), null));
                        break;
                    }
                    case "free":     HANDLES.remove(((Number) msg.get("handle")).longValue());
                                     out.println(ok(idObj, true, null)); break;
                    case "stats": {
                        Map<String, Object> st = new LinkedHashMap<>();
                        st.put("handles", HANDLES.size());
                        out.println(ok(idObj, st, null)); break;
                    }
                    case "resolve":  out.println(ok(idObj, doResolve((String) msg.get("target")), null)); break;
                    case "exec": {
                        Map<String, Object> env = asMap(msg.getOrDefault("env", new HashMap<>()));
                        Object val = doExec((String) msg.get("code"), env);
                        out.println(okWithExports(idObj, val, env));
                        break;
                    }
                    default: throw new RuntimeException("unknown op '" + op + "'");
                }
            } catch (Throwable t) {
                StringWriter sw = new StringWriter();
                t.printStackTrace(new PrintWriter(sw));
                Map<String, Object> resp = new LinkedHashMap<>();
                resp.put("id", idObj); resp.put("ok", false);
                resp.put("error", t.getClass().getSimpleName() + ": " + t.getMessage());
                resp.put("trace", sw.toString());
                out.println(Json.write(resp));
            }
        }
    }

    // ── ops ──────────────────────────────────────────────────
    // Resolve a dotted path: class? nested class? static field / enum constant?
    // "org.apache.lucene.document.Field.Store.YES"
    //   -> class Field$Store -> static field YES -> its value
    static Object doResolve(String target) throws Exception {
        String[] parts = target.split("\\.");
        // try longest class name first, converting trailing dots to $ for nesting
        for (int i = parts.length; i >= 1; i--) {
            for (int nest = 0; nest <= Math.min(2, parts.length - i); nest++) {
                StringBuilder name = new StringBuilder(String.join(".",
                        Arrays.copyOfRange(parts, 0, i)));
                for (int k = 0; k < nest; k++) name.append('$').append(parts[i + k]);
                Class<?> cls;
                try { cls = loadClass(name.toString()); }
                catch (Throwable t) { continue; }
                int consumed = i + nest;
                Object current = null;
                Class<?> curCls = cls;
                // walk remaining parts as static fields (enum constants are fields)
                for (int p = consumed; p < parts.length; p++) {
                    try {
                        java.lang.reflect.Field f = curCls.getField(parts[p]);
                        current = f.get(current);
                        curCls = current == null ? Object.class : current.getClass();
                    } catch (NoSuchFieldException e) {
                        // maybe deeper nesting: Field.Store as nested class
                        try {
                            curCls = Class.forName(curCls.getName() + "$" + parts[p]);
                            current = null;
                            continue;
                        } catch (ClassNotFoundException e2) {
                            Map<String, Object> miss = new LinkedHashMap<>();
                            miss.put("kind", "none");
                            miss.put("error", "no field/class '" + parts[p]
                                    + "' on " + curCls.getName());
                            return miss;
                        }
                    }
                }
                Map<String, Object> res = new LinkedHashMap<>();
                if (parts.length == consumed && current == null) {
                    res.put("kind", "class");
                    res.put("name", cls.getName());
                    LOADED.put(target, cls);
                } else if (current == null) {
                    res.put("kind", "class");
                    res.put("name", curCls.getName());
                    LOADED.put(target, curCls);
                } else {
                    res.put("kind", "value");
                    res.put("value", toSymbol(current));
                }
                return res;
            }
        }
        Map<String, Object> res = new LinkedHashMap<>();
        res.put("kind", "package");
        return res;
    }

    static Object doImport(String target) throws Exception {
        Class<?> cls = loadClass(target);
        LOADED.put(target, cls);
        List<String> methods = new ArrayList<>();
        for (Method m : cls.getDeclaredMethods())
            if (Modifier.isPublic(m.getModifiers())) methods.add(m.getName());
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("class", cls.getName());
        meta.put("methods", methods);
        return meta;
    }

    static Object doCall(String target, List<Object> args) throws Exception {
        int split = target.lastIndexOf('.');
        if (split < 0) throw new RuntimeException("call target needs Class.method: " + target);
        String clsName = target.substring(0, split);
        String method = target.substring(split + 1);
        Class<?> cls = LOADED.containsKey(clsName) ? LOADED.get(clsName) : loadClass(clsName);
        args = derefArgs(args);
        Method best = pickMethod(cls, method, args, true);
        if (best == null)
            throw new RuntimeException("no static method " + method + "/" + args.size()
                    + " on " + cls.getName() + " (for instance methods, construct first: Class())");
        return toSymbol(invoke(best, null, args));
    }

    static Object doNew(String target, List<Object> args) throws Exception {
        Class<?> cls = LOADED.containsKey(target) ? LOADED.get(target) : loadClass(target);
        args = derefArgs(args);
        Constructor<?> best = null; int bestScore = Integer.MAX_VALUE;
        for (Constructor<?> c : cls.getConstructors()) {
            if (c.getParameterCount() != args.size()) continue;
            int s = score(c.getParameterTypes(), args);
            if (s < bestScore) { bestScore = s; best = c; }
        }
        if (best == null)
            throw new RuntimeException("no constructor " + target + "/" + args.size());
        Object[] coerced = coerceAll(best.getParameterTypes(), args);
        Object obj = best.newInstance(coerced);
        return handleFor(obj);
    }

    static Object doHandleCall(long handle, String method, List<Object> args) throws Exception {
        Object self = HANDLES.get(handle);
        if (self == null) throw new RuntimeException("stale handle #" + handle);
        args = derefArgs(args);
        Method best = pickMethod(self.getClass(), method, args, false);
        if (best == null && args.isEmpty()) {
            // no such method — try a public FIELD (TopDocs.scoreDocs etc.)
            try {
                java.lang.reflect.Field f = self.getClass().getField(method);
                return toSymbol(f.get(self));
            } catch (NoSuchFieldException ignored) { }
        }
        if (best == null)
            throw new RuntimeException("no method " + method + "/" + args.size()
                    + " on " + self.getClass().getName());
        return toSymbol(invoke(best, self, args));
    }

    // ── overload resolution: score candidates by conversion cost ──
    static Method pickMethod(Class<?> cls, String name, List<Object> args, boolean staticOnly) {
        Method best = null; int bestScore = Integer.MAX_VALUE;
        for (Method m : cls.getMethods()) {
            if (!m.getName().equals(name) || m.getParameterCount() != args.size()) continue;
            if (staticOnly && !Modifier.isStatic(m.getModifiers())) continue;
            int s = score(m.getParameterTypes(), args);
            if (s < bestScore) { bestScore = s; best = m; }
        }
        return bestScore == Integer.MAX_VALUE ? null : best;
    }

    static int score(Class<?>[] types, List<Object> args) {
        int total = 0;
        for (int i = 0; i < types.length; i++) {
            int s = scoreOne(args.get(i), types[i]);
            if (s < 0) return Integer.MAX_VALUE;
            total += s;
        }
        return total;
    }

    // 0 = exact, higher = costlier conversion, -1 = impossible
    static int scoreOne(Object a, Class<?> t) {
        if (a == null) return t.isPrimitive() ? -1 : 1;
        if (a instanceof Long) {
            if (t == long.class || t == Long.class) return 0;
            if (t == int.class || t == Integer.class) return 1;
            if (t == double.class || t == Double.class) return 2;
            if (t == float.class || t == Float.class) return 3;
            if (t == short.class || t == byte.class) return 4;
            if (t == Object.class || t == Number.class) return 5;
            return -1;
        }
        if (a instanceof Double) {
            if (t == double.class || t == Double.class) return 0;
            if (t == float.class || t == Float.class) return 1;
            if (t == Object.class || t == Number.class) return 5;
            return -1;
        }
        if (a instanceof Boolean)
            return (t == boolean.class || t == Boolean.class) ? 0 : (t == Object.class ? 5 : -1);
        if (a instanceof String)
            return t == String.class ? 0 : (t == CharSequence.class ? 1 : (t == Object.class ? 5 : -1));
        if (a instanceof List)
            return t.isAssignableFrom(List.class) ? 0 : (t == Object.class ? 5 : -1);
        if (a instanceof Map)
            return t.isAssignableFrom(Map.class) ? 0 : (t == Object.class ? 5 : -1);
        return t.isInstance(a) ? 0 : (t == Object.class ? 5 : -1);
    }

    static Object[] coerceAll(Class<?>[] types, List<Object> args) {
        Object[] out = new Object[args.size()];
        for (int i = 0; i < args.size(); i++) out[i] = coerce(args.get(i), types[i]);
        return out;
    }

    static Object invoke(Method m, Object self, List<Object> args) throws Exception {
        return m.invoke(self, coerceAll(m.getParameterTypes(), args));
    }

    // ── handle plumbing ──────────────────────────────────────
    static Map<String, Object> handleFor(Object obj) {
        long id = ++handleCounter;
        HANDLES.put(id, obj);
        Map<String, Object> tag = new LinkedHashMap<>();
        tag.put("__sym__", "handle");
        tag.put("runtime", "java");
        tag.put("id", id);
        tag.put("type", obj.getClass().getName());
        return tag;
    }

    @SuppressWarnings("unchecked")
    static List<Object> derefArgs(List<Object> args) {
        List<Object> out = new ArrayList<>();
        for (Object a : args) {
            if (a instanceof Map && "handle".equals(((Map<String, Object>) a).get("__sym__"))) {
                long id = ((Number) ((Map<String, Object>) a).get("id")).longValue();
                Object o = HANDLES.get(id);
                if (o == null) throw new RuntimeException("stale handle #" + id);
                out.add(o);
            } else out.add(a);
        }
        return out;
    }

    static Object doExec(String code, Map<String, Object> env) throws Exception {
        // Wrap block in a class:  Object run(Map<String,Object> sym) { ... }
        String cls = "SymExec" + (++execCounter);
        String src = "import java.util.*;\n"
                + "public class " + cls + " {\n"
                + "  @SuppressWarnings(\"unchecked\")\n"
                + "  public static Object run(Map<String,Object> sym) throws Exception {\n"
                + code + "\n"
                + "    return null;\n"
                + "  }\n}\n";
        Path dir = Files.createTempDirectory("symexec");
        Path file = dir.resolve(cls + ".java");
        Files.writeString(file, src);
        compile(file, dir);
        Class<?> compiled = loadFrom(dir, cls);
        return toSymbol(compiled.getMethod("run", Map.class).invoke(null, env));
    }

    // ── class loading + on-the-fly compilation ───────────────
    static Class<?> loadClass(String name) throws Exception {
        try { return Class.forName(name); }
        catch (ClassNotFoundException e) { /* try compiling source */ }
        // look for source file matching package path in cwd / ./javalib
        String rel = name.replace('.', File.separatorChar) + ".java";
        for (String root : new String[]{".", "javalib"}) {
            Path src = Paths.get(root, rel);
            if (Files.exists(src)) {
                Path outDir = Paths.get(root);
                compile(src, outDir);
                return loadFrom(outDir, name);
            }
        }
        throw new ClassNotFoundException(
                name + " (not on classpath, no source at ./" + rel + " or ./javalib/" + rel + ")");
    }

    static void compile(Path source, Path outDir) throws Exception {
        JavaCompiler jc = ToolProvider.getSystemJavaCompiler();
        if (jc == null) throw new RuntimeException("JDK required (JRE has no compiler)");
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        int rc = jc.run(null, null, err,
                "-cp", System.getProperty("java.class.path"),
                "-d", outDir.toString(), source.toString());
        if (rc != 0) throw new RuntimeException("javac failed:\n" + err.toString(StandardCharsets.UTF_8));
    }

    static Class<?> loadFrom(Path dir, String name) throws Exception {
        java.net.URLClassLoader cl = new java.net.URLClassLoader(
                new java.net.URL[]{dir.toUri().toURL()}, SymWorker.class.getClassLoader());
        return Class.forName(name, true, cl);
    }

    // ── Symbol Object conversion ─────────────────────────────
    static Object coerce(Object v, Class<?> t) {
        if (v == null) return null;
        if (t == int.class || t == Integer.class) return ((Number) v).intValue();
        if (t == long.class || t == Long.class) return ((Number) v).longValue();
        if (t == double.class || t == Double.class) return ((Number) v).doubleValue();
        if (t == float.class || t == Float.class) return ((Number) v).floatValue();
        if (t == boolean.class || t == Boolean.class) return v;
        if (t == String.class) return String.valueOf(v);
        return v; // List / Map pass through
    }

    static Object toSymbol(Object v) {
        if (v == null || v instanceof Boolean || v instanceof Number || v instanceof String) return v;
        if (v instanceof byte[]) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("__sym__", "bytes");
            m.put("b64", Base64.getEncoder().encodeToString((byte[]) v));
            return m;
        }
        if (v instanceof Collection) {
            List<Object> out = new ArrayList<>();
            for (Object x : (Collection<?>) v) out.add(toSymbol(x));
            return out;
        }
        if (v instanceof Object[]) {
            List<Object> out = new ArrayList<>();
            for (Object x : (Object[]) v) out.add(toSymbol(x));
            return out;
        }
        if (v instanceof Map) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet())
                out.put(String.valueOf(e.getKey()), toSymbol(e.getValue()));
            return out;
        }
        return handleFor(v);  // live object → handle, calls route back
    }

    // ── protocol helpers ─────────────────────────────────────
    static String ok(Object id, Object value, Map<String, Object> exports) {
        Map<String, Object> resp = new LinkedHashMap<>();
        resp.put("id", id); resp.put("ok", true); resp.put("value", value);
        if (exports != null) resp.put("exports", exports);
        return Json.write(resp);
    }

    static String okWithExports(Object id, Object value, Map<String, Object> env) {
        Map<String, Object> exports = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : env.entrySet())
            exports.put(e.getKey(), toSymbol(e.getValue()));
        return ok(id, value, exports);
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object o) { return (Map<String, Object>) o; }
    @SuppressWarnings("unchecked")
    static List<Object> asList(Object o) { return o == null ? new ArrayList<>() : (List<Object>) o; }

    // ── minimal JSON codec (zero deps) ───────────────────────
    static class Json {
        static Object parse(String s) { return new Json(s).value(); }
        String s; int i = 0;
        Json(String s) { this.s = s; }
        Object value() {
            ws();
            char c = s.charAt(i);
            if (c == '{') return obj();
            if (c == '[') return arr();
            if (c == '"') return str();
            if (c == 't') { i += 4; return Boolean.TRUE; }
            if (c == 'f') { i += 5; return Boolean.FALSE; }
            if (c == 'n') { i += 4; return null; }
            return num();
        }
        Map<String, Object> obj() {
            Map<String, Object> m = new LinkedHashMap<>();
            i++; ws();
            if (s.charAt(i) == '}') { i++; return m; }
            while (true) {
                ws(); String k = str(); ws(); i++; // ':'
                m.put(k, value()); ws();
                if (s.charAt(i) == ',') { i++; continue; }
                i++; return m; // '}'
            }
        }
        List<Object> arr() {
            List<Object> l = new ArrayList<>();
            i++; ws();
            if (s.charAt(i) == ']') { i++; return l; }
            while (true) {
                l.add(value()); ws();
                if (s.charAt(i) == ',') { i++; continue; }
                i++; return l; // ']'
            }
        }
        String str() {
            StringBuilder b = new StringBuilder();
            i++; // opening quote
            while (s.charAt(i) != '"') {
                char c = s.charAt(i++);
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n': b.append('\n'); break;
                        case 't': b.append('\t'); break;
                        case 'r': b.append('\r'); break;
                        case 'b': b.append('\b'); break;
                        case 'f': b.append('\f'); break;
                        case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                        default: b.append(e);
                    }
                } else b.append(c);
            }
            i++; return b.toString();
        }
        Object num() {
            int start = i;
            while (i < s.length() && "-+.eE0123456789".indexOf(s.charAt(i)) >= 0) i++;
            String n = s.substring(start, i);
            if (n.contains(".") || n.contains("e") || n.contains("E")) return Double.parseDouble(n);
            long l = Long.parseLong(n);
            return l; 
        }
        void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        static String write(Object o) {
            StringBuilder b = new StringBuilder();
            w(b, o);
            return b.toString();
        }
        static void w(StringBuilder b, Object o) {
            if (o == null) { b.append("null"); return; }
            if (o instanceof String) { wstr(b, (String) o); return; }
            if (o instanceof Boolean || o instanceof Integer || o instanceof Long) { b.append(o); return; }
            if (o instanceof Number) {
                double d = ((Number) o).doubleValue();
                if (d == Math.floor(d) && !Double.isInfinite(d) && Math.abs(d) < 9e15)
                    b.append((long) d);
                else b.append(d);
                return;
            }
            if (o instanceof Map) {
                b.append('{'); boolean first = true;
                for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                    if (!first) b.append(','); first = false;
                    wstr(b, String.valueOf(e.getKey())); b.append(':'); w(b, e.getValue());
                }
                b.append('}'); return;
            }
            if (o instanceof Collection) {
                b.append('['); boolean first = true;
                for (Object x : (Collection<?>) o) {
                    if (!first) b.append(','); first = false;
                    w(b, x);
                }
                b.append(']'); return;
            }
            wstr(b, String.valueOf(o));
        }
        static void wstr(StringBuilder b, String s) {
            b.append('"');
            for (char c : s.toCharArray()) {
                switch (c) {
                    case '"': b.append("\\\""); break;
                    case '\\': b.append("\\\\"); break;
                    case '\n': b.append("\\n"); break;
                    case '\r': b.append("\\r"); break;
                    case '\t': b.append("\\t"); break;
                    default:
                        if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                        else b.append(c);
                }
            }
            b.append('"');
        }
    }
}
