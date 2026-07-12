"""
Sym AST Node Definitions
All syntax modes (normal, compact, symbol) parse into these same nodes.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Any


# ── Base ───────────────────────────────────────────────────────
@dataclass
class Node:
    line: int = 0
    col: int = 0

@dataclass
class Program(Node):
    statements: List[Node] = field(default_factory=list)
    mode: str = "normal"  # normal | compact | symbol


# ── Literals ───────────────────────────────────────────────────
@dataclass
class IntLiteral(Node):
    value: int = 0

@dataclass
class FloatLiteral(Node):
    value: float = 0.0

@dataclass
class StringLiteral(Node):
    value: str = ""
    is_fstring: bool = False  # contains {expr} interpolation

@dataclass
class BoolLiteral(Node):
    value: bool = False

@dataclass
class NoneLiteral(Node):
    pass

@dataclass
class ListLiteral(Node):
    elements: List[Node] = field(default_factory=list)

@dataclass
class DictLiteral(Node):
    keys: List[Node] = field(default_factory=list)
    values: List[Node] = field(default_factory=list)

@dataclass
class SetLiteral(Node):
    elements: List[Node] = field(default_factory=list)

@dataclass
class TupleLiteral(Node):
    elements: List[Node] = field(default_factory=list)


# ── Expressions ────────────────────────────────────────────────
@dataclass
class Identifier(Node):
    name: str = ""

@dataclass
class BinaryOp(Node):
    left: Node = None
    op: str = ""      # +, -, *, /, //, %, **, ==, !=, <, >, <=, >=, and, or, in, |>
    right: Node = None

@dataclass
class UnaryOp(Node):
    op: str = ""       # -, not, ~
    operand: Node = None

@dataclass
class FunctionCall(Node):
    func: Node = None
    args: List[Node] = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)  # name -> Node

@dataclass
class MethodCall(Node):
    obj: Node = None
    method: str = ""
    args: List[Node] = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)

@dataclass
class IndexAccess(Node):
    obj: Node = None
    index: Node = None

@dataclass
class SliceAccess(Node):
    obj: Node = None
    start: Optional[Node] = None
    stop: Optional[Node] = None
    step: Optional[Node] = None

@dataclass
class MemberAccess(Node):
    obj: Node = None
    member: str = ""

@dataclass
class Lambda(Node):
    params: List[str] = field(default_factory=list)
    body: Node = None

@dataclass
class Ternary(Node):
    condition: Node = None
    true_val: Node = None
    false_val: Node = None

@dataclass
class RangeLiteral(Node):
    start: Node = None
    stop: Node = None
    step: Optional[Node] = None

@dataclass
class FString(Node):
    """String with {expr} interpolation"""
    parts: List[Any] = field(default_factory=list)  # str or Node alternating

@dataclass
class PipeExpr(Node):
    """value |> func1 |> func2"""
    value: Node = None
    functions: List[Node] = field(default_factory=list)


# ── Statements ─────────────────────────────────────────────────
@dataclass
class LetStatement(Node):
    name: str = ""
    type_annotation: Optional[str] = None
    value: Node = None
    is_const: bool = False

@dataclass
class AssignStatement(Node):
    target: Node = None
    value: Node = None
    op: str = "="  # =, +=, -=, *=, /=

@dataclass
class FunctionDef(Node):
    name: str = ""
    params: List['Param'] = field(default_factory=list)
    return_type: Optional[str] = None
    body: List[Node] = field(default_factory=list)
    is_optimize: bool = False
    is_benchmark: bool = False
    is_gpu: bool = False
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)

@dataclass
class Param(Node):
    name: str = ""
    type_annotation: Optional[str] = None
    default: Optional[Node] = None

@dataclass
class ReturnStatement(Node):
    value: Optional[Node] = None

@dataclass
class IfStatement(Node):
    condition: Node = None
    body: List[Node] = field(default_factory=list)
    elif_clauses: List['ElifClause'] = field(default_factory=list)
    else_body: List[Node] = field(default_factory=list)

@dataclass
class ElifClause(Node):
    condition: Node = None
    body: List[Node] = field(default_factory=list)

@dataclass
class ForLoop(Node):
    var: str = ""
    var2: Optional[str] = None  # for i, val in ...
    iterable: Node = None
    body: List[Node] = field(default_factory=list)

@dataclass
class WhileLoop(Node):
    condition: Node = None
    body: List[Node] = field(default_factory=list)

@dataclass
class MatchStatement(Node):
    value: Node = None
    cases: List['MatchCase'] = field(default_factory=list)

@dataclass
class MatchCase(Node):
    pattern: Node = None  # None means wildcard (_)
    is_wildcard: bool = False
    body: List[Node] = field(default_factory=list)

@dataclass
class ImportStatement(Node):
    module: str = ""       # e.g. "py.numpy" or "math"
    alias: Optional[str] = None
    is_python: bool = False  # True if starts with py.
    from_module: Optional[str] = None
    names: List[str] = field(default_factory=list)

@dataclass
class StructDef(Node):
    name: str = ""
    fields: List['StructField'] = field(default_factory=list)
    methods: List[FunctionDef] = field(default_factory=list)

@dataclass
class StructField(Node):
    name: str = ""
    type_annotation: Optional[str] = None
    default: Optional[Node] = None

@dataclass
class TraitDef(Node):
    name: str = ""
    methods: List[FunctionDef] = field(default_factory=list)

@dataclass
class ImplBlock(Node):
    trait_name: str = ""
    struct_name: str = ""
    methods: List[FunctionDef] = field(default_factory=list)

@dataclass
class TryStatement(Node):
    body: List[Node] = field(default_factory=list)
    catch_var: Optional[str] = None
    catch_type: Optional[str] = None
    catch_body: List[Node] = field(default_factory=list)

@dataclass
class BreakStatement(Node):
    pass

@dataclass
class ContinueStatement(Node):
    pass

@dataclass
class ExpressionStatement(Node):
    """Wraps a bare expression as a statement (e.g. function call)"""
    expr: Node = None

@dataclass
class Comment(Node):
    text: str = ""

@dataclass
class DirectiveStatement(Node):
    """#compact, #symbol, #manual_memory, etc."""
    name: str = ""
    value: Optional[str] = None
