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

// describe: reflection for editors (Velnor's cross-runtime completion)
function sym_php_sig($name, $fn) {
    $ps = [];
    foreach ($fn->getParameters() as $p) {
        $t = $p->hasType() ? $p->getType() . " " : "";
        $ps[] = $t . ($p->isVariadic() ? "..." : "") . "$" . $p->getName() . ($p->isOptional() && !$p->isVariadic() ? " = …" : "");
    }
    $ret = $fn->hasReturnType() ? ": " . $fn->getReturnType() : "";
    return "$name(" . implode(", ", $ps) . ")$ret";
}

function sym_describe($target) {
    try { sym_import($target); } catch (Throwable $e) {}
    $cls = str_replace(".", "\\", $target);
    $members = [];
    if (class_exists($cls) || interface_exists($cls)) {
        $rc = new ReflectionClass($cls);
        foreach ($rc->getMethods(ReflectionMethod::IS_PUBLIC) as $m)
            $members[] = ["name" => $m->getName(), "kind" => $m->isConstructor() ? "constructor" : "method",
                          "signature" => sym_php_sig($m->getName(), $m), "static" => $m->isStatic()];
        foreach ($rc->getProperties(ReflectionProperty::IS_PUBLIC) as $p)
            $members[] = ["name" => $p->getName(), "kind" => "field", "signature" => "$" . $p->getName(), "static" => $p->isStatic()];
        foreach ($rc->getConstants() as $k => $v)
            $members[] = ["name" => $k, "kind" => "constant", "signature" => $k, "static" => true];
        return ["kind" => "class", "name" => $rc->getName(), "members" => $members];
    }
    if (function_exists($cls)) {
        $f = new ReflectionFunction($cls);
        return ["kind" => "function", "name" => $cls, "members" => [["name" => $cls, "kind" => "function",
                "signature" => sym_php_sig($cls, $f), "static" => true]]];
    }
    // a namespace / extension: list the functions and classes under it
    $prefix = strtolower(rtrim($cls, "\\"));
    foreach (get_defined_functions()["internal"] + get_defined_functions()["user"] as $fn) {
        if ($prefix === "" || strpos($fn, $prefix) === 0 || (extension_loaded($target) && in_array($fn, get_extension_funcs($target) ?: [])))
            $members[] = ["name" => $fn, "kind" => "function", "signature" => sym_php_sig($fn, new ReflectionFunction($fn)), "static" => true];
        if (count($members) > 600) break;
    }
    foreach (get_declared_classes() as $c)
        if (stripos($c, $cls) === 0) $members[] = ["name" => $c, "kind" => "class", "signature" => $c, "static" => true];
    if (!$members) throw new Exception("cannot describe '$target'");
    return ["kind" => "namespace", "name" => $target, "members" => $members];
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
            case "describe": ok($id, sym_describe($msg["target"])); break;
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
