// sym — the SymBridge client for JAVA.
//
//     Sym sym = new Sym();
//     double v = ((Number) sym.call("python", "math.sqrt", 81)).doubleValue();
//     Sym.Handle df = sym.imp("python", "statistics");   // Java importing Python
//
// Yes: a Java program consuming Python, R, Ruby, PHP libraries.
// Uses the same zero-dependency mini-JSON as the worker.

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class Sym {
    private final Process proc;
    private final BufferedReader in;
    private final Writer out;
    private int id = 0;

    public Sym() throws IOException {
        String root = findRoot();
        proc = new ProcessBuilder("python3", root + "/bridge/stdio_host.py")
                .redirectErrorStream(false).start();
        in = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8));
        out = new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8);
    }

    static String findRoot() {
        String env = System.getenv("SYM_HOME");
        if (env != null && new File(env, "bridge").isDirectory()) return env;
        File home = new File(System.getProperty("user.home"), ".sym");
        if (new File(home, "bridge").isDirectory()) return home.getPath();
        try {
            Process p = new ProcessBuilder("python3", "-c",
                "import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))")
                .start();
            String core = new String(p.getInputStream().readAllBytes(),
                StandardCharsets.UTF_8).trim();
            if (!core.isEmpty() && new File(core, "bridge").isDirectory()) return core;
        } catch (Exception ignored) { }
        throw new RuntimeException("sym: no Sym found (pip install sym-lang, or set SYM_HOME)");
    }

    @SuppressWarnings("unchecked")
    public synchronized Object request(Map<String, Object> msg) {
        try {
            msg.put("id", ++id);
            out.write(Json.write(msg) + "\n");
            out.flush();
            String line;
            while ((line = in.readLine()) != null) {
                Map<String, Object> resp;
                try { resp = (Map<String, Object>) Json.parse(line); }
                catch (Exception e) { continue; }
                Object rid = resp.get("id");
                if (!(rid instanceof Number) || ((Number) rid).intValue() != id) continue;
                if (!Boolean.TRUE.equals(resp.get("ok")))
                    throw new RuntimeException("sym: " + resp.get("error"));
                return wrap(resp.get("value"));
            }
            throw new RuntimeException("sym host died");
        } catch (IOException e) { throw new RuntimeException(e); }
    }

    @SuppressWarnings("unchecked")
    Object wrap(Object v) {
        if (v instanceof Map && "handle".equals(((Map<String, Object>) v).get("__sym__")))
            return new Handle(this, (Map<String, Object>) v);
        return v;
    }

    static List<Object> unwrap(Object[] args) {
        List<Object> out = new ArrayList<>();
        for (Object a : args) out.add(a instanceof Handle ? ((Handle) a).tag : a);
        return out;
    }

    private Map<String, Object> msg(String op, String lang) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", op); m.put("lang", lang);
        return m;
    }

    public Object imp(String lang, String target) {
        Map<String, Object> m = msg("import", lang); m.put("target", target);
        return request(m);
    }
    public Object call(String lang, String target, Object... args) {
        Map<String, Object> m = msg("call", lang); m.put("target", target);
        m.put("args", unwrap(args));
        return request(m);
    }
    public Object newObj(String lang, String target, Object... args) {
        Map<String, Object> m = msg("new", lang); m.put("target", target);
        m.put("args", unwrap(args));
        return request(m);
    }
    public Object block(String lang, String code) {
        Map<String, Object> m = msg("exec", lang); m.put("code", code);
        return request(m);
    }
    public Object cCall(String libFn, String ret, List<String> argtypes, Object... args) {
        Map<String, Object> m = msg("call", "c"); m.put("target", libFn);
        m.put("args", unwrap(args)); m.put("ret", ret); m.put("argtypes", argtypes);
        return request(m);
    }
    public void close() {
        try { request(msg("shutdown", "")); } catch (Exception ignored) {}
        proc.destroy();
    }

    public static class Handle {
        final Sym sym; final Map<String, Object> tag;
        Handle(Sym s, Map<String, Object> t) { sym = s; tag = t; }
        public Object call(String method, Object... args) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("op", "hcall"); m.put("lang", tag.get("runtime"));
            m.put("handle", tag.get("id")); m.put("method", method);
            m.put("args", unwrap(args));
            return sym.request(m);
        }
        public String toString() { return "<sym handle " + tag.get("runtime") + "#" + tag.get("id") + ">"; }
    }

    // ── selftest ─────────────────────────────────────────────
    public static void main(String[] a) throws Exception {
        Sym sym = new Sym();
        check("java",   32.0 == num(sym.call("java", "java.lang.Math.pow", 2, 5)));
        check("js",     9.0 == num(sym.call("js", "Math.max", 3, 9, 2)));
        check("python", 9.0 == num(sym.call("python", "math.sqrt", 81)));
        check("php",    "SYM".equals(sym.call("php", "strtoupper", "sym")));
        check("ruby",   12.0 == num(sym.call("ruby", "Math.sqrt", 144)));
        check("r",      3.0 == num(sym.call("r", "mean", Arrays.asList(1, 2, 3, 4, 5))));
        sym.imp("perl", "POSIX");
        check("perl",   3.0 == num(sym.call("perl", "POSIX.floor", 3.7)));
        check("c",      Math.abs(num(sym.cCall("m.sqrt", "double",
                            Arrays.asList("double"), 2.0)) - 1.41421) < 0.001);
        Handle lst = (Handle) sym.newObj("python", "collections.deque");
        lst.call("append", "from java");
        check("python live object", 1.0 == num(sym.call("python", "len", lst)));
        sym.close();
        System.out.println("MATRIX_ROW_OK java");
    }
    static double num(Object o) { return ((Number) o).doubleValue(); }
    static void check(String name, boolean ok) {
        System.out.println("  " + (ok ? "\u2705" : "\u274c") + " java \u2192 " + name);
    }

    // ── mini JSON (same as SymWorker) ────────────────────────
    static class Json {
        static Object parse(String s) { return new Json(s).value(); }
        String s; int i = 0;
        Json(String s) { this.s = s; }
        Object value() {
            ws(); char c = s.charAt(i);
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
                ws(); String k = str(); ws(); i++;
                m.put(k, value()); ws();
                if (s.charAt(i) == ',') { i++; continue; }
                i++; return m;
            }
        }
        List<Object> arr() {
            List<Object> l = new ArrayList<>();
            i++; ws();
            if (s.charAt(i) == ']') { i++; return l; }
            while (true) {
                l.add(value()); ws();
                if (s.charAt(i) == ',') { i++; continue; }
                i++; return l;
            }
        }
        String str() {
            StringBuilder b = new StringBuilder();
            i++;
            while (s.charAt(i) != '"') {
                char c = s.charAt(i++);
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n': b.append('\n'); break;
                        case 't': b.append('\t'); break;
                        case 'r': b.append('\r'); break;
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
            return Long.parseLong(n);
        }
        void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }
        static String write(Object o) { StringBuilder b = new StringBuilder(); w(b, o); return b.toString(); }
        static void w(StringBuilder b, Object o) {
            if (o == null) { b.append("null"); return; }
            if (o instanceof String) { wstr(b, (String) o); return; }
            if (o instanceof Boolean || o instanceof Integer || o instanceof Long) { b.append(o); return; }
            if (o instanceof Number) {
                double d = ((Number) o).doubleValue();
                if (d == Math.floor(d) && !Double.isInfinite(d) && Math.abs(d) < 9e15) b.append((long) d);
                else b.append(d);
                return;
            }
            if (o instanceof Map) {
                b.append('{'); boolean f = true;
                for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                    if (!f) b.append(','); f = false;
                    wstr(b, String.valueOf(e.getKey())); b.append(':'); w(b, e.getValue());
                }
                b.append('}'); return;
            }
            if (o instanceof Collection) {
                b.append('['); boolean f = true;
                for (Object x : (Collection<?>) o) { if (!f) b.append(','); f = false; w(b, x); }
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
                    case '\t': b.append("\\t"); break;
                    default: if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
                }
            }
            b.append('"');
        }
    }
}
