# -*- coding: utf-8 -*-
"""
Browser Automation Backend for ACRPA — Playwright-powered web automation.

单文件内部分层（自上而下单向依赖，P0 决策）::

    facade 命令处理器 _browser_*
        └─ session 会话层  _ensure_session / _run_js / cookies_*
              └─ waiter 等待层  parse_wait_state / wait_for
                    └─ locator 定位解析层  parse_locator / build_locator
                          └─ Playwright (Chromium, 运行时函数内延迟 import)

命令:
- 打开网页        : 打开 URL
- 浏览器点击      : 定位 DSL / CSS / text= 点击
- 浏览器输入      : 定位 DSL / CSS / text= 填值（支持 ${var}）
- 等待元素        : 等待元素/页面状态（visible/hidden/attached/detached/clickable/enabled/url/title/download）
- 浏览器截图      : 视口 / 整页 / 元素 + 保存路径 + 写回变量
- 浏览器执行JS    : 执行 JS，返回值写回变量（别名 执行JS）
- 浏览器读取Cookie: 导出 Cookie（json/header/netscape）到变量/文件
- 浏览器设置Cookie: 从变量/文件注入 Cookie
- 切换框架        : 进入 iframe（DSL 定位 / 索引）或回到主文档（P1）
- 返回主框架      : 回到顶层 frame（P1，等价 切换框架, main）
- 新建标签页      : 新建 tab（可选 URL）并切换为活动页（P1）
- 切换标签页      : 按序号 / 标题 / URL 切换活动标签页（P1）
- 关闭标签页      : 关闭当前或指定标签页（P1）
- 等待下载        : 等待浏览器下载完成并保存文件（P1）
- 浏览器上传      : 对 input[type=file] 设置本地文件（P1）
- 连接已开浏览器  : 通过 CDP endpoint 接管已开浏览器（别名 接管浏览器，P2）
- 开始监听        : 监听网络响应入队（抓包，P2）
- 等待数据包      : 等待并取回命中数据包，写回变量/落盘（P2）
- 停止监听        : 停止监听并清空队列（P2）
- 启动浏览器录制  : 调用 playwright codegen 并转为 DSL 追加（可选入口，P2）

codegen → DSL 转换器（P2，纯函数、仅 stdlib、不依赖 playwright）:
  `codegen_to_dsl(code_text, strict=False) -> (rows, warnings)`
  行格式 `{"cmd": "浏览器点击", "args": [...]}`，可直接转 ScriptData。

定位 DSL 链式（P1）:
  `A >> B >> C` 逐级收窄；步骤算子 nth:/index:/first:/last:/parent:/next:/prev:/
  child:/filter:/has:；`@@` 优先级高于 `>>`（即 A@@B>>C ≡ (A@@B)>>C）。

净室说明（硬约束）:
  本模块仅借鉴公开的“DSL 语法约定”与“状态枚举 + 轮询 + 超时”通用工程思路，
  算法/数据结构/错误处理均独立实现，不包含任何第三方库源码或片段，
  不引入任何新第三方依赖（仅使用 Python 标准库 + 可选 playwright）。

关键约束:
  `import browser_backend` 不得触发 `import playwright`
  （所有 playwright import 均发生在函数体内），未安装时静默降级。

体积说明:
  - pip install playwright: ~5 MB (Python 包)
  - playwright install chromium: ~150 MB (Chromium)
  - 总计约 155 MB，不打包进 ACRPA.exe；未安装时命令会提示安装方法

Excel 脚本示例:
  打开网页, https://example.com
  等待元素, text:用户名, 15, 可见
  浏览器输入, #username, ${login_user}
  浏览器点击, role:button[name=登录]
  浏览器执行JS, () => document.querySelectorAll('a').length, link_count, 否
  浏览器截图, dashboard, 整页, , shot_path
  浏览器读取Cookie, cookie_json, json, D:\\out\\cookies.json
"""

import os
import re
import ast
import time
import json
import collections
import state
from utils import log1, safe_filename

# ======================================================================
# Lazy Playwright singleton (unchanged semantics)
# ======================================================================
_playwright = None          # Playwright instance
_browser = None             # Browser instance
_page = None                # Current page（始终为“当前活动顶层页”，与 P0 兼容）
_context = None             # Session context handle (P0: page.context)
_available = None           # None=未检测, True=可用, False=不可用
_init_attempted = False     # 是否已尝试初始化

# ── P1 扩展句柄（默认值保持 P0 行为完全一致） ──
_pages = []                 # 顶层页句柄列表（创建顺序）
_active_index = -1          # 当前活动页在 _pages 中的下标（-1=未登记）
_frame = None               # 当前 iframe 句柄（None=顶层；P0 默认行为）
_download_queue = []        # session 级下载事件队列（P1-C）
_hooked_pages = set()       # 已挂载 download 监听的页 id（去重）

# ── P2 扩展句柄（默认值保持既有行为完全一致） ──
_session_mode = "launch"    # 会话模式："launch"=自启 | "cdp"=接管已开浏览器
_listen_queue = collections.deque(maxlen=200)  # 网络响应队列（开始监听时按配置重建）
_listen_seen = set()        # 去重键集合 (method, url, status)
_listen_handler = None      # 已注册的 response 回调（None=未监听）
_listen_page = None         # 监听绑定的页对象
_listen_pattern = ""        # URL 匹配值
_listen_mode = "contains"   # URL 匹配模式：contains | regex
_listen_types = frozenset() # 资源类型过滤（空=全部）


def _check_playwright_available():
    """Check if playwright is installed (import only, no browser launch)."""
    global _available
    if _available is not None:
        return _available
    try:
        import playwright.sync_api  # noqa: F401  (函数内 import，勿上移)
        _available = True
    except ImportError:
        _available = False
    return _available


def _get_page():
    """Lazy-init Playwright browser + page. Returns page or None."""
    global _page, _browser, _playwright, _init_attempted, _session_mode

    if _page is not None:
        # Check if page is still alive
        try:
            _page.title()
            return _page
        except Exception:
            # Page closed externally, re-create
            _page = None
            _browser = None

    if _init_attempted and _page is None:
        return None

    if not _check_playwright_available():
        _init_attempted = True
        log1("Playwright 未安装。请运行: pip install playwright && playwright install chromium", "warning")
        return None

    try:
        from playwright.sync_api import sync_playwright  # 函数内 import

        _playwright = sync_playwright().start()

        # Launch browser (headless by default for RPA; can be configured)
        headless = getattr(state, 'BROWSER_HEADLESS', True) if hasattr(state, 'BROWSER_HEADLESS') else True
        slow_mo = getattr(state, 'BROWSER_SLOW_MO', 0) if hasattr(state, 'BROWSER_SLOW_MO') else 0

        _browser = _playwright.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
        )
        _page = _browser.new_page()
        _page.set_default_timeout(30000)  # 30s default timeout
        _register_page(_page)  # P1: 登记到 _pages + 挂载下载监听（默认行为不变）
        _session_mode = "launch"  # P2: 自启路径标记会话模式

        log1("Playwright 浏览器已启动 (headless={})".format(headless), "info")
        _init_attempted = True
        return _page

    except Exception as e:
        _init_attempted = True
        log1("Playwright 浏览器启动失败: {}".format(e), "error")
        _cleanup()
        return None


def _cleanup():
    """Close browser and stop Playwright."""
    global _page, _browser, _playwright, _context, _active_index, _frame, _session_mode
    try:
        _stop_listen()  # P2: 清理监听注册，避免遗留回调
    except Exception:
        pass
    # P1: 遍历关闭全部标签页（含非活动页），再收尾浏览器
    for _p in list(_pages):
        try:
            _p.close()
        except Exception:
            pass
    try:
        if _page:
            _page.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _pages[:] = []
    _download_queue[:] = []
    _hooked_pages.clear()
    _active_index = -1
    _frame = None
    _page = None
    _browser = None
    _playwright = None
    _context = None
    _session_mode = "launch"  # P2: 复位会话模式


def browser_close():
    """Explicitly close the browser."""
    _cleanup()
    log1("浏览器已关闭")


# ======================================================================
# 1) locator 定位解析层 —— 纯函数（仅 stdlib，不依赖 playwright）
# ======================================================================
# 原生 Playwright 前缀（原样直通 page.locator）
_NATIVE_PREFIXES = ("text=", "xpath=", "css=", "role=")
# CSS 显著字符（“形似 CSS”判定）
_CSS_SIG_CHARS = "#.[]>:*, "
# 裸标签名（如 button / div / input）也视为 CSS，保证旧脚本零破坏
_BARE_TAG_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*$')


def _looks_native_or_css(spec):
    """判断 spec 是否原生 Playwright 前缀或“形似 CSS”（→ 原样直通）。"""
    if spec.startswith(_NATIVE_PREFIXES):
        return True
    if spec and spec[0] in "#.[*>":
        return True
    for ch in _CSS_SIG_CHARS:
        if ch in spec:
            return True
    # 裸标签名（纯 ASCII 单词）按 CSS 处理，避免 button/div 之类的旧用法被当作文本
    if _BARE_TAG_RE.match(spec):
        return True
    return False


def _css_attr(expr):
    """把 `attr` / `attr=val` 表达式转为 CSS 属性选择器片段。"""
    expr = expr.strip()
    if "=" in expr:
        key, val = expr.split("=", 1)
        key = key.strip()
        val = val.strip()
        if val and any(c in val for c in ' ]"\''):
            val = '"' + val.replace('"', '\\"') + '"'
        return "[{0}={1}]".format(key, val)
    return "[{0}]".format(expr)


def _locator_error(code, message):
    return {"engine": "error", "code": code, "message": message}


def _parse_tag_segment(rest):
    """解析 `tag:NAME[@attr=val][@attr]...` → CSS 标签+属性选择器。"""
    rest = rest.strip()
    if not rest:
        return _locator_error("INVALID_LOCATOR", "tag: 缺少标签名")
    parts = rest.split("@")
    tag = parts[0].strip()
    if not tag:
        return _locator_error("INVALID_LOCATOR", "tag: 缺少标签名")
    raw = tag
    for attr in parts[1:]:
        attr = attr.strip()
        if attr:
            raw += _css_attr(attr)
    return {"engine": "playwright", "raw": raw}


def _parse_role_segment(rest):
    """解析 `role:button` / `role:button[name=登录]`。"""
    rest = rest.strip()
    if not rest:
        return _locator_error("INVALID_LOCATOR", "role: 缺少角色名")
    m = re.match(r'^([^\[\s]+)\s*(?:\[([^\]]*)\])?$', rest)
    if not m:
        return _locator_error("INVALID_LOCATOR", "role: 语法无效: {}".format(rest))
    role = m.group(1)
    plan = {"engine": "get_by_role", "role": role}
    inner = m.group(2)
    if inner:
        for pair in inner.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" in pair:
                k, v = pair.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "name":
                    plan["name"] = v
                elif k == "exact":
                    plan["exact"] = v.strip().lower() in ("true", "1", "是", "yes")
    return plan


def _parse_compound(spec):
    """解析 `A@@B@@C` 组合定位。"""
    parts = spec.split("@@")
    base = parse_locator(parts[0])
    if base.get("engine") == "error":
        return base
    has_text = None
    extra_attrs = ""
    for extra in parts[1:]:
        e = extra.strip()
        if not e:
            continue
        if e.startswith("text^") or e.startswith("text$"):
            return _locator_error("INVALID_LOCATOR", "@@ 组合暂不支持 text^/text$ 片段")
        if e.startswith("text:") or e.startswith("text="):
            has_text = e[5:]
        elif e.startswith("@"):
            extra_attrs += _css_attr(e[1:])
        elif e.startswith("tag:"):
            seg = _parse_tag_segment(e[4:])
            if seg.get("engine") == "error":
                return seg
            if base.get("engine") == "playwright":
                extra_attrs += seg["raw"]
        else:
            return _locator_error("INVALID_LOCATOR", "@@ 组合不支持片段: {}".format(e))
    if base.get("engine") != "playwright":
        if extra_attrs and base.get("engine") != "playwright":
            return _locator_error("INVALID_LOCATOR", "@@ 属性片段仅支持 CSS/tag 基串")
        if has_text:
            return _locator_error("INVALID_LOCATOR", "@@ text 片段仅支持 CSS/tag 基串")
        return base
    if extra_attrs:
        base = dict(base)
        base["raw"] = base.get("raw", "") + extra_attrs
    if has_text is not None:
        base = dict(base)
        base["has_text"] = has_text
    return base


# ── 链式组合 / 相对定位（P1-A，纯函数） ──
_CHAIN_STEP_PREFIXES = ("nth:", "index:", "first:", "last:", "parent:",
                        "next:", "prev:", "child:", "filter:", "has:")


def _looks_chain_step(s):
    """判定字符串是否以“链式步骤算子”开头（此类 token 不可独立作基串）。"""
    low = str(s).strip().lower()
    for p in _CHAIN_STEP_PREFIXES:
        if low.startswith(p):
            return True
    return False


def _parse_step(seg):
    """解析 `>>` 右侧的单个步骤 → step dict；非法返回 error plan。"""
    e = str(seg).strip()
    if not e:
        return _locator_error("INVALID_LOCATOR", "链式步骤为空")
    low = e.lower()
    if low.startswith("nth:") or low.startswith("index:"):
        val = e.split(":", 1)[1].strip()
        if not re.match(r'^-?\d+$', val):
            return _locator_error("INVALID_LOCATOR", "下标非整数: {}".format(e))
        return {"op": "nth", "index": int(val)}
    if low in ("first:", "first"):
        return {"op": "nth", "index": 0}
    if low in ("last:", "last"):
        return {"op": "nth", "index": -1}
    if low in ("parent:", "parent"):
        return {"op": "parent"}
    if low in ("next:", "next"):
        return {"op": "next"}
    if low in ("prev:", "prev"):
        return {"op": "prev"}
    if low.startswith("child:"):
        sub = e[6:].strip()
        if not sub:
            return _locator_error("INVALID_LOCATOR", "child: 缺少子定位")
        plan = parse_locator(sub)
        if plan.get("engine") == "error":
            return plan
        return {"op": "child", "plan": plan}
    if low.startswith("filter:"):
        txt = e[7:]
        if not txt.strip():
            return _locator_error("INVALID_LOCATOR", "filter: 缺少文本")
        return {"op": "filter", "text": txt}
    if low.startswith("has:"):
        sub = e[4:].strip()
        if not sub:
            return _locator_error("INVALID_LOCATOR", "has: 缺少子定位")
        plan = parse_locator(sub)
        if plan.get("engine") == "error":
            return plan
        return {"op": "has", "plan": plan}
    # 其余片段视为“子代定位”（等价 child:），与文档示例一致
    plan = parse_locator(e)
    if plan.get("engine") == "error":
        return plan
    return {"op": "child", "plan": plan}


def _parse_chain(spec):
    """解析 `A >> B >> C` 链式组合（左→右逐级收窄，纯函数）。"""
    parts = str(spec).split(">>")
    left = parts[0]
    if not left.strip():
        return _locator_error("INVALID_LOCATOR", "链式左侧缺少基串")
    base = parse_locator(left.strip())
    if base.get("engine") == "error":
        return base
    chain = []
    for seg in parts[1:]:
        step = _parse_step(seg)
        if step.get("engine") == "error":
            return step
        chain.append(step)
    if not chain:
        return _locator_error("INVALID_LOCATOR", "链式缺少步骤")
    base = dict(base)
    base["chain"] = chain
    return base


def parse_locator(spec):
    """把 DSL/选择器字符串解析为定位计划（纯函数，可单测）。

    返回:
      {"engine": "playwright", "raw": ...[, "has_text": ...]} |
      {"engine": "get_by_text", ...} | {"engine": "get_by_role", ...} |
      {"engine": "get_by_label", ...} | {"engine": "get_by_placeholder", ...} |
      {"engine": "get_by_test_id", ...} |
      {"engine": "error", "code": "...", "message": "..."}

    规则:
      R-dsl  自定义 DSL 前缀优先识别（保证 text:/tag:/role: 等不被误判为 CSS）
      R1     原生前缀 / “形似 CSS” → 原样直通（向后兼容）
      R2     其余（无前缀且非 CSS）→ 默认模糊文本 get_by_text(exact=False)
    """
    if spec is None:
        return _locator_error("INVALID_LOCATOR", "定位表达式为空")
    original = str(spec)
    s = original.strip()
    if not s:
        return _locator_error("INVALID_LOCATOR", "定位表达式为空")

    # ── 链式组合 `>>` 优先识别（R3：先于 CSS 直通；@@ 在其左半递归时仍优先求值） ──
    if ">>" in s:
        return _parse_chain(s)

    # ── 链式步骤算子不可独立出现（缺基串 → 非法） ──
    if _looks_chain_step(s):
        return _locator_error("INVALID_LOCATOR", "链式步骤缺基串: {}".format(s))

    # ── @@ 组合优先 ──
    if "@@" in s:
        return _parse_compound(s)

    # ── 自定义 DSL（先于 CSS 判定） ──
    if s.startswith("css:"):
        rest = s[4:].strip()
        if not rest:
            return _locator_error("INVALID_LOCATOR", "css: 缺少选择器")
        return {"engine": "playwright", "raw": rest}
    if s.startswith("xpath:"):
        rest = s[6:].strip()
        if not rest:
            return _locator_error("INVALID_LOCATOR", "xpath: 缺少表达式")
        return {"engine": "playwright", "raw": "xpath=" + rest}
    if s.startswith("text:"):
        rest = s[5:]
        if not rest.strip():
            return _locator_error("INVALID_LOCATOR", "text: 缺少文本")
        return {"engine": "get_by_text", "text": rest, "exact": False}
    if s.startswith("text^"):
        rest = s[5:]
        if not rest:
            return _locator_error("INVALID_LOCATOR", "text^ 缺少文本")
        return {"engine": "get_by_text", "regex": "^" + re.escape(rest), "exact": True}
    if s.startswith("text$"):
        rest = s[5:]
        if not rest:
            return _locator_error("INVALID_LOCATOR", "text$ 缺少文本")
        return {"engine": "get_by_text", "regex": re.escape(rest) + "$", "exact": True}
    if s.startswith("tag:"):
        return _parse_tag_segment(s[4:])
    if s.startswith("@"):
        return {"engine": "playwright", "raw": _css_attr(s[1:])}
    if s.startswith("role:"):
        return _parse_role_segment(s[5:])
    if s.startswith("label:"):
        rest = s[6:].strip()
        if not rest:
            return _locator_error("INVALID_LOCATOR", "label: 缺少文本")
        return {"engine": "get_by_label", "text": rest}
    if s.startswith("placeholder:"):
        rest = s[12:].strip()
        if not rest:
            return _locator_error("INVALID_LOCATOR", "placeholder: 缺少文本")
        return {"engine": "get_by_placeholder", "text": rest}
    if s.startswith("testid:"):
        rest = s[7:].strip()
        if not rest:
            return _locator_error("INVALID_LOCATOR", "testid: 缺少标识")
        return {"engine": "get_by_test_id", "test_id": rest}

    # ── R1: 原生前缀 / 形似 CSS → 原样直通 ──
    if _looks_native_or_css(s):
        return {"engine": "playwright", "raw": original}

    # ── R2: 默认模糊文本 ──
    return {"engine": "get_by_text", "text": s, "exact": False}


def _build_base_locator(page, plan):
    """按 plan 构造**基础** Locator 对象（不含 chain）；纯转发，不做业务判断。

    参数无效返回 None。`page` 可为 Page / Frame / Locator（均支持 locator/get_by_*）。
    """
    if not page or not plan or plan.get("engine") == "error":
        return None
    engine = plan.get("engine")
    if engine == "playwright":
        loc = page.locator(plan.get("raw", ""))
        if plan.get("has_text") is not None:
            loc = loc.filter(has_text=plan["has_text"])
        return loc
    if engine == "get_by_text":
        if plan.get("regex"):
            return page.get_by_text(re.compile(plan["regex"]))
        kwargs = {"exact": bool(plan.get("exact", False))}
        return page.get_by_text(plan.get("text", ""), **kwargs)
    if engine == "get_by_role":
        kwargs = {}
        if plan.get("name") is not None:
            kwargs["name"] = plan["name"]
        if plan.get("exact") is not None:
            kwargs["exact"] = bool(plan["exact"])
        return page.get_by_role(plan.get("role", ""), **kwargs)
    if engine == "get_by_label":
        return page.get_by_label(plan.get("text", ""))
    if engine == "get_by_placeholder":
        return page.get_by_placeholder(plan.get("text", ""))
    if engine == "get_by_test_id":
        return page.get_by_test_id(plan.get("test_id", ""))
    return None


def _apply_chain_step(loc, step):
    """把单个链式步骤应用到 Locator 上；失败返回 None。"""
    if loc is None or not step:
        return None
    op = step.get("op")
    try:
        if op == "nth":
            return loc.nth(int(step.get("index", 0)))
        if op == "parent":
            return loc.locator("xpath=..")
        if op == "next":
            return loc.locator("xpath=following-sibling::*[1]")
        if op == "prev":
            return loc.locator("xpath=preceding-sibling::*[1]")
        if op == "child":
            return _build_base_locator(loc, step.get("plan") or {})
        if op == "filter":
            return loc.filter(has_text=step.get("text", ""))
        if op == "has":
            sub = _build_base_locator(loc, step.get("plan") or {})
            if sub is None:
                return None
            return loc.filter(has=sub)
    except Exception:
        return None
    return None


def build_locator(page, plan):
    """按 plan 构造 Playwright Locator 对象；先构造基础 Locator，再顺序套用 chain。

    纯转发，不做业务判断；任一步骤失败返回 None（由 facade 统一转 INVALID_LOCATOR）。
    """
    loc = _build_base_locator(page, plan)
    if loc is None:
        return None
    chain = plan.get("chain") if isinstance(plan, dict) else None
    if chain:
        for step in chain:
            loc = _apply_chain_step(loc, step)
            if loc is None:
                return None
    return loc


# ======================================================================
# 2) waiter 等待层 —— 状态枚举 + 超时/轮询
# ======================================================================
WAIT_STATES = ("visible", "hidden", "attached", "detached",
               "clickable", "enabled", "url", "title", "download", "navigation")

# 中文/英文/同义词 → 归一化状态码（纯数据表，独立设计）
_WAIT_STATE_ALIASES = {
    "出现": "visible", "可见": "visible", "显示": "visible",
    "visible": "visible",
    "消失": "hidden", "隐藏": "hidden", "hidden": "hidden",
    "存在": "attached", "附加": "attached", "attached": "attached",
    "移除": "detached", "删除": "detached", "detached": "detached",
    "可点击": "clickable", "clickable": "clickable",
    "可用": "enabled", "enabled": "enabled",
    "url变动": "url", "网址变动": "url", "url": "url",
    "标题变动": "title", "标题": "title", "title": "title",
    "下载开始": "download", "download": "download",
    "导航": "navigation", "navigation": "navigation",
}


def parse_wait_state(text):
    """中文/英文/同义词 → 归一化状态码（纯函数，可单测）。

    未知或空返回 ""（由调用方决定回退/报错）。
    """
    if text is None:
        return ""
    key = str(text).strip()
    if not key:
        return ""
    if key in _WAIT_STATE_ALIASES:
        return _WAIT_STATE_ALIASES[key]
    low = key.lower()
    if low in _WAIT_STATE_ALIASES:
        return _WAIT_STATE_ALIASES[low]
    return ""


def wait_for(page, plan, state="visible", timeout_ms=15000, poll_ms=200, expect=None):
    """统一轮询等待。返回 (ok: bool, code: str, detail: str)。

    - visible/hidden/attached/detached → Playwright 原生 locator.wait_for
    - clickable/enabled/url/title      → 轮询判定
    - download/navigation              → 预留；download 的队列委托在命令层完成
    """
    if not page:
        return (False, "PW_NOT_INSTALLED", "浏览器后端不可用")
    if not plan or plan.get("engine") == "error":
        msg = (plan or {}).get("message", "定位表达式无效")
        return (False, "INVALID_LOCATOR", msg)

    if state in ("download", "navigation"):
        # download 的队列委托在命令层（等待元素）完成；wait_for 保持 P0 预留契约不变
        return (False, "INVALID_ARGUMENT",
                "状态 '{}' 由命令层处理，wait_for 不直接支持".format(state))

    if state in ("visible", "hidden", "attached", "detached"):
        try:
            loc = build_locator(page, plan)
            if loc is None:
                return (False, "INVALID_LOCATOR", "定位表达式无效")
            loc.wait_for(state=state, timeout=max(int(timeout_ms), 0))
            return (True, "", "")
        except Exception as e:
            return (False, "TIMEOUT", "等待超时: {}".format(e))

    if state not in ("clickable", "enabled", "url", "title"):
        return (False, "INVALID_ARGUMENT", "未知状态: {}".format(state))

    try:
        loc = build_locator(page, plan)
    except Exception as e:
        return (False, "INVALID_LOCATOR", str(e))

    deadline = time.time() + max(float(timeout_ms), 0.0) / 1000.0
    interval = max(float(poll_ms), 1.0) / 1000.0

    initial_url = ""
    initial_title = ""
    if state == "url":
        try:
            initial_url = page.url
        except Exception:
            initial_url = ""
    if state == "title":
        try:
            initial_title = page.title()
        except Exception:
            initial_title = ""

    last_bbox = None
    while True:
        try:
            if state == "clickable":
                if loc is not None and loc.is_visible() and loc.is_enabled():
                    bbox = loc.bounding_box()
                    if bbox is not None and bbox == last_bbox:
                        return (True, "", "")
                    last_bbox = bbox
            elif state == "enabled":
                if loc is not None and loc.is_enabled():
                    return (True, "", "")
            elif state == "url":
                cur = page.url
                target = expect if expect is not None else initial_url
                if cur != target:
                    return (True, "", "")
            elif state == "title":
                cur = page.title()
                target = expect if expect is not None else initial_title
                if cur != target:
                    return (True, "", "")
        except Exception as e:
            return (False, "BROWSER_DISCONNECTED", "浏览器连接已断开: {}".format(e))

        if time.time() >= deadline:
            return (False, "TIMEOUT", "等待超时")
        time.sleep(interval)


# ======================================================================
# 3) session 会话层 —— 页面/上下文句柄、tab/frame、JS、Cookie、下载
# ======================================================================
# ── P1 句柄管理：tab（_pages/_active_index）+ frame（_frame）+ download（_download_queue） ──
def _register_page(p):
    """登记顶层页到 _pages、置为活动页，并挂载下载监听（默认行为不变）。"""
    global _page, _active_index
    if p is None:
        return
    try:
        if p not in _pages:
            _pages.append(p)
        _active_index = _pages.index(p)
        _page = p
        _hook_downloads(p)
    except Exception:
        pass


def _hook_downloads(page):
    """为 page 挂载 session 级 download 监听（按 id 去重）。"""
    try:
        if id(page) in _hooked_pages:
            return
        page.on("download", _on_download)
        _hooked_pages.add(id(page))
    except Exception:
        pass


def _on_download(download):
    """download 事件回调：入队（按对象去重）。"""
    try:
        if download not in _download_queue:
            _download_queue.append(download)
    except Exception:
        pass


def _active_top_page():
    """返回当前活动顶层页（_pages 优先，退化到 _page）。"""
    if 0 <= _active_index < len(_pages):
        return _pages[_active_index]
    return _page


def _reset_frame():
    """复位 frame 上下文到顶层（切/建/关 tab 时使用）。"""
    global _frame
    _frame = None


def _ensure_session():
    """确保浏览器会话可用；返回**当前顶层** page 或 None（复用 _get_page 语义）。"""
    global _context
    page = _get_page()
    if page is not None:
        _register_page(page)
        try:
            _context = page.context
        except Exception:
            _context = None
    elif _pages:
        page = _active_top_page()
    return page


def get_top_page():
    """始终返回顶层 page（导航 / 视口·整页截图 / url·title 等待使用）。"""
    _ensure_session()
    return _active_top_page()


def get_active_page():
    """当前活动目标：已切框架则返回 _frame，否则当前顶层页（P0 默认行为一致）。"""
    page = _ensure_session()
    if _frame is not None:
        return _frame
    return page


def close_session():
    """关闭会话（等价于 _get_page/_cleanup 生命周期收尾，保留旧名语义）。"""
    _cleanup()


# ── frame / tab 选择器解析（纯函数，可单测） ──
def parse_frame_selector(text):
    """解析 切换框架 参数 → ("main", None) | ("index", n) | ("locator", spec)。"""
    t = str(text).strip() if text is not None else ""
    if t.lower() in ("main", "top", "主文档", "顶层"):
        return ("main", None)
    if re.match(r'^-?\d+$', t):
        return ("index", int(t))
    return ("locator", t)


def parse_tab_selector(text):
    """解析 切换标签页 参数 → ("index", n) | ("match", text)。"""
    t = str(text).strip() if text is not None else ""
    if re.match(r'^\d+$', t):
        return ("index", int(t))
    return ("match", t)


def _switch_frame(spec):
    """切换当前 frame 上下文。返回 (ok, code, detail)；失败保持原上下文不变。"""
    global _frame
    top = get_top_page()
    if not top:
        return (False, "PW_NOT_INSTALLED", "浏览器后端不可用")
    kind, payload = parse_frame_selector(spec)
    if kind == "main":
        _frame = None
        return (True, "", "")
    try:
        if kind == "index":
            frames = list(top.frames)
            if payload < 0 or payload >= len(frames):
                return (False, "FRAME_NOT_FOUND", "未找到框架")
            _frame = frames[payload]
        else:
            plan = parse_locator(payload)
            if plan.get("engine") == "error":
                return (False, "INVALID_LOCATOR", plan.get("message", "定位表达式无效"))
            loc = build_locator(top, plan)
            if loc is None:
                return (False, "FRAME_NOT_FOUND", "未找到框架")
            handle = loc.element_handle(timeout=10000)
            frame = handle.content_frame() if handle is not None else None
            if frame is None:
                return (False, "FRAME_NOT_FOUND", "未找到框架")
            _frame = frame
        return (True, "", "")
    except Exception as e:
        return (False, "FRAME_NOT_FOUND", "未找到框架: {}".format(e))


def _new_top_page(url=""):
    """新建顶层页（tab）并置为活动页。返回 (ok, code, detail, page)。"""
    base = _ensure_session()
    if not base:
        return (False, "PW_NOT_INSTALLED", "浏览器后端不可用", None)
    try:
        p = base.context.new_page()
        try:
            p.set_default_timeout(30000)
        except Exception:
            pass
        _register_page(p)
        _reset_frame()
        if url:
            p.goto(url, wait_until="domcontentloaded")
        return (True, "", "", p)
    except Exception as e:
        return (False, "TAB_FAILED", "标签页操作失败: {}".format(e), None)


def _find_tab(payload):
    """按标题、再按 URL 包含匹配定位 _pages 下标；未命中返回 -1。"""
    for i, p in enumerate(list(_pages)):
        try:
            if payload and payload in (p.title() or ""):
                return i
        except Exception:
            continue
    for i, p in enumerate(list(_pages)):
        try:
            if payload and payload in (p.url or ""):
                return i
        except Exception:
            continue
    return -1


def _switch_tab(spec):
    """切换活动标签页。返回 (ok, code, detail)。"""
    global _page, _active_index
    if not _ensure_session():
        return (False, "PW_NOT_INSTALLED", "浏览器后端不可用")
    kind, payload = parse_tab_selector(spec)
    if kind == "index":
        if 0 <= payload < len(_pages):
            _active_index = payload
            _page = _pages[payload]
            _reset_frame()
            return (True, "", "")
        return (False, "TAB_NOT_FOUND", "未找到标签页")
    idx = _find_tab(payload)
    if idx < 0:
        return (False, "TAB_NOT_FOUND", "未找到标签页")
    _active_index = idx
    _page = _pages[idx]
    _reset_frame()
    return (True, "", "")


def _close_tab(spec=""):
    """关闭指定/当前标签页。返回 (ok, code, detail)。"""
    global _page, _active_index
    if not _ensure_session() and not _pages:
        # 设计：关闭标签页失败短语为 TAB_NOT_FOUND / TAB_CLOSE_FAILED
        return (False, "TAB_NOT_FOUND", "未找到标签页")
    idx = _active_index
    if str(spec or "").strip():
        kind, payload = parse_tab_selector(spec)
        if kind == "index":
            if not (0 <= payload < len(_pages)):
                return (False, "TAB_NOT_FOUND", "未找到标签页")
            idx = payload
        else:
            idx = _find_tab(payload)
            if idx < 0:
                return (False, "TAB_NOT_FOUND", "未找到标签页")
    if not (0 <= idx < len(_pages)):
        return (False, "TAB_NOT_FOUND", "未找到标签页")
    target = _pages[idx]
    try:
        target.close()
    except Exception as e:
        return (False, "TAB_CLOSE_FAILED", "关闭标签页失败: {}".format(e))
    try:
        _pages.pop(idx)
    except Exception:
        pass
    if not _pages:
        _page = None
        _active_index = -1
    else:
        _active_index = len(_pages) - 1
        _page = _pages[_active_index]
    _reset_frame()
    return (True, "", "")


# ── 下载（P1-C）：纯函数 + session 队列 ──
def match_download_name(name, pattern):
    """下载文件名匹配：包含匹配；`/re/` 正则；空 pattern 恒真（纯函数）。"""
    if pattern is None:
        return True
    p = str(pattern)
    if p == "":
        return True
    n = str(name or "")
    if len(p) >= 2 and p.startswith("/") and p.endswith("/"):
        try:
            return re.search(p[1:-1], n) is not None
        except Exception:
            return False
    return p in n


def _download_dir(override=""):
    """解析下载目录：参数 > browser_download_dir > CONFIG_PATH 同级 downloads。"""
    d = str(override or "").strip()
    if not d:
        d = str(getattr(state, "BROWSER_DOWNLOAD_DIR", "") or "").strip()
    if not d:
        d = os.path.join(os.path.dirname(state.CONFIG_PATH), "downloads")
    return d


def _is_inside_dir(path, out_dir):
    """判断 path 是否落在 out_dir 之内（按真实绝对路径比较，防目录穿越）。"""
    try:
        root = os.path.abspath(out_dir)
        target = os.path.abspath(path)
    except Exception:
        return False
    return target == root or target.startswith(root + os.sep)


def _download_target_path(out_dir, filename, overwrite=True):
    """计算落盘路径（overwrite=False 时自动避让重名）（纯函数）。

    安全项 #6「浏览器下载文件名净化」: `filename` 来自远端
    `download.suggested_filename`（Content-Disposition，不可信），
    必须先经 `utils.safe_filename` 净化为**单段文件名**再 join；
    随后再复核最终路径确实落在 out_dir 之内（防御性兜底）。
    """
    name = safe_filename(filename, "download")
    fp = os.path.join(out_dir, name)
    # 防御性兜底: 理论上 safe_filename 已保证不含分隔符，此处二次确认。
    if not _is_inside_dir(fp, out_dir):
        name = safe_filename("download", "download")
        fp = os.path.join(out_dir, name)
    if overwrite or not os.path.exists(fp):
        return fp
    base, ext = os.path.splitext(name)
    i = 1
    while True:
        cand = os.path.join(out_dir, "{}_{}{}".format(base, i, ext))
        if not _is_inside_dir(cand, out_dir):
            return fp
        if not os.path.exists(cand):
            return cand
        i += 1


def _pop_matching_download(pattern):
    """从队列取出首个匹配的 Download；无则返回 None（不消费不匹配项）。"""
    for i, d in enumerate(list(_download_queue)):
        try:
            name = d.suggested_filename
        except Exception:
            name = ""
        if match_download_name(name, pattern):
            try:
                return _download_queue.pop(i)
            except Exception:
                return d
    return None


def _wait_download_state(timeout_ms, pattern=None):
    """轮询下载队列（供 wait_for 的 download 状态委托）。命中不消费队列。"""
    deadline = time.time() + max(float(timeout_ms or 0), 0.0) / 1000.0
    while True:
        d = _pop_matching_download(pattern)
        if d is not None:
            try:
                _download_queue.insert(0, d)
            except Exception:
                pass
            return (True, "", "")
        if time.time() >= deadline:
            return (False, "TIMEOUT", "等待下载超时")
        time.sleep(0.05)


def _js_is_function(script):
    """判断脚本串是否已是函数体（避免 as_expr 二次包裹）。"""
    t = script.strip()
    return t.startswith("(") or t.startswith("function") or t.startswith("async")


def _run_js(script, arg=None, as_expr=False):
    """在当前活动目标（frame/page）执行 JS。返回 (ok: bool, value_or_code, detail)。"""
    page = get_active_page()
    if not page:
        return (False, "PW_NOT_INSTALLED", "浏览器后端不可用")
    expr = script
    if as_expr and not _js_is_function(script):
        expr = "() => ({})".format(script)
    try:
        js_timeout = float(getattr(state, "BROWSER_JS_TIMEOUT", 15.0) or 15.0)
    except Exception:
        js_timeout = 15.0
    try:
        # 将默认超时临时收敛到 JS 超时，执行后恢复 30s（不改全局长期状态）
        try:
            page.set_default_timeout(int(js_timeout * 1000))
        except Exception:
            pass
        try:
            value = page.evaluate(expr)
        finally:
            try:
                page.set_default_timeout(30000)
            except Exception:
                pass
        return (True, value, "")
    except Exception as e:
        return (False, "JS_ERROR", str(e))


def cookies_get_all():
    """返回当前上下文全部 Cookie（list[dict]）；不可用返回 []。"""
    page = _ensure_session()
    if not page:
        return []
    try:
        return list(page.context.cookies())
    except Exception:
        return []


def cookies_set(items):
    """向当前上下文注入 Cookie。返回 (ok: bool, detail: str)。"""
    page = _ensure_session()
    if not page:
        return (False, "PW_NOT_INSTALLED")
    try:
        page.context.add_cookies(list(items))
        return (True, "")
    except Exception as e:
        return (False, str(e))


# ── Cookie 序列化 / 反序列化（纯函数，可单测） ──
def cookies_to_json(cookies):
    """Cookie 列表 → JSON 字符串。"""
    try:
        return json.dumps(list(cookies or []), ensure_ascii=False)
    except Exception:
        return "[]"


def cookies_to_header(cookies):
    """Cookie 列表 → `k=v; k2=v2` 形式。"""
    parts = []
    for c in (cookies or []):
        try:
            parts.append("{0}={1}".format(c.get("name", ""), c.get("value", "")))
        except Exception:
            continue
    return "; ".join(parts)


def cookies_to_netscape(cookies):
    """Cookie 列表 → Netscape Cookie File 文本。"""
    lines = ["# Netscape HTTP Cookie File"]
    for c in (cookies or []):
        try:
            domain = c.get("domain", "") or ""
            include_sub = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/") or "/"
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = c.get("expires", 0)
            try:
                expires = int(float(expires))
            except Exception:
                expires = 0
            if expires < 0:
                expires = 0
            lines.append("\t".join([
                domain, include_sub, path, secure,
                str(expires), str(c.get("name", "")), str(c.get("value", "")),
            ]))
        except Exception:
            continue
    return "\n".join(lines) + "\n"


def parse_cookies(text):
    """把 JSON 串 / Netscape 串 / `k=v; k2=v2` 串解析为 Cookie 列表（纯函数）。

    也接受已是 list/dict 的输入。
    """
    if text is None:
        return []
    if isinstance(text, dict):
        return [dict(text)]
    if isinstance(text, (list, tuple)):
        return [dict(x) if isinstance(x, dict) else {"name": "", "value": str(x)} for x in text]

    s = str(text).strip()
    if not s:
        return []

    # 1) JSON
    if s[0] in "[{":
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return [obj]
            if isinstance(obj, list):
                return [x for x in obj if isinstance(x, dict)]
        except Exception:
            pass

    # 2) Netscape Cookie File（制表符分隔，7 字段）
    if "\t" in s:
        out = []
        for line in s.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) >= 7:
                domain, _inc, path, secure, expires, name, value = fields[:7]
                try:
                    exp = int(float(expires))
                except Exception:
                    exp = 0
                cookie = {
                    "name": name, "value": value,
                    "domain": domain, "path": path or "/",
                    "secure": str(secure).strip().upper() == "TRUE",
                }
                if exp > 0:
                    cookie["expires"] = exp
                out.append(cookie)
        if out:
            return out

    # 3) header 形式 `k=v; k2=v2`
    out = []
    for pair in s.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out.append({"name": k.strip(), "value": v.strip()})
    return out


# ======================================================================
# 3b) P2 扩展层 —— CDP 接管 / 接口监听 / codegen→DSL 转换器
# ======================================================================
# ── P2-B: CDP 接管已开浏览器（纯函数 + session 层） ──
_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*://')


def parse_cdp_endpoint(arg, default=""):
    """解析 CDP endpoint（纯函数）：参数 > 默认；空 → ""；补 http://；去尾斜杠。"""
    v = str(arg or "").strip()
    if not v:
        v = str(default or "").strip()
    if not v:
        return ""
    if not _SCHEME_RE.match(v):
        v = "http://" + v
    return v.rstrip("/")


def plan_session_switch(current_mode, has_session, close_old=True):
    """决定接管时的会话动作（纯函数）→ "connect" | "reconnect" | "reuse"。

    - 无活动会话                 → connect
    - 已是 CDP 会话              → reuse（不重复连接）
    - 有会话且 是否关闭旧会话=否 → reuse
    - 有会话且 是否关闭旧会话=是 → reconnect（先 close_session 再接管）
    """
    if not has_session:
        return "connect"
    if str(current_mode or "") == "cdp":
        return "reuse"
    if not close_old:
        return "reuse"
    return "reconnect"


def _connect_cdp(endpoint, close_old=True):
    """通过 CDP 接管已开浏览器。返回 (ok, code, detail)。"""
    global _playwright, _browser, _context, _page, _pages, _active_index, _session_mode
    action = plan_session_switch(_session_mode, bool(_page is not None or _pages), close_old)
    if action == "reuse":
        return (True, "reuse", "")
    if action == "reconnect":
        close_session()
    try:
        from playwright.sync_api import sync_playwright  # 函数内 import（懒加载契约）
    except ImportError:
        return (False, "PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    try:
        if _playwright is None:
            _playwright = sync_playwright().start()
        _browser = _playwright.chromium.connect_over_cdp(endpoint)
        try:
            contexts = list(_browser.contexts)
        except Exception:
            contexts = []
        _context = contexts[0] if contexts else _browser.new_context()
        try:
            pages = list(_context.pages)
        except Exception:
            pages = []
        if not pages:
            pages = [_context.new_page()]
        _pages[:] = pages
        _active_index = 0
        _page = pages[0]
        for _p in pages:
            try:
                _p.set_default_timeout(30000)
            except Exception:
                pass
            _hook_downloads(_p)
        _reset_frame()
        _session_mode = "cdp"
        return (True, "", "")
    except Exception as e:
        return (False, "CDP_CONNECT_FAILED", "接管浏览器失败: {}".format(e))


# ── P2-C: 接口监听 / 抓包（纯函数 + session 层） ──
RESOURCE_TYPES = ("document", "xhr", "fetch", "script", "stylesheet",
                  "image", "font", "media", "websocket", "manifest", "other")

_RESOURCE_TYPE_SYNONYMS = {
    "文档": "document", "页面": "document",
    "接口": "xhr", "ajax": "xhr", "xhr": "xhr",
    "数据": "fetch", "fetch": "fetch",
    "脚本": "script", "js": "script", "script": "script",
    "样式": "stylesheet", "样式表": "stylesheet", "css": "stylesheet",
    "图片": "image", "图像": "image", "image": "image",
    "字体": "font", "font": "font",
    "媒体": "media", "音视频": "media", "media": "media",
    "websocket": "websocket", "ws": "websocket",
    "清单": "manifest", "manifest": "manifest",
    "其他": "other", "other": "other",
}
_ALL_TYPES_TOKENS = ("", "全部", "所有", "all", "*")


def parse_resource_types(text):
    """归一化资源类型过滤 → frozenset（纯函数）；空/全部 → frozenset()（=不过滤）。"""
    t = str(text or "").strip()
    if t.lower() in _ALL_TYPES_TOKENS:
        return frozenset()
    out = set()
    for part in re.split(r'[,，;；\s]+', t):
        p = part.strip()
        if not p:
            continue
        key = p.lower()
        out.add(_RESOURCE_TYPE_SYNONYMS.get(p, _RESOURCE_TYPE_SYNONYMS.get(key, key)))
    return frozenset(out)


def dedup_key(item):
    """监听去重键 (method, url, status)（纯函数）。"""
    it = item or {}
    return (it.get("method", ""), it.get("url", ""), it.get("status"))


def parse_url_pattern(pattern):
    """URL 匹配解析 → (value, mode)（纯函数）；空 → ("", "contains")；/re/ → (re, "regex")。"""
    p = str(pattern or "").strip()
    if not p:
        return ("", "contains")
    if len(p) >= 2 and p.startswith("/") and p.endswith("/"):
        return (p[1:-1], "regex")
    return (p, "contains")


def match_url(url, value, mode="contains"):
    """URL 匹配（纯函数）：空 value 恒真；mode=regex 走正则，否则包含匹配。"""
    if not value:
        return True
    u = str(url or "")
    if mode == "regex":
        try:
            return re.search(value, u) is not None
        except Exception:
            return False
    return str(value) in u


def match_response(items, pattern, mode=None):
    """按 URL 过滤响应列表（纯函数）；mode=None 时自动识别 `/re/` 正则。"""
    if mode is None:
        value, m = parse_url_pattern(pattern)
    else:
        value, m = str(pattern or ""), mode
    return [it for it in (items or []) if match_url((it or {}).get("url", ""), value, m)]


def export_packets(items, fmt="json"):
    """导出命中数据包为字符串（纯函数；格式 json）。"""
    data = [dict(it) for it in (items or []) if isinstance(it, dict)]
    return json.dumps(data, ensure_ascii=False)


def _resource_match(rtype, allowed):
    """资源类型是否命中过滤（allowed 为空集合=全部命中）。"""
    if not allowed:
        return True
    return str(rtype or "").lower() in allowed


def _on_response(response):
    """page.on("response") 回调：过滤 + 去重后入队（不采集 body）。"""
    try:
        req = response.request
        method = req.method
        rtype = req.resource_type
        url = response.url
        status = response.status
    except Exception:
        return
    if not _resource_match(rtype, _listen_types):
        return
    if not match_url(url, _listen_pattern, _listen_mode):
        return
    key = (method, url, status)
    if key in _listen_seen:
        return
    try:
        headers = dict(response.headers)
    except Exception:
        headers = {}
    item = {"url": url, "method": method, "status": status,
            "resource_type": rtype, "headers": headers}
    try:
        _listen_queue.append(item)
        _listen_seen.add(key)
        maxlen = getattr(_listen_queue, "maxlen", None)
        if maxlen and len(_listen_seen) > maxlen * 4:
            # 收缩去重集，防止长跑脚本内存膨胀
            _listen_seen.clear()
            for it in _listen_queue:
                _listen_seen.add(dedup_key(it))
    except Exception:
        pass


def _start_listen(pattern="", types_text="", maxlen=None):
    """注册网络响应监听（page.on("response")）。返回 (ok, code, detail)。"""
    global _listen_queue, _listen_seen, _listen_handler, _listen_page
    global _listen_pattern, _listen_mode, _listen_types
    page = get_active_page()
    if not page:
        return (False, "PW_NOT_INSTALLED", "浏览器后端不可用")
    try:
        _stop_listen()
    except Exception:
        pass
    try:
        n = int(maxlen) if maxlen not in (None, "") else int(getattr(state, "BROWSER_LISTEN_MAX", 200))
    except Exception:
        n = 200
    if n <= 0:
        n = 200
    _listen_queue = collections.deque(maxlen=n)
    _listen_seen = set()
    _listen_pattern, _listen_mode = parse_url_pattern(pattern)
    _listen_types = parse_resource_types(types_text)

    def _handler(response):
        _on_response(response)

    try:
        page.on("response", _handler)
    except Exception as e:
        return (False, "LISTEN_ERROR", "监听失败: {}".format(e))
    _listen_handler = _handler
    _listen_page = page
    return (True, "", "")


def _stop_listen():
    """移除监听并清空队列。返回 (ok, code, detail)。"""
    global _listen_handler, _listen_page
    try:
        if _listen_page is not None and _listen_handler is not None:
            _listen_page.remove_listener("response", _listen_handler)
    except Exception:
        pass
    _listen_handler = None
    _listen_page = None
    try:
        _listen_queue.clear()
        _listen_seen.clear()
    except Exception:
        pass
    return (True, "", "")


def _pop_matching_packet(pattern=""):
    """从队列取出首个 URL 匹配的包（消费）；无 → None。"""
    value, mode = parse_url_pattern(pattern)
    for it in list(_listen_queue):
        if match_url((it or {}).get("url", ""), value, mode):
            try:
                _listen_queue.remove(it)
            except Exception:
                pass
            return it
    return None


def _collect_packets(pattern="", count=1, timeout=None, poll=0.05):
    """等待并取回数据包。返回 (ok, items, detail)；超时 ok=False（items 为已收到的部分）。"""
    try:
        cnt = int(count) if count not in (None, "") else 1
    except Exception:
        cnt = 1
    if cnt < 1:
        cnt = 1
    try:
        tmo = float(timeout) if timeout not in (None, "") else 15.0
    except Exception:
        tmo = 15.0
    deadline = time.time() + max(tmo, 0.0)
    out = []
    while True:
        while len(out) < cnt:
            it = _pop_matching_packet(pattern)
            if it is None:
                break
            out.append(it)
        if len(out) >= cnt:
            return (True, out, "")
        if time.time() >= deadline:
            return (False, out, "TIMEOUT")
        time.sleep(max(float(poll), 0.01))


# ── P2-A: codegen → DSL 转换器（纯函数，仅 stdlib，不依赖 playwright） ──
class CodegenConvertError(ValueError):
    """strict=True 且存在无法识别语句时抛出（失败码 CONVERT_ERROR）。"""
    code = "CONVERT_ERROR"


_CODEGEN_IGNORE_RE = re.compile(
    r'^\s*(?:'
    r'(?:import|from)\s+'
    r'|(?:async\s+)?def\s+'
    r'|(?:async\s+)?with\s+sync_playwright'
    r'|(?:async\s+)?with\s+playwright'
    r')'
)

# 链式“定位构造器”方法（→ DSL 选择器片段）
_CODEGEN_BUILDERS = frozenset((
    "locator", "get_by_text", "get_by_role", "get_by_label",
    "get_by_placeholder", "get_by_test_id", "nth", "first", "last", "filter",
    "frame_locator",
))
# 动作方法
_CODEGEN_ACTIONS = frozenset((
    "goto", "click", "dblclick", "check", "uncheck", "fill", "press",
    "evaluate", "screenshot", "wait_for_timeout", "add_cookies",
    "expect_download", "set_input_files",
))
_CLICK_ALIASES = ("click", "dblclick", "check", "uncheck")
_KEYBOARD_PRESS_RE = re.compile(r'\.keyboard\.press\s*\(')
# 样板/生命周期方法（跳过且不告警）
_CODEGEN_IGNORE_METHODS = frozenset((
    "close", "new_page", "new_context", "launch", "start", "stop",
    "set_default_timeout", "set_viewport_size", "set_extra_http_headers",
    "add_init_script", "wait_for_load_state", "save_as", "cancel", "delete",
    "route", "unroute", "pause", "value",
))
# 下载流程变量根名（download = download_info.value 之类）
_CODEGEN_IGNORE_ROOTS = frozenset(("download", "download_info"))


def _codegen_logical_lines(code_text):
    """按括号平衡把 codegen 文本切成逻辑语句（合并跨行调用）。"""
    text = str(code_text).replace("\r\n", "\n").replace("\r", "\n")
    out = []
    buf = ""
    depth = 0
    for ln in text.split("\n"):
        buf = (buf + "\n" + ln) if buf else ln
        depth += ln.count("(") + ln.count("[") + ln.count("{")
        depth -= ln.count(")") + ln.count("]") + ln.count("}")
        if depth <= 0:
            out.append(buf)
            buf = ""
            depth = 0
    if buf.strip():
        out.append(buf)
    return out


def _codegen_attr_root(node):
    """取属性链根 Name（如 page.keyboard → page）。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _codegen_decompose(node):
    """把调用链拆为 (root, [(method, values, kwargs), ...])（内层→外层）。

    values 为 None 表示该调用存在无法字面求值的实参（视为不可转换）。
    kwargs 为 None 表示存在 **kwargs 或无法求值的关键字实参。
    """
    calls = []
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        try:
            vals = [ast.literal_eval(a) for a in cur.args]
        except Exception:
            vals = None
        kws = {}
        kw_ok = True
        for kw in cur.keywords:
            if kw.arg is None:
                kw_ok = False
                continue
            try:
                kws[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                kw_ok = False
        calls.append((cur.func.attr, vals, (kws if kw_ok else None)))
        cur = cur.func.value
    root = cur.id if isinstance(cur, ast.Name) else _codegen_attr_root(cur)
    calls.reverse()
    return (root, calls)


def _codegen_arg_str(values, idx, default=""):
    if not values or idx >= len(values):
        return default
    v = values[idx]
    return default if v is None else str(v)


def _codegen_kw_str(kwargs, name, default=""):
    if not kwargs or name not in kwargs:
        return default
    v = kwargs.get(name)
    return default if v is None else str(v)


def _codegen_secs(ms):
    """毫秒 → 秒字符串（如 1000→"1"、500→"0.5"）。"""
    try:
        return "%g" % (float(ms) / 1000.0)
    except Exception:
        return "0"


def _codegen_build_selector(calls):
    """把构造器调用序列 → DSL 选择器串（base + ` >> ` 步骤）；无法构造 → None。"""
    base = None
    steps = []
    for name, values, kwargs in calls:
        low = name.lower()
        if low == "locator":
            nxt = _codegen_arg_str(values, 0)
            if nxt == "":
                return None
            base = nxt
        elif low == "get_by_text":
            t = _codegen_arg_str(values, 0)
            exact = _codegen_kw_str(kwargs, "exact", "")
            base = ("text=" + t) if str(exact).lower() in ("true", "1", "是") else ("text:" + t)
        elif low == "get_by_role":
            role = _codegen_arg_str(values, 0)
            nm = _codegen_kw_str(kwargs, "name", "")
            base = "role:" + role + (("[name=" + nm + "]") if nm else "")
        elif low == "get_by_label":
            base = "label:" + _codegen_arg_str(values, 0)
        elif low == "get_by_placeholder":
            base = "placeholder:" + _codegen_arg_str(values, 0)
        elif low == "get_by_test_id":
            base = "testid:" + _codegen_arg_str(values, 0)
        elif low == "nth":
            steps.append("nth:" + _codegen_arg_str(values, 0))
        elif low == "first":
            steps.append("first:")
        elif low == "last":
            steps.append("last:")
        elif low == "filter":
            txt = _codegen_kw_str(kwargs, "has_text", "") or _codegen_arg_str(values, 0)
            steps.append("filter:" + txt)
    if base is None:
        return None
    if steps:
        return base + " >> " + " >> ".join(steps)
    return base


def _codegen_statement(stmt, frame_vars):
    """转换单条逻辑语句 → (rows:list, warning:str|None)。"""
    s = stmt.strip()
    if not s or s.startswith("#"):
        return ([], None)
    if _CODEGEN_IGNORE_RE.match(s):
        return ([], None)
    s2 = re.sub(r'^(?:await\s+|async\s+)', '', s)

    # with page.expect_download() as ...: → 等待下载（缺省参数）
    if "expect_download" in s2:
        return ([{"cmd": "等待下载", "args": ["", "", "", ""]}], None)

    # page.keyboard.press("X") → 按键（键盘命令）
    if _KEYBOARD_PRESS_RE.search(s2):
        try:
            node = ast.parse(s2, mode="eval").body
            _, calls = _codegen_decompose(node)
            if calls:
                key = _codegen_arg_str(calls[-1][1], 0)
                if key:
                    return ([{"cmd": "按键", "args": [key]}], None)
        except Exception:
            pass
        return ([], s)

    try:
        tree = ast.parse(s2)
    except SyntaxError:
        return ([], s)
    if not tree.body:
        return ([], None)
    node = tree.body[0]

    # 赋值语句：仅识别 frame = page.frame_locator(...)（其余视为样板代码跳过）
    if isinstance(node, ast.Assign):
        val = node.value
        tgt = node.targets[0] if node.targets else None
        if isinstance(val, ast.Call):
            _root, calls = _codegen_decompose(val)
            names = [c[0].lower() for c in calls]
            if "frame_locator" in names:
                spec = _codegen_arg_str(calls[names.index("frame_locator")][1], 0)
                if isinstance(tgt, ast.Name):
                    frame_vars[tgt.id] = spec
                return ([{"cmd": "切换框架", "args": [spec]}], None)
        return ([], None)

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.With, ast.AsyncWith,
                         ast.Import, ast.ImportFrom, ast.Pass)):
        return ([], None)
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return ([], s)

    root, calls = _codegen_decompose(node.value)
    if not calls:
        return ([], s)
    if root in _CODEGEN_IGNORE_ROOTS:
        return ([], None)

    rows = []
    frames = []
    if root in frame_vars:
        frames.append(frame_vars[root])

    action = None
    builders = []
    for name, values, kwargs in calls:
        low = name.lower()
        if low in _CODEGEN_BUILDERS:
            if low == "frame_locator":
                frames.append(_codegen_arg_str(values, 0))
            else:
                builders.append((name, values, kwargs))
            continue
        action = (low, values, kwargs)
        break

    if action is not None and action[0] in _CODEGEN_IGNORE_METHODS:
        return ([], None)

    for f in frames:
        rows.append({"cmd": "切换框架", "args": [f]})

    if action is None:
        # 仅定位、无动作（少见）：不产出动作行，也不告警
        return (rows, None)

    method, values, kwargs = action
    if values is None:
        return (rows, s)

    sel = _codegen_build_selector(builders)

    def _need_sel():
        return sel if sel is not None else _codegen_arg_str(values, 0)

    if method == "goto":
        url = _codegen_arg_str(values, 0)
        if not url:
            return (rows, s)
        rows.append({"cmd": "打开网页", "args": [url]})
    elif method in _CLICK_ALIASES:
        rows.append({"cmd": "浏览器点击", "args": [_need_sel()]})
    elif method == "fill":
        if sel is not None:
            rows.append({"cmd": "浏览器输入", "args": [sel, _codegen_arg_str(values, 0)]})
        else:
            rows.append({"cmd": "浏览器输入",
                         "args": [_codegen_arg_str(values, 0), _codegen_arg_str(values, 1)]})
    elif method == "press":
        if sel is not None:
            rows.append({"cmd": "浏览器点击", "args": [sel]})
            rows.append({"cmd": "按键", "args": [_codegen_arg_str(values, 0)]})
        else:
            rows.append({"cmd": "浏览器点击", "args": [_codegen_arg_str(values, 0)]})
            rows.append({"cmd": "按键", "args": [_codegen_arg_str(values, 1)]})
    elif method == "evaluate":
        rows.append({"cmd": "浏览器执行JS",
                     "args": [_codegen_arg_str(values, 0), "", "否"]})
    elif method == "screenshot":
        path = _codegen_kw_str(kwargs, "path", "") or _codegen_arg_str(values, 0)
        stem = os.path.splitext(os.path.basename(path))[0] if path else ""
        rows.append({"cmd": "浏览器截图", "args": [stem or "browser", "page", path]})
    elif method == "wait_for_timeout":
        rows.append({"cmd": "等待", "args": [_codegen_secs(_codegen_arg_str(values, 0, "0"))]})
    elif method == "add_cookies":
        arg0 = values[0] if values else None
        if not isinstance(arg0, list):
            return (rows, s)
        rows.append({"cmd": "浏览器设置Cookie",
                     "args": [json.dumps(arg0, ensure_ascii=False)]})
    elif method == "expect_download":
        rows.append({"cmd": "等待下载", "args": ["", "", "", ""]})
    elif method == "set_input_files":
        arg0 = values[0] if values else None
        files = arg0 if isinstance(arg0, list) else ([arg0] if arg0 else [])
        if not files or sel is None:
            return (rows, s)
        rows.append({"cmd": "浏览器上传", "args": [sel] + [str(f) for f in files]})
    else:
        return (rows, s)

    return (rows, None)


def codegen_to_dsl(code_text, strict=False):
    """Playwright codegen 文本 → ACRPA DSL 行（纯函数，可离线单测）。

    返回 ``(rows, warnings)``：
      * ``rows``     ``list[dict]``，形如 ``{"cmd": "浏览器点击", "args": [...]}``；
      * ``warnings`` 无法识别语句原文（已跳过、不产出对应行）。

    ``strict=True`` 时：存在无法识别语句 → 抛 ``CodegenConvertError``（code=CONVERT_ERROR）。
    仅使用 stdlib（re/ast/json/os）解析，不依赖 playwright、不触网、不执行输入代码。
    """
    if code_text is None:
        if strict:
            raise CodegenConvertError("CONVERT_ERROR: 输入为空")
        return ([], [])
    if not isinstance(code_text, str):
        if strict:
            raise CodegenConvertError("CONVERT_ERROR: 输入类型非文本")
        return ([], [str(code_text)])

    rows = []
    warnings = []
    frame_vars = {}
    for stmt in _codegen_logical_lines(code_text):
        try:
            r, w = _codegen_statement(stmt, frame_vars)
        except Exception:
            r, w = ([], stmt.strip())
        rows.extend(r)
        if w is not None:
            warnings.append(w)

    if strict and warnings:
        raise CodegenConvertError(
            "CONVERT_ERROR: {} 条语句无法识别（首条: {}）".format(len(warnings), warnings[0]))
    return (rows, warnings)


def codegen_rows_to_text(rows):
    """rows → 行式 DSL 文本（便于查看/复制；纯函数）。"""
    out = []
    for r in (rows or []):
        args = [str(a) for a in (r.get("args") or [])]
        out.append(",".join([str(r.get("cmd", ""))] + args))
    return "\n".join(out)


# ======================================================================
# 4) facade 命令处理器 —— 取参 / 变量 / 错误码 / 命令实现
# ======================================================================
class BrowserSettings(object):
    """从 state 读取的轻量运行时设置（不新增依赖）。"""

    __slots__ = ("wait_timeout", "poll_interval", "full_page_screenshot",
                 "js_timeout", "retry", "retry_interval", "silent")

    def __init__(self):
        self.wait_timeout = _cfg_float("BROWSER_WAIT_TIMEOUT", 15.0)
        self.poll_interval = _cfg_float("BROWSER_POLL_INTERVAL", 0.2)
        self.full_page_screenshot = bool(getattr(state, "BROWSER_FULL_PAGE_SCREENSHOT", False))
        self.js_timeout = _cfg_float("BROWSER_JS_TIMEOUT", 15.0)
        self.retry = int(_cfg_float("BROWSER_RETRY", 1))
        self.retry_interval = _cfg_float("BROWSER_RETRY_INTERVAL", 0.5)
        self.silent = bool(getattr(state, "BROWSER_SILENT", False))


def _cfg_float(name, default):
    try:
        return float(getattr(state, name, default))
    except Exception:
        return default


def _settings():
    return BrowserSettings()


def _row_arg(row, idx, default=""):
    """双接口统一取参：`row.args[idx]`（ScriptData）或 `row[idx+1].value`（xlrd 风格）。

    缺失 / 空值 → default。
    """
    try:
        if hasattr(row, "args") and row.args is not None:
            args = row.args
            value = args[idx] if idx < len(args) else None
        else:
            value = row[idx + 1].value
    except Exception:
        return default
    if value is None or value == "":
        return default
    return value


def _engine_variables():
    try:
        from engine import engine
        return engine.variables
    except Exception:
        return None


def _resolve_vars(text):
    """`${var}` 替换（缺失变量替换为空串；无变量时原样返回）。"""
    raw = str(text)
    variables = _engine_variables()
    if not variables:
        return re.sub(r'\$\{([^}]+)\}', '', raw)
    return re.sub(r'\$\{([^}]+)\}', lambda m: str(variables.get(m.group(1), '')), raw)


def _write_var(name, value):
    """写回变量（engine.variables[name] = value）。"""
    if not name:
        return
    variables = _engine_variables()
    if variables is None:
        return
    try:
        variables[str(name)] = value
    except Exception:
        pass


def _set_last_error(code, message):
    """仅写回稳定失败码/描述（不返回 False，用于保持旧命令返回值语义）。"""
    variables = _engine_variables()
    if variables is not None:
        try:
            variables["browser_last_error"] = code
            variables["browser_last_message"] = message
        except Exception:
            pass
    log1(message, "error" if code else "info")


def _fail(code, message):
    """统一失败出口：写 browser_last_error/browser_last_message + log + return False。"""
    variables = _engine_variables()
    if variables is not None:
        try:
            variables["browser_last_error"] = code
            variables["browser_last_message"] = message
        except Exception:
            pass
    log1(message, "error")
    return False


def _clear_last_error():
    """成功时清空错误变量。"""
    variables = _engine_variables()
    if variables is not None:
        try:
            variables["browser_last_error"] = ""
            variables["browser_last_message"] = ""
        except Exception:
            pass


def _is_silent():
    return bool(getattr(state, "BROWSER_SILENT", False))


def _with_retry(fn, retries, interval):
    """对瞬态失败做轻量重试。fn() → (ok, detail)。返回 (ok, detail)。"""
    last = ""
    attempts = max(int(retries), 0)
    for i in range(attempts + 1):
        ok, detail = fn()
        if ok:
            return True, ""
        last = detail
        if i < attempts:
            try:
                time.sleep(max(float(interval), 0.0))
            except Exception:
                pass
    return False, last


# ── 命令: 打开网页 ──
def _browser_navigate(row, script_dir=""):
    """打开网页 — navigate to URL.

    Excel: 打开网页, https://example.com
    """
    url = str(_row_arg(row, 0, "")).strip()
    url = _resolve_vars(url)

    if not url:
        log1("打开网页: 未指定URL", "error")
        _set_last_error("INVALID_ARGUMENT", "打开网页: 未指定URL")
        return

    page = get_top_page()
    if not page:
        log1("打开网页: 浏览器后端不可用", "error")
        _set_last_error("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
        return

    try:
        log1("打开网页: {}".format(url))
        page.goto(url, wait_until="domcontentloaded")
        # Store current URL in variables
        variables = _engine_variables()
        if variables is not None:
            try:
                variables["browser_url"] = page.url
                variables["browser_title"] = page.title()
            except Exception:
                pass
        _clear_last_error()
        log1("网页已加载: {}".format(page.title()))
    except Exception as e:
        log1("打开网页失败: {}".format(e), "error")
        _set_last_error("NAV_FAILED", "网页打开失败: {}".format(e))


# ── 命令: 浏览器点击 ──
def _browser_click(row, script_dir=""):
    """浏览器点击 — click element by DSL / CSS / text=.

    Excel: 浏览器点击, #login-btn
           浏览器点击, role:button[name=登录]
    """
    selector = str(_row_arg(row, 0, "")).strip()

    if not selector:
        log1("浏览器点击: 未指定选择器", "error")
        _set_last_error("INVALID_ARGUMENT", "浏览器点击: 未指定选择器")
        return

    page = get_active_page()
    if not page:
        log1("浏览器点击: 浏览器后端不可用", "error")
        _set_last_error("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
        return

    plan = parse_locator(selector)
    if plan.get("engine") == "error":
        log1("浏览器点击: 定位表达式无效 ({})".format(plan.get("message", "")), "error")
        _set_last_error("INVALID_LOCATOR", "浏览器点击: 定位表达式无效")
        return

    st = _settings()

    def _attempt():
        try:
            loc = build_locator(page, plan)
            if loc is None:
                return (False, "定位表达式无效")
            loc.click(timeout=15000)
            return (True, "")
        except Exception as e:
            return (False, str(e))

    try:
        log1("浏览器点击: {}".format(selector))
        ok, detail = _with_retry(_attempt, st.retry, st.retry_interval)
        if ok:
            _clear_last_error()
            log1("已点击元素: {}".format(selector))
        else:
            log1("浏览器点击失败 '{}': {}".format(selector, detail), "error")
            _set_last_error("ELEMENT_NOT_FOUND", "浏览器点击失败: {}".format(detail))
    except Exception as e:
        log1("浏览器点击失败 '{}': {}".format(selector, e), "error")
        _set_last_error("ELEMENT_NOT_FOUND", "浏览器点击失败: {}".format(e))


# ── 命令: 浏览器输入 ──
def _browser_input(row, script_dir=""):
    """浏览器输入 — type text into input element.

    Excel: 浏览器输入, #username, admin
           浏览器输入, #search, ${keyword}
    """
    selector = str(_row_arg(row, 0, "")).strip()
    text = _row_arg(row, 1, "")

    if not selector:
        log1("浏览器输入: 未指定选择器", "error")
        _set_last_error("INVALID_ARGUMENT", "浏览器输入: 未指定选择器")
        return

    page = get_active_page()
    if not page:
        log1("浏览器输入: 浏览器后端不可用", "error")
        _set_last_error("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
        return

    plan = parse_locator(selector)
    if plan.get("engine") == "error":
        log1("浏览器输入: 定位表达式无效 ({})".format(plan.get("message", "")), "error")
        _set_last_error("INVALID_LOCATOR", "浏览器输入: 定位表达式无效")
        return

    try:
        text = _resolve_vars(text)
        log1("浏览器输入: {} ← '{}'".format(selector, str(text)[:50]))

        st = _settings()

        def _attempt():
            try:
                loc = build_locator(page, plan)
                if loc is None:
                    return (False, "定位表达式无效")
                loc.fill(text)
                return (True, "")
            except Exception as e:
                return (False, str(e))

        ok, detail = _with_retry(_attempt, st.retry, st.retry_interval)
        if ok:
            _clear_last_error()
        else:
            log1("浏览器输入失败 '{}': {}".format(selector, detail), "error")
            _set_last_error("ELEMENT_NOT_FOUND", "浏览器输入失败: {}".format(detail))
    except Exception as e:
        log1("浏览器输入失败 '{}': {}".format(selector, e), "error")
        _set_last_error("ELEMENT_NOT_FOUND", "浏览器输入失败: {}".format(e))


# ── 命令: 等待元素（批次 B 扩展） ──
def _browser_wait_element(row, script_dir=""):
    """等待元素 — 等待元素/页面达到指定状态.

    Excel: 等待元素, .result, 10, 出现
           等待元素, .loading, 30, 消失
           等待元素, .dashboard, 20, 存在
           等待元素, #btn, 10, 可点击
           等待元素, .x, 15, url, https://example.com/done

    向后兼容: 第 3 参数缺省 = 出现(visible)；`消失` → hidden；返回值保持 None（不置 False）。
    """
    selector = str(_row_arg(row, 0, "")).strip()

    timeout_raw = _row_arg(row, 1, None)
    try:
        timeout = float(timeout_raw) if timeout_raw else 10.0
    except Exception:
        timeout = 10.0

    state_raw = str(_row_arg(row, 2, "")).strip()
    expect_raw = str(_row_arg(row, 3, "")).strip()

    if not selector:
        log1("等待元素: 未指定选择器", "error")
        _set_last_error("INVALID_ARGUMENT", "等待元素: 未指定选择器")
        return

    page = get_active_page()
    if not page:
        log1("等待元素: 浏览器后端不可用", "error")
        _set_last_error("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
        return

    # 状态归一（未知状态回退 visible，保持旧“非消失即可见”语义并写码）
    if not state_raw:
        code = "visible"
    else:
        code = parse_wait_state(state_raw)
        if not code:
            log1("等待元素: 未知状态 '{}'，按'出现'处理".format(state_raw), "warning")
            _set_last_error("INVALID_ARGUMENT", "等待元素: 未知状态 '{}'".format(state_raw))
            code = "visible"

    plan = parse_locator(selector)
    if plan.get("engine") == "error":
        log1("等待元素: 定位表达式无效 ({})".format(plan.get("message", "")), "error")
        _set_last_error("INVALID_LOCATOR", "等待元素: 定位表达式无效")
        return

    st = _settings()
    wait_ms = int(timeout * 1000)
    poll_ms = int(st.poll_interval * 1000)

    try:
        log1("等待元素 '{}' (状态: {}, 超时: {}s)".format(selector, code, timeout))
        if code == "download":
            # P1-C：download 状态委托 session 级下载队列（命中即成功，不消费队列）
            ok, err_code, detail = _wait_download_state(wait_ms, expect_raw or None)
        else:
            # P1: url/title 状态始终作用于顶层页；其余状态作用于当前活动目标（frame/page）
            target = get_top_page() if code in ("url", "title") else page
            ok, err_code, detail = wait_for(
                target, plan, state=code, timeout_ms=wait_ms,
                poll_ms=poll_ms, expect=(expect_raw or None),
            )
        if ok:
            _clear_last_error()
            log1("元素 '{}' 已满足状态 '{}'".format(selector, code))
        else:
            log1("等待元素 '{}' 失败: {} ({})".format(selector, detail, err_code), "warning")
            _set_last_error(err_code or "TIMEOUT", detail)
    except Exception as e:
        log1("等待元素 '{}' 失败: {}".format(selector, e), "warning")
        _set_last_error("TIMEOUT", "等待元素失败: {}".format(e))


# ── 命令: 浏览器截图（批次 B 扩展） ──
def _browser_screenshot(row, script_dir=""):
    """浏览器截图 — 视口 / 整页 / 元素截图，可指定保存路径并写回变量.

    Excel: 浏览器截图, result, page           (旧用法: 视口)
           浏览器截图, header, #header        (旧用法: 元素)
           浏览器截图, result, 整页           (新增: 整页)
           浏览器截图, header, tag:h1, D:\\shot\\a.png  (新增: 元素+路径)
           浏览器截图, result, page, , shot_path         (新增: 写回变量)
    """
    name = str(_row_arg(row, 0, "browser")) or "browser"
    target = str(_row_arg(row, 1, "page")) or "page"
    save_path = str(_row_arg(row, 2, "") or "")
    write_name = str(_row_arg(row, 3, "") or "")

    page = get_active_page()
    if not page:
        log1("浏览器截图: 浏览器后端不可用", "error")
        _set_last_error("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
        return

    t = target.strip()
    is_element = t not in ("", "page", "视口", "viewport", "整页", "full", "fullpage", "full_page")

    if is_element:
        plan = parse_locator(t)
        if plan.get("engine") == "error":
            log1("浏览器截图: 定位表达式无效 ({})".format(plan.get("message", "")), "error")
            _set_last_error("INVALID_LOCATOR", "浏览器截图: 定位表达式无效")
            return

    try:
        import datetime
        ts = datetime.datetime.now().strftime("%m%d_%H%M%S")

        if save_path:
            fp = _resolve_vars(save_path)
            parent = os.path.dirname(fp)
            if parent:
                os.makedirs(parent, exist_ok=True)
        else:
            ss_dir = str(getattr(state, "BROWSER_SCREENSHOT_DIR", "") or "").strip()
            if not ss_dir:
                ss_dir = os.path.join(os.path.dirname(state.CONFIG_PATH), "screenshots")
            os.makedirs(ss_dir, exist_ok=True)
            fp = os.path.join(ss_dir, "browser_{}_{}.png".format(name, ts))

        if t == "整页" or t in ("full", "fullpage", "full_page"):
            # P1: 整页/视口截图始终作用于顶层页
            get_top_page().screenshot(path=fp, full_page=True)
        elif is_element:
            loc = build_locator(page, plan)
            if loc is None:
                _set_last_error("INVALID_LOCATOR", "浏览器截图: 定位表达式无效")
                return
            loc.screenshot(path=fp)
        else:
            full = bool(getattr(state, "BROWSER_FULL_PAGE_SCREENSHOT", False))
            get_top_page().screenshot(path=fp, full_page=full)

        log1("浏览器截图已保存: {}".format(fp))
        _write_var(write_name or "浏览器截图路径", fp)
        _clear_last_error()
    except Exception as e:
        log1("浏览器截图失败: {}".format(e), "error")
        _set_last_error("SCREENSHOT_ERROR", "截图失败: {}".format(e))


# ── 命令: 浏览器执行JS（批次 C 新增，别名 执行JS） ──
def _browser_exec_js(row, script_dir=""):
    """浏览器执行JS — 在当前页执行 JS，返回值写回变量.

    Excel: 浏览器执行JS, document.title, page_title, 是
           浏览器执行JS, () => document.querySelectorAll('a').length, link_count, 否

    返回: 成功 None；失败 False（browser_last_error=JS_ERROR/INVALID_ARGUMENT/PW_NOT_INSTALLED）。
    """
    script = _row_arg(row, 0, "")
    target_var = str(_row_arg(row, 1, "") or "")
    as_expr_raw = str(_row_arg(row, 2, "") or "").strip()

    if script is None or str(script).strip() == "":
        return _fail("INVALID_ARGUMENT", "浏览器执行JS: 缺少脚本")

    page = _ensure_session()
    if not page:
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")

    as_expr = as_expr_raw in ("是", "true", "True", "TRUE", "1", "yes", "y", "Y")

    try:
        ok, payload, detail = _run_js(script, as_expr=as_expr)
        if not ok:
            if payload == "PW_NOT_INSTALLED":
                return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
            return _fail("JS_ERROR", "脚本执行错误: {}".format(detail))
        if target_var:
            _write_var(target_var, payload)
        _clear_last_error()
        return None
    except Exception as e:
        return _fail("JS_ERROR", "脚本执行错误: {}".format(e))


# ── 命令: 浏览器读取Cookie（批次 C 新增） ──
def _browser_cookie_get(row, script_dir=""):
    """浏览器读取Cookie — 导出当前上下文 Cookie 到变量/文件.

    Excel: 浏览器读取Cookie, cookie_json, json
           浏览器读取Cookie, cookie_header, header, D:\\out\\cookies.txt

    返回: 成功 None；失败 False。
    """
    target_var = str(_row_arg(row, 0, "cookie") or "cookie")
    fmt = str(_row_arg(row, 1, "json") or "json").strip().lower()
    save_path = str(_row_arg(row, 2, "") or "")

    page = _ensure_session()
    if not page:
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")

    try:
        data = cookies_get_all()
        if fmt in ("json", ""):
            text = cookies_to_json(data)
        elif fmt in ("header", "请求头", "headers"):
            text = cookies_to_header(data)
        elif fmt in ("netscape", "bn"):
            text = cookies_to_netscape(data)
        else:
            return _fail("COOKIE_ERROR", "Cookie 操作失败: 未知格式 '{}'".format(fmt))

        if save_path:
            fp = _resolve_vars(save_path)
            parent = os.path.dirname(fp)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)

        _write_var(target_var, text)
        _clear_last_error()
        return None
    except Exception as e:
        return _fail("COOKIE_ERROR", "Cookie 操作失败: {}".format(e))


# ── 命令: 浏览器设置Cookie（批次 C 新增） ──
def _browser_cookie_set(row, script_dir=""):
    """浏览器设置Cookie — 从变量或文件注入 Cookie.

    Excel: 浏览器设置Cookie, cookie_json
           浏览器设置Cookie, D:\\in\\cookies.json, .example.com

    返回: 成功 None；失败 False。
    """
    source = str(_row_arg(row, 0, "") or "").strip()
    domain = str(_row_arg(row, 1, "") or "").strip()

    if not source:
        return _fail("INVALID_ARGUMENT", "浏览器设置Cookie: 缺少来源")

    page = _ensure_session()
    if not page:
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")

    try:
        # 来源解析：文件路径 > 变量名 > 字面量串
        src = _resolve_vars(source)
        text = None
        if src and os.path.exists(src):
            with open(src, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            variables = _engine_variables()
            if variables is not None and source in variables:
                text = variables.get(source)
            else:
                text = source

        items = parse_cookies(text)

        if not items:
            return _fail("COOKIE_ERROR", "Cookie 操作失败: 未解析到有效 Cookie")

        if domain:
            for c in items:
                if isinstance(c, dict) and not c.get("domain"):
                    c["domain"] = domain

        ok, detail = cookies_set(items)
        if not ok:
            return _fail("COOKIE_ERROR", "Cookie 操作失败: {}".format(detail))
        _clear_last_error()
        return None
    except Exception as e:
        return _fail("COOKIE_ERROR", "Cookie 操作失败: {}".format(e))


# ── 命令: 切换框架 / 返回主框架（P1-B 新增） ──
def _browser_switch_frame(row, script_dir=""):
    """切换框架 — 进入 iframe（DSL 定位 / 索引）或回到主文档.

    Excel: 切换框架, #payment-iframe
           切换框架, 2
           切换框架, main

    返回: 成功 None；失败 False（FRAME_NOT_FOUND / INVALID_ARGUMENT / PW_NOT_INSTALLED）。
    """
    spec = str(_row_arg(row, 0, "") or "").strip()
    if not spec:
        return _fail("INVALID_ARGUMENT", "切换框架: 缺少参数")
    if not get_top_page():
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    ok, code, detail = _switch_frame(spec)
    if not ok:
        return _fail(code or "FRAME_NOT_FOUND", detail or "未找到框架")
    _clear_last_error()
    log1("已切换框架: {}".format(spec))
    return None


def _browser_main_frame(row, script_dir=""):
    """返回主框架 — 回到顶层 frame（等价 切换框架, main）.

    返回: 成功 None；失败 False（FRAME_NOT_FOUND / PW_NOT_INSTALLED）。
    """
    if not get_top_page():
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    ok, code, detail = _switch_frame("main")
    if not ok:
        return _fail(code or "FRAME_NOT_FOUND", detail or "未找到框架")
    _clear_last_error()
    return None


def _refresh_page_vars(p):
    """把当前页 url/title 写回 browser_url/browser_title。"""
    if p is None:
        return
    variables = _engine_variables()
    if variables is None:
        return
    try:
        variables["browser_url"] = p.url
        variables["browser_title"] = p.title()
    except Exception:
        pass


# ── 命令: 新建标签页 / 切换标签页 / 关闭标签页（P1-B 新增） ──
def _browser_new_tab(row, script_dir=""):
    """新建标签页 — 新建 tab（可选 URL）并切换为活动页.

    Excel: 新建标签页
           新建标签页, https://example.com/report

    返回: 成功 None；失败 False（TAB_FAILED / NAV_FAILED / PW_NOT_INSTALLED）。
    """
    url = str(_row_arg(row, 0, "") or "").strip()
    if url:
        url = _resolve_vars(url)
    if not _ensure_session():
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    ok, code, detail, p = _new_top_page(url)
    if not ok:
        return _fail(code or "TAB_FAILED", detail or "标签页操作失败")
    _refresh_page_vars(p or _active_top_page())
    _clear_last_error()
    log1("已新建标签页: {}".format(url or "(空白页)"))
    return None


def _browser_switch_tab(row, script_dir=""):
    """切换标签页 — 按序号 / 标题 / URL 切换活动页.

    Excel: 切换标签页, 0
           切换标签页, 报表

    返回: 成功 None；失败 False（TAB_NOT_FOUND / INVALID_ARGUMENT / PW_NOT_INSTALLED）。
    """
    sel = str(_row_arg(row, 0, "") or "").strip()
    if not sel:
        return _fail("INVALID_ARGUMENT", "切换标签页: 缺少参数")
    if not _ensure_session():
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    ok, code, detail = _switch_tab(sel)
    if not ok:
        return _fail(code or "TAB_NOT_FOUND", detail or "未找到标签页")
    _refresh_page_vars(_active_top_page())
    _clear_last_error()
    return None


def _browser_close_tab(row, script_dir=""):
    """关闭标签页 — 关闭当前或指定标签页.

    Excel: 关闭标签页
           关闭标签页, 1

    返回: 成功 None；失败 False（TAB_NOT_FOUND / TAB_CLOSE_FAILED）。
    """
    spec = str(_row_arg(row, 0, "") or "").strip()
    if not _ensure_session() and not _pages:
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    ok, code, detail = _close_tab(spec)
    if not ok:
        return _fail(code or "TAB_NOT_FOUND", detail or "未找到标签页")
    _refresh_page_vars(_active_top_page())
    _clear_last_error()
    return None


# ── 命令: 等待下载（P1-C 新增） ──
def _browser_wait_download(row, script_dir=""):
    """等待下载 — 等待浏览器下载完成并保存文件.

    Excel: 等待下载, D:\\out, 报表, 30, dl_path

    返回: 成功 None（绝对路径写回变量）；失败 False（DOWNLOAD_TIMEOUT / DOWNLOAD_ERROR）。
    """
    dir_arg = str(_row_arg(row, 0, "") or "").strip()
    pattern = str(_row_arg(row, 1, "") or "").strip()
    timeout_raw = _row_arg(row, 2, None)
    var = str(_row_arg(row, 3, "") or "").strip() or "浏览器下载路径"
    try:
        timeout = float(timeout_raw) if timeout_raw not in (None, "") \
            else _cfg_float("BROWSER_DOWNLOAD_TIMEOUT", 15.0)
    except Exception:
        timeout = _cfg_float("BROWSER_DOWNLOAD_TIMEOUT", 15.0)

    page = get_active_page()
    if not page:
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")

    deadline = time.time() + max(timeout, 0.0)
    download = None
    while True:
        cand = _pop_matching_download(pattern)
        if cand is not None:
            download = cand
            break
        remaining = deadline - time.time()
        if remaining <= 0:
            return _fail("DOWNLOAD_TIMEOUT", "下载超时")
        try:
            ev = page.wait_for_event("download", timeout=int(remaining * 1000))
        except Exception:
            return _fail("DOWNLOAD_TIMEOUT", "下载超时")
        try:
            name = ev.suggested_filename
        except Exception:
            name = ""
        if match_download_name(name, pattern):
            download = ev
            break
        if time.time() >= deadline:
            return _fail("DOWNLOAD_TIMEOUT", "下载超时")

    try:
        out_dir = _download_dir(dir_arg)
        os.makedirs(out_dir, exist_ok=True)
        overwrite = bool(getattr(state, "BROWSER_DOWNLOAD_OVERWRITE", True))
        try:
            fname = download.suggested_filename
        except Exception:
            fname = "download"
        target = _download_target_path(out_dir, fname, overwrite)
        download.save_as(target)
    except Exception as e:
        return _fail("DOWNLOAD_ERROR", "下载保存失败: {}".format(e))

    _write_var(var, os.path.abspath(target))
    _clear_last_error()
    log1("下载已保存: {}".format(target))
    return None


_BOOL_TRUE = ("是", "true", "True", "TRUE", "1", "yes", "y", "Y")
_BOOL_FALSE = ("否", "false", "False", "FALSE", "0", "no", "n", "N")


# ── 命令: 浏览器上传（P1-C 新增） ──
def _browser_upload(row, script_dir=""):
    """浏览器上传 — 对 input[type=file] 设置本地文件（支持多文件）.

    Excel: 浏览器上传, tag:input@type=file, D:\\发票\\a.pdf, D:\\发票\\b.pdf

    返回: 成功 None；失败 False（UPLOAD_ERROR / ELEMENT_NOT_FOUND / INVALID_ARGUMENT）。
    """
    selector = str(_row_arg(row, 0, "") or "").strip()
    if not selector:
        return _fail("INVALID_ARGUMENT", "浏览器上传: 缺少定位表达式")

    # 收集可变长文件参数；末尾若为 是/否 视作“是否清空已有”开关
    raw_args = []
    i = 1
    while i <= 64:
        v = _row_arg(row, i, None)
        if v is None:
            break
        raw_args.append(str(v))
        i += 1

    clear = True
    if raw_args:
        last = raw_args[-1].strip()
        if last in _BOOL_TRUE or last in _BOOL_FALSE:
            clear = last in _BOOL_TRUE
            raw_args = raw_args[:-1]
    # clear 仅作语义声明：Playwright set_input_files 本体为“替换”语义，无需分支
    _ = clear

    files = [_resolve_vars(f) for f in raw_args if str(f).strip()]
    if not files:
        return _fail("INVALID_ARGUMENT", "浏览器上传: 缺少文件路径")
    for fp in files:
        if not os.path.exists(fp):
            return _fail("INVALID_ARGUMENT", "浏览器上传: 文件不存在: {}".format(fp))

    page = get_active_page()
    if not page:
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")

    plan = parse_locator(selector)
    if plan.get("engine") == "error":
        return _fail("INVALID_LOCATOR", "浏览器上传: 定位表达式无效")

    try:
        loc = build_locator(page, plan)
        if loc is None:
            return _fail("INVALID_LOCATOR", "浏览器上传: 定位表达式无效")
        payload = files[0] if len(files) == 1 else files
        loc.set_input_files(payload, timeout=15000)
        _clear_last_error()
        log1("已上传 {} 个文件".format(len(files)))
        return None
    except Exception as e:
        return _fail("UPLOAD_ERROR", "上传失败: {}".format(e))


# ── 命令: 连接已开浏览器 / 接管浏览器（P2-B 新增） ──
def _browser_connect_cdp(row, script_dir=""):
    """连接已开浏览器 — 通过 CDP endpoint 接管已运行的 Chrome/Edge.

    Excel: 连接已开浏览器
           连接已开浏览器, http://127.0.0.1:9222
           连接已开浏览器, 127.0.0.1:9222, 否

    获取 endpoint 指引（Windows）:
      chrome.exe --remote-debugging-port=9222 --user-data-dir="D:\\cdp-profile"
      （须使用独立 user-data-dir，否则已开实例不会开启调试端口）
      验证: http://127.0.0.1:9222/json/version

    返回: 成功 None；失败 False（CDP_CONNECT_FAILED / INVALID_ARGUMENT / PW_NOT_INSTALLED）。
    """
    ep_arg = str(_row_arg(row, 0, "") or "").strip()
    close_raw = str(_row_arg(row, 1, "") or "").strip()
    close_old = close_raw not in _BOOL_FALSE  # 缺省=是
    default_ep = str(getattr(state, "BROWSER_CDP_ENDPOINT", "") or "")
    ep = parse_cdp_endpoint(ep_arg, default_ep)
    if not ep:
        return _fail(
            "INVALID_ARGUMENT",
            "连接已开浏览器: 缺少 endpoint（示例：chrome.exe --remote-debugging-port=9222 "
            "--user-data-dir=D:\\cdp-profile，再填 http://127.0.0.1:9222）")
    ok, code, detail = _connect_cdp(ep, close_old)
    if not ok:
        return _fail(code or "CDP_CONNECT_FAILED", detail or "接管浏览器失败")
    if code == "reuse":
        log1("连接已开浏览器: 复用当前会话")
    else:
        log1("已接管浏览器: {}".format(ep))
    _refresh_page_vars(_active_top_page())
    _clear_last_error()
    return None


# ── 命令: 开始监听（P2-C 新增） ──
def _browser_start_listen(row, script_dir=""):
    """开始监听 — 对当前页注册 response 监听，命中过滤后入队（抓包）.

    Excel: 开始监听
           开始监听, /api/login, xhr
           开始监听, 订单接口, fetch, 500

    参数: URL匹配(可选，包含匹配或 /正则/), 资源类型(可选，全部=不过滤), 队列上限(可选，默认配置)

    返回: 成功 None；失败 False（LISTEN_ERROR / PW_NOT_INSTALLED）。
    """
    pattern = str(_row_arg(row, 0, "") or "").strip()
    types_text = str(_row_arg(row, 1, "") or "").strip()
    maxlen = _row_arg(row, 2, None)
    if not get_active_page():
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    ok, code, detail = _start_listen(pattern, types_text, maxlen)
    if not ok:
        return _fail(code or "LISTEN_ERROR", detail or "监听失败")
    _clear_last_error()
    log1("已开始监听: url={} 类型={} 上限={}".format(
        pattern or "*", types_text or "全部", maxlen if maxlen else "配置默认"))
    return None


# ── 命令: 等待数据包（P2-C 新增） ──
def _browser_wait_packets(row, script_dir=""):
    """等待数据包 — 等待并取回命中的数据包，写回变量或落盘.

    Excel: 等待数据包, /api/token, 1, 20, token_json
           等待数据包, /api/list, 3, 30, packets, D:\\out\\packets.json, json

    参数: URL匹配(可选), 数量(可选,默认1), 超时秒数(可选,默认配置), 写回变量(可选),
          导出路径(可选), 导出格式(可选,json)

    说明: 结果写成 JSON 列表（去重键 = method+url+status）；超时→TIMEOUT。
    与 浏览器执行JS / 浏览器设置Cookie 配合可用于取 token 复用登录态。
    返回: 成功 None；失败 False（TIMEOUT / INVALID_ARGUMENT / LISTEN_ERROR）。
    """
    pattern = str(_row_arg(row, 0, "") or "").strip()
    count = _row_arg(row, 1, None)
    timeout = _row_arg(row, 2, None) or _cfg_float("BROWSER_LISTEN_DEFAULT_TIMEOUT", 15.0)
    var = str(_row_arg(row, 3, "") or "").strip()
    path = str(_row_arg(row, 4, "") or "").strip()
    fmt = str(_row_arg(row, 5, "json") or "json").strip().lower()

    if not get_active_page():
        return _fail("PW_NOT_INSTALLED", "浏览器后端未安装(Playwright)")
    if _listen_handler is None and not _listen_queue:
        return _fail("INVALID_ARGUMENT", "等待数据包: 尚未开始监听（请先执行 开始监听）")

    try:
        ok, items, detail = _collect_packets(pattern, count, timeout)
    except Exception as e:
        return _fail("TIMEOUT", "等待数据包失败: {}".format(e))
    if not ok:
        return _fail("TIMEOUT", "等待数据包超时")
    if not items:
        return _fail("TIMEOUT", "等待数据包超时（无命中）")

    text = export_packets(items, fmt)
    try:
        if var:
            _write_var(var, text)
        if path:
            fp = _resolve_vars(path)
            parent = os.path.dirname(fp)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
    except Exception as e:
        return _fail("LISTEN_ERROR", "等待数据包: 导出失败: {}".format(e))

    _clear_last_error()
    log1("已获取 {} 个数据包".format(len(items)))
    return None


# ── 命令: 停止监听（P2-C 新增） ──
def _browser_stop_listen(row, script_dir=""):
    """停止监听 — 停止监听并清空队列.

    Excel: 停止监听

    返回: 成功 None（幂等；未监听时同样成功）。
    """
    _stop_listen()
    _clear_last_error()
    log1("已停止监听")
    return None


# ── 命令: 启动浏览器录制（P2-A 可选入口） ──
def _browser_record(row, script_dir=""):
    """启动浏览器录制 — 调用 playwright codegen CLI 并把结果转为 DSL 追加.

    说明: 依赖 playwright CLI（`playwright codegen`）。缺失/失败时降级为稳定失败码
    RECORD_ERROR，并提示手动执行 `playwright codegen -o rec.py`；转换器本体
    [`codegen_to_dsl`] 为纯函数，可离线使用。

    返回: 成功 None；失败 False（RECORD_ERROR）。
    """
    import subprocess
    import tempfile
    import shutil

    exe = shutil.which("playwright")
    if not exe:
        return _fail(
            "RECORD_ERROR",
            "录制失败: 未找到 playwright CLI；请手动执行 playwright codegen -o rec.py 后使用转换器")
    out = os.path.join(tempfile.gettempdir(), "acrpa_codegen_rec.py")
    try:
        rc = subprocess.call([exe, "codegen", "-o", out])
    except Exception as e:
        return _fail("RECORD_ERROR", "录制失败: {}".format(e))
    if rc != 0 or not os.path.exists(out):
        return _fail("RECORD_ERROR", "录制失败: codegen 未生成文件")
    try:
        with open(out, "r", encoding="utf-8") as f:
            text = f.read()
        from recorder import convert_and_append  # 薄适配（不改 GUI）
        added, warnings = convert_and_append(text)
    except Exception as e:
        return _fail("RECORD_ERROR", "录制失败: {}".format(e))
    _clear_last_error()
    log1("录制转换完成: 追加 {} 条，{} 条告警".format(added, len(warnings)))
    return None


# ======================================================================
# Browser is available? (for status reporting)
# ======================================================================
def browser_get_status():
    """Return browser availability status."""
    available = _check_playwright_available()
    if available:
        return {"available": True, "name": "Playwright (Chromium)", "connected": _page is not None}
    return {"available": False, "name": None, "connected": False,
            "hint": "pip install playwright && playwright install chromium"}
