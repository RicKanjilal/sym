"""
Sym → C Code Generator (Phase 2)
Transpiles pure Sym functions to C for 10-100x speedup.
Python interop sections stay as Python calls via CPython C API.

Compilation: gcc -O3 -march=native -o program program.c -lpython3.X
"""

import subprocess
import sys
import os
import sysconfig
from typing import List, Set, Dict, Optional, Tuple
from .ast_nodes import *


# ── Type Mapping ───────────────────────────────────────────────
SYM_TO_C_TYPE = {
    "int": "int64_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "float": "double",
    "f32": "float",
    "f64": "double",
    "bool": "int",
    "str": "char*",
    "byte": "uint8_t",
    "void": "void",
}


class CCodegen:
    def __init__(self):
        self.headers: List[str] = []
        self.globals: List[str] = []
        self.functions: List[str] = []
        self.main_body: List[str] = []
        self.indent = 0
        self.temp_counter = 0
        self.string_literals: Dict[str, str] = {}  # content → var name
        self.uses_python = False
        self.func_return_types: Dict[str, str] = {}  # func name → C return type
        self.func_param_types: Dict[str, List[Tuple[str, str]]] = {}  # func → [(name, ctype)]
        self.local_vars: Dict[str, str] = {}  # var name → C type (current scope)
        self.scope_stack: List[Dict[str, str]] = []
        self.array_vars: Set[str] = set()  # vars that are int64_t* arrays
        self.array_sizes: Dict[str, str] = {}  # array var → size expression

    def _indent(self) -> str:
        return "    " * self.indent

    def _temp(self, prefix="tmp") -> str:
        self.temp_counter += 1
        return f"_{prefix}_{self.temp_counter}"

    def _c_type(self, type_ann: Optional[str], default: str = "int64_t") -> str:
        if not type_ann:
            return default
        return SYM_TO_C_TYPE.get(type_ann, default)

    def _push_scope(self):
        self.scope_stack.append(dict(self.local_vars))

    def _pop_scope(self):
        self.local_vars = self.scope_stack.pop()

    # ── Main Entry ─────────────────────────────────────────────
    def generate(self, program: Program) -> str:
        """Generate complete C source from AST"""
        # First pass: collect function signatures
        self._collect_signatures(program)

        # Standard headers
        self.headers = [
            "#include <stdio.h>",
            "#include <stdlib.h>",
            "#include <string.h>",
            "#include <stdint.h>",
            "#include <math.h>",
            "#include <time.h>",
        ]

        # Runtime helpers
        self.globals.append(self._runtime_helpers())

        # Generate all statements
        for stmt in program.statements:
            if isinstance(stmt, FunctionDef):
                self._gen_function(stmt)
            elif isinstance(stmt, (DirectiveStatement, ImportStatement)):
                pass  # Skip directives and imports in C mode
            else:
                self._gen_main_statement(stmt)

        # Assemble
        return self._assemble()

    def _collect_signatures(self, program: Program):
        """First pass to know function return types"""
        for stmt in program.statements:
            if isinstance(stmt, FunctionDef):
                ret = self._c_type(stmt.return_type, "int64_t")
                if not stmt.return_type:
                    ret = "void"
                    # Check if function has return statements with values
                    if self._has_return_value(stmt.body):
                        ret = "int64_t"
                self.func_return_types[stmt.name] = ret
                params = []
                for p in stmt.params:
                    if p.name == "self":
                        continue
                    ct = self._c_type(p.type_annotation, "int64_t")
                    params.append((p.name, ct))
                self.func_param_types[stmt.name] = params

    def _has_return_value(self, body: List[Node]) -> bool:
        for stmt in body:
            if isinstance(stmt, ReturnStatement) and stmt.value is not None:
                return True
            if isinstance(stmt, IfStatement):
                if self._has_return_value(stmt.body):
                    return True
                for ec in stmt.elif_clauses:
                    if self._has_return_value(ec.body):
                        return True
                if self._has_return_value(stmt.else_body):
                    return True
        return False

    def _runtime_helpers(self) -> str:
        return """
/* ═══ Sym C Runtime ═══ */

/* Dynamic int array */
typedef struct {
    int64_t* data;
    int64_t len;
    int64_t cap;
} IntArray;

IntArray intarray_new(int64_t cap) {
    IntArray a;
    a.data = (int64_t*)malloc(cap * sizeof(int64_t));
    a.len = 0;
    a.cap = cap;
    return a;
}

void intarray_push(IntArray* a, int64_t val) {
    if (a->len >= a->cap) {
        a->cap *= 2;
        a->data = (int64_t*)realloc(a->data, a->cap * sizeof(int64_t));
    }
    a->data[a->len++] = val;
}

void intarray_free(IntArray* a) {
    free(a->data);
    a->data = NULL;
    a->len = 0;
    a->cap = 0;
}

/* Dynamic float array */
typedef struct {
    double* data;
    int64_t len;
    int64_t cap;
} FloatArray;

FloatArray floatarray_new(int64_t cap) {
    FloatArray a;
    a.data = (double*)malloc(cap * sizeof(double));
    a.len = 0;
    a.cap = cap;
    return a;
}

void floatarray_push(FloatArray* a, double val) {
    if (a->len >= a->cap) {
        a->cap *= 2;
        a->data = (double*)realloc(a->data, a->cap * sizeof(double));
    }
    a->data[a->len++] = val;
}

void floatarray_free(FloatArray* a) {
    free(a->data);
    a->data = NULL;
    a->len = 0;
    a->cap = 0;
}

/* Smart sort — adapts to data size */
int _cmp_int64(const void* a, const void* b) {
    int64_t va = *(const int64_t*)a;
    int64_t vb = *(const int64_t*)b;
    return (va > vb) - (va < vb);
}

int _cmp_double(const void* a, const void* b) {
    double va = *(const double*)a;
    double vb = *(const double*)b;
    return (va > vb) - (va < vb);
}

void _insertion_sort_int(int64_t* arr, int64_t n) {
    for (int64_t i = 1; i < n; i++) {
        int64_t key = arr[i];
        int64_t j = i - 1;
        while (j >= 0 && arr[j] > key) {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }
}

void sym_smart_sort_int(int64_t* arr, int64_t n) {
    if (n < 16) {
        _insertion_sort_int(arr, n);
    } else {
        qsort(arr, n, sizeof(int64_t), _cmp_int64);
    }
}

void sym_smart_sort_double(double* arr, int64_t n) {
    qsort(arr, n, sizeof(double), _cmp_double);
}

/* Timing */
double _sym_time_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1000000.0;
}

/* String formatting helper */
char* _sym_int_to_str(int64_t val) {
    char* buf = (char*)malloc(32);
    snprintf(buf, 32, "%ld", val);
    return buf;
}

char* _sym_double_to_str(double val) {
    char* buf = (char*)malloc(64);
    snprintf(buf, 64, "%.6g", val);
    return buf;
}
"""

    # ── Functions ──────────────────────────────────────────────
    def _gen_function(self, node: FunctionDef):
        self._push_scope()
        self.local_vars.clear()

        ret_type = self.func_return_types.get(node.name, "void")
        params = self.func_param_types.get(node.name, [])

        # Register params in scope
        for pname, ptype in params:
            self.local_vars[pname] = ptype

        param_str = ", ".join(f"{t} {n}" for n, t in params)
        if not param_str:
            param_str = "void"

        lines = []
        if node.is_benchmark:
            lines.append(f"{ret_type} {node.name}({param_str}) {{")
            lines.append(f'    double _bm_start = _sym_time_ms();')
        else:
            lines.append(f"{ret_type} {node.name}({param_str}) {{")

        self.indent = 1
        for stmt in node.body:
            lines.extend(self._gen_stmt(stmt))

        if node.is_benchmark:
            lines.append(f'    double _bm_end = _sym_time_ms();')
            lines.append(f'    printf("  Benchmark {node.name}: %.3f ms\\n", _bm_end - _bm_start);')

        lines.append("}")
        lines.append("")
        self.functions.append("\n".join(lines))

        self._pop_scope()

    # ── Statements ─────────────────────────────────────────────
    def _gen_stmt(self, node: Node) -> List[str]:
        ind = self._indent()

        if isinstance(node, LetStatement):
            return self._gen_let(node)
        if isinstance(node, AssignStatement):
            return self._gen_assign(node)
        if isinstance(node, ReturnStatement):
            if node.value:
                return [f"{ind}return {self._gen_expr(node.value)};"]
            return [f"{ind}return;"]
        if isinstance(node, IfStatement):
            return self._gen_if(node)
        if isinstance(node, ForLoop):
            return self._gen_for(node)
        if isinstance(node, WhileLoop):
            return self._gen_while(node)
        if isinstance(node, ExpressionStatement):
            return self._gen_expr_stmt(node)
        if isinstance(node, BreakStatement):
            return [f"{ind}break;"]
        if isinstance(node, ContinueStatement):
            return [f"{ind}continue;"]
        return [f"{ind}/* TODO: {type(node).__name__} */"]

    def _gen_let(self, node: LetStatement) -> List[str]:
        ind = self._indent()
        c_type = self._c_type(node.type_annotation, "int64_t")

        # Infer type from value
        if not node.type_annotation and node.value:
            c_type = self._infer_type(node.value)

        # Handle list literals → arrays
        if isinstance(node.value, ListLiteral):
            n = len(node.value.elements)
            elem_type = self._infer_elem_type(node.value)
            self.local_vars[node.name] = f"{elem_type}*"
            self.array_vars.add(node.name)
            self.array_sizes[node.name] = str(n)
            elems = ", ".join(self._gen_expr(e) for e in node.value.elements)
            return [
                f"{ind}{elem_type} {node.name}[] = {{{elems}}};",
                f"{ind}int64_t {node.name}_len = {n};"
            ]

        self.local_vars[node.name] = c_type
        val = self._gen_expr(node.value) if node.value else "0"
        return [f"{ind}{c_type} {node.name} = {val};"]

    def _gen_assign(self, node: AssignStatement) -> List[str]:
        ind = self._indent()
        target = self._gen_expr(node.target)
        val = self._gen_expr(node.value)
        return [f"{ind}{target} {node.op} {val};"]

    def _gen_if(self, node: IfStatement) -> List[str]:
        ind = self._indent()
        lines = [f"{ind}if ({self._gen_expr(node.condition)}) {{"]
        self.indent += 1
        for s in node.body:
            lines.extend(self._gen_stmt(s))
        self.indent -= 1
        lines.append(f"{ind}}}")

        for ec in node.elif_clauses:
            lines.append(f"{ind}else if ({self._gen_expr(ec.condition)}) {{")
            self.indent += 1
            for s in ec.body:
                lines.extend(self._gen_stmt(s))
            self.indent -= 1
            lines.append(f"{ind}}}")

        if node.else_body:
            lines.append(f"{ind}else {{")
            self.indent += 1
            for s in node.else_body:
                lines.extend(self._gen_stmt(s))
            self.indent -= 1
            lines.append(f"{ind}}}")

        return lines

    def _gen_for(self, node: ForLoop) -> List[str]:
        ind = self._indent()
        lines = []

        # Range-based for: for i in 0..n
        if isinstance(node.iterable, RangeLiteral):
            start = self._gen_expr(node.iterable.start)
            stop = self._gen_expr(node.iterable.stop)
            step = self._gen_expr(node.iterable.step) if node.iterable.step else "1"
            self.local_vars[node.var] = "int64_t"
            lines.append(f"{ind}for (int64_t {node.var} = {start}; {node.var} < {stop}; {node.var} += {step}) {{")
            self.indent += 1
            for s in node.body:
                lines.extend(self._gen_stmt(s))
            self.indent -= 1
            lines.append(f"{ind}}}")
            return lines

        # Array-based for: for x in arr → iterate over C array
        iter_name = self._gen_expr(node.iterable)
        if iter_name in self.array_vars:
            size_var = f"{iter_name}_len"
            idx = self._temp("i")
            elem_type = self.local_vars.get(iter_name, "int64_t*").rstrip("*")
            self.local_vars[node.var] = elem_type
            lines.append(f"{ind}for (int64_t {idx} = 0; {idx} < {size_var}; {idx}++) {{")
            lines.append(f"{ind}    {elem_type} {node.var} = {iter_name}[{idx}];")
            self.indent += 1
            for s in node.body:
                lines.extend(self._gen_stmt(s))
            self.indent -= 1
            lines.append(f"{ind}}}")
            return lines

        # Fallback: assume it's an IntArray
        idx = self._temp("i")
        lines.append(f"{ind}for (int64_t {idx} = 0; {idx} < {iter_name}.len; {idx}++) {{")
        lines.append(f"{ind}    int64_t {node.var} = {iter_name}.data[{idx}];")
        self.indent += 1
        for s in node.body:
            lines.extend(self._gen_stmt(s))
        self.indent -= 1
        lines.append(f"{ind}}}")
        return lines

    def _gen_while(self, node: WhileLoop) -> List[str]:
        ind = self._indent()
        lines = [f"{ind}while ({self._gen_expr(node.condition)}) {{"]
        self.indent += 1
        for s in node.body:
            lines.extend(self._gen_stmt(s))
        self.indent -= 1
        lines.append(f"{ind}}}")
        return lines

    def _gen_expr_stmt(self, node: ExpressionStatement) -> List[str]:
        ind = self._indent()
        expr = node.expr

        # Handle print specially
        if isinstance(expr, FunctionCall) and isinstance(expr.func, Identifier):
            name = expr.func.name
            if name == "print":
                return self._gen_print(expr.args)
            if name == "sort" and len(expr.args) == 1:
                return self._gen_sort_call(expr.args[0])

        return [f"{ind}{self._gen_expr(expr)};"]

    def _gen_print(self, args: List[Node]) -> List[str]:
        ind = self._indent()
        if not args:
            return [f'{ind}printf("\\n");']

        parts = []
        fmt_parts = []
        for arg in args:
            if isinstance(arg, StringLiteral):
                # Check for f-string interpolation
                if "{" in arg.value:
                    return self._gen_fstring_print(arg, ind)
                fmt_parts.append("%s")
                parts.append(f'"{arg.value}"')
            elif isinstance(arg, FString):
                return self._gen_fstring_print_node(arg, ind)
            elif isinstance(arg, Identifier):
                vtype = self.local_vars.get(arg.name, "int64_t")
                if vtype == "double":
                    fmt_parts.append("%g")
                elif vtype == "char*":
                    fmt_parts.append("%s")
                elif arg.name in self.array_vars:
                    # Print array
                    return self._gen_array_print(arg.name, ind)
                else:
                    fmt_parts.append("%ld")
                parts.append(arg.name)
            elif isinstance(arg, IntLiteral):
                fmt_parts.append("%ld")
                parts.append(str(arg.value))
            elif isinstance(arg, FloatLiteral):
                fmt_parts.append("%g")
                parts.append(str(arg.value))
            else:
                fmt_parts.append("%ld")
                parts.append(self._gen_expr(arg))

        fmt = " ".join(fmt_parts)
        args_str = ", ".join(parts)
        return [f'{ind}printf("{fmt}\\n", {args_str});']

    def _gen_fstring_print(self, node: StringLiteral, ind: str) -> List[str]:
        """Handle print of a string with {var} interpolation"""
        text = node.value
        fmt = ""
        args = []
        i = 0
        while i < len(text):
            if text[i] == "{":
                i += 1
                var = ""
                while i < len(text) and text[i] != "}":
                    var += text[i]
                    i += 1
                i += 1  # skip }
                vtype = self.local_vars.get(var, "int64_t")
                if vtype == "double":
                    fmt += "%g"
                elif vtype == "char*":
                    fmt += "%s"
                else:
                    fmt += "%ld"
                args.append(var)
            else:
                fmt += text[i]
                i += 1
        args_str = ", ".join(args)
        if args_str:
            return [f'{ind}printf("{fmt}\\n", {args_str});']
        return [f'{ind}printf("{fmt}\\n");']

    def _gen_fstring_print_node(self, node: FString, ind: str) -> List[str]:
        fmt = ""
        args = []
        for part in node.parts:
            if isinstance(part, str):
                fmt += part
            elif isinstance(part, Identifier):
                vtype = self.local_vars.get(part.name, "int64_t")
                if vtype == "double":
                    fmt += "%g"
                elif vtype == "char*":
                    fmt += "%s"
                else:
                    fmt += "%ld"
                args.append(part.name)
            else:
                fmt += "%ld"
                args.append(self._gen_expr(part))
        args_str = ", ".join(args)
        if args_str:
            return [f'{ind}printf("{fmt}\\n", {args_str});']
        return [f'{ind}printf("{fmt}\\n");']

    def _gen_array_print(self, name: str, ind: str) -> List[str]:
        lines = []
        lines.append(f'{ind}printf("[");')
        idx = self._temp("i")
        lines.append(f'{ind}for (int64_t {idx} = 0; {idx} < {name}_len; {idx}++) {{')
        lines.append(f'{ind}    if ({idx} > 0) printf(", ");')
        lines.append(f'{ind}    printf("%ld", {name}[{idx}]);')
        lines.append(f'{ind}}}')
        lines.append(f'{ind}printf("]\\n");')
        return lines

    def _gen_sort_call(self, arg: Node) -> List[str]:
        ind = self._indent()
        name = self._gen_expr(arg)
        if name in self.array_vars:
            return [f"{ind}sym_smart_sort_int({name}, {name}_len);"]
        return [f"{ind}/* sort: unsupported type */"]

    # ── Expressions ────────────────────────────────────────────
    def _gen_expr(self, node: Node) -> str:
        if node is None:
            return "0"
        if isinstance(node, IntLiteral):
            return f"{node.value}LL"
        if isinstance(node, FloatLiteral):
            return str(node.value)
        if isinstance(node, BoolLiteral):
            return "1" if node.value else "0"
        if isinstance(node, StringLiteral):
            escaped = node.value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            return f'"{escaped}"'
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, BinaryOp):
            left = self._gen_expr(node.left)
            right = self._gen_expr(node.right)
            op_map = {"≠": "!=", "≤": "<=", "≥": ">=", "and": "&&", "or": "||", "**": ""}
            op = op_map.get(node.op, node.op)
            if node.op == "**":
                return f"((int64_t)pow((double){left}, (double){right}))"
            if node.op == "//":
                return f"({left} / {right})"
            if node.op == "%":
                return f"({left} % {right})"
            return f"({left} {op} {right})"
        if isinstance(node, UnaryOp):
            if node.op == "not":
                return f"(!{self._gen_expr(node.operand)})"
            return f"({node.op}{self._gen_expr(node.operand)})"
        if isinstance(node, FunctionCall):
            return self._gen_call_expr(node)
        if isinstance(node, IndexAccess):
            return f"{self._gen_expr(node.obj)}[{self._gen_expr(node.index)}]"
        if isinstance(node, MemberAccess):
            return f"{self._gen_expr(node.obj)}.{node.member}"
        if isinstance(node, MethodCall):
            obj = self._gen_expr(node.obj)
            args = ", ".join(self._gen_expr(a) for a in node.args)
            return f"{obj}_{node.method}({args})"
        if isinstance(node, RangeLiteral):
            # Used inline — shouldn't happen in expression context
            return f"/* range */"
        if isinstance(node, FString):
            # In expression context, return first part as string
            return '"<fstring>"'
        return f"/* unknown: {type(node).__name__} */"

    def _gen_call_expr(self, node: FunctionCall) -> str:
        name = self._gen_expr(node.func)
        args = ", ".join(self._gen_expr(a) for a in node.args)

        # Map builtins to C
        builtin_map = {
            "len": None,  # special
            "sqrt": "sqrt",
            "sin": "sin",
            "cos": "cos",
            "tan": "tan",
            "log": "log",
            "log2": "log2",
            "log10": "log10",
            "abs": "llabs",
            "floor": "floor",
            "ceil": "ceil",
        }

        if name == "len" and len(node.args) == 1:
            arg_name = self._gen_expr(node.args[0])
            if arg_name in self.array_vars:
                return f"{arg_name}_len"
            return f"strlen({arg_name})"

        if name in builtin_map and builtin_map[name]:
            return f"{builtin_map[name]}({args})"

        if name == "range":
            return args  # handled by for loop

        return f"{name}({args})"

    # ── Type Inference ─────────────────────────────────────────
    def _infer_type(self, node: Node) -> str:
        if isinstance(node, IntLiteral):
            return "int64_t"
        if isinstance(node, FloatLiteral):
            return "double"
        if isinstance(node, BoolLiteral):
            return "int"
        if isinstance(node, StringLiteral):
            return "char*"
        if isinstance(node, FString):
            return "char*"
        if isinstance(node, BinaryOp):
            lt = self._infer_type(node.left)
            rt = self._infer_type(node.right)
            if lt == "double" or rt == "double":
                return "double"
            return "int64_t"
        if isinstance(node, FunctionCall):
            if isinstance(node.func, Identifier):
                fname = node.func.name
                if fname in self.func_return_types:
                    return self.func_return_types[fname]
                if fname in ("sqrt", "sin", "cos", "tan", "log", "log2", "log10"):
                    return "double"
            return "int64_t"
        if isinstance(node, Identifier):
            return self.local_vars.get(node.name, "int64_t")
        return "int64_t"

    def _infer_elem_type(self, node: ListLiteral) -> str:
        for e in node.elements:
            if isinstance(e, FloatLiteral):
                return "double"
        return "int64_t"

    # ── Main statements ────────────────────────────────────────
    def _gen_main_statement(self, node: Node):
        self.indent = 1
        lines = self._gen_stmt(node)
        self.main_body.extend(lines)

    # ── Assembly ───────────────────────────────────────────────
    def _assemble(self) -> str:
        parts = []
        parts.extend(self.headers)
        parts.append("")
        parts.extend(self.globals)
        parts.append("")

        # Forward declarations
        for fname, ret in self.func_return_types.items():
            params = self.func_param_types.get(fname, [])
            param_str = ", ".join(f"{t} {n}" for n, t in params) or "void"
            parts.append(f"{ret} {fname}({param_str});")
        parts.append("")

        # Functions
        for f in self.functions:
            parts.append(f)

        # Main
        parts.append("int main(int argc, char** argv) {")
        parts.extend(self.main_body)
        parts.append("    return 0;")
        parts.append("}")
        parts.append("")

        return "\n".join(parts)


def generate_c(program: Program) -> str:
    """Generate C source code from AST"""
    return CCodegen().generate(program)


def compile_c(c_source: str, output_path: str, optimize: bool = True) -> bool:
    """Compile C source to native binary using gcc"""
    c_file = output_path + ".c"
    with open(c_file, "w") as f:
        f.write(c_source)

    flags = ["-O3", "-march=native", "-lm"] if optimize else ["-g", "-O0", "-lm"]

    try:
        result = subprocess.run(
            ["gcc"] + flags + [c_file, "-o", output_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Try clang as fallback
            result = subprocess.run(
                ["clang"] + flags + [c_file, "-o", output_path],
                capture_output=True, text=True
            )
        if result.returncode != 0:
            print(f"  ❌ Compilation failed:\n{result.stderr}")
            return False
        return True
    except FileNotFoundError:
        print("  ❌ Neither gcc nor clang found. Install with: sudo apt install gcc")
        return False
