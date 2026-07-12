# typecheck.py - lightweight static type checker for Sym.
# Uses the type annotations you already write. Catches obvious mismatches
# before runtime. Warnings, not hard errors (gradual typing).

from .ast_nodes import *

NUM = {"int", "float"}

def _lit_type(node):
    if isinstance(node, IntLiteral): return "int"
    if isinstance(node, FloatLiteral): return "float"
    if isinstance(node, StringLiteral): return "str"
    if isinstance(node, FString): return "str"
    if isinstance(node, BoolLiteral): return "bool"
    if isinstance(node, ListLiteral): return "list"
    if isinstance(node, DictLiteral): return "dict"
    return None

class TypeChecker:
    def __init__(self):
        self.warnings = []
        self.func_returns = {}   # name -> declared return type
        self.func_params = {}    # name -> [types]
        self.vars = {}           # name -> inferred type (current scope)

    def check(self, program: Program):
        # pass 1: collect function signatures
        for s in program.statements:
            if isinstance(s, FunctionDef):
                self.func_returns[s.name] = s.return_type
                self.func_params[s.name] = [p.type_annotation for p in s.params]
        # pass 2: check bodies + top level
        for s in program.statements:
            self._check_stmt(s)
        return self.warnings

    def _warn(self, node, msg):
        ln = getattr(node, "line", "?")
        self.warnings.append(f"line {ln}: {msg}")

    def _check_stmt(self, s):
        if isinstance(s, FunctionDef):
            saved = self.vars
            self.vars = {p.name: p.type_annotation for p in s.params}
            for st in s.body:
                self._check_stmt(st)
            self.vars = saved
        elif isinstance(s, LetStatement):
            declared = getattr(s, "type_annotation", None)
            actual = self._infer(s.value)
            if declared and actual and declared != actual and not (declared in NUM and actual in NUM):
                self._warn(s, f"'{s.name}' declared {declared} but assigned {actual}")
            self.vars[s.name] = declared or actual
        elif isinstance(s, ReturnStatement):
            pass  # checked per-function below via _infer if needed
        elif isinstance(s, IfStatement):
            for st in s.body: self._check_stmt(st)
            for ec in s.elif_clauses:
                for st in ec.body: self._check_stmt(st)
            for st in s.else_body: self._check_stmt(st)
        elif isinstance(s, (ForLoop, WhileLoop)):
            for st in s.body: self._check_stmt(st)

    def _infer(self, node):
        t = _lit_type(node)
        if t: return t
        if isinstance(node, Identifier):
            return self.vars.get(node.name)
        if isinstance(node, BinaryOp):
            lt = self._infer(node.left); rt = self._infer(node.right)
            if node.op in ("<", ">", "<=", ">=", "==", "!=", "≠", "≤", "≥"):
                return "bool"
            if lt in NUM and rt in NUM:
                return "float" if "float" in (lt, rt) else "int"
            if lt == "str" or rt == "str":
                return "str"
            return lt or rt
        if isinstance(node, FunctionCall):
            name = node.func.name if isinstance(node.func, Identifier) else None
            # arg count / type check against signature
            if name in self.func_params:
                expected = self.func_params[name]
                for i, a in enumerate(node.args):
                    if i < len(expected) and expected[i]:
                        at = self._infer(a)
                        if at and expected[i] != at and not (expected[i] in NUM and at in NUM):
                            self._warn(node, f"{name}() arg {i+1} expects {expected[i]}, got {at}")
                return self.func_returns.get(name)
        return None


def typecheck(program: Program):
    return TypeChecker().check(program)
