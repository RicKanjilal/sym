<?php
// sym — the SymBridge client for PHP.
//
//     require "sym.php";
//     echo Sym::call("python", "math.sqrt", [81]);       // 9 — PHP importing Python
//     $lst = Sym::newObj("java", "java.util.ArrayList"); // live Java object
//     $lst->add("from php");
//     echo $lst->size();

class SymHandle {
    public $tag;
    function __construct($tag) { $this->tag = $tag; }
    function __call($method, $args) {
        return Sym::request([
            "op" => "hcall", "lang" => $this->tag["runtime"],
            "handle" => $this->tag["id"], "method" => $method,
            "args" => Sym::unwrap($args)]);
    }
    function idx($key) {
        return Sym::request(["op" => "index", "lang" => $this->tag["runtime"],
                             "handle" => $this->tag["id"], "args" => [$key]]);
    }
}

class Sym {
    private static $proc = null, $pipes = null, $id = 0;

    private static function root() {
        $env = getenv("SYM_HOME");
        if ($env && is_dir("$env/bridge")) return $env;
        $home = getenv("HOME") . "/.sym";
        if (is_dir("$home/bridge")) return $home;
        $d = __DIR__;
        for ($i = 0; $i < 5; $i++) {
            if (is_dir("$d/bridge")) return $d;
            $d = dirname($d);
        }
        $core = trim(@shell_exec("python3 -c \"import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))\" 2>/dev/null") ?? "");
        if ($core && is_dir("$core/bridge")) return $core;
        throw new Exception("sym: no Sym found (pip install sym-lang, or set SYM_HOME)");
    }

    private static function ensure() {
        if (self::$proc) return;
        $root = self::root();
        self::$proc = proc_open(
            ["python3", "$root/bridge/stdio_host.py"],
            [0 => ["pipe", "r"], 1 => ["pipe", "w"], 2 => STDERR],
            self::$pipes);
        register_shutdown_function(fn() => self::close());
    }

    static function request($msg) {
        self::ensure();
        $msg["id"] = ++self::$id;
        fwrite(self::$pipes[0], json_encode($msg) . "\n");
        fflush(self::$pipes[0]);
        while (($line = fgets(self::$pipes[1])) !== false) {
            $resp = json_decode(trim($line), true);
            if (!$resp || ($resp["id"] ?? null) !== self::$id) continue;
            if (!$resp["ok"]) throw new Exception("sym: " . $resp["error"]);
            return self::wrap($resp["value"]);
        }
        throw new Exception("sym host died");
    }

    static function wrap($v) {
        if (is_array($v) && ($v["__sym__"] ?? "") === "handle") return new SymHandle($v);
        if (is_array($v)) return array_map([self::class, "wrap"], $v);
        return $v;
    }

    static function unwrap($args) {
        return array_map(fn($a) => $a instanceof SymHandle ? $a->tag : $a, $args);
    }

    static function import($lang, $t) { return self::request(["op" => "import", "lang" => $lang, "target" => $t]); }
    static function call($lang, $t, $args = []) { return self::request(["op" => "call", "lang" => $lang, "target" => $t, "args" => self::unwrap($args)]); }
    static function newObj($lang, $t, $args = []) { return self::request(["op" => "new", "lang" => $lang, "target" => $t, "args" => self::unwrap($args)]); }
    static function block($lang, $code) { return self::request(["op" => "exec", "lang" => $lang, "code" => $code]); }
    static function cCall($libFn, $args, $ret = "int", $argtypes = []) { return self::request(["op" => "call", "lang" => "c", "target" => $libFn, "args" => $args, "ret" => $ret, "argtypes" => $argtypes]); }
    static function close() {
        if (!self::$proc) return;
        try { self::request(["op" => "shutdown"]); } catch (Throwable $e) {}
        self::$proc = null;
    }
}

// ── selftest ─────────────────────────────────────────────────
if (realpath($argv[0] ?? "") === __FILE__) {
    $checks = [
        "java"   => fn() => Sym::call("java", "java.lang.Math.pow", [2, 5]) == 32,
        "js"     => fn() => Sym::call("js", "Math.max", [3, 9, 2]) == 9,
        "python" => fn() => Sym::call("python", "math.sqrt", [81]) == 9,
        "php"    => fn() => Sym::call("php", "strtoupper", ["sym"]) === "SYM",
        "ruby"   => fn() => Sym::call("ruby", "Math.sqrt", [144]) == 12,
        "r"      => fn() => Sym::call("r", "mean", [[1, 2, 3, 4, 5]]) == 3,
        "perl"   => function() { Sym::import("perl", "POSIX"); return Sym::call("perl", "POSIX.floor", [3.7]) == 3; },
        "c"      => fn() => abs(Sym::cCall("m.sqrt", [2.0], "double", ["double"]) - 1.41421) < 0.001,
    ];
    foreach ($checks as $name => $fn) echo "  " . ($fn() ? "✅" : "❌") . " php → $name\n";
    $lst = Sym::newObj("java", "java.util.ArrayList");
    $lst->add("from php");
    echo "  " . ($lst->size() == 1 ? "✅" : "❌") . " php → java live object\n";
    Sym::close();
    echo "MATRIX_ROW_OK php\n";
}
