<?php
// SymBridge PHP worker — a PHP runtime conducted by Sym.
// JSON lines over stdin/stdout. Talks only to Sym.

error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE & ~E_WARNING);
$GLOBALS['__modules'] = [];
$GLOBALS['__handles'] = [];
$GLOBALS['__hid'] = 0;

function handle_for($obj) {
    $id = ++$GLOBALS['__hid'];
    $GLOBALS['__handles'][$id] = $obj;
    return ["__sym__" => "handle", "runtime" => "php", "id" => $id,
            "type" => get_class($obj)];
}
function deref($args) {
    $out = [];
    foreach ($args as $a) {
        if (is_array($a) && ($a["__sym__"] ?? "") === "handle") {
            $o = $GLOBALS['__handles'][$a["id"]] ?? null;
            if ($o === null) throw new Exception("stale handle #" . $a["id"]);
            $out[] = $o;
        } else $out[] = $a;
    }
    return $out;
}
function to_symbol($v) {
    if (is_object($v) && !($v instanceof stdClass)) return handle_for($v);
    if (is_array($v)) { $o = []; foreach ($v as $k => $x) $o[$k] = to_symbol($x); return $o; }
    return $v;
}

function respond($a) { fwrite(STDOUT, json_encode($a) . "\n"); fflush(STDOUT); }
function ok($id, $value = null, $exports = null) {
    $r = ["id" => $id, "ok" => true, "value" => $value];
    if ($exports !== null) $r["exports"] = $exports;
    respond($r);
}
function fail($id, $e) {
    respond(["id" => $id, "ok" => false,
             "error" => $e->getMessage(), "trace" => $e->getTraceAsString()]);
}

function sym_import($target) {
    // resolution: composer autoload → ./<t>.php → ./phplib/<t>.php → builtin ext
    foreach (["vendor/autoload.php"] as $auto)
        if (file_exists($auto)) require_once $auto;
    foreach (["./$target.php", "./phplib/$target.php"] as $f)
        if (file_exists($f)) { require_once $f; return ["file" => $f]; }
    if (class_exists($target) || function_exists($target) || extension_loaded($target))
        return ["builtin" => $target];
    throw new Exception("cannot import '$target' (no composer entry, ./$target.php, ./phplib/$target.php, class or extension)");
}

function sym_call($target, $args) {
    // "Class::method", "Class.method", plain function, or namespaced
    $target = str_replace(".", "::", $target);
    if (strpos($target, "::") !== false) {
        [$cls, $m] = explode("::", $target, 2);
        if (!class_exists($cls)) throw new Exception("class '$cls' not found");
        return call_user_func_array([$cls, $m], $args);
    }
    if (!is_callable($target)) throw new Exception("'$target' is not callable");
    return call_user_func_array($target, $args);
}

$stdin = fopen("php://stdin", "r");
while (($line = fgets($stdin)) !== false) {
    $line = trim($line);
    if ($line === "") continue;
    $msg = json_decode($line, true);
    if (!$msg) continue;
    $id = $msg["id"] ?? null;
    try {
        switch ($msg["op"] ?? "") {
            case "ping":     ok($id, "pong"); break;
            case "shutdown": ok($id, "bye"); exit(0);
            case "import":   ok($id, sym_import($msg["target"])); break;
            case "call":     ok($id, to_symbol(sym_call($msg["target"], deref($msg["args"] ?? [])))); break;
            case "new": {
                $cls = str_replace(".", "\\", $msg["target"]);
                if (!class_exists($cls)) throw new Exception("class '$cls' not found");
                $obj = new $cls(...deref($msg["args"] ?? []));
                ok($id, handle_for($obj)); break;
            }
            case "hcall": {
                $obj = $GLOBALS['__handles'][$msg["handle"]] ?? null;
                if ($obj === null) throw new Exception("stale handle #" . $msg["handle"]);
                $m = $msg["method"];
                ok($id, to_symbol($obj->$m(...deref($msg["args"] ?? [])))); break;
            }
            case "free": unset($GLOBALS['__handles'][$msg["handle"]]); ok($id, true); break;
            case "exec":
                $sym = $msg["env"] ?? [];
                $__code = $msg["code"];
                $value = (function () use (&$sym, $__code) {
                    return eval($__code);
                })();
                ok($id, $value, $sym);
                break;
            default: throw new Exception("unknown op");
        }
    } catch (Throwable $e) { fail($id, $e); }
}
