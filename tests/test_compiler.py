#!/usr/bin/env python3
"""
Sym Test Suite
Tests lexer, parser, and codegen.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from compiler.lexer import lex, TT
from compiler.parser import parse
from compiler.codegen_python import generate_python

PASS = 0
FAIL = 0

def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✅ {name}")
        PASS += 1
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        FAIL += 1


# ── Lexer Tests ────────────────────────────────────────────────

def test_lex_basic():
    tokens = lex('let x = 42\n')
    types = [t.type for t in tokens if t.type not in (TT.NEWLINE, TT.EOF)]
    assert TT.LET in types, f"Expected LET in {types}"
    assert TT.IDENT in types, f"Expected IDENT in {types}"
    assert TT.ASSIGN in types, f"Expected ASSIGN in {types}"
    assert TT.INTEGER in types, f"Expected INTEGER in {types}"

def test_lex_function():
    tokens = lex('fn add(a: int, b: int) -> int\n    return a + b\n')
    types = [t.type for t in tokens if t.type not in (TT.NEWLINE, TT.EOF)]
    assert TT.FN in types
    assert TT.ARROW in types

def test_lex_string():
    tokens = lex('let s = "hello world"\n')
    vals = [t.value for t in tokens if t.type == TT.STRING]
    assert "hello world" in vals, f"Expected string, got {vals}"

def test_lex_operators():
    tokens = lex('x = 1 + 2 * 3 ** 4\n')
    types = [t.type for t in tokens]
    assert TT.PLUS in types
    assert TT.STAR in types
    assert TT.POWER in types

def test_lex_indent():
    tokens = lex('if true\n    x = 1\n')
    types = [t.type for t in tokens]
    assert TT.INDENT in types
    assert TT.DEDENT in types

def test_lex_compact():
    tokens = lex('#compact\n@ x = 42\n>> x\n')
    types = [t.type for t in tokens if t.type not in (TT.NEWLINE, TT.EOF, TT.DIRECTIVE)]
    assert TT.LET in types, f"Expected LET, got {types}"
    assert TT.PRINT in types, f"Expected PRINT, got {types}"


# ── Parser Tests ───────────────────────────────────────────────

def test_parse_let():
    tokens = lex('let x = 42\n')
    prog = parse(tokens)
    assert len(prog.statements) == 1
    assert prog.statements[0].__class__.__name__ == "LetStatement"
    assert prog.statements[0].name == "x"

def test_parse_function():
    src = 'fn add(a: int, b: int) -> int\n    return a + b\n'
    tokens = lex(src)
    prog = parse(tokens)
    assert len(prog.statements) == 1
    fn = prog.statements[0]
    assert fn.__class__.__name__ == "FunctionDef"
    assert fn.name == "add"
    assert len(fn.params) == 2

def test_parse_if():
    src = 'if x > 0\n    print "positive"\nelse\n    print "negative"\n'
    tokens = lex(src)
    prog = parse(tokens)
    stmt = prog.statements[0]
    assert stmt.__class__.__name__ == "IfStatement"
    assert len(stmt.else_body) > 0

def test_parse_for():
    src = 'for i in 0..10\n    print i\n'
    tokens = lex(src)
    prog = parse(tokens)
    stmt = prog.statements[0]
    assert stmt.__class__.__name__ == "ForLoop"
    assert stmt.var == "i"

def test_parse_import():
    src = 'import py.numpy as np\n'
    tokens = lex(src)
    prog = parse(tokens)
    stmt = prog.statements[0]
    assert stmt.__class__.__name__ == "ImportStatement"
    assert stmt.is_python == True
    assert stmt.alias == "np"

def test_parse_struct():
    src = 'struct Point\n    x: float\n    y: float\n'
    tokens = lex(src)
    prog = parse(tokens)
    stmt = prog.statements[0]
    assert stmt.__class__.__name__ == "StructDef"
    assert stmt.name == "Point"
    assert len(stmt.fields) == 2

def test_parse_list():
    src = 'let data = [1, 2, 3]\n'
    tokens = lex(src)
    prog = parse(tokens)
    stmt = prog.statements[0]
    assert stmt.value.__class__.__name__ == "ListLiteral"
    assert len(stmt.value.elements) == 3


# ── Codegen Tests ──────────────────────────────────────────────

def test_codegen_let():
    tokens = lex('let x = 42\n')
    prog = parse(tokens)
    code = generate_python(prog)
    assert "x = 42" in code

def test_codegen_function():
    src = 'fn double(x: int) -> int\n    return x * 2\n'
    tokens = lex(src)
    prog = parse(tokens)
    code = generate_python(prog)
    assert "def double(x):" in code
    assert "return (x * 2)" in code

def test_codegen_import():
    src = 'import py.json as json\n'
    tokens = lex(src)
    prog = parse(tokens)
    code = generate_python(prog)
    assert "import json as json" in code

def test_codegen_sort():
    src = 'let data = [3, 1, 2]\nsort data\n'
    tokens = lex(src)
    prog = parse(tokens)
    code = generate_python(prog)
    assert "_sym_smart_sort(data)" in code

def test_codegen_fstring():
    src = 'let name = "world"\nprint "Hello {name}"\n'
    tokens = lex(src)
    prog = parse(tokens)
    code = generate_python(prog)
    assert "f\"Hello {name}\"" in code or "f'Hello {name}'" in code or "name" in code

def test_codegen_struct():
    src = 'struct Dog\n    name: str\n    age: int\n'
    tokens = lex(src)
    prog = parse(tokens)
    code = generate_python(prog)
    assert "class Dog:" in code
    assert "def __init__" in code


# ── Run All ────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  ⚡ Sym Test Suite")
    print("  ─────────────────────────\n")

    print("  Lexer:")
    test("basic tokens", test_lex_basic)
    test("function tokens", test_lex_function)
    test("string literal", test_lex_string)
    test("operators", test_lex_operators)
    test("indentation", test_lex_indent)
    test("compact mode", test_lex_compact)

    print("\n  Parser:")
    test("let statement", test_parse_let)
    test("function def", test_parse_function)
    test("if/else", test_parse_if)
    test("for loop", test_parse_for)
    test("python import", test_parse_import)
    test("struct def", test_parse_struct)
    test("list literal", test_parse_list)

    print("\n  Codegen:")
    test("let → assignment", test_codegen_let)
    test("fn → def", test_codegen_function)
    test("py.import → import", test_codegen_import)
    test("sort → smart_sort", test_codegen_sort)
    test("fstring interpolation", test_codegen_fstring)
    test("struct → class", test_codegen_struct)

    print(f"\n  ─────────────────────────")
    print(f"  Results: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("  ✅ All tests passed!")
    else:
        print("  ❌ Some tests failed")
    print()
    sys.exit(1 if FAIL else 0)
