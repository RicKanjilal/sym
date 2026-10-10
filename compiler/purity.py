"""
Purity report — which functions compile to native C, and why the rest don't.

    from compiler.purity import purity_report
    purity_report(program) -> [{"name", "line", "native", "reason", "reason_line"}]

Uses the real PurityAnalyzer (the same verdict `sym run` acts on); the reasons
are reconstructed by walking the body with the same rules the analyzer uses.
"""

from .ast_nodes import *
from .hybrid import PurityAnalyzer

_DYNAMIC_CALLS = ("str", "int", "float", "dict", "list", "open", "input",
                  "sorted", "map", "filter", "range")


def _line(node, fallback=0):
    return getattr(node, "line", 0) or fallback


def _expr_reason(node, line):
    if node is None:
        return None
    if isinstance(node, Identifier) and node.name.startswith("__") and node.name != "__name__":
        return (f"uses runtime internal {node.name}", line)
    if isinstance(node, DictLiteral):
        return ("builds a dict — C has no dict type", line)
    if isinstance(node, ListLiteral):
        return ("builds a list literal — dynamic, stays Python", line)
    if isinstance(node, (StringLiteral, FString)):
        return ("works with strings — stays Python", line)
    if isinstance(node, BinaryOp):
        return _expr_reason(node.left, line) or _expr_reason(node.right, line)
    if isinstance(node, MethodCall):
        return (f".{node.method}() is a dynamic method call", line)
    if isinstance(node, FunctionCall):
        fname = node.func.name if isinstance(node.func, Identifier) else ""
        if fname == "round" and len(node.args) > 1:
            return ("round(x, n) needs Python", line)
        if fname in _DYNAMIC_CALLS:
            return (f"calls {fname}(), a dynamic builtin", line)
        for a in node.args:
            r = _expr_reason(a, line)
            if r:
                return r
    return None


def _body_reason(body):
    for stmt in body:
        ln = _line(stmt)
        if isinstance(stmt, (StructDef, TraitDef, ImplBlock)):
            return ("defines a type inside the function", ln)
        if isinstance(stmt, TryStatement):
            return ("uses try/catch — exceptions need Python", ln)
        if isinstance(stmt, MatchStatement):
            return ("uses match — not lowered to C yet", ln)
        if isinstance(stmt, AssignStatement):
            r = _expr_reason(stmt.value, ln)
            if r:
                return r
        if isinstance(stmt, LetStatement) and stmt.value is not None:
            r = _expr_reason(stmt.value, ln)
            if r:
                return r
        if isinstance(stmt, ReturnStatement) and getattr(stmt, "value", None) is not None:
            r = _expr_reason(stmt.value, ln)
            if r:
                return r
        if isinstance(stmt, ExpressionStatement) and isinstance(stmt.expr, FunctionCall) \
                and isinstance(stmt.expr.func, Identifier) and stmt.expr.func.name == "print":
            for arg in stmt.expr.args:
                if isinstance(arg, (StringLiteral, FString)):
                    return ("prints a string — formatted output stays Python", ln)
        if isinstance(stmt, IfStatement):
            r = _body_reason(stmt.body) or _body_reason(stmt.else_body)
            for ec in stmt.elif_clauses:
                r = r or _body_reason(ec.body)
            if r:
                return r
        if isinstance(stmt, (ForLoop, WhileLoop)):
            r = _body_reason(stmt.body)
            if r:
                return r
    return None


def purity_report(program: Program):
    an = PurityAnalyzer()
    pure, _ = an.analyze(program)
    pure_names = {f.name for f in pure}
    out = []
    for stmt in program.statements:
        if not isinstance(stmt, FunctionDef):
            continue
        entry = {"name": stmt.name, "line": stmt.line, "native": stmt.name in pure_names,
                 "reason": None, "reason_line": None}
        if not entry["native"]:
            why = an.reasons.get(stmt.name)
            if why is None:
                if stmt.is_benchmark:
                    why = ("benchmark functions time themselves in Python", stmt.line)
                elif any(p.name == "self" for p in stmt.params):
                    why = ("methods (self) stay Python", stmt.line)
                else:
                    why = _body_reason(stmt.body) or ("not C-compatible", stmt.line)
            entry["reason"], entry["reason_line"] = why[0], why[1] or stmt.line
        out.append(entry)
    return out
