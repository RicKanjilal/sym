"""
Sym Parser
Recursive descent parser. Builds AST from token stream.
Handles all syntax constructs: functions, structs, control flow,
Python interop imports, match statements, etc.
"""

from typing import List, Optional
from .lexer import Token, TT
from .ast_nodes import *


class Parser:
    def __init__(self, tokens: List[Token], filename: str = "<stdin>"):
        self.tokens = tokens
        self.filename = filename
        self.pos = 0

    def error(self, msg: str):
        tok = self.current()
        raise SyntaxError(f"{self.filename}:{tok.line}:{tok.col}: {msg} (got {tok.type.name} '{tok.value}')")

    def current(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token(TT.EOF, "", 0, 0)

    def peek(self, offset=1) -> Token:
        p = self.pos + offset
        return self.tokens[p] if p < len(self.tokens) else Token(TT.EOF, "", 0, 0)

    def at(self, *types: TT) -> bool:
        return self.current().type in types

    def eat(self, tt: TT) -> Token:
        tok = self.current()
        if tok.type != tt:
            self.error(f"Expected {tt.name}")
        self.pos += 1
        return tok

    def maybe(self, tt: TT) -> Optional[Token]:
        if self.at(tt):
            tok = self.current()
            self.pos += 1
            return tok
        return None

    def skip_newlines(self):
        while self.at(TT.NEWLINE):
            self.pos += 1

    # ── Entry Point ────────────────────────────────────────────
    def parse(self) -> Program:
        prog = Program()
        # Check for mode directive
        if self.at(TT.DIRECTIVE):
            prog.mode = self.current().value
            self.pos += 1
            self.skip_newlines()

        while not self.at(TT.EOF):
            self.skip_newlines()
            if self.at(TT.EOF):
                break
            stmt = self.parse_statement()
            if stmt:
                prog.statements.append(stmt)
        return prog

    # ── Statements ─────────────────────────────────────────────
    def parse_statement(self) -> Optional[Node]:
        self.skip_newlines()
        if self.at(TT.EOF):
            return None

        tok = self.current()

        if tok.type == TT.DIRECTIVE:
            return self.parse_directive()
        if tok.type == TT.ASYNC:
            self.advance()
            fn = self.parse_function_def()
            fn.is_async = True
            return fn
        if tok.type == TT.FN:
            return self.parse_function_def()
        if tok.type == TT.OPTIMIZE:
            return self.parse_decorated_function("optimize")
        if tok.type == TT.BENCHMARK:
            return self.parse_decorated_function("benchmark")
        if tok.type == TT.AT and self.peek().type == TT.IDENT:
            return self.parse_decorator_function()
        if tok.type == TT.LET:
            return self.parse_let()
        if tok.type == TT.CONST:
            return self.parse_const()
        if tok.type == TT.IF:
            return self.parse_if()
        if tok.type == TT.FOR:
            return self.parse_for()
        if tok.type == TT.WHILE:
            return self.parse_while()
        if tok.type == TT.RETURN:
            return self.parse_return()
        if tok.type == TT.IMPORT:
            return self.parse_import()
        if tok.type == TT.FROM:
            return self.parse_from_import()
        if tok.type == TT.STRUCT:
            return self.parse_struct()
        if tok.type == TT.TRAIT:
            return self.parse_trait()
        if tok.type == TT.IMPL:
            return self.parse_impl()
        if tok.type == TT.MATCH:
            return self.parse_match()
        if tok.type == TT.TRY:
            return self.parse_try()
        if tok.type == TT.BREAK:
            self.pos += 1
            self.skip_newlines()
            return BreakStatement(line=tok.line, col=tok.col)
        if tok.type == TT.CONTINUE:
            self.pos += 1
            self.skip_newlines()
            return ContinueStatement(line=tok.line, col=tok.col)
        if tok.type in (TT.PRINT, TT.SORT):
            return self.parse_builtin_call(tok.type)

        # Assignment or expression statement
        return self.parse_assignment_or_expr()

    def parse_directive(self) -> DirectiveStatement:
        tok = self.eat(TT.DIRECTIVE)
        self.skip_newlines()
        return DirectiveStatement(name=tok.value, line=tok.line, col=tok.col)

    # ── Functions ──────────────────────────────────────────────
    def parse_function_def(self, is_optimize=False, is_benchmark=False, decorators=None) -> FunctionDef:
        tok = self.eat(TT.FN)
        name = self.eat(TT.IDENT).value
        self.eat(TT.LPAREN)
        params = self.parse_params()
        self.eat(TT.RPAREN)

        return_type = None
        if self.maybe(TT.ARROW):
            return_type = self.parse_type_annotation()

        # Single expression with =>
        if self.maybe(TT.FAT_ARROW):
            expr = self.parse_expression()
            self.skip_newlines()
            body = [ReturnStatement(value=expr)]
            return FunctionDef(name=name, params=params, return_type=return_type,
                             body=body, is_optimize=is_optimize, is_benchmark=is_benchmark,
                             decorators=decorators or [], line=tok.line, col=tok.col)

        self.skip_newlines()
        body = self.parse_block()
        return FunctionDef(name=name, params=params, return_type=return_type,
                         body=body, is_optimize=is_optimize, is_benchmark=is_benchmark,
                         decorators=decorators or [], line=tok.line, col=tok.col)

    def parse_decorated_function(self, kind: str) -> FunctionDef:
        self.pos += 1  # skip optimize/benchmark
        return self.parse_function_def(
            is_optimize=(kind == "optimize"),
            is_benchmark=(kind == "benchmark")
        )

    def parse_decorator_function(self) -> FunctionDef:
        decorators = []
        while self.at(TT.AT):
            self.pos += 1
            name = self.eat(TT.IDENT).value
            decorators.append(name)
            self.skip_newlines()
        return self.parse_function_def(decorators=decorators)

    def parse_params(self) -> List[Param]:
        params = []
        while not self.at(TT.RPAREN):
            if params:
                self.eat(TT.COMMA)
            if self.at(TT.SELF):
                self.pos += 1
                params.append(Param(name="self"))
                continue
            name = self.eat(TT.IDENT).value
            type_ann = None
            default = None
            if self.maybe(TT.COLON):
                type_ann = self.parse_type_annotation()
            if self.maybe(TT.ASSIGN):
                default = self.parse_expression()
            params.append(Param(name=name, type_annotation=type_ann, default=default))
        return params

    def parse_type_annotation(self) -> str:
        """Parse type annotation like int, str, list<int>, float[100], int?"""
        name = self.eat(TT.IDENT).value
        # Generic: list<int>
        if self.maybe(TT.LT):
            inner = self.parse_type_annotation()
            if self.maybe(TT.COMMA):
                inner2 = self.parse_type_annotation()
                self.eat(TT.GT)
                return f"{name}<{inner},{inner2}>"
            self.eat(TT.GT)
            return f"{name}<{inner}>"
        # Fixed array: float[100]
        if self.maybe(TT.LBRACKET):
            size = self.eat(TT.INTEGER).value
            self.eat(TT.RBRACKET)
            return f"{name}[{size}]"
        # Optional: int?
        if self.maybe(TT.QUESTION):
            return f"{name}?"
        return name

    # ── Variables ──────────────────────────────────────────────
    def parse_let(self) -> LetStatement:
        tok = self.eat(TT.LET)
        name = self.eat(TT.IDENT).value
        type_ann = None
        if self.maybe(TT.COLON):
            type_ann = self.parse_type_annotation()
        self.eat(TT.ASSIGN)
        value = self.parse_expression()
        self.skip_newlines()
        return LetStatement(name=name, type_annotation=type_ann, value=value, line=tok.line, col=tok.col)

    def parse_const(self) -> LetStatement:
        tok = self.eat(TT.CONST)
        name = self.eat(TT.IDENT).value
        type_ann = None
        if self.maybe(TT.COLON):
            type_ann = self.parse_type_annotation()
        self.eat(TT.ASSIGN)
        value = self.parse_expression()
        self.skip_newlines()
        return LetStatement(name=name, type_annotation=type_ann, value=value, is_const=True, line=tok.line, col=tok.col)

    # ── Control Flow ───────────────────────────────────────────
    def parse_if(self) -> IfStatement:
        tok = self.eat(TT.IF)
        cond = self.parse_expression()
        self.skip_newlines()
        body = self.parse_block()
        elifs = []
        else_body = []
        while self.at(TT.ELIF):
            self.pos += 1
            elif_cond = self.parse_expression()
            self.skip_newlines()
            elif_body = self.parse_block()
            elifs.append(ElifClause(condition=elif_cond, body=elif_body))
        if self.maybe(TT.ELSE):
            self.skip_newlines()
            else_body = self.parse_block()
        return IfStatement(condition=cond, body=body, elif_clauses=elifs, else_body=else_body, line=tok.line)

    def parse_for(self) -> ForLoop:
        tok = self.eat(TT.FOR)
        var1 = self.eat(TT.IDENT).value
        var2 = None
        if self.maybe(TT.COMMA):
            var2 = self.eat(TT.IDENT).value
            # Swap: for i, val → var=i, var2=val
        self.eat(TT.IN)
        iterable = self.parse_expression()
        self.skip_newlines()
        body = self.parse_block()
        if var2:
            return ForLoop(var=var1, var2=var2, iterable=iterable, body=body, line=tok.line)
        return ForLoop(var=var1, iterable=iterable, body=body, line=tok.line)

    def parse_while(self) -> WhileLoop:
        tok = self.eat(TT.WHILE)
        cond = self.parse_expression()
        self.skip_newlines()
        body = self.parse_block()
        return WhileLoop(condition=cond, body=body, line=tok.line)

    def parse_return(self) -> ReturnStatement:
        tok = self.eat(TT.RETURN)
        value = None
        if not self.at(TT.NEWLINE, TT.EOF, TT.DEDENT):
            value = self.parse_expression()
        self.skip_newlines()
        return ReturnStatement(value=value, line=tok.line)

    def parse_match(self) -> MatchStatement:
        tok = self.eat(TT.MATCH)
        value = self.parse_expression()
        self.skip_newlines()
        self.eat(TT.INDENT)
        cases = []
        while not self.at(TT.DEDENT, TT.EOF):
            self.skip_newlines()
            if self.at(TT.DEDENT, TT.EOF):
                break
            if self.at(TT.UNDERSCORE):
                self.pos += 1
                self.eat(TT.FAT_ARROW)
                case_body = [self.parse_statement()]
                cases.append(MatchCase(is_wildcard=True, body=case_body))
            else:
                pattern = self.parse_expression()
                self.eat(TT.FAT_ARROW)
                case_body = [self.parse_statement()]
                cases.append(MatchCase(pattern=pattern, body=case_body))
            self.skip_newlines()
        self.maybe(TT.DEDENT)
        return MatchStatement(value=value, cases=cases, line=tok.line)

    def parse_try(self) -> TryStatement:
        tok = self.eat(TT.TRY)
        self.skip_newlines()
        body = self.parse_block()
        catch_var = None
        catch_type = None
        catch_body = []
        if self.maybe(TT.CATCH):
            if self.at(TT.IDENT):
                catch_var = self.eat(TT.IDENT).value
                if self.maybe(TT.COLON):
                    catch_type = self.eat(TT.IDENT).value
            self.skip_newlines()
            catch_body = self.parse_block()
        return TryStatement(body=body, catch_var=catch_var, catch_type=catch_type, catch_body=catch_body, line=tok.line)

    # ── Imports ────────────────────────────────────────────────
    def parse_import(self) -> ImportStatement:
        tok = self.eat(TT.IMPORT)
        module = self._read_dotted_name()
        is_python = module.startswith("py.")
        alias = None
        if self.maybe(TT.AS):
            alias = self.eat(TT.IDENT).value
        self.skip_newlines()
        return ImportStatement(module=module, alias=alias, is_python=is_python, line=tok.line)

    def parse_from_import(self) -> ImportStatement:
        tok = self.eat(TT.FROM)
        module = self._read_dotted_name()
        is_python = module.startswith("py.")
        self.eat(TT.IMPORT)
        names = [self.eat(TT.IDENT).value]
        while self.maybe(TT.COMMA):
            names.append(self.eat(TT.IDENT).value)
        alias = None
        if self.maybe(TT.AS):
            alias = self.eat(TT.IDENT).value
        self.skip_newlines()
        return ImportStatement(from_module=module, names=names, is_python=is_python, line=tok.line)

    def _read_dotted_name(self) -> str:
        name = self.eat(TT.IDENT).value
        while self.maybe(TT.DOT):
            name += "." + self.eat(TT.IDENT).value
        return name

    # ── Structs ────────────────────────────────────────────────
    def parse_struct(self) -> StructDef:
        tok = self.eat(TT.STRUCT)
        name = self.eat(TT.IDENT).value
        self.skip_newlines()
        self.eat(TT.INDENT)
        fields = []
        methods = []
        while not self.at(TT.DEDENT, TT.EOF):
            self.skip_newlines()
            if self.at(TT.DEDENT, TT.EOF):
                break
            if self.at(TT.FN):
                methods.append(self.parse_function_def())
            else:
                fname = self.eat(TT.IDENT).value
                self.eat(TT.COLON)
                ftype = self.parse_type_annotation()
                default = None
                if self.maybe(TT.ASSIGN):
                    default = self.parse_expression()
                fields.append(StructField(name=fname, type_annotation=ftype, default=default))
                self.skip_newlines()
        self.maybe(TT.DEDENT)
        return StructDef(name=name, fields=fields, methods=methods, line=tok.line)

    def parse_trait(self) -> TraitDef:
        tok = self.eat(TT.TRAIT)
        name = self.eat(TT.IDENT).value
        self.skip_newlines()
        self.eat(TT.INDENT)
        methods = []
        while not self.at(TT.DEDENT, TT.EOF):
            self.skip_newlines()
            if self.at(TT.DEDENT, TT.EOF):
                break
            methods.append(self.parse_function_def())
        self.maybe(TT.DEDENT)
        return TraitDef(name=name, methods=methods, line=tok.line)

    def parse_impl(self) -> ImplBlock:
        tok = self.eat(TT.IMPL)
        trait_name = self.eat(TT.IDENT).value
        self.eat(TT.FOR)
        struct_name = self.eat(TT.IDENT).value
        self.skip_newlines()
        self.eat(TT.INDENT)
        methods = []
        while not self.at(TT.DEDENT, TT.EOF):
            self.skip_newlines()
            if self.at(TT.DEDENT, TT.EOF):
                break
            methods.append(self.parse_function_def())
        self.maybe(TT.DEDENT)
        return ImplBlock(trait_name=trait_name, struct_name=struct_name, methods=methods, line=tok.line)

    # ── Builtins ───────────────────────────────────────────────
    def parse_builtin_call(self, tt: TT) -> ExpressionStatement:
        tok = self.current()
        self.pos += 1
        if tt == TT.PRINT:
            args = []
            while not self.at(TT.NEWLINE, TT.EOF, TT.DEDENT):
                args.append(self.parse_expression())
                if not self.maybe(TT.COMMA):
                    break
            self.skip_newlines()
            return ExpressionStatement(
                expr=FunctionCall(func=Identifier(name="print"), args=args),
                line=tok.line, col=tok.col
            )
        elif tt == TT.SORT:
            arg = self.parse_expression()
            self.skip_newlines()
            return ExpressionStatement(
                expr=FunctionCall(func=Identifier(name="sort"), args=[arg]),
                line=tok.line, col=tok.col
            )

    # ── Assignment or Expression ───────────────────────────────
    def parse_assignment_or_expr(self) -> Node:
        expr = self.parse_expression()
        tok = self.current()
        if tok.type == TT.ASSIGN:
            self.pos += 1
            value = self.parse_expression()
            self.skip_newlines()
            return AssignStatement(target=expr, value=value, line=tok.line)
        if tok.type in (TT.PLUS_EQ, TT.MINUS_EQ, TT.STAR_EQ, TT.SLASH_EQ):
            op = tok.value
            self.pos += 1
            value = self.parse_expression()
            self.skip_newlines()
            return AssignStatement(target=expr, value=value, op=op, line=tok.line)
        self.skip_newlines()
        return ExpressionStatement(expr=expr, line=expr.line if hasattr(expr, 'line') else 0)

    # ── Block (indented body) ──────────────────────────────────
    def parse_block(self) -> List[Node]:
        self.skip_newlines()
        if not self.at(TT.INDENT):
            # Single statement on same line (not indented block)
            stmt = self.parse_statement()
            return [stmt] if stmt else []
        self.eat(TT.INDENT)
        stmts = []
        while not self.at(TT.DEDENT, TT.EOF):
            self.skip_newlines()
            if self.at(TT.DEDENT, TT.EOF):
                break
            stmt = self.parse_statement()
            if stmt:
                stmts.append(stmt)
        self.maybe(TT.DEDENT)
        return stmts

    # ── Expressions (Pratt-style precedence) ───────────────────
    def parse_expression(self) -> Node:
        return self.parse_pipe()

    def parse_pipe(self) -> Node:
        left = self.parse_ternary()
        while self.maybe(TT.PIPE):
            func = self.parse_ternary()
            left = FunctionCall(func=func, args=[left])
        return left

    def parse_ternary(self) -> Node:
        expr = self.parse_or()
        if self.maybe(TT.IF):
            cond = self.parse_or()
            self.eat(TT.ELSE)
            false_val = self.parse_or()
            return Ternary(condition=cond, true_val=expr, false_val=false_val)
        return expr

    def parse_or(self) -> Node:
        left = self.parse_and()
        while self.maybe(TT.OR):
            right = self.parse_and()
            left = BinaryOp(left=left, op="or", right=right)
        return left

    def parse_and(self) -> Node:
        left = self.parse_not()
        while self.maybe(TT.AND):
            right = self.parse_not()
            left = BinaryOp(left=left, op="and", right=right)
        return left

    def parse_not(self) -> Node:
        if self.maybe(TT.NOT):
            return UnaryOp(op="not", operand=self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        left = self.parse_range()
        while self.at(TT.EQ, TT.NEQ, TT.LT, TT.GT, TT.LTE, TT.GTE, TT.IN):
            op = self.current().value
            self.pos += 1
            right = self.parse_range()
            left = BinaryOp(left=left, op=op, right=right)
        return left

    def parse_range(self) -> Node:
        left = self.parse_addition()
        if self.maybe(TT.DOTDOT):
            right = self.parse_addition()
            step = None
            if self.maybe(TT.DOTDOT):
                step = self.parse_addition()
            return RangeLiteral(start=left, stop=right, step=step)
        return left

    def parse_addition(self) -> Node:
        left = self.parse_multiplication()
        while self.at(TT.PLUS, TT.MINUS):
            op = self.current().value
            self.pos += 1
            right = self.parse_multiplication()
            left = BinaryOp(left=left, op=op, right=right)
        return left

    def parse_multiplication(self) -> Node:
        left = self.parse_power()
        while self.at(TT.STAR, TT.SLASH, TT.DSLASH, TT.PERCENT):
            op = self.current().value
            self.pos += 1
            right = self.parse_power()
            left = BinaryOp(left=left, op=op, right=right)
        return left

    def parse_power(self) -> Node:
        left = self.parse_unary()
        if self.maybe(TT.POWER):
            right = self.parse_power()  # Right-associative
            left = BinaryOp(left=left, op="**", right=right)
        return left

    def parse_unary(self) -> Node:
        if self.at(TT.AWAIT):
            self.pos += 1
            return UnaryOp(op="await ", operand=self.parse_unary())
        if self.at(TT.MINUS):
            self.pos += 1
            return UnaryOp(op="-", operand=self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self) -> Node:
        expr = self.parse_primary()
        while True:
            if self.maybe(TT.DOT):
                member = self.eat(TT.IDENT).value
                if self.at(TT.LPAREN):
                    self.eat(TT.LPAREN)
                    args, kwargs = self.parse_call_args()
                    self.eat(TT.RPAREN)
                    expr = MethodCall(obj=expr, method=member, args=args, kwargs=kwargs)
                else:
                    expr = MemberAccess(obj=expr, member=member)
            elif self.at(TT.LPAREN):
                self.eat(TT.LPAREN)
                args, kwargs = self.parse_call_args()
                self.eat(TT.RPAREN)
                expr = FunctionCall(func=expr, args=args, kwargs=kwargs)
            elif self.at(TT.LBRACKET):
                self.eat(TT.LBRACKET)
                idx = self.parse_expression()
                if self.maybe(TT.COLON):
                    stop = self.parse_expression() if not self.at(TT.RBRACKET) else None
                    expr = SliceAccess(obj=expr, start=idx, stop=stop)
                else:
                    expr = IndexAccess(obj=expr, index=idx)
                self.eat(TT.RBRACKET)
            else:
                break
        return expr

    def parse_call_args(self):
        args = []
        kwargs = {}
        while not self.at(TT.RPAREN):
            if args or kwargs:
                self.eat(TT.COMMA)
            # Check for kwarg: name=value
            if self.at(TT.IDENT) and self.peek().type == TT.ASSIGN:
                name = self.eat(TT.IDENT).value
                self.eat(TT.ASSIGN)
                value = self.parse_expression()
                kwargs[name] = value
            else:
                args.append(self.parse_expression())
        return args, kwargs

    def parse_primary(self) -> Node:
        tok = self.current()

        if tok.type == TT.INTEGER:
            self.pos += 1
            return IntLiteral(value=int(tok.value), line=tok.line, col=tok.col)
        if tok.type == TT.FLOAT:
            self.pos += 1
            return FloatLiteral(value=float(tok.value), line=tok.line, col=tok.col)
        if tok.type == TT.STRING:
            self.pos += 1
            # Check for f-string interpolation
            if "{" in tok.value:
                return self._parse_fstring(tok)
            return StringLiteral(value=tok.value, line=tok.line, col=tok.col)
        if tok.type == TT.TRUE:
            self.pos += 1
            return BoolLiteral(value=True, line=tok.line, col=tok.col)
        if tok.type == TT.FALSE:
            self.pos += 1
            return BoolLiteral(value=False, line=tok.line, col=tok.col)
        if tok.type == TT.NONE:
            self.pos += 1
            return NoneLiteral(line=tok.line, col=tok.col)
        if tok.type == TT.IDENT:
            self.pos += 1
            return Identifier(name=tok.value, line=tok.line, col=tok.col)
        if tok.type == TT.SELF:
            self.pos += 1
            return Identifier(name="self", line=tok.line, col=tok.col)
        if tok.type == TT.LPAREN:
            self.eat(TT.LPAREN)
            # Lambda: (x, y) => expr
            if self._looks_like_lambda():
                params = self._parse_lambda_params()
                self.eat(TT.RPAREN)
                self.eat(TT.FAT_ARROW)
                body = self.parse_expression()
                return Lambda(params=params, body=body, line=tok.line)
            # Tuple or grouped expression
            expr = self.parse_expression()
            if self.maybe(TT.COMMA):
                elements = [expr]
                while not self.at(TT.RPAREN):
                    elements.append(self.parse_expression())
                    if not self.maybe(TT.COMMA):
                        break
                self.eat(TT.RPAREN)
                return TupleLiteral(elements=elements, line=tok.line)
            self.eat(TT.RPAREN)
            return expr
        if tok.type == TT.LBRACKET:
            return self.parse_list_literal()
        if tok.type == TT.LBRACE:
            return self.parse_dict_literal()

        self.error(f"Unexpected token in expression")

    def _parse_fstring(self, tok: Token) -> FString:
        """Parse a string with {expr} interpolation into an FString node"""
        parts = []
        current = ""
        i = 0
        s = tok.value
        while i < len(s):
            if s[i] == "{" and i + 1 < len(s) and s[i+1] != "{":
                if current:
                    parts.append(current)
                    current = ""
                i += 1
                expr_str = ""
                depth = 1
                while i < len(s) and depth > 0:
                    if s[i] == "{":
                        depth += 1
                    elif s[i] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    expr_str += s[i]
                    i += 1
                i += 1  # skip closing }
                # Parse the expression; if it isn't valid Sym
                # (e.g. CSS "{margin:0}"), treat the whole {...} as literal text
                from .lexer import Lexer
                try:
                    sub_tokens = Lexer(expr_str).tokenize()
                    sub_parser = Parser(sub_tokens)
                    expr = sub_parser.parse_expression()
                    if sub_parser.pos < len(sub_tokens) - 1:
                        # leftover tokens → not a clean expression → literal
                        raise ValueError("not an expression")
                    parts.append(expr)
                except Exception:
                    current += "{" + expr_str + "}"
            else:
                current += s[i]
                i += 1
        if current:
            parts.append(current)
        return FString(parts=parts, line=tok.line, col=tok.col)

    def _looks_like_lambda(self) -> bool:
        """Peek ahead to see if we're in a lambda: (x, y) => ..."""
        save = self.pos
        try:
            while not self.at(TT.RPAREN, TT.EOF):
                self.pos += 1
            if self.at(TT.RPAREN) and self.peek().type == TT.FAT_ARROW:
                return True
            return False
        finally:
            self.pos = save

    def _parse_lambda_params(self) -> List[str]:
        params = []
        while not self.at(TT.RPAREN):
            if params:
                self.eat(TT.COMMA)
            params.append(self.eat(TT.IDENT).value)
        return params

    def parse_list_literal(self) -> ListLiteral:
        tok = self.eat(TT.LBRACKET)
        elements = []
        while not self.at(TT.RBRACKET):
            if elements:
                self.eat(TT.COMMA)
            elements.append(self.parse_expression())
        self.eat(TT.RBRACKET)
        return ListLiteral(elements=elements, line=tok.line)

    def parse_dict_literal(self) -> DictLiteral:
        tok = self.eat(TT.LBRACE)
        keys = []
        values = []
        while not self.at(TT.RBRACE):
            if keys:
                self.eat(TT.COMMA)
            key = self.parse_expression()
            self.eat(TT.COLON)
            val = self.parse_expression()
            keys.append(key)
            values.append(val)
        self.eat(TT.RBRACE)
        return DictLiteral(keys=keys, values=values, line=tok.line)


def parse(tokens: List[Token], filename: str = "<stdin>") -> Program:
    return Parser(tokens, filename).parse()
