# codegen_js.py - Sym → JavaScript / TypeScript.
# Mirrors codegen_python.py node-for-node, emitting JS (or TS with types).
# This is the frontend/web target: .sym compiles to browser-ready JS.

from .ast_nodes import *

# Sym builtins → JS equivalents
JS_BUILTINS = {
    "print": "console.log",
    "len": "__len",
    "str": "String",
    "int": "Math.trunc",
    "float": "Number",
    "bool": "Boolean",
    "range": "__range",
    "input": "prompt",
    "abs": "Math.abs",
    "min": "Math.min",
    "max": "Math.max",
    "round": "__round",
    "sorted": "__sorted",
    "sum": "__sum",
    "sqrt": "Math.sqrt", "pow": "Math.pow", "floor": "Math.floor",
    "ceil": "Math.ceil", "random": "Math.random",
}

# small JS runtime prelude for the builtins above
JS_PRELUDE = """// ── Sym JS runtime ──
const __len = (x) => (x == null ? 0 : (x.length !== undefined ? x.length : Object.keys(x).length));
const __range = (a, b, step=1) => { if (b===undefined){b=a;a=0;} const o=[]; for(let i=a;i<b;i+=step)o.push(i); return o; };
const __round = (x, n=0) => { const f=10**n; return Math.round(x*f)/f; };
const __sorted = (a, opts={}) => { const c=[...a]; c.sort(opts.key?((x,y)=>opts.key(x)-opts.key(y)):((x,y)=>x>y?1:-1)); if(opts.reverse)c.reverse(); return c; };
const __sum = (a) => a.reduce((s,x)=>s+x,0);
// ───────────────────────────
"""


class JSCodegen:
    def __init__(self, typescript: bool = False):
        self.lines = []
        self.structs = set()
        self.indent = 0
        self.ts = typescript

    def emit(self, line: str = ""):
        self.lines.append("  " * self.indent + line if line else "")

    def emit_blank(self):
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    # ── entry ──────────────────────────────────────────────
    def generate(self, program: Program) -> str:
        self.lines = [JS_PRELUDE]
        self.structs = {st.name for st in program.statements if isinstance(st, StructDef)}
        self.impls = {}
        for st in program.statements:
            if isinstance(st, ImplBlock):
                self.impls.setdefault(st.struct_name, []).extend(st.methods)
        for stmt in program.statements:
            self.gen_statement(stmt)
        return "\n".join(self.lines)

    def gen_statement(self, node: Node):
        if isinstance(node, LetStatement):
            self.gen_let(node)
        elif isinstance(node, AssignStatement):
            t = self.gen_expr(node.target); v = self.gen_expr(node.value)
            self.emit(f"{t} {node.op} {v};")
        elif isinstance(node, FunctionDef):
            self.gen_function(node)
        elif isinstance(node, ReturnStatement):
            if node.value is not None:
                self.emit(f"return {self.gen_expr(node.value)};")
            else:
                self.emit("return;")
        elif isinstance(node, IfStatement):
            self.gen_if(node)
        elif isinstance(node, ForLoop):
            self.gen_for(node)
        elif isinstance(node, WhileLoop):
            self.gen_while(node)
        elif isinstance(node, ImportStatement):
            self.gen_import(node)
        elif isinstance(node, StructDef):
            self.gen_struct(node)
        elif isinstance(node, TryStatement):
            self.gen_try(node)
        elif isinstance(node, ExpressionStatement):
            self.emit(self.gen_expr(node.expr) + ";")
        elif isinstance(node, BreakStatement):
            self.emit("break;")
        elif isinstance(node, ContinueStatement):
            self.emit("continue;")
        elif isinstance(node, DirectiveStatement):
            self.emit(f"// directive: {node.name}")
        # Traits/impls/match: skip or best-effort (JS has no direct match)
        elif isinstance(node, MatchStatement):
            self.gen_match(node)
        elif isinstance(node, ImplBlock):
            pass  # methods injected into their struct's class

    def gen_let(self, node: LetStatement):
        val = self.gen_expr(node.value)
        if self.ts and getattr(node, "type_annotation", None):
            self.emit(f"let {node.name}: {self._ts_type(node.type_annotation)} = {val};")
        else:
            self.emit(f"let {node.name} = {val};")

    def gen_function(self, node: FunctionDef):
        self.emit_blank()
        params = ", ".join(self._gen_param(p) for p in node.params)
        ret = ""
        if self.ts and getattr(node, "return_type", None):
            ret = f": {self._ts_type(node.return_type)}"
        self.emit(f"function {node.name}({params}){ret} {{")
        self.indent += 1
        for stmt in node.body:
            self.gen_statement(stmt)
        self.indent -= 1
        self.emit("}")
        self.emit_blank()

    def _gen_param(self, p: Param) -> str:
        s = p.name
        if self.ts and getattr(p, "type_annotation", None):
            s += f": {self._ts_type(p.type_annotation)}"
        if p.default is not None:
            s += f" = {self.gen_expr(p.default)}"
        return s

    def _ts_type(self, t: str) -> str:
        return {"int": "number", "float": "number", "str": "string",
                "bool": "boolean"}.get(t, "any")

    def gen_if(self, node: IfStatement):
        self.emit(f"if ({self.gen_expr(node.condition)}) {{")
        self.indent += 1
        for s in node.body:
            self.gen_statement(s)
        self.indent -= 1
        for ec in node.elif_clauses:
            self.emit(f"}} else if ({self.gen_expr(ec.condition)}) {{")
            self.indent += 1
            for s in ec.body:
                self.gen_statement(s)
            self.indent -= 1
        if node.else_body:
            self.emit("} else {")
            self.indent += 1
            for s in node.else_body:
                self.gen_statement(s)
            self.indent -= 1
        self.emit("}")

    def gen_for(self, node: ForLoop):
        it = self.gen_expr(node.iterable)
        if node.var2:
            self.emit(f"for (const [{node.var}, {node.var2}] of {it}) {{")
        else:
            self.emit(f"for (const {node.var} of {it}) {{")
        self.indent += 1
        for s in node.body:
            self.gen_statement(s)
        self.indent -= 1
        self.emit("}")

    def gen_while(self, node: WhileLoop):
        self.emit(f"while ({self.gen_expr(node.condition)}) {{")
        self.indent += 1
        for s in node.body:
            self.gen_statement(s)
        self.indent -= 1
        self.emit("}")

    def gen_import(self, node: ImportStatement):
        # js.* imports → real JS import; py.* can't run in browser (skip w/ note)
        mod = node.module or node.from_module or ""
        if mod.startswith("js."):
            real = mod[3:]
            if node.from_module:
                names = ", ".join(node.names)
                self.emit(f"import {{ {names} }} from '{real}';")
            elif node.alias:
                self.emit(f"import * as {node.alias} from '{real}';")
            else:
                self.emit(f"import '{real}';")
        elif mod.startswith("py."):
            self.emit(f"// py import '{mod}' skipped (not available in JS target)")

    def gen_struct(self, node: StructDef):
        # struct → JS class with a constructor
        self.emit_blank()
        self.emit(f"class {node.name} {{")
        self.indent += 1
        fields = [f.name for f in node.fields]
        self.emit(f"constructor({', '.join(fields)}) {{")
        self.indent += 1
        for fn in fields:
            self.emit(f"this.{fn} = {fn};")
        self.indent -= 1
        self.emit("}")
        for m in list(node.methods) + self.impls.get(node.name, []):
            params = ", ".join(self._gen_param(p) for p in m.params if p.name != "self")
            self.emit(f"{m.name}({params}) {{")
            self.indent += 1
            for stmt in m.body:
                self.gen_statement(stmt)
            self.indent -= 1
            self.emit("}")
        self.indent -= 1
        self.emit("}")

    def gen_try(self, node: TryStatement):
        self.emit("try {")
        self.indent += 1
        for s in node.body:
            self.gen_statement(s)
        self.indent -= 1
        cv = node.catch_var or "_e"
        self.emit(f"}} catch ({cv}) {{")
        self.indent += 1
        for s in (node.catch_body or []):
            self.gen_statement(s)
        self.indent -= 1
        self.emit("}")

    def gen_match(self, node: MatchStatement):
        subj = self.gen_expr(node.subject)
        self.emit(f"switch ({subj}) {{")
        self.indent += 1
        for case in node.cases:
            self.emit(f"case {self.gen_expr(case.pattern)}: {{")
            self.indent += 1
            for s in case.body:
                self.gen_statement(s)
            self.emit("break;")
            self.indent -= 1
            self.emit("}")
        self.indent -= 1
        self.emit("}")

    # ── expressions ────────────────────────────────────────
    def gen_expr(self, node: Node) -> str:
        if node is None:
            return "null"
        if isinstance(node, IntLiteral):
            return str(node.value)
        if isinstance(node, FloatLiteral):
            return str(node.value)
        if isinstance(node, StringLiteral):
            return self._js_str(node.value)
        if isinstance(node, FString):
            return self._gen_template(node)
        if isinstance(node, BoolLiteral):
            return "true" if node.value else "false"
        if isinstance(node, NoneLiteral):
            return "null"
        if isinstance(node, Identifier):
            return "this" if node.name == "self" else node.name
        if isinstance(node, ListLiteral):
            return "[" + ", ".join(self.gen_expr(e) for e in node.elements) + "]"
        if isinstance(node, DictLiteral):
            pairs = ", ".join(f"{self.gen_expr(k)}: {self.gen_expr(v)}"
                              for k, v in zip(node.keys, node.values))
            return "{" + pairs + "}"
        if isinstance(node, TupleLiteral):
            return "[" + ", ".join(self.gen_expr(e) for e in node.elements) + "]"
        if isinstance(node, BinaryOp):
            l = self.gen_expr(node.left); r = self.gen_expr(node.right)
            op_map = {"≠": "!==", "≤": "<=", "≥": ">=", "==": "===",
                      "!=": "!==", "and": "&&", "or": "||"}
            op = op_map.get(node.op, node.op)
            return f"({l} {op} {r})"
        if isinstance(node, UnaryOp):
            op = "!" if node.op in ("not", "!") else node.op
            return f"({op}{self.gen_expr(node.operand)})"
        if isinstance(node, FunctionCall):
            return self._gen_call(node)
        if isinstance(node, MethodCall):
            obj = self.gen_expr(node.obj)
            args = ", ".join(self.gen_expr(a) for a in node.args)
            m = self._map_method(node.method)
            return f"{obj}.{m}({args})"
        if isinstance(node, MemberAccess):
            return f"{self.gen_expr(node.obj)}.{node.member}"
        if isinstance(node, IndexAccess):
            return f"{self.gen_expr(node.obj)}[{self.gen_expr(node.index)}]"
        if isinstance(node, SliceAccess):
            obj = self.gen_expr(node.obj)
            start = self.gen_expr(node.start) if node.start else "0"
            if node.stop:
                return f"{obj}.slice({start}, {self.gen_expr(node.stop)})"
            return f"{obj}.slice({start})"
        if isinstance(node, Lambda):
            params = ", ".join(node.params)
            return f"(({params}) => {self.gen_expr(node.body)})"
        if isinstance(node, Ternary):
            return f"({self.gen_expr(node.condition)} ? {self.gen_expr(node.true_val)} : {self.gen_expr(node.false_val)})"
        if isinstance(node, RangeLiteral):
            start = self.gen_expr(node.start); stop = self.gen_expr(node.stop)
            return f"__range({start}, {stop})"
        return "/* unknown expr */null"

    def _map_method(self, m: str) -> str:
        return {"append": "push", "upper": "toUpperCase", "lower": "toLowerCase",
                "strip": "trim", "startswith": "startsWith", "endswith": "endsWith",
                "keys": "__keys", "values": "__values"}.get(m, m)

    def _gen_call(self, node: FunctionCall) -> str:
        name = node.func.name if isinstance(node.func, Identifier) else self.gen_expr(node.func)
        args = ", ".join(self.gen_expr(a) for a in node.args)
        # sort x  → x.sort((a,b)=>a-b)  (in-place, numeric-aware)
        if name == "sort" and len(node.args) == 1:
            a = self.gen_expr(node.args[0])
            return f"{a}.sort((a,b)=>(a>b?1:a<b?-1:0))"
        # map(list, fn) → list.map(fn) ; filter(list, fn) → list.filter(fn)
        if name in ("map", "filter") and len(node.args) == 2:
            coll = self.gen_expr(node.args[0]); fn = self.gen_expr(node.args[1])
            return f"{coll}.{name}({fn})"
        # sorted with kwargs → runtime handles opts object
        if name == "sorted" and node.kwargs:
            opts = ", ".join(f"{k}: {self.gen_expr(v)}" for k, v in node.kwargs.items())
            return f"__sorted({args}, {{{opts}}})"
        if name in self.structs:
            return f"new {name}({args})"
        jsname = JS_BUILTINS.get(name, name)
        return f"{jsname}({args})"

    def _js_str(self, s: str) -> str:
        s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return f'"{s}"'

    def _gen_template(self, node: FString) -> str:
        parts = []
        for p in node.parts:
            if isinstance(p, str):
                parts.append(p.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$"))
            else:
                parts.append("${" + self.gen_expr(p) + "}")
        return "`" + "".join(parts) + "`"


def generate_js(program: Program, typescript: bool = False) -> str:
    return JSCodegen(typescript=typescript).generate(program)
