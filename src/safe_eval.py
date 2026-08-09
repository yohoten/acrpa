"""Safe expression evaluator — AST-based, no raw eval().

Replaces the unsafe eval() calls in engine.py for condition and math evaluation.
Only allows a whitelisted subset of Python operators — no function calls,
no attribute access, no subscript, no comprehension, no import possible.
"""

import ast
import operator


# ── Allowed binary operators ──
_BINOPS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.Lt:       operator.lt,
    ast.LtE:      operator.le,
    ast.Gt:       operator.gt,
    ast.GtE:      operator.ge,
    ast.Eq:       operator.eq,
    ast.NotEq:    operator.ne,
    ast.And:      lambda a, b: a and b,
    ast.Or:       lambda a, b: a or b,
    ast.BitAnd:   operator.and_,
    ast.BitOr:    operator.or_,
    ast.BitXor:   operator.xor,
    ast.LShift:   operator.lshift,
    ast.RShift:   operator.rshift,
}

# ── Allowed unary operators ──
_UNOPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not:  operator.not_,
}


class EvalError(Exception):
    """Raised when the expression contains disallowed constructs."""
    pass


def _eval_node(node, variables):
    """Recursively evaluate an allowed AST node."""
    # Python 3.8+ uses ast.Constant, older versions use ast.Num/ast.Str/ast.Bytes
    if isinstance(node, ast.Constant):
        return node.value
    
    # Fallback for Python 3.7 compatibility
    if hasattr(ast, 'Num') and isinstance(node, ast.Num):
        return node.n
    if hasattr(ast, 'Str') and isinstance(node, ast.Str):
        return node.s
    if hasattr(ast, 'Bytes') and isinstance(node, ast.Bytes):
        return node.s
    # Python 3.7 uses NameConstant for True/False/None
    if hasattr(ast, 'NameConstant') and isinstance(node, ast.NameConstant):
        return node.value

    if isinstance(node, ast.Name):
        name = node.id
        if name in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[name]
        if name in variables:
            return variables[name]
        raise EvalError("未定义变量 '{}'".format(name))

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        op_type = type(node.op)
        if op_type not in _BINOPS:
            raise EvalError("不允许的运算符: {}".format(op_type.__name__))
        return _BINOPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, variables)
        op_type = type(node.op)
        if op_type not in _UNOPS:
            raise EvalError("不允许的一元运算符: {}".format(op_type.__name__))
        return _UNOPS[op_type](operand)

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, variables)
            op_type = type(op)
            if op_type not in _BINOPS:
                raise EvalError("不允许的比较运算符: {}".format(op_type.__name__))
            if not _BINOPS[op_type](left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for val in node.values:
                if not _eval_node(val, variables):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for val in node.values:
                if _eval_node(val, variables):
                    return True
            return False

    raise EvalError("不支持的表达式类型: {}".format(type(node).__name__))


def safe_eval_condition(expr, variables=None):
    """Safely evaluate a boolean condition expression.

    Args:
        expr: String expression, e.g. "${x} > 5"
        variables: Dict of variable name -> value

    Returns:
        bool result

    Raises:
        EvalError if expression contains disallowed constructs
    """
    if variables is None:
        variables = {}
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise EvalError("表达式语法错误: {}".format(e))
    return bool(_eval_node(tree.body, variables))


def safe_eval_math(expr, variables=None):
    """Safely evaluate a mathematical expression.

    Args:
        expr: String expression, e.g. "x + y * 2"
        variables: Dict of variable name -> value

    Returns:
        Numeric result

    Raises:
        EvalError if expression contains disallowed constructs
    """
    if variables is None:
        variables = {}
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise EvalError("表达式语法错误: {}".format(e))
    result = _eval_node(tree.body, variables)
    if not isinstance(result, (int, float, bool)):
        raise EvalError("数学运算必须返回数值，而非 {}".format(type(result).__name__))
    return result
