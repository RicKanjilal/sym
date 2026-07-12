"""
Sym Hybrid Compiler
Analyzes which functions are "pure" (no Python interop) and compiles
those to a C shared library (.so), while keeping Python-dependent code
as Python. The generated Python imports and calls the C functions via ctypes.

This gives you C speed for computation + full Python library access.

Usage:
    sym run --hybrid file.sym     (auto-detect and split)
    sym build --target=hybrid     (build .so + .py)
"""

import os
import sys
import subprocess
import tempfile
from typing import List, Set, Dict, Tuple
from .ast_nodes import *
from .codegen_c import CCodegen, SYM_TO_C_TYPE
from .codegen_python import PythonCodegen, ensure_installed


class PurityAnalyzer:
    """Determines which functions can be compiled to C (are 'pure')"""

    def __init__(self):
        self.python_imports: Set[str] = set()
        self.python_funcs: Set[str] = set()  # functions that use py.* stuff
        self.pure_funcs: Set[str] = set()    # functions safe for C
        self.func_deps: Dict[str, Set[str]] = {}  # func → set of called funcs
        self.func_nodes: Dict[str, FunctionDef] = {}

    def analyze(self, program: Program) -> Tuple[List[FunctionDef], List[Node]]:
        """
        Returns (pure_functions, python_statements)
        pure_functions: can be compiled to C
        python_statements: everything else (stays as Python)
        """
        # Collect imports and function definitions
        for stmt in program.statements:
            if isinstance(stmt, ImportStatement) and stmt.is_python:
                mod = stmt.module.removeprefix("py.") if stmt.module else ""
                if stmt.from_module:
                    mod = stmt.from_module.removeprefix("py.")
                self.python_imports.add(mod.split(".")[0])
            elif isinstance(stmt, FunctionDef):
                self.func_nodes[stmt.name] = stmt
                self.func_deps[stmt.name] = set()
                self._collect_deps(stmt.name, stmt.body)

        # Any function referenced by bare name (as a value/callback, e.g.
        # passed to serve_realtime) must stay Python — C funcs aren't callables.
        import re as _re
        _src_names = set()
        def _scan(n):
            if isinstance(n, Identifier):
                _src_names.add(n.name)
            for attr in ("args", "elements", "values", "keys", "body", "statements", "else_body"):
                v = getattr(n, attr, None)
                if isinstance(v, list):
                    for x in v:
                        if hasattr(x, "__dict__"): _scan(x)
            for attr in ("value", "left", "right", "obj", "func", "condition", "expr", "iterable"):
                v = getattr(n, attr, None)
                if v is not None and hasattr(v, "__dict__"): _scan(v)
            # don't count the direct callee of a call as a "value" reference
        for stmt in program.statements:
            if hasattr(stmt, "__dict__"): _scan(stmt)
        # a name used as a value but NOT as the callee of a call → callback
        for fname in list(self.func_nodes):
            if fname in _src_names:
                self.python_funcs.add(fname)

        # Mark functions that directly use Python
        for fname, deps in self.func_deps.items():
            if self._uses_python(self.func_nodes[fname]):
                self.python_funcs.add(fname)

        # Propagate: if a function calls a python func, it's python too
        changed = True
        while changed:
            changed = False
            for fname, deps in self.func_deps.items():
                if fname in self.python_funcs:
                    continue
                for dep in deps:
                    if dep in self.python_funcs:
                        self.python_funcs.add(fname)
                        changed = True
                        break

        # Everything not marked as python is pure
        for fname in self.func_nodes:
            if fname not in self.python_funcs:
                # Additional check: must have typed params for C
                fn = self.func_nodes[fname]
                if self._is_c_compatible(fn):
                    self.pure_funcs.add(fname)

        # Split statements
        pure_functions = [self.func_nodes[f] for f in self.pure_funcs]
        python_stmts = []
        for stmt in program.statements:
            if isinstance(stmt, FunctionDef) and stmt.name in self.pure_funcs:
                continue  # Goes to C
            python_stmts.append(stmt)

        return pure_functions, python_stmts

    def _collect_deps(self, func_name: str, body: List[Node]):
        """Find all function calls in a function body"""
        for stmt in body:
            self._walk_for_calls(func_name, stmt)

    def _walk_for_calls(self, func_name: str, node):
        if node is None:
            return
        if isinstance(node, FunctionCall) and isinstance(node.func, Identifier):
            self.func_deps[func_name].add(node.func.name)
        if isinstance(node, MethodCall):
            # Method calls on py objects → python
            if isinstance(node.obj, Identifier) and node.obj.name in self.python_imports:
                self.python_funcs.add(func_name)
        # Walk children
        for attr in vars(node).values():
            if isinstance(attr, Node):
                self._walk_for_calls(func_name, attr)
            elif isinstance(attr, list):
                for item in attr:
                    if isinstance(item, Node):
                        self._walk_for_calls(func_name, item)

    def _uses_python(self, func: FunctionDef) -> bool:
        """Check if function body directly references py.* imports"""
        return self._body_uses_python(func.body)

    def _body_uses_python(self, body: List[Node]) -> bool:
        for stmt in body:
            if self._node_uses_python(stmt):
                return True
        return False

    def _node_uses_python(self, node) -> bool:
        if node is None:
            return False
        if isinstance(node, Identifier) and node.name in self.python_imports:
            return True
        if isinstance(node, MemberAccess):
            if isinstance(node.obj, Identifier) and node.obj.name in self.python_imports:
                return True
        if isinstance(node, ImportStatement) and node.is_python:
            return True
        # Walk children
        for attr in vars(node).values():
            if isinstance(attr, Node):
                if self._node_uses_python(attr):
                    return True
            elif isinstance(attr, list):
                for item in attr:
                    if isinstance(item, Node) and self._node_uses_python(item):
                        return True
        return False

    def _is_c_compatible(self, func: FunctionDef) -> bool:
        """Check if function can be expressed in C"""
        # Benchmark functions use runtime timing — stay Python
        if func.is_benchmark:
            return False
        # Struct methods stay Python
        for p in func.params:
            if p.name == "self":
                return False
        # Check for unsupported constructs
        if self._has_unsupported(func.body):
            return False
        return True

    def _has_unsupported(self, body: List[Node]) -> bool:
        for stmt in body:
            if isinstance(stmt, (StructDef, TraitDef, ImplBlock, TryStatement)):
                return True
            if isinstance(stmt, MatchStatement):
                return True
            # List concat: data = data + [...] — not C compatible
            if isinstance(stmt, AssignStatement):
                if self._expr_has_unsupported(stmt.value):
                    return True
            if isinstance(stmt, LetStatement):
                if stmt.value and self._expr_has_unsupported(stmt.value):
                    return True
            # return <dict/list/string/...> — check the returned expression
            if isinstance(stmt, ReturnStatement):
                if getattr(stmt, "value", None) is not None and self._expr_has_unsupported(stmt.value):
                    return True
            if isinstance(stmt, IfStatement):
                if self._has_unsupported(stmt.body):
                    return True
                for ec in stmt.elif_clauses:
                    if self._has_unsupported(ec.body):
                        return True
                if self._has_unsupported(stmt.else_body):
                    return True
            if isinstance(stmt, (ForLoop, WhileLoop)):
                if self._has_unsupported(stmt.body):
                    return True
        return False

    def _expr_has_unsupported(self, node) -> bool:
        from .ast_nodes import Identifier as _Id
        if isinstance(node, _Id) and node.name.startswith("__") and node.name not in ("__name__",):
            return True
        """Check if an expression contains constructs that can't compile to C"""
        if node is None:
            return False
        # Any dict literal — C has no dict type
        if isinstance(node, DictLiteral):
            return True
        # Empty list literal [] — dynamic, not C
        if isinstance(node, ListLiteral) and len(node.elements) == 0:
            return True
        # Any non-empty list literal is also dynamic here — stay Python
        if isinstance(node, ListLiteral):
            return True
        # String literals in expressions (concat etc.) — stay Python
        if isinstance(node, (StringLiteral, FString)):
            return True
        # BinaryOp with list or string concat
        if isinstance(node, BinaryOp):
            if isinstance(node.right, ListLiteral):
                return True
            if isinstance(node.left, ListLiteral):
                return True
            return self._expr_has_unsupported(node.left) or self._expr_has_unsupported(node.right)
        # Method calls like .append — dynamic, not C
        if isinstance(node, MethodCall):
            if node.method in ("append", "insert", "pop", "extend", "remove"):
                return True
            return True  # any method call is dynamic — stay Python
        # Function calls that are Python-only or 2-arg round → stay Python
        if isinstance(node, FunctionCall):
            fname = node.func.name if isinstance(node.func, Identifier) else ""
            if fname == "round" and len(node.args) > 1:
                return True
            if fname in ("str", "int", "float", "dict", "list", "open", "input",
                         "sorted", "map", "filter", "range"):
                return True
            for a in node.args:
                if self._expr_has_unsupported(a):
                    return True
        # Index into string variable — stay Python
        if isinstance(node, IndexAccess):
            return False  # Could be array or string, allow for now
        return False

    def _has_unsupported(self, body: List[Node]) -> bool:
        for stmt in body:
            if isinstance(stmt, (StructDef, TraitDef, ImplBlock, TryStatement)):
                return True
            if isinstance(stmt, MatchStatement):
                return True
            # Check assignments and lets for string ops
            if isinstance(stmt, AssignStatement):
                if self._expr_has_unsupported(stmt.value):
                    return True
            if isinstance(stmt, LetStatement):
                if stmt.value and self._expr_has_unsupported(stmt.value):
                    return True
            # return <dict/list/string/fstring> — stay Python
            if isinstance(stmt, ReturnStatement):
                if getattr(stmt, "value", None) is not None and self._expr_has_unsupported(stmt.value):
                    return True
            # print with string interpolation — stay Python
            if isinstance(stmt, ExpressionStatement):
                if isinstance(stmt.expr, FunctionCall) and isinstance(stmt.expr.func, Identifier):
                    if stmt.expr.func.name == "print":
                        # print with string args → not C
                        for arg in stmt.expr.args:
                            if isinstance(arg, (StringLiteral, FString)):
                                return True
            if isinstance(stmt, IfStatement):
                if self._has_unsupported(stmt.body):
                    return True
                for ec in stmt.elif_clauses:
                    if self._has_unsupported(ec.body):
                        return True
                if self._has_unsupported(stmt.else_body):
                    return True
            if isinstance(stmt, (ForLoop, WhileLoop)):
                if self._has_unsupported(stmt.body):
                    return True
        return False


class HybridCodegen:
    """Generates a C shared library for pure functions + Python wrapper"""

    def __init__(self):
        self.c_codegen = CCodegen()

    def generate(self, program: Program) -> Tuple[str, str, List[str]]:
        """
        Returns:
            c_source: C code for pure functions (compile to .so)
            py_source: Python code with ctypes imports for C funcs
            pure_func_names: list of function names compiled to C
        """
        analyzer = PurityAnalyzer()
        pure_funcs, python_stmts = analyzer.analyze(program)
        pure_names = [f.name for f in pure_funcs]

        # Generate C shared library source
        c_source = self._gen_c_library(pure_funcs) if pure_funcs else ""

        # Generate Python with ctypes bridge
        py_source = self._gen_python_hybrid(program, python_stmts, pure_funcs, pure_names)

        return c_source, py_source, pure_names

    def _gen_c_library(self, funcs: List[FunctionDef]) -> str:
        """Generate a C shared library from pure functions"""
        lines = [
            "#include <stdio.h>",
            "#include <stdlib.h>",
            "#include <string.h>",
            "#include <stdint.h>",
            "#include <math.h>",
            "",
            "/* Sym Hybrid — C Shared Library */",
            "/* Compiled pure functions for native speed */",
            "",
        ]

        # Smart sort runtime (minimal)
        lines.append("""
int _cmp_int64(const void* a, const void* b) {
    int64_t va = *(const int64_t*)a;
    int64_t vb = *(const int64_t*)b;
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
    if (n < 16) _insertion_sort_int(arr, n);
    else qsort(arr, n, sizeof(int64_t), _cmp_int64);
}
""")

        # Forward declarations
        cg = CCodegen()
        cg._collect_signatures(Program(statements=funcs))

        for fname, ret in cg.func_return_types.items():
            params = cg.func_param_types.get(fname, [])
            param_str = ", ".join(f"{t} {n}" for n, t in params) or "void"
            lines.append(f"{ret} {fname}({param_str});")
        lines.append("")

        # Generate each function
        for func in funcs:
            cg2 = CCodegen()
            cg2.func_return_types = dict(cg.func_return_types)
            cg2.func_param_types = dict(cg.func_param_types)
            cg2._gen_function(func)
            for f in cg2.functions:
                lines.append(f)

        return "\n".join(lines) + "\n"

    def _gen_python_hybrid(self, program: Program, python_stmts: List[Node],
                            pure_funcs: List[FunctionDef], pure_names: List[str]) -> str:
        """Generate Python code that loads the C .so and calls native functions"""
        pg = PythonCodegen()

        # Collect python imports for auto-install
        pg._collect_python_imports(program)
        for mod in pg.python_imports:
            ensure_installed(mod)

        # Header
        pg.emit("#!/usr/bin/env python3")
        pg.emit("# Generated by Sym Hybrid Compiler")
        pg.emit("# Pure functions run as native C. Python libs work normally.")
        pg.emit("")

        # Runtime
        pg.emit("import sys as _sys, os as _os, ctypes as _ct")
        pg.emit("_sym_dir = _os.path.dirname(_os.path.abspath(__file__))")
        pg.emit("for _p in [_os.path.join(_sym_dir, '..'), _os.environ.get('SYM_HOME', '')]:")
        pg.emit("    if _p and _p not in _sys.path: _sys.path.insert(0, _p)")
        pg.emit("try:")
        pg.emit("    from runtime.builtins import *")
        pg.emit("except ImportError:")
        pg.emit("    import copy as _copy, math as _math")
        pg.emit("    def _sym_smart_sort(d):")
        pg.emit("        if isinstance(d,list): d.sort()")
        pg.emit("        return d")
        pg.emit("    def _sym_clone(o): return _copy.deepcopy(o)")
        pg.emit("    sqrt=_math.sqrt;sin=_math.sin;cos=_math.cos;log=_math.log")
        pg.emit("    PI=_math.pi;E=_math.e;floor=_math.floor;ceil=_math.ceil")
        pg.emit("")

        # Load C shared library
        if pure_names:
            pg.emit("# ═══ Load native C functions ═══")
            pg.emit("_so_path = _os.path.join(_sym_dir, '_sym_native.so')")
            pg.emit("_c_lib = None")
            pg.emit("if _os.path.exists(_so_path):")
            pg.emit("    try:")
            pg.emit("        _c_lib = _ct.CDLL(_so_path)")

            # Set up ctypes signatures
            cg = CCodegen()
            cg._collect_signatures(Program(statements=pure_funcs))

            for fname in pure_names:
                ret = cg.func_return_types.get(fname, "void")
                params = cg.func_param_types.get(fname, [])

                ct_ret = self._c_to_ctypes(ret)
                ct_args = [self._c_to_ctypes(t) for _, t in params]

                pg.emit(f"        _c_lib.{fname}.restype = {ct_ret}")
                if ct_args:
                    pg.emit(f"        _c_lib.{fname}.argtypes = [{', '.join(ct_args)}]")

            pg.emit("    except (OSError, AttributeError):")
            pg.emit("        _c_lib = None  # Stale or incompatible .so — use Python fallback")
            pg.emit("")

            # Generate Python wrapper functions that call C
            for func in pure_funcs:
                params = [(p.name, p.type_annotation) for p in func.params if p.name != "self"]
                param_names = [p[0] for p in params]
                param_str = ", ".join(param_names)

                pg.emit(f"def {func.name}({param_str}):")
                pg.emit(f"    if _c_lib:")
                pg.emit(f"        return _c_lib.{func.name}({param_str})")
                pg.emit(f"    else:")
                pg.emit(f"        # Fallback: pure Python implementation")

                # Generate Python fallback body
                pg.indent = 2
                for stmt in func.body:
                    pg.gen_statement(stmt)
                pg.indent = 0
                pg.emit("")

        # Generate remaining Python statements
        for stmt in python_stmts:
            pg.gen_statement(stmt)

        return "\n".join(pg.output) + "\n"

    def _c_to_ctypes(self, c_type: str) -> str:
        """Convert C type to ctypes type"""
        m = {
            "int64_t": "_ct.c_int64",
            "int32_t": "_ct.c_int32",
            "double": "_ct.c_double",
            "float": "_ct.c_float",
            "int": "_ct.c_int",
            "void": "None",
            "char*": "_ct.c_char_p",
            "uint8_t": "_ct.c_uint8",
        }
        return m.get(c_type, "_ct.c_int64")


def compile_shared_library(c_source: str, output_dir: str) -> bool:
    """Compile C source to a shared library (.so)"""
    c_path = os.path.join(output_dir, "_sym_native.c")
    so_path = os.path.join(output_dir, "_sym_native.so")

    with open(c_path, "w") as f:
        f.write(c_source)

    # Try compilation with various flag sets
    flag_sets = [
        ["-shared", "-fPIC", "-O3", "-march=native", "-lm"],
        ["-shared", "-fPIC", "-O3", "-lm"],
        ["-shared", "-fPIC", "-O2", "-lm"],
        ["-shared", "-fPIC", "-lm"],
    ]

    last_error = ""
    compiler_found = False

    for compiler in ["gcc", "clang", "cc"]:
        try:
            # First check if compiler exists
            check = subprocess.run([compiler, "--version"], capture_output=True, timeout=5)
            if check.returncode != 0:
                continue
            compiler_found = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

        for flags in flag_sets:
            try:
                result = subprocess.run(
                    [compiler] + flags + [c_path, "-o", so_path],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    # Clean up .c file
                    try: os.remove(c_path)
                    except: pass
                    return True
                else:
                    last_error = result.stderr.strip()
            except subprocess.TimeoutExpired:
                last_error = "compilation timed out"
                continue

    if not compiler_found:
        print("  ⚠ No C compiler found. Pure functions will run as Python.")
        print("    Install gcc: sudo apt install gcc")
    elif last_error:
        print(f"  ⚠ C compilation failed:")
        for line in last_error.split("\n")[:5]:
            print(f"    {line}")
    else:
        print("  ⚠ C compilation failed (unknown error)")

    return False
