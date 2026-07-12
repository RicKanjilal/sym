"""
Sym → Python Code Generator
Transpiles the AST into executable Python code.
Handles:
- Python interop (py.* imports → real pip packages, auto-installed)
- Smart builtins (sort → adaptive algorithm)
- F-string interpolation
- Structs → Python classes
- Benchmark → timing wrapper
"""

import subprocess
import sys
import importlib
from typing import List, Set
from .ast_nodes import *

# ── Known PyPI package name mappings ────────────────────────
# Some Python module names differ from their pip package names
PYPI_NAME_MAP = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "gi": "PyGObject",
    "attr": "attrs",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "wx": "wxPython",
    "serial": "pyserial",
    "usb": "pyusb",
    "Crypto": "pycryptodome",
    "google.cloud": "google-cloud",
    "telegram": "python-telegram-bot",
}


def get_pip_name(module_name: str) -> str:
    """Convert a Python module name to its pip package name"""
    # Check exact match first
    if module_name in PYPI_NAME_MAP:
        return PYPI_NAME_MAP[module_name]
    # Check parent module (e.g. sklearn.ensemble → scikit-learn)
    parts = module_name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in PYPI_NAME_MAP:
            return PYPI_NAME_MAP[prefix]
    # Default: pip name = module name (true for numpy, pandas, requests, etc.)
    return parts[0]


def _try_pip_install(pip_name: str) -> bool:
    """Try installing with various pip methods"""
    strategies = [
        # Strategy 1: python -m pip (most reliable)
        [sys.executable, "-m", "pip", "install", pip_name, "-q", "--user"],
        # Strategy 2: pip3 directly
        ["pip3", "install", pip_name, "-q", "--user"],
        # Strategy 3: pip directly
        ["pip", "install", pip_name, "-q", "--user"],
        # Strategy 4: pipx (some distros)
        ["pipx", "install", pip_name],
    ]
    for cmd in strategies:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


def _try_apt_install(module_name: str) -> bool:
    """Try installing via apt (Debian/Ubuntu/Pop!_OS)"""
    # Map common modules to apt package names
    apt_map = {
        "numpy": "python3-numpy",
        "scipy": "python3-scipy",
        "matplotlib": "python3-matplotlib",
        "pandas": "python3-pandas",
        "PIL": "python3-pil",
        "cv2": "python3-opencv",
        "sklearn": "python3-sklearn",
        "yaml": "python3-yaml",
        "requests": "python3-requests",
        "flask": "python3-flask",
        "bs4": "python3-bs4",
        "lxml": "python3-lxml",
        "setuptools": "python3-setuptools",
        "pip": "python3-pip",
        "cryptography": "python3-cryptography",
        "sqlalchemy": "python3-sqlalchemy",
        "psycopg2": "python3-psycopg2",
        "pymongo": "python3-pymongo",
        "redis": "python3-redis",
        "celery": "python3-celery",
        "django": "python3-django",
        "tornado": "python3-tornado",
        "twisted": "python3-twisted",
        "serial": "python3-serial",
        "gi": "python3-gi",
        "dbus": "python3-dbus",
        "apt": "python3-apt",
        "zmq": "python3-zmq",
        "h5py": "python3-h5py",
        "sympy": "python3-sympy",
    }
    top = module_name.split(".")[0]
    apt_pkg = apt_map.get(top, f"python3-{top}")

    # Check if apt is available
    try:
        subprocess.run(["apt", "--version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    print(f"  ⚡ Trying: sudo apt install {apt_pkg}")
    try:
        result = subprocess.run(
            ["sudo", "apt", "install", "-y", apt_pkg],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def ensure_installed(module_name: str) -> bool:
    """Check if a Python module is installed; if not, install it with cascading fallbacks"""
    top_module = module_name.split(".")[0]
    try:
        importlib.import_module(top_module)
        return True
    except ImportError:
        pass

    pip_name = get_pip_name(module_name)
    print(f"  ⚡ Package '{top_module}' not found. Installing {pip_name}...")

    # Strategy 1: pip install
    print(f"  → Trying pip install {pip_name} --user")
    if _try_pip_install(pip_name):
        print(f"  ✅ Installed {pip_name} via pip")
        # Reload
        try:
            importlib.import_module(top_module)
            return True
        except ImportError:
            pass

    # Strategy 2: apt install (Debian/Ubuntu/Pop!_OS)
    print(f"  → pip failed. Trying apt...")
    if _try_apt_install(module_name):
        print(f"  ✅ Installed via apt")
        try:
            importlib.import_module(top_module)
            return True
        except ImportError:
            pass

    # Strategy 3: Tell the user
    print(f"")
    print(f"  ❌ Could not auto-install '{pip_name}'.")
    print(f"  ")
    print(f"  Please install it manually:")
    print(f"  ")
    print(f"    pip install {pip_name}            # if pip works")
    print(f"    pip3 install {pip_name} --user     # user install")
    print(f"    sudo apt install python3-{top_module}  # Debian/Ubuntu")
    print(f"    conda install {pip_name}           # if using conda")
    print(f"  ")
    print(f"  If pip itself is missing:")
    print(f"    sudo apt install python3-pip")
    print(f"  ")
    return False


class PythonCodegen:
    def __init__(self):
        self.output: List[str] = []
        self.indent = 0
        self.python_imports: Set[str] = set()
        self.needs_runtime = False
        self.needs_benchmark = False

    def emit(self, line: str, sym_line: int = None):
        self.output.append("    " * self.indent + line)
        if not hasattr(self, "_linemap"):
            self._linemap = {}
        if sym_line:
            self._linemap[len(self.output)] = sym_line

    def emit_blank(self):
        self.output.append("")

    def generate(self, program: Program) -> str:
        """Generate complete Python source from AST"""
        # First pass: collect all Python imports for auto-install
        self._collect_python_imports(program)

        # Auto-install missing packages (skip ones present as local files)
        import os as _os2
        srcdir = _os2.path.dirname(_os2.path.abspath(self.filename)) if getattr(self, 'filename', None) else _os2.getcwd()
        for mod in self.python_imports:
            top = mod.split(".")[0]
            local = (_os2.path.exists(_os2.path.join(srcdir, top + ".py")) or
                     _os2.path.exists(_os2.path.join(srcdir, top + ".sym")) or
                     _os2.path.exists(_os2.path.join(_os2.getcwd(), top + ".py")))
            if local:
                continue
            ensure_installed(mod)

        # Generate header
        self.emit("#!/usr/bin/env python3")
        self.emit("# Generated by Sym compiler")
        self.emit("# Do not edit — regenerate from .sym source")
        self.emit_blank()

        # Runtime imports — the repo that COMPILED this file wins, always.
        # (Otherwise a stale install at SYM_HOME shadows new builtins.)
        import os as _hostos
        _root_at_compile_time = _hostos.path.dirname(
            _hostos.path.dirname(_hostos.path.abspath(__file__)))
        self.emit("import sys as _sys, os as _os")
        self.emit("# Sym runtime discovery — compiling repo pinned first")
        self.emit(f"_SYM_ROOT = {_root_at_compile_time!r}")
        self.emit("for _p in [_os.environ.get('SYM_HOME', ''),")
        self.emit("           _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..'),")
        self.emit("           _os.path.dirname(_os.path.abspath(__file__)),")
        self.emit("           _SYM_ROOT]:")
        self.emit("    if _p and _os.path.isdir(_os.path.join(_p, 'runtime')) and _p not in _sys.path:")
        self.emit("        _sys.path.insert(0, _p)")
        self.emit("try:")
        self.emit("    from runtime.builtins import *")
        self.emit("except ImportError:")
        self.emit("    import copy as _copy, math as _math")
        self.emit("    def _sym_smart_sort(d):")
        self.emit("        if isinstance(d,list): d.sort()")
        self.emit("        return d")
        self.emit("    def _sym_clone(o): return _copy.deepcopy(o)")
        self.emit("    sqrt=_math.sqrt;sin=_math.sin;cos=_math.cos;log=_math.log")
        self.emit("    PI=_math.pi;E=_math.e;floor=_math.floor;ceil=_math.ceil")
        self.emit_blank()

        # Generate statements
        for stmt in program.statements:
            self.gen_statement(stmt)

        result = "\n".join(self.output) + "\n"
        lm = getattr(self, "_linemap", {})
        hook_body = (
            "import sys as _sys2, traceback as _tb2\n"
            "__SYM_LINEMAP__ = %s\n"
            "__SYM_SRC__ = %s\n"
            "def __sym_excepthook(et, ev, tb):\n"
            "    frames = _tb2.extract_tb(tb)\n"
            "    for fr in reversed(frames):\n"
            "        if fr.filename.endswith('.gen.py') and fr.lineno in __SYM_LINEMAP__:\n"
            "            sl = __SYM_LINEMAP__[fr.lineno]\n"
            "            print(f'\\n\\u274c {et.__name__}: {ev}', file=_sys2.stderr)\n"
            "            print(f'   at {__SYM_SRC__} line {sl}', file=_sys2.stderr)\n"
            "            return\n"
            "    _tb2.print_exception(et, ev, tb)\n"
            "_sys2.excepthook = __sym_excepthook\n"
        )
        n_hook = hook_body.count("\n")  # lines the hook occupies (inserted after shebang)
        shifted = {k + n_hook: v for k, v in lm.items()}
        hook = hook_body % (repr(shifted), repr(getattr(self, "sym_source_name", "source.sym")))
        lines = result.split("\n", 1)
        return lines[0] + "\n" + hook + (lines[1] if len(lines) > 1 else "")

    def _collect_python_imports(self, program: Program):
        """Walk AST to find all py.* imports"""
        for stmt in program.statements:
            if isinstance(stmt, ImportStatement) and stmt.is_python:
                mod = stmt.module.removeprefix("py.")
                self.python_imports.add(mod)
            elif isinstance(stmt, ImportStatement) and stmt.from_module and stmt.from_module.startswith("py."):
                mod = stmt.from_module.removeprefix("py.")
                self.python_imports.add(mod)

    # ── Statements ─────────────────────────────────────────────
    def gen_statement(self, node: Node):
        if not hasattr(self, "_linemap"):
            self._linemap = {}
        _ln = getattr(node, "line", None)
        if _ln:
            self._linemap[len(self.output) + 1] = _ln
        if isinstance(node, LetStatement):
            self.gen_let(node)
        elif isinstance(node, AssignStatement):
            self.gen_assign(node)
        elif isinstance(node, FunctionDef):
            self.gen_function(node)
        elif isinstance(node, ReturnStatement):
            self.gen_return(node)
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
        elif isinstance(node, MatchStatement):
            self.gen_match(node)
        elif isinstance(node, TryStatement):
            self.gen_try(node)
        elif isinstance(node, ExpressionStatement):
            self.emit(self.gen_expr(node.expr))
        elif isinstance(node, BreakStatement):
            self.emit("break")
        elif isinstance(node, ContinueStatement):
            self.emit("continue")
        elif isinstance(node, DirectiveStatement):
            self.emit(f"# directive: {node.name}")
        elif isinstance(node, TraitDef):
            self.gen_trait(node)
        elif isinstance(node, ImplBlock):
            self.gen_impl(node)

    def gen_let(self, node: LetStatement):
        val = self.gen_expr(node.value)
        self.emit(f"{node.name} = {val}")

    def gen_assign(self, node: AssignStatement):
        target = self.gen_expr(node.target)
        val = self.gen_expr(node.value)
        self.emit(f"{target} {node.op} {val}")

    def gen_function(self, node: FunctionDef):
        _async = "async " if getattr(node, "is_async", False) else ""
        self.emit_blank()
        # Decorators
        for dec in node.decorators:
            self.emit(f"@{dec}")
        if node.is_benchmark:
            self.emit("@_sym_benchmark")

        params = ", ".join(self._gen_param(p) for p in node.params)
        self.emit(f"{_async}def {node.name}({params}):")
        self.indent += 1
        if not node.body:
            self.emit("pass")
        else:
            for stmt in node.body:
                self.gen_statement(stmt)
        self.indent -= 1
        self.emit_blank()

    def _gen_param(self, p: Param) -> str:
        s = p.name
        if p.default:
            s += f"={self.gen_expr(p.default)}"
        return s

    def gen_return(self, node: ReturnStatement):
        if node.value:
            self.emit(f"return {self.gen_expr(node.value)}")
        else:
            self.emit("return")

    def gen_if(self, node: IfStatement):
        self.emit(f"if {self.gen_expr(node.condition)}:")
        self.indent += 1
        for stmt in node.body:
            self.gen_statement(stmt)
        if not node.body:
            self.emit("pass")
        self.indent -= 1
        for elif_c in node.elif_clauses:
            self.emit(f"elif {self.gen_expr(elif_c.condition)}:")
            self.indent += 1
            for stmt in elif_c.body:
                self.gen_statement(stmt)
            self.indent -= 1
        if node.else_body:
            self.emit("else:")
            self.indent += 1
            for stmt in node.else_body:
                self.gen_statement(stmt)
            self.indent -= 1

    def gen_for(self, node: ForLoop):
        iterable = self.gen_expr(node.iterable)
        if node.var2:
            self.emit(f"for {node.var}, {node.var2} in {iterable}:")
        else:
            self.emit(f"for {node.var} in {iterable}:")
        self.indent += 1
        for stmt in node.body:
            self.gen_statement(stmt)
        if not node.body:
            self.emit("pass")
        self.indent -= 1

    def gen_while(self, node: WhileLoop):
        self.emit(f"while {self.gen_expr(node.condition)}:")
        self.indent += 1
        for stmt in node.body:
            self.gen_statement(stmt)
        if not node.body:
            self.emit("pass")
        self.indent -= 1

    def gen_import(self, node: ImportStatement):
        if node.is_python:
            # py.numpy → import numpy
            mod = node.module.removeprefix("py.")
            if node.from_module:
                fmod = node.from_module.removeprefix("py.")
                names = ", ".join(node.names)
                self.emit(f"from {fmod} import {names}")
            elif node.alias:
                self.emit(f"import {mod} as {node.alias}")
            else:
                # Use last part as default alias
                alias = mod.split(".")[-1]
                self.emit(f"import {mod} as {alias}")
        else:
            # Standard library / local import
            if node.from_module:
                names = ", ".join(node.names)
                self.emit(f"from {node.from_module} import {names}")
            elif node.alias:
                self.emit(f"import {node.module} as {node.alias}")
            elif node.module:
                self.emit(f"import {node.module}")

    def gen_struct(self, node: StructDef):
        self.emit_blank()
        self.emit(f"class {node.name}:")
        self.indent += 1
        # __init__
        field_params = ", ".join(
            f"{f.name}={self.gen_expr(f.default)}" if f.default else f.name
            for f in node.fields
        )
        self.emit(f"def __init__(self, {field_params}):")
        self.indent += 1
        for f in node.fields:
            self.emit(f"self.{f.name} = {f.name}")
        if not node.fields:
            self.emit("pass")
        self.indent -= 1
        # __repr__
        fields_repr = ", ".join(f"{f.name}={{self.{f.name}!r}}" for f in node.fields)
        self.emit_blank()
        self.emit(f"def __repr__(self):")
        self.indent += 1
        self.emit(f'return f"{node.name}({fields_repr})"')
        self.indent -= 1
        # Methods
        for method in node.methods:
            self.gen_function(method)
        self.indent -= 1
        self.emit_blank()

    def gen_trait(self, node: TraitDef):
        self.emit_blank()
        self.emit(f"class {node.name}:")
        self.indent += 1
        for method in node.methods:
            params = ", ".join(self._gen_param(p) for p in method.params)
            self.emit(f"def {method.name}({params}):")
            self.indent += 1
            self.emit("raise NotImplementedError")
            self.indent -= 1
            self.emit_blank()
        if not node.methods:
            self.emit("pass")
        self.indent -= 1

    def gen_impl(self, node: ImplBlock):
        """Generate mixin-style impl by adding methods to the struct class"""
        self.emit_blank()
        for method in node.methods:
            params = ", ".join(self._gen_param(p) for p in method.params)
            self.emit(f"def _{node.struct_name}_{method.name}({params}):")
            self.indent += 1
            for stmt in method.body:
                self.gen_statement(stmt)
            if not method.body:
                self.emit("pass")
            self.indent -= 1
            self.emit(f"{node.struct_name}.{method.name} = _{node.struct_name}_{method.name}")
            self.emit_blank()

    def gen_match(self, node: MatchStatement):
        val_expr = self.gen_expr(node.value)
        tmp = f"_match_val_{node.line}"
        self.emit(f"{tmp} = {val_expr}")
        first = True
        for case in node.cases:
            if case.is_wildcard:
                self.emit("else:")
            elif first:
                self.emit(f"if {tmp} == {self.gen_expr(case.pattern)}:")
                first = False
            else:
                self.emit(f"elif {tmp} == {self.gen_expr(case.pattern)}:")
            self.indent += 1
            for stmt in case.body:
                self.gen_statement(stmt)
            self.indent -= 1

    def gen_try(self, node: TryStatement):
        self.emit("try:")
        self.indent += 1
        for stmt in node.body:
            self.gen_statement(stmt)
        self.indent -= 1
        catch_type = node.catch_type or "Exception"
        catch_var = node.catch_var or "_e"
        self.emit(f"except {catch_type} as {catch_var}:")
        self.indent += 1
        if node.catch_body:
            for stmt in node.catch_body:
                self.gen_statement(stmt)
        else:
            self.emit("pass")
        self.indent -= 1

    # ── Expressions ────────────────────────────────────────────
    def gen_expr(self, node: Node) -> str:
        if node is None:
            return "None"
        if isinstance(node, IntLiteral):
            return str(node.value)
        if isinstance(node, FloatLiteral):
            return str(node.value)
        if isinstance(node, StringLiteral):
            return repr(node.value)
        if isinstance(node, FString):
            return self._gen_fstring(node)
        if isinstance(node, BoolLiteral):
            return "True" if node.value else "False"
        if isinstance(node, NoneLiteral):
            return "None"
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, ListLiteral):
            elems = ", ".join(self.gen_expr(e) for e in node.elements)
            return f"[{elems}]"
        if isinstance(node, DictLiteral):
            pairs = ", ".join(
                f"{self.gen_expr(k)}: {self.gen_expr(v)}"
                for k, v in zip(node.keys, node.values)
            )
            return f"{{{pairs}}}"
        if isinstance(node, TupleLiteral):
            elems = ", ".join(self.gen_expr(e) for e in node.elements)
            return f"({elems},)" if len(node.elements) == 1 else f"({elems})"
        if isinstance(node, BinaryOp):
            left = self.gen_expr(node.left)
            right = self.gen_expr(node.right)
            # Translate symbol-mode operators to Python
            op_map = {"≠": "!=", "≤": "<=", "≥": ">=", "≔": "="}
            op = op_map.get(node.op, node.op)
            return f"({left} {op} {right})"
        if isinstance(node, UnaryOp):
            return f"({node.op} {self.gen_expr(node.operand)})"
        if isinstance(node, FunctionCall):
            return self._gen_call(node)
        if isinstance(node, MethodCall):
            obj = self.gen_expr(node.obj)
            args = ", ".join(self.gen_expr(a) for a in node.args)
            kw = ", ".join(f"{k}={self.gen_expr(v)}" for k, v in node.kwargs.items())
            all_args = ", ".join(filter(None, [args, kw]))
            return f"{obj}.{node.method}({all_args})"
        if isinstance(node, MemberAccess):
            return f"{self.gen_expr(node.obj)}.{node.member}"
        if isinstance(node, IndexAccess):
            return f"{self.gen_expr(node.obj)}[{self.gen_expr(node.index)}]"
        if isinstance(node, SliceAccess):
            start = self.gen_expr(node.start) if node.start else ""
            stop = self.gen_expr(node.stop) if node.stop else ""
            return f"{self.gen_expr(node.obj)}[{start}:{stop}]"
        if isinstance(node, Lambda):
            params = ", ".join(node.params)
            body = self.gen_expr(node.body)
            return f"(lambda {params}: {body})"
        if isinstance(node, Ternary):
            return f"({self.gen_expr(node.true_val)} if {self.gen_expr(node.condition)} else {self.gen_expr(node.false_val)})"
        if isinstance(node, RangeLiteral):
            start = self.gen_expr(node.start)
            stop = self.gen_expr(node.stop)
            if node.step:
                step = self.gen_expr(node.step)
                return f"range({start}, {stop}, {step})"
            return f"range({start}, {stop})"
        return f"<UNKNOWN:{type(node).__name__}>"

    def _gen_fstring(self, node: FString) -> str:
        parts = []
        for p in node.parts:
            if isinstance(p, str):
                # escape braces, then backslashes/quotes/newlines so the
                # emitted f-string stays a single valid literal
                s = p.replace("{", "{{").replace("}", "}}")
                s = s.replace("\\", "\\\\").replace('"', '\\"')
                s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                parts.append(s)
            else:
                parts.append("{" + self.gen_expr(p) + "}")
        return 'f"' + "".join(parts) + '"'

    def _gen_call(self, node: FunctionCall) -> str:
        func_name = self.gen_expr(node.func)
        args = ", ".join(self.gen_expr(a) for a in node.args)
        kw = ", ".join(f"{k}={self.gen_expr(v)}" for k, v in node.kwargs.items())
        all_args = ", ".join(filter(None, [args, kw]))

        # Smart builtins
        if func_name == "sort" and len(node.args) == 1:
            return f"_sym_smart_sort({args})"
        if func_name == "print":
            return f"print({all_args})"
        if func_name == "len":
            return f"len({all_args})"
        if func_name == "map" and len(node.args) == 2:
            return f"list(map({self.gen_expr(node.args[1])}, {self.gen_expr(node.args[0])}))"
        if func_name == "filter" and len(node.args) == 2:
            return f"list(filter({self.gen_expr(node.args[1])}, {self.gen_expr(node.args[0])}))"
        if func_name == "reduce":
            return f"functools.reduce({all_args})"
        if func_name == "enumerate":
            return f"enumerate({all_args})"
        if func_name == "zip":
            return f"zip({all_args})"
        if func_name == "type":
            return f"type({all_args})"
        if func_name == "clone":
            return f"_sym_clone({all_args})"
        if func_name == "range":
            return f"range({all_args})"
        return f"{func_name}({all_args})"


def generate_python(program: Program, source_name: str = "source.sym") -> str:
    cg = PythonCodegen()
    cg.sym_source_name = source_name
    return cg.generate(program)
