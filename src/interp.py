# -*- coding: utf-8 -*-
"""ACRPA 统一变量插值 (方案文档 §5 L3 变量与表达式体系)。

**唯一插值入口**。设计目标 (硬约束, 改动前先读):

1. **纯标准库 + 零副作用**: 顶层只 import ``os`` / ``re`` / ``datetime``;
   ``log1`` 延迟导入 (与 ``commands._log`` 同款), 因此本模块既不拉
   ``utils``/tkinter, 也不建任何全局状态 —— ``import interp`` 可安全用于
   ``script_validate`` 之类的无 GUI 链路。
2. **不依赖 engine 全局**: 变量/参数/时间/环境变量全部经 ``ctx`` 注入
   (``get_var`` / ``set_var`` / ``env`` / ``now``), 避免 ``interp ↔ engine``
   循环 import。调用方 (engine / cli / 测试) 自己构造 :class:`Context`。
3. **单遍扫描 O(n)**: 一次左到右扫描拼装结果, **不做正则递归替换**,
   因此不存在二次展开 (injection / 死循环) 风险:
   ``{var:a}`` 的值里含 ``{var:b}`` 时不会再被展开。转义 ``{{`` / ``$$``
   在同一遍里被还原为字面 ``{`` / ``$``。
4. **插值结果绝不进 eval/exec**: ``${...}`` 里的**表达式**仍只走
   ``safe_eval`` 的 AST 白名单 (与 ``engine._evaluate_condition`` 同一套),
   本模块不提供也不引入任何新的求值通道。
5. **长度截断只作用于日志副本**: :func:`interpolate` 始终返回完整值;
   :func:`truncate` / :func:`log_preview` 仅给日志用 (默认 512 字符)。

占位符语法 (与既有 ``${名称}`` 并存, 均不废弃)::

    {var:名称}        脚本变量值; 未定义 → 空串 + warning 日志
    {env:NAME}        环境变量; 支持默认值 {env:NAME|默认值}
    {date:格式}       当前时间; 支持单字母标记 (见 DATE_MAP) 与 strftime
    {arg:名称}        调用方参数 (CLI -p0:xxx / ex:xxx → {arg:p0} / {arg:ex})
    ${名称}           既有语法: 简单名直接取值, 表达式走 safe_eval 白名单
    {{  }}  $$        转义: 字面 { 字面 } 与 字面 $

转义规则 (为什么 ``}}`` 要"配对还原"):

    只有**前面出现过** ``{{`` 时, 后续的 ``}}`` 才还原为字面 ``}``。
    这样 ``{"a":{"b":1}}`` 这类以双右括号结尾的 JSON 参数不会被吃掉
    (它们从未出现 ``{{``, 因此两个 ``}`` 原样保留); 想写字面 ``{var:x}``
    就写 ``{{var:x}}``, 结果正是 ``{var:x}``。

非法/未闭合/嵌套的占位符一律**字面保留** (不抛异常、不吃掉用户的 JSON)。

单测: ``tools/_test_interp_l3.py`` (覆盖文档 §5 L3 的 8 条单测点)。
"""

import os
import re
from datetime import datetime

# ── 默认长度上限 (只作用于日志副本, 不截断交给 handler 的实际值) ────────
DEFAULT_MAX_LEN = 512

# ── {date:} 单字母标记 → strftime 映射表 (zTasker 风格) ────────────────
# 大写管"日期"、小写管"时间", 与 zTasker 的 Y/M/D + h/m/s 习惯一致;
# 为兼容两派写法, i 与 m 都映射到分钟, h 与 H 都映射到 24 小时制。
DATE_MAP = {
    "Y": "%Y",   # 四位年
    "y": "%y",   # 两位年
    "M": "%m",   # 两位月
    "m": "%M",   # 两位分钟
    "D": "%d",   # 两位日
    "d": "%d",   # 两位日 (同 D)
    "h": "%H",   # 24 小时制两位
    "H": "%H",   # 24 小时制两位 (同 h)
    "i": "%M",   # 两位分钟 (同 m)
    "s": "%S",   # 两位秒
    "w": "%w",   # 星期 (0=周日)
    "W": "%W",   # 年中第几周
}

DATE_MAP_TEXT = ("Y=四位年 y=两位年 M=两位月 m=两位分 D=两位日 d=两位日(同D) "
                 "h=24小时 H=24小时(同h) i=两位分 s=两位秒 w=星期(0=周日) W=年中周数")

# strftime 允许出现的指令字符 (用于判定 {date:...} 是否非法, 非法则字面保留)
_DATE_DIRECTIVES = frozenset("aAbBcCdDeFgGhHIjklmMnprRsStTuUVwWxXyYzZ%")

# 形如 "name" 的简单变量名 (允许中文/下划线开头)
_SIMPLE_NAME_RE = re.compile(r"^[^\W\d]\w*$", re.UNICODE)

# 占位符最大跨度: 超过即视为普通文本 (防止把超长 JSON 当占位符扫掉)
_MAX_SPAN = 4096


# ═══════════════════════════════════════════════════════════════════════
# 日志 (统一 log1; 延迟导入, 本模块顶层零依赖)
# ═══════════════════════════════════════════════════════════════════════

def _log(msg, level="info"):
    """延迟导入 log1 记一条日志; 任何失败都不得影响插值本身。"""
    try:
        from utils import log1
        log1(msg, level)
    except Exception:
        pass


def _warn(msg):
    _log("[插值] " + msg, "warning")


# ═══════════════════════════════════════════════════════════════════════
# 日志副本: 截断 + 脱敏 (只给日志用, 绝不改变实际值)
# ═══════════════════════════════════════════════════════════════════════

def truncate(text, max_len=None):
    """把 text 截到 max_len 字符 (含尾部省略号); 仅用于日志副本。"""
    s = "" if text is None else (text if isinstance(text, str) else str(text))
    try:
        max_len = DEFAULT_MAX_LEN if max_len is None else int(max_len)
    except (TypeError, ValueError):
        max_len = DEFAULT_MAX_LEN
    if max_len <= 0 or len(s) <= max_len:
        return s
    if max_len == 1:
        return "…"
    return s[:max_len - 1] + "…"


# 敏感片段: Bearer / 查询串或 KV 里的 token / 认证头 / 长 token 串
_RX_BEARER = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9\-._~+/]{8,}=*)")
_RX_QUERY_KEY = re.compile(
    r"(?i)([?&;](?:key|token|secret|password|passwd|pwd|sig|signature|auth)=)"
    r"([^&\s\"'<>]+)")
_RX_KV = re.compile(
    r"(?i)((?:^|[\s,;{(])(?:access_token|refresh_token|api_key|apikey|api-key|"
    r"secret|password|passwd|pwd|client_secret)\s*[=:]\s*)([^\s\"'<>,;)}\]]+)")
_RX_HEADER = re.compile(
    r"(?i)\b(authorization|proxy-authorization|x-api-key|cookie|set-cookie)"
    r"\s*[:=]\s*([^\r\n\";]+)")
# 长度 ≥16 的 base64/hex 形态串 (含数字且含字母才算"疑似 token")
_RX_TOKEN_RUN = re.compile(r"(?<![\\/.\w])[A-Za-z0-9+/_\-]{16,}={0,2}(?![\\/.\w])")


def _looks_like_token(run):
    """含数字且含字母 → 更像 token/密钥; 纯英文长词 (如超长单词) 不脱敏。"""
    has_digit = any(c.isdigit() for c in run)
    has_alpha = any(c.isalpha() for c in run)
    return has_digit and has_alpha


def _redact_run(match):
    run = match.group(0)
    return "***" if _looks_like_token(run) else run


def redact(text):
    """把疑似 token/密钥替换为 ``***``; 只用于日志 (幂等, 不抛异常)。"""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    if not s:
        return s
    try:
        s = _RX_BEARER.sub(lambda m: m.group(1) + " ***", s)
        s = _RX_HEADER.sub(lambda m: m.group(1) + ": ***", s)
        s = _RX_QUERY_KEY.sub(lambda m: m.group(1) + "***", s)
        s = _RX_KV.sub(lambda m: m.group(1) + "***", s)
        s = _RX_TOKEN_RUN.sub(_redact_run, s)
    except Exception:
        return s
    return s


def log_preview(text, max_len=None):
    """日志用副本 = 先截断再脱敏; **不改变**传给 handler 的实际值。"""
    return redact(truncate(text, max_len))


# ═══════════════════════════════════════════════════════════════════════
# 插值上下文 (调用方注入; interp 不认 engine 全局)
# ═══════════════════════════════════════════════════════════════════════

class Context(object):
    """轻量插值上下文: ``get_var`` / ``set_var`` / ``env`` / ``now``。

    · ``variables``: 脚本变量表 (engine.variables 的引用即可, 读到最新值);
    · ``args``     : 调用方参数命名空间 (CLI ``-p0:`` / ``ex:``);
    · ``getenv``   : 环境变量取值函数 (默认 ``os.environ.get``, 便于测试注入);
    · ``now``      : datetime 实例或返回 datetime 的可调用对象 (便于测试固定时间)。
    """

    __slots__ = ("vars", "args", "getenv", "now_source")

    def __init__(self, variables=None, args=None, getenv=None, now=None):
        if isinstance(variables, dict):
            self.vars = variables                 # 引用: 运行时读到最新变量
        else:
            self.vars = dict(variables or {})
        self.args = args if isinstance(args, dict) else dict(args or {})
        self.getenv = getenv or os.environ.get
        self.now_source = now

    # ── 统一接口 ──
    def get_var(self, name, default=None):
        return self.vars.get(name, default)

    def set_var(self, name, value):
        self.vars[name] = value
        return True

    def env(self, name):
        try:
            return self.getenv(name)
        except Exception:
            return None

    def now(self):
        src = self.now_source
        if callable(src):
            try:
                return src()
            except Exception:
                return datetime.now()
        if isinstance(src, datetime):
            return src
        return datetime.now()


def _as_ctx(ctx):
    """把 dict / None / duck-typed 对象统一成可用上下文。"""
    if isinstance(ctx, Context):
        return ctx
    if ctx is None:
        return Context()
    if hasattr(ctx, "get_var"):
        return ctx
    if isinstance(ctx, dict):
        return Context(ctx)
    return Context()


def _ctx_var(ctx, name):
    try:
        return ctx.get_var(name)
    except Exception:
        return None


def _ctx_env(ctx, name):
    fn = getattr(ctx, "env", None)
    if fn is None:
        fn = getattr(ctx, "getenv", None)
    if fn is None:
        return None
    try:
        return fn(name)
    except Exception:
        return None


def _ctx_arg(ctx, name):
    args = getattr(ctx, "args", None)
    if not isinstance(args, dict):
        return None
    if name in args:
        return args[name]
    low = name.strip().lower()
    if low in args:
        return args[low]
    for key in args:
        if isinstance(key, str) and key.strip().lower() == low:
            return args[key]
    return None


def _ctx_now(ctx):
    fn = getattr(ctx, "now", None)
    if callable(fn):
        try:
            value = fn()
            if isinstance(value, datetime):
                return value
        except Exception:
            pass
    return datetime.now()


def _to_text(value):
    """值 → 文本 (None→空串; 不做二次展开)。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


# ═══════════════════════════════════════════════════════════════════════
# {date:} 格式翻译
# ═══════════════════════════════════════════════════════════════════════

def build_date_format(fmt):
    """把 ``{date:}`` 的格式串翻译成 strftime 格式; 非法返回 None。

    · 含 ``%`` 的片段按 strftime 原样保留 (``%Y-%m-%d`` 直接可用);
    · 其余单字母按 :data:`DATE_MAP` 翻译 (``YMD`` → ``%Y%m%d``);
    · 其它字符 (含中文/标点/- _ . T 等) 作为字面量混排;
    · ``%`` 后跟非白名单指令 (如 ``{date:%%%}`` 的尾部孤立 ``%``) → None。
    """
    if not isinstance(fmt, str):
        return None
    out = []
    i, n = 0, len(fmt)
    while i < n:
        ch = fmt[i]
        if ch == "%":
            if i + 1 >= n:
                return None                      # 孤立 % → 非法
            nxt = fmt[i + 1]
            if nxt not in _DATE_DIRECTIVES:
                return None                      # 未知指令 → 非法
            out.append("%" + nxt)
            i += 2
            continue
        out.append(DATE_MAP.get(ch, ch))
        i += 1
    return "".join(out)


def _render_date(fmt, now):
    translated = build_date_format(fmt)
    if translated is None:
        return None
    try:
        return now.strftime(translated)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
# 占位符展开
# ═══════════════════════════════════════════════════════════════════════

def _expand(content, ctx, max_len):
    """展开 ``{...}`` 内部文本 → str; 返回 None 表示"原样保留"。"""
    kind, sep, rest = content.partition(":")
    if not sep:
        return None
    kind = kind.strip().lower()

    if kind == "var":
        name = rest.strip()
        if not name:
            return None                          # {var:} → 字面保留
        value = _ctx_var(ctx, name)
        if value is None:
            _warn("变量 '{}' 未定义 → 空串 (原文 {})".format(
                name, log_preview(content, max_len)))
            return ""
        return _to_text(value)

    if kind == "env":
        name, bar, default = rest.partition("|")
        name = name.strip()
        if not name:
            return None
        value = _ctx_env(ctx, name)
        if value is None or value == "":
            if bar:
                return default
            _warn("环境变量 '{}' 未设置 → 空串".format(name))
            return ""
        return _to_text(value)

    if kind == "date":
        fmt = rest
        if not fmt.strip():
            return None
        rendered = _render_date(fmt, _ctx_now(ctx))
        if rendered is None:
            _warn("日期格式非法 → 字面保留 (原文 {})".format(
                log_preview(content, max_len)))
            return None
        return rendered

    if kind == "arg":
        name = rest.strip()
        if not name:
            return None
        value = _ctx_arg(ctx, name)
        if value is None:
            _warn("参数 '{}' 未提供 → 空串 ({})".format(name, log_preview(content, max_len)))
            return ""
        return _to_text(value)

    return None                                  # 未知前缀 → 字面保留


def _expand_dollar(expr, ctx, max_len):
    """展开 ``${...}`` → str; None 表示"原样保留"。

    简单名直接取值 (需要保留字符串语义); 其余按表达式走 safe_eval 白名单。
    **不新增求值通道**: 只有 ``safe_eval.safe_eval_math`` 一条路径, 不用 eval。
    """
    name = expr.strip()
    if not name:
        return None
    if _SIMPLE_NAME_RE.match(name):
        value = _ctx_var(ctx, name)
        if value is not None:
            return _to_text(value)
        # 未定义变量: 文本场景下按空串处理 (条件/数学运算的 0 语义见 engine)
        _warn("变量 '{}' 未定义 → 空串 (原文 ${{{}}})".format(
            name, log_preview(name, max_len)))
        return ""
    try:
        import safe_eval
    except Exception:
        return None
    try:
        variables = dict(getattr(ctx, "vars", None) or {})
        return _to_text(safe_eval.safe_eval_math(name, variables))
    except Exception:
        _warn("表达式求值失败 → 字面保留 (原文 ${{{}}})".format(
            log_preview(name, max_len)))
        return None


def _scan_brace(text, start):
    """从 start 处的 '{' 找配对的 '}' → (结束下标, 是否嵌套); 失败 None。

    嵌套 (内部还有 '{') → 调用方原样输出整段, 因此 ``{var:{var:x}}`` 不会被
    二次展开; 跨行 → 视为普通文本, 避免吞掉用户的 JSON。
    """
    depth = 0
    n = len(text)
    limit = min(n, start + _MAX_SPAN)
    j = start
    while j < limit:
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j, ("{" in text[start + 1:j])
        elif ch == "\n":
            return None
        j += 1
    return None


def interpolate(text, ctx=None, max_len=None):
    """单遍扫描完成全部替换 → str (**完整值, 不截断**)。

    · ``{var:}`` / ``{env:}`` / ``{date:}`` / ``{arg:}`` 与 ``${}`` 可混用;
    · ``{{`` → 字面 ``{``, ``$$`` → 字面 ``$``;
    · 未闭合 / 嵌套 / 非法的占位符一律字面保留, **绝不抛异常**;
    · ``max_len`` 只影响内部日志副本 (见 :func:`log_preview`), 不影响返回值。
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    # 快路径: 既无 '{' 也无 '$' 时不构造 ctx (绝大多数参数走这里)
    if "{" not in text and "$" not in text:
        return text

    ctx = _as_ctx(ctx)
    out = []
    i, n = 0, len(text)
    esc_depth = 0                    # {{ 的配对计数: 只有配过对才还原 }}
    while i < n:
        ch = text[i]

        if ch == "{":
            if text.startswith("{{", i):          # 转义 {{
                out.append("{")
                esc_depth += 1
                i += 2
                continue
            span = _scan_brace(text, i)
            if span is None:                      # 未闭合 / 跨行 → 字面
                out.append("{")
                i += 1
                continue
            j, nested = span
            if nested:                            # 嵌套 → 原样整段 (不二次展开)
                out.append(text[i:j + 1])
                i = j + 1
                continue
            piece = _expand(text[i + 1:j], ctx, max_len)
            out.append(text[i:j + 1] if piece is None else piece)
            i = j + 1
            continue

        if ch == "}":
            # 只有配过 {{ 才把 }} 还原成字面 }, 避免吃掉 {"a":{"b":1}} 的尾部
            if esc_depth > 0 and text.startswith("}}", i):
                out.append("}")
                esc_depth -= 1
                i += 2
                continue
            out.append("}")
            i += 1
            continue

        if ch == "$":
            if text.startswith("$$", i):          # 转义 $$
                out.append("$")
                i += 2
                continue
            if text.startswith("${", i):
                j = text.find("}", i + 2)
                if j < 0 or "{" in text[i + 2:j] or "\n" in text[i:j]:
                    out.append("$")               # 未闭合/嵌套 → 字面
                    i += 1
                    continue
                piece = _expand_dollar(text[i + 2:j], ctx, max_len)
                out.append(text[i:j + 1] if piece is None else piece)
                i = j + 1
                continue
            out.append("$")                       # 孤立的 $ → 字面
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def interpolate_args(args, ctx=None, skip_idx=()):
    """批量插值参数列表 → 新 list (非 str / None 原样保留)。

    ``skip_idx`` 是 **0 基参数下标**集合 (0 = 第一个参数), 命中的位置原样返回
    —— 供 ``commands.NO_INTERP`` 白名单 (变量名 / 安全闸门 / 枚举参数) 使用。
    """
    skip = frozenset(skip_idx or ())
    items = list(args or [])
    if not items:
        return items
    ctx = _as_ctx(ctx)
    out = []
    for idx, value in enumerate(items):
        if idx in skip or value is None or not isinstance(value, str) or not value:
            out.append(value)
            continue
        try:
            out.append(interpolate(value, ctx))
        except Exception:                         # 插值失败 → 字面量, 绝不中断
            out.append(value)
    return out
