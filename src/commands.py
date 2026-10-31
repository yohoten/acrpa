"""Command registry — single source of truth for all automation commands.

除了既有的 (name, description, params, handler) 四元组, 本模块现在还提供
**结构化参数 schema**:

    commands.schema("找图")
    # [{'name': '图片名', 'kind': 'string', ...},
    #  {'name': '精度', 'kind': 'number', 'default': '0.96', ...}]

    commands.validate("按键", ["a", "abc"])      # → (["参数『次数』不是数字: 'abc'"], ...)
    commands.signature("按键")                    # → "按键名, 次数=1, 间隔=0.1"

设计取舍
--------
1. **向后兼容**: `list_all()` 仍返回四元组, 既有的 20+ 处消费方 (帮助窗口 /
   脚本市场校验 / 插件系统 / 文档生成器) 一律不用改。
2. **存量零改动即得 schema**: 68 条命令的参数说明本来就是
   `名称(默认X)` / `名称(可选)` / `名称(是/否)` 这套写法, 直接解析出来即可,
   不必回头去改每一条注册语句。
3. **新命令可显式声明**: `register(..., schema=[{...}])` 优先于字符串解析。
4. **不谎报必填**: 旧写法里"没有写(可选)"≠"必填"(脚本约定是: 不填的位置写 None),
   因此 legacy 解析只标注 optional 标记, 不据此拒绝执行。
"""
import os
import re

_registry = []
_schemas = {}
# 可选依赖/能力声明: name → tuple(capability_id, ...)。语义见文件尾「可选依赖声明」段。
_requires = {}

# ── 参数说明解析 ─────────────────────────────────────────────────────
_NUMERIC_HINTS = ("秒", "次数", "间隔", "精度", "超时", "宽度", "高度", "距离",
                  "置信度", "数量", "上限", "序号", "坐标")
_NUMERIC_EXACT = ("x", "y", "dx", "dy", "左", "上", "宽", "高", "n")
_MARKER_WORDS = ("可选", "必填", "可空", "默认", "无参数", "-")


def _split_params(text):
    """按逗号切分参数说明, 但不切括号内的逗号 (如 `权限(sandbox/trusted/full, 可空)`)。"""
    text = (text or "").strip()
    if not text or text in ("无参数", "无", "-", "None"):
        return []
    parts, buf, depth = [], [], 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if ch in ",，、" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _parse_param(token):
    """单条参数说明 → schema 描述 dict。

    支持写法: `名称` / `名称(默认X)` / `名称(可选)` / `名称(可选,默认X)` /
              `名称(是/否)` / `名称(模式A/模式B)` / `名称..N(可选)` (变长)
    """
    raw = token.strip()
    m = re.match(r"^(?P<name>[^()（）]+?)\s*(?:[（(](?P<meta>.*)[)）])?$", raw)
    name = (m.group("name") if m else raw).strip()
    meta = (m.group("meta") if m else "") or ""

    variadic = bool(re.search(r"\.\.", name))
    name = re.sub(r"\.\.+.*$", "", name).strip()

    optional = ("可选" in meta) or ("默认" in meta)
    default = None
    dm = re.search(r"默认\s*[:=]?\s*([^,，/]+)", meta)
    if dm:
        default = dm.group(1).strip()

    choices = ()
    if "/" in meta:
        cand = [c.strip() for c in re.split(r"[/|]", _strip_markers(meta))]
        cand = [c for c in cand if c and not any(w in c for w in _MARKER_WORDS)]
        choices = tuple(cand)

    kind = "string"
    if name.lower() in _NUMERIC_EXACT or any(h in name for h in _NUMERIC_HINTS):
        kind = "number"

    return {"name": name or raw, "raw": raw, "kind": kind, "choices": choices,
            "default": default, "optional": optional, "variadic": variadic}


def _strip_markers(meta):
    """剔除括号内的纯标记片段 (可选 / 可空 / 必填 / 默认X), 只留枚举与说明。

    必要性: `权限(sandbox/trusted/full, 可空)` 若不先剔除标记, 按 `/` 切出来的
    最后一段会是 `full, 可空` —— 它命中标记词被丢弃, 枚举就少了一个 `full`。
    """
    parts = [x.strip() for x in re.split(r"[,，]", meta or "") if x.strip()]
    keep = [x for x in parts
            if x not in ("可选", "可空", "必填") and not x.startswith("默认")]
    return ", ".join(keep)


def _parse_params(text):
    return [_parse_param(t) for t in _split_params(text)]


def register(name, description, params, handler=None, schema=None, requires=()):
    """Register a command. handler=None means engine provides it later.

    schema:   可选的显式参数声明 (list[dict]); 缺省时由 params 字符串解析而来。
    requires: 该命令运行所需的能力 id 元组 (路线图 阶段二新增项①)。**末位新增**
              且默认空, 旧调用全兼容; 未显式声明时 register 仅记空元组, 由文件尾的
              「可选依赖声明」段统一补齐默认 ``("core",)``。
    """
    _registry.append((name, description, params, handler))
    _schemas[name] = list(schema) if schema else _parse_params(params)
    _requires[name] = tuple(requires or ())


def get_handler(name):
    for n, _, _, h in _registry:
        if n == name: return h
    return None


def list_names():
    return [n for n, _, _, _ in _registry]


def list_all():
    return list(_registry)


def requires(name):
    """→ 该命令声明的能力 id 元组; 未声明返回 ()。"""
    return _requires.get(name, ())


def list_requires():
    """→ {name: tuple(capability_id, ...)} 的快照。"""
    return dict(_requires)


# ── 命令下拉「能力角标」展示辅助 (路线图 阶段二新增项①) ──────────────
# 仅用于 UI 下拉展示: 需要外部能力且当前非 READY 的命令名后追加 " ⚠";
# ("core",) 命令不加。读取选中值时务必先经 strip_display_badge() 清洗。
_DISPLAY_BADGE = " ⚠"


def display_names():
    """命令名列表 + 能力角标; 失败开放回退 list_names()。仅用于下拉展示。"""
    try:
        import capabilities
        out = []
        for n, _d, _p, _h in _registry:
            bad = any(c != "core"
                      and capabilities.state(c) != capabilities.CapState.READY
                      for c in requires(n))
            out.append(n + _DISPLAY_BADGE if bad else n)
        return out
    except Exception:
        return list_names()


def strip_display_badge(name):
    """还原下拉展示值 → 纯命令名。"""
    s = "" if name is None else str(name)
    return s[:-len(_DISPLAY_BADGE)] if s.endswith(_DISPLAY_BADGE) else s


# ── 结构化参数 schema ────────────────────────────────────────────────
def schema(name):
    """命令的参数 schema (list[dict]); 未注册返回空列表。"""
    return [dict(p) for p in _schemas.get(name, [])]


def signature(name):
    """重建人类可读签名, 如 `按键名, 次数=1, 间隔=0.1, 按键(左/右)`。"""
    out = []
    for p in _schemas.get(name, []):
        s = p["name"]
        if p.get("choices"):
            s += "(" + "/".join(p["choices"]) + ")"
        if p.get("default") is not None:
            s += "=" + str(p["default"])
        elif p.get("optional"):
            s += "?"
        if p.get("variadic"):
            s += "…"
        out.append(s)
    return ", ".join(out) if out else "无参数"


def max_args(name):
    """该命令可接受的最大参数个数 (None 表示不限)。

    注意: 变长参数未必在末位 —— 例如 `浏览器上传` 是
    `定位表达式, 文件路径1, 文件路径2..N(可选), 是否清空(可选)`,
    因此只要**任一**参数是变长, 就不做个数上限判定。
    """
    ps = _schemas.get(name, [])
    if any(p.get("variadic") for p in ps):
        return None
    return len(ps)


def _variadic_index(name):
    for i, p in enumerate(_schemas.get(name, [])):
        if p.get("variadic"):
            return i
    return -1


def _clean(v):
    """归一化单元格值: None/空白视为"未填写"。"""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("none", "null", "-", ""):
        return ""
    return s


def validate(name, args):
    """按 schema 校验参数 → (errors, normalized)。

    只做**能确定**的检查: 参数个数超限 / 数字写成了非数字 / 取值不在枚举内。
    未写(空/None)一律放行 —— 脚本约定"不填的位置写 None", 由各命令自行取默认值。
    """
    ps = _schemas.get(name, [])
    if name not in _schemas:
        # 未注册的命令不在这里报错 (由 engine 统一报"未知命令")
        return [], list(args or [])
    args = list(args or [])
    # 去掉尾部连续空值 (Excel 行常带尾随空列), 避免误报"参数过多"
    while args and _clean(args[-1]) == "":
        args.pop()

    errors, normalized = [], []
    limit = max_args(name)
    if limit is not None and len(args) > limit:
        errors.append("参数过多: 最多 {} 个 ({}), 收到 {} 个".format(
            limit, signature(name), len(args)))

    for i, p in enumerate(ps):
        if i >= len(args):
            break
        val = args[i]
        s = _clean(val)
        if s == "":
            normalized.append(val)
            continue
        if p["kind"] == "number":
            try:
                float(s)
            except ValueError:
                errors.append("参数『{}』不是数字: {!r}".format(p["name"], s))
                normalized.append(val)
                continue
        if p.get("choices") and s not in p["choices"]:
            errors.append("参数『{}』取值应为 {} 之一, 收到 {!r}".format(
                p["name"], "/".join(p["choices"]), s))
        normalized.append(val)
    # 按命令名的补充校验 (schema 表达不了的跨参数约束)
    if name == "热键":
        # 组合键至少要 2 个有效按键 (engine._hotkey 运行期同样会报错)
        n_keys = sum(1 for v in args[:4] if _clean(v))
        if n_keys < 2:
            errors.append("至少需要 2 个非空按键参数 (如 ctrl, a), 实收 {} 个".format(
                n_keys))
    # 变长参数: 展开剩余位置 (用该变长项自身的类型/枚举做校验)
    vi = _variadic_index(name)
    if vi >= 0:
        tail = ps[vi]
        for val in args[len(ps):]:
            s = _clean(val)
            if s and tail["kind"] == "number":
                try:
                    float(s)
                except ValueError:
                    errors.append("参数『{}』不是数字: {!r}".format(tail["name"], s))
            normalized.append(val)
    return errors, normalized


def hints(name, args):
    """执行期参数体检 → 人类可读提示列表 (调用方决定是否只记日志)。"""
    errors, _ = validate(name, args)
    return ["参数提示[{}]: {}".format(name, e) for e in errors]


def stats():
    """注册表概览 (供自检/文档使用)。"""
    return {
        "commands": len(_registry),
        "with_schema": sum(1 for n in _schemas if _schemas[n]),
        "no_params": sum(1 for n in _schemas if not _schemas[n]),
        "variadic": sum(1 for n in _schemas
                        if any(p.get("variadic") for p in _schemas[n])),
        "with_choices": sum(1 for n in _schemas
                            if any(p.get("choices") for p in _schemas[n])),
    }


# ── 19 commands — name, description, params ──
register("找图",     "找到目标图并悬停",           "图片名, 精度(默认0.96)")
register("区域找图", "限定范围找图(更快)",          "图片名, 精度, 左, 上, 宽, 高, 灰度")
register("点图",     "找到图片并点击",              "图片名, 精度(默认0.96)")
register("区域点图", "限定范围找图并点击",          "图片名, 精度, 左, 上, 宽, 高, 灰度, 按键, 次数")
register("按键",     "模拟键盘按键",                "按键名, 次数(默认1), 间隔(默认0.1)")
register("热键",     "组合键操作(2~4个按键)",       "按键1, 按键2, 按键3(可选), 按键4(可选)")
register("输入",     "剪贴板方式输入文本",          "内容")
register("写入",     "模拟真实键盘逐字输入（支持DD驱动）", "内容, 按键间隔(秒), 模式(auto/direct/simulate)")
register("等待",     "延时等待(秒)",                "秒数")
register("坐标",     "点击屏幕坐标",                "x, y, 按键(左/右), 次数, 间隔")
register("悬停",     "鼠标移动到坐标",              "x, y")
register("拖拽",     "鼠标拖拽到坐标",              "x, y")
register("滚轮",     "鼠标滚轮滚动",                "距离(负=下, 正=上)")
register("相移",     "相对当前位置移动",            "dx, dy")
register("按下",     "按下按键不松开",              "按键名")
register("释放",     "释放已按下的按键",            "按键名")
register("复制",     "Ctrl+A, Ctrl+C",              "无参数")
register("粘贴",     "Ctrl+A, Ctrl+V",              "无参数")
register("截屏",     "截图并保存",                  "名称, 保存路径")
register("代码",     "执行脚本目录下的txt/Python代码（已受权限与AST预检约束）", "文件名(不含后缀)")
register("Python",   "执行 Python 代码（受权限与 AST 预检约束）", "代码, 权限(sandbox/trusted/full, 可空)")
register("如果",     "条件判断（如果为真则执行）",    "条件表达式")
register("否则",     "否则分支（否则执行）",          "无参数")
register("结束如果", "结束条件块",                   "无参数")
register("循环开始", "开始循环",                     "次数或条件表达式")
register("循环结束", "结束当前循环",                 "无参数")
register("跳出循环", "立即跳出循环",                 "无参数")

# ── Window Management Commands (新增窗口管理命令) ──
register("激活窗口", "激活指定标题的窗口（支持模糊匹配）", "窗口标题")
register("关闭窗口", "关闭指定窗口", "窗口标题")
register("最小化窗口", "最小化指定窗口", "窗口标题")
register("最大化窗口", "最大化指定窗口", "窗口标题")
register("获取窗口位置", "获取窗口坐标和大小并保存到变量", "窗口标题, X变量名, Y变量名, 宽变量名, 高变量名")
register("等待窗口", "等待窗口出现或消失", "窗口标题, 超时秒数, 存在/不存在")
register("窗口坐标", "相对于窗口左上角的坐标点击（窗口移动后仍准确）", "窗口标题, x, y, 按键(左/右), 次数, 间隔")
register("设置变量", "设置变量值", "变量名, 值")
register("读取剪贴板", "读取剪贴板内容到变量", "变量名")
register("字符串处理", "截取/替换字符串", "源变量, 操作类型(截取/替换), 参数, 目标变量")
register("数学运算", "执行数学计算", "表达式, 结果变量")

# ── OCR Commands (OCR文字识别命令) ──
register("识别文字", "OCR识别屏幕区域文字并存入变量", "左, 上, 宽, 高, 变量名")
register("等待文字", "等待指定文字出现或消失", "目标文字, 超时秒数, 存在/不存在, 左, 上, 宽, 高")
register("点击文字", "找到文字位置并点击", "目标文字, 置信度, 按键(左/右), 左, 上, 宽, 高")

# ── AI 增强命令 (AI Enhancement Commands) ──
register("AI找图", "AI分析截屏,用自然语言描述找元素并点击", "描述, 置信度(0-1), 操作(click/hover/find), 按键(左/右)")
register("AI识别界面", "AI分析截屏列出所有UI元素存入变量", "变量名前缀(默认ui)")
register("AI优化建议", "AI分析执行数据给出脚本优化建议", "模式(log/save/var), 变量名")

# ── 工作流命令 (Workflow Commands) ──
register("运行工作流", "调用工作流文件(.json)执行多脚本编排", "工作流路径")
register("工作流变量", "设置工作流级共享变量", "变量名, 值")

# ── 浏览器自动化命令 (Browser Automation Commands, 需 pip install playwright) ──
register("打开网页",   "浏览器打开指定URL",           "网址")
register("浏览器点击", "点击页面元素(CSS选择器或text=)", "选择器")
register("浏览器输入", "在输入框中填入文本",          "选择器, 文本")
register("等待元素",   "等待页面元素出现或消失",      "选择器, 超时秒数, 出现/消失")
register("浏览器截图", "截取页面或元素截图",          "名称, 目标(page或选择器)")

# ── 浏览器后端增强 P0 新增命令 (纯追加，不改现有 5 条) ──
register("浏览器执行JS",   "在页面执行JS并把返回值写入变量",   "脚本, 目标变量名(可选), 是否表达式(是/否)")
register("执行JS",         "在页面执行JS(浏览器执行JS的别名)", "脚本, 目标变量名(可选), 是否表达式(是/否)")
register("浏览器读取Cookie", "导出当前上下文Cookie到变量或文件", "目标变量名(默认cookie), 格式(json/header/netscape), 保存路径(可选)")
register("浏览器设置Cookie", "从变量或文件注入Cookie",         "来源(变量名/文件路径), 域名(可选)")

# ── 浏览器后端增强 P1 新增命令 (纯追加，不改任何既有命令) ──
register("切换框架",   "进入iframe(DSL定位/索引)或回到主文档", "定位表达式/索引/main")
register("返回主框架", "回到顶层frame(等价 切换框架,main)",    "无参数")
register("新建标签页", "新建标签页并切换为活动页",              "网址(可选)")
register("切换标签页", "按序号/标题/URL切换活动标签页",        "序号或标题/URL匹配")
register("关闭标签页", "关闭当前或指定标签页",                 "序号(可选,默认当前页)")
register("等待下载",   "等待浏览器下载完成并保存文件",         "保存目录(可选), 文件名匹配(可选), 超时秒数(可选), 写回变量(可选)")
register("浏览器上传", "对input[type=file]设置本地文件",       "定位表达式, 文件路径1, 文件路径2..N(可选), 是否清空(可选)")

# ── 浏览器后端增强 P2 新增命令 (纯追加，不改任何既有命令) ──
register("连接已开浏览器", "通过CDP endpoint接管已开浏览器(需--remote-debugging-port)", "endpoint(可选,默认配置), 是否关闭旧会话(可选,默认是)")
register("接管浏览器",     "连接已开浏览器 的别名",                                    "endpoint(可选), 是否关闭旧会话(可选)")
register("开始监听",       "监听网络响应入队(抓包),支持URL匹配/资源类型",              "URL匹配(可选), 资源类型(可选), 队列上限(可选)")
register("等待数据包",     "等待并取回命中的数据包,写回变量或落盘",                    "URL匹配(可选), 数量(可选,默认1), 超时秒数(可选), 写回变量(可选), 导出路径(可选), 导出格式(可选,默认json)")
register("停止监听",       "停止监听并清空队列",                                      "无参数")
register("启动浏览器录制", "调用 playwright codegen 并把结果转为DSL追加到录制",       "无参数")

# ══════════════════════════════════════════════════════════════════════
# L2 动作库扩充 —— handler 区 (方案文档 §5 L2 的 20 条 P0 命令)
# ══════════════════════════════════════════════════════════════════════
# 约定 (与既有 handler 保持一致, 便于 `commands` 保持纯逻辑):
#   · 签名 `def _cmd_x(row, script_dir, api)` —— engine 的 _invoke_handler 见到
#     3 参签名时会自动注入 AcrpaAPI (engine.py:114), 于是变量读写走公开 API;
#   · 参数统一用 `_arg(row, i)` 取 (i=0 对应「参数1」), 空/None 归一成 None;
#   · 业务逻辑全部落在 file_ops / sys_ops / net_ops 三个纯逻辑模块, 这里只做
#     「取参 → 转调 → 写变量 → 记日志」的薄封装 (无 GUI 也能单测);
#   · **不在模块顶层 import 这三个模块**, 保持 commands 的零重依赖特性
#     (_test_import_side_effect_free 要求 script_validate → commands 不拉 GUI);
#   · 失败不抛: 记 error 日志并 return False (engine 据此判定失败);
#   · AbortSignal 必须原样上抛, 否则「停止」无法打断等待类命令。
_TRUE_WORDS = ("是", "1", "true", "yes", "y", "on", "√")


def _arg(row, index):
    """取第 index+1 个参数 → str 或 None(未填写)。"""
    try:
        if hasattr(row, "args"):
            value = row.args[index] if index < len(row.args) else None
        else:
            value = row[index + 1].value
    except Exception:
        value = None
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in ("none", "null", "-"):
        return None
    return text


def _is_yes(value, default=False):
    """是/否参数 → bool (空值取 default)。"""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in _TRUE_WORDS


def _num(value, default):
    """宽松数字解析 (失败返回 default, 不抛)。"""
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _log(msg, level="info"):
    """延迟导入 log1 (避免 commands 顶层拉起 utils/tkinter)。"""
    try:
        from utils import log1
        log1(msg, level)
    except Exception:
        pass


def _abort_cls():
    """延迟取 engine.AbortSignal (commands ↔ engine 有既有循环依赖, 只能运行期取)。"""
    try:
        import engine as _engine_mod
        return getattr(_engine_mod, "AbortSignal", None)
    except Exception:
        return None


def _guarded(fn, *args, **kwargs):
    """统一收口: 调 fn 并把 AbortSignal 原样上抛, 其余异常转成失败三元组。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:                 # noqa: BLE001 — 薄封装不抛
        abort = _abort_cls()
        if abort is not None and isinstance(e, abort):
            raise
        return False, "执行失败: {}".format(e), None


def _set_var(api, name, value):
    """写脚本变量: 优先 AcrpaAPI.set_var, 退化到 engine.variables。"""
    key = ("" if name is None else str(name)).strip()
    if not key:
        return False
    try:
        if api is not None and api.set_var(key, value):
            return True
    except Exception:
        pass
    try:
        import engine as _engine_mod
        variables = getattr(getattr(_engine_mod, "engine", None), "variables", None)
        if isinstance(variables, dict):
            variables[key] = value
            return True
    except Exception:
        pass
    return False


def _get_var(api, name):
    """读脚本变量 (取不到返回 None)。"""
    key = ("" if name is None else str(name)).strip()
    if not key:
        return None
    try:
        if api is not None:
            value = api.get_var(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        import engine as _engine_mod
        variables = getattr(getattr(_engine_mod, "engine", None), "variables", None)
        if isinstance(variables, dict):
            return variables.get(key)
    except Exception:
        pass
    return None


def _gui_available():
    """当前进程是否有活的 Tk 主窗 (决定危险操作能否弹二次确认)。"""
    try:
        import tkinter
        return getattr(tkinter, "_default_root", None) is not None
    except Exception:
        return False


def _confirm(msg, title="危险操作确认"):
    """GUI 二次确认 → True/False; 无 GUI 时由调用方传 None (不调用本函数)。"""
    from tkinter import messagebox
    return bool(messagebox.askyesno(title, msg))


def _confirm_cb():
    """→ 可用的确认回调, 或 None (无 GUI)。"""
    return _confirm if _gui_available() else None


def _engine_chk():
    """→ 引擎的停止/暂停闸门 (`engine._chk`), 或 None。"""
    try:
        import engine as _engine_mod
        chk = getattr(getattr(_engine_mod, "engine", None), "_chk", None)
        return chk if callable(chk) else None
    except Exception:
        return None


def _window_finder():
    """→ 引擎既有的窗口查找函数 (复用, 不另起窗口枚举实现), 或 None。"""
    try:
        import engine as _engine_mod
        finder = getattr(getattr(_engine_mod, "engine", None),
                         "_find_window_by_pattern", None)
        return finder if callable(finder) else None
    except Exception:
        return None


def _report(tag, ok, msg, var=None, value=None, api=None):
    """统一「写变量 + 记日志 + 返回成功标志」。"""
    if var and value is not None:
        if not _set_var(api, var, value):
            _log("[{}] 变量 {} 写入失败".format(tag, var), "warning")
    _log("[{}] {}".format(tag, msg), "info" if ok else "error")
    return bool(ok)


# ── 文件 / 目录族 (8 条) ─────────────────────────────────────────────
def _cmd_copy_file(row, script_dir, api):
    """复制文件/目录 → 目标路径写变量。"""
    import file_ops
    src, dst = _arg(row, 0), _arg(row, 1)
    ok, msg, value = _guarded(file_ops.copy_path, src, dst,
                              _is_yes(_arg(row, 2), False), script_dir or "")
    return _report("复制文件", ok, msg, _arg(row, 3), value, api)


def _cmd_move_file(row, script_dir, api):
    """移动(或同目录改名)文件/目录 → 目标路径写变量。"""
    import file_ops
    src, dst = _arg(row, 0), _arg(row, 1)
    ok, msg, value = _guarded(file_ops.move_path, src, dst,
                              _is_yes(_arg(row, 2), False), script_dir or "")
    return _report("移动文件", ok, msg, _arg(row, 3), value, api)


def _cmd_delete_file(row, script_dir, api):
    """删除文件/目录(默认送回收站) → 被删路径写变量。"""
    import file_ops
    ok, msg, value = _guarded(
        file_ops.delete_path,
        _arg(row, 0), _is_yes(_arg(row, 1), False), _is_yes(_arg(row, 2), True),
        script_dir or "", _confirm_cb())
    return _report("删除文件", ok, msg, None, None, api)


def _cmd_make_dir(row, script_dir, api):
    """创建目录 → 目录绝对路径写变量。"""
    import file_ops
    ok, msg, value = _guarded(file_ops.make_dir, _arg(row, 0),
                              _is_yes(_arg(row, 1), True), script_dir or "")
    return _report("创建目录", ok, msg, None, None, api)


def _cmd_list_dir(row, script_dir, api):
    """列出目录 → 换行连接的名称串写变量。"""
    import file_ops
    var = _arg(row, 3) or "dir_list"
    ok, msg, value = _guarded(file_ops.list_dir, _arg(row, 0), _arg(row, 1),
                              _arg(row, 2) or "名", script_dir or "")
    return _report("列出目录", ok, msg, var, value, api)


def _cmd_read_text(row, script_dir, api):
    """读取文本文件 → 内容写变量。"""
    import file_ops
    var = _arg(row, 2) or "file_text"
    ok, msg, value = _guarded(file_ops.read_text, _arg(row, 0),
                              _arg(row, 1) or "auto", script_dir or "")
    return _report("读取文本文件", ok, msg, var, value, api)


def _cmd_write_text(row, script_dir, api):
    """写入文本文件(原子写) → 写入路径写变量。"""
    import file_ops
    ok, msg, value = _guarded(file_ops.write_text, _arg(row, 0), _arg(row, 1),
                              _arg(row, 2) or "覆盖", _arg(row, 3) or "utf-8",
                              script_dir or "")
    return _report("写入文本文件", ok, msg, None, None, api)


def _cmd_path_exists(row, script_dir, api):
    """判断路径是否存在 → "1"/"0" 写变量。"""
    import file_ops
    var = _arg(row, 1) or "file_exists"
    ok, msg, value = _guarded(file_ops.path_exists, _arg(row, 0), script_dir or "")
    return _report("文件是否存在", ok, msg, var, value, api)


# ── 启动程序 / 结束进程 ──────────────────────────────────────────────
def _cmd_start_program(row, script_dir, api):
    """启动程序/文档/URL → PID 或返回码写变量。"""
    import sys_ops
    ok, msg, value = _guarded(
        sys_ops.start_program, _arg(row, 0), _arg(row, 1) or "",
        _arg(row, 2) or "", _is_yes(_arg(row, 3), False),
        _is_yes(_arg(row, 4), False), script_dir or "", _engine_chk())
    return _report("启动程序", ok, msg, "process_id", value, api)


def _cmd_kill_process(row, script_dir, api):
    """按进程名结束进程 (内置白名单硬拒绝)。"""
    import sys_ops
    ok, msg, value = _guarded(
        sys_ops.kill_process, _arg(row, 0), _is_yes(_arg(row, 1), False),
        _is_yes(_arg(row, 2), True), _confirm_cb())
    return _report("结束进程", ok, msg, "killed_count", value, api)


# ── HTTP 请求 / JSON 取值 ────────────────────────────────────────────
def _cmd_http_request(row, script_dir, api):
    """HTTP(S) 请求 → 响应体与状态码写变量。"""
    import file_ops
    import net_ops
    body_var = _arg(row, 5) or "http_body"
    status_var = _arg(row, 6) or "http_status"
    save_path = _arg(row, 7)
    if save_path:
        save_path = file_ops.resolve(save_path, script_dir or "")
        if not save_path:
            _log("[HTTP 请求] 保存路径非法: {}".format(_arg(row, 7)), "error")
            return False
    ok, msg, detail = _guarded(
        net_ops.http_request, _arg(row, 0) or "GET", _arg(row, 1),
        _arg(row, 2) or "", _arg(row, 3) or "",
        _num(_arg(row, 4), net_ops.DEFAULT_TIMEOUT), save_path or "")
    if isinstance(detail, dict):
        _set_var(api, body_var, detail.get("text", ""))
        _set_var(api, status_var, detail.get("status", 0))
    _log("[HTTP 请求] {}".format(msg), "info" if ok else "error")
    return bool(ok)


def _cmd_json_get(row, script_dir, api):
    """按路径从变量/文件/JSON 文本中取值。"""
    import file_ops
    import net_ops
    source = _arg(row, 0)
    path_text = _arg(row, 1) or ""
    default = _arg(row, 2)
    default = "" if default is None else default
    var = _arg(row, 3) or "json_value"

    content = None
    if source:
        value = _get_var(api, source)       # ① 变量名
        if value is not None:
            content = value if isinstance(value, str) else str(value)
    if content is None and source:           # ② 文件路径
        candidate = file_ops.resolve(source, script_dir or "")
        if candidate and os.path.isfile(candidate):
            read_ok, read_msg, content = _guarded(file_ops.read_text, candidate, "auto", "")
            if not read_ok:
                return _report("JSON 取值", False, read_msg, var, default, api)
    if content is None:                      # ③ 原样 JSON 文本
        content = source or ""

    ok, msg, value = _guarded(net_ops.json_get, content, path_text, default)
    return _report("JSON 取值", ok, msg, var, value, api)


# ── 编码工具 (3 条) ──────────────────────────────────────────────────
def _cmd_base64(row, script_dir, api):
    """Base64 编码/解码。"""
    import net_ops
    var = _arg(row, 2) or "b64_result"
    ok, msg, value = _guarded(net_ops.b64, _arg(row, 1) or "", _arg(row, 0) or "编码")
    return _report("Base64 编解码", ok, msg, var, value, api)


def _cmd_url_codec(row, script_dir, api):
    """URL 百分号编码/解码。"""
    import net_ops
    var = _arg(row, 2) or "url_result"
    ok, msg, value = _guarded(net_ops.url_codec, _arg(row, 1) or "",
                              _arg(row, 0) or "编码")
    return _report("URL 编解码", ok, msg, var, value, api)


def _cmd_hash(row, script_dir, api):
    """MD5/SHA1/SHA256 (文本或文件)。"""
    import file_ops
    import net_ops
    var = _arg(row, 3) or "hash_value"
    kind = _arg(row, 1) or "文本"
    source = _arg(row, 2) or ""
    if kind == "文件" and source:
        source = file_ops.resolve(source, script_dir or "") or source
    ok, msg, value = _guarded(net_ops.hash_source, _arg(row, 0) or "sha256",
                              kind, source)
    return _report("哈希值", ok, msg, var, value, api)


# ── 系统面板 / 电源 / 音量 / 磁盘 / 窗口样式 ─────────────────────────
def _cmd_open_panel(row, script_dir, api):
    """打开系统面板 (白名单固定项)。"""
    import sys_ops
    ok, msg, value = _guarded(sys_ops.open_panel, _arg(row, 0))
    return _report("打开系统面板", ok, msg, None, None, api)


def _cmd_power(row, script_dir, api):
    """电源操作 (预演/二次确认)。"""
    import sys_ops
    dry_run = _is_yes(_arg(row, 3), False)
    ok, msg, detail = _guarded(
        sys_ops.power_action, _arg(row, 0), _num(_arg(row, 1), 60),
        _is_yes(_arg(row, 2), False), dry_run,
        None if dry_run else _confirm_cb())
    return _report("电源操作", ok, msg, None, None, api)


def _cmd_volume(row, script_dir, api):
    """音量控制 → 生效音量写变量。"""
    import sys_ops
    ok, msg, value = _guarded(sys_ops.volume, _arg(row, 0) or "设置",
                              _num(_arg(row, 1), 0))
    return _report("音量控制", ok, msg, "volume_level", value, api)


def _cmd_disk_space(row, script_dir, api):
    """磁盘空间 → 总量/可用/使用率写变量。"""
    import sys_ops
    var_total = _arg(row, 1) or "disk_total"
    var_free = _arg(row, 2) or "disk_free"
    var_percent = _arg(row, 3) or "disk_used_percent"
    ok, msg, detail = _guarded(sys_ops.disk_space, _arg(row, 0))
    if ok and isinstance(detail, dict):
        _set_var(api, var_total, detail.get("total", 0))
        _set_var(api, var_free, detail.get("free", 0))
        _set_var(api, var_percent, detail.get("percent", 0.0))
    _log("[磁盘空间] {}".format(msg), "info" if ok else "error")
    return bool(ok)


def _cmd_window_style(row, script_dir, api):
    """窗口置顶/透明度/隐藏显示 (复用引擎窗口查找)。"""
    import sys_ops
    ok, msg, value = _guarded(sys_ops.window_style, _arg(row, 0), _arg(row, 1),
                              _arg(row, 2), _arg(row, 3), _window_finder())
    return _report("设置窗口样式", ok, msg, "window_handle", value, api)


# ── L2 动作库扩充：20 条命令注册 (68 → 88) ───────────────────────────
# 全部**追加在注册表末尾**, 不改动任何既有命令的名称/参数顺序/语义
# (.xls 位置式 9 参数布局依赖既有顺序)。
register("复制文件", "复制文件/目录(相对路径按脚本目录解析)",
         "源路径, 目标路径, 覆盖(是/否), 目标变量名(可选)", _cmd_copy_file)
register("移动文件", "移动文件/目录(同目录即重命名)",
         "源路径, 目标路径, 覆盖(是/否), 目标变量名(可选)", _cmd_move_file)
register("删除文件", "删除文件/目录(默认送回收站, 失败回退永久删除)",
         "路径, 递归(是/否), 送回收站(是/否)", _cmd_delete_file)
register("创建目录", "创建目录(支持多级)",
         "路径, 已存在则忽略(是/否)", _cmd_make_dir)
register("列出目录", "列出目录内容, 结果按行写入变量",
         "路径, 匹配(通配), 排序(名/时间/大小), 目标变量名", _cmd_list_dir)
register("读取文本文件", "读取文本文件内容到变量",
         "路径, 编码(utf-8/gbk/auto), 目标变量名", _cmd_read_text)
register("写入文本文件", "写入文本文件(原子写)",
         "路径, 内容, 模式(覆盖/追加), 编码", _cmd_write_text)
register("文件是否存在", "判断文件/目录是否存在, 结果 1/0 写入变量",
         "路径, 目标变量名", _cmd_path_exists)
register("启动程序", "启动外部程序/文档/URL, 可等待结束",
         "程序路径, 参数, 工作目录, 等待结束(是/否), 隐藏窗口(是/否)", _cmd_start_program)
register("结束进程", "按进程名结束进程(内置系统关键进程白名单)",
         "进程名, 强制(是/否), 保护白名单(是/否)", _cmd_kill_process)
register("HTTP 请求", "发送 HTTP(S) 请求, 响应体/状态码写入变量",
         "方法(GET/POST/PUT/DELETE/HEAD), URL, 请求头, 请求体, 超时秒, 响应变量名, 状态码变量名, 保存路径(可选)",
         _cmd_http_request)
# 注: 「源」的说明刻意不用 "/" 分隔 (如"变量名/文件路径/JSON文本"), 否则
# commands._parse_param 会把它当成**枚举** → script_validate 误报"取值应为…"。
register("JSON 取值", "按路径从变量/文件/JSON 文本中取值",
         "源(变量名或文件路径或JSON文本), 路径表达式, 默认值(可选), 目标变量名", _cmd_json_get)
register("Base64 编解码", "Base64 编码或解码",
         "模式(编码/解码), 源(变量值或文本), 目标变量名", _cmd_base64)
register("URL 编解码", "URL 百分号编码或解码",
         "模式(编码/解码), 源(变量值或文本), 目标变量名", _cmd_url_codec)
register("哈希值", "计算文本或文件的 MD5/SHA1/SHA256",
         "算法(md5/sha1/sha256), 源类型(文本/文件), 源(文本或路径), 目标变量名", _cmd_hash)
register("打开系统面板", "打开系统面板(白名单固定项)",
         "面板(任务管理器/服务/注册表/控制面板/设备管理器/网络连接/电源选项/系统属性/回收站/计算器/记事本)",
         _cmd_open_panel)
register("电源操作", "关机/重启/注销/锁屏/待机/休眠(支持延迟与预演)",
         "动作(关机/重启/注销/锁屏/待机/休眠), 延迟秒, 强制(是/否), 预演(是/否)", _cmd_power)
register("音量控制", "设置/增减音量、切换静音",
         "动作(设置/增加/减少/静音切换/静音开/静音关), 音量(0-100)", _cmd_volume)
register("磁盘空间", "查询磁盘总容量/可用/使用率并写入变量",
         "盘符或路径, 总容量变量名, 可用容量变量名, 使用率变量名", _cmd_disk_space)
register("设置窗口样式", "设置窗口置顶/透明度/隐藏显示",
         "窗口标题, 透明度(0-255,可空), 置顶(是/否,可空), 隐藏(隐藏/显示,可空)",
         _cmd_window_style)

# ── 可选依赖声明 (路线图 阶段二新增项①) ────────────────────────────────
# requires(name) → 该命令运行所需的能力 id 元组:
#   · ("core",) —— 主包内置能力: **永不拦截、永不加角标**;
#   · 其余 id 见 src/capabilities.py (cv.match / browser.playwright /
#     ocr.paddle_dll / input.dd)。
# 默认全部命令声明 ("core",); 需要外部能力的命令由下表覆盖。
_EXTERNAL_REQUIRES = {
    # OpenCV 图像匹配 (找图系列)
    "找图": ("cv.match",), "区域找图": ("cv.match",),
    "点图": ("cv.match",), "区域点图": ("cv.match",),
    # 浏览器自动化 (playwright)
    "打开网页": ("browser.playwright",), "浏览器点击": ("browser.playwright",),
    "浏览器输入": ("browser.playwright",), "等待元素": ("browser.playwright",),
    "浏览器截图": ("browser.playwright",), "浏览器执行JS": ("browser.playwright",),
    "执行JS": ("browser.playwright",), "浏览器读取Cookie": ("browser.playwright",),
    "浏览器设置Cookie": ("browser.playwright",), "切换框架": ("browser.playwright",),
    "返回主框架": ("browser.playwright",), "新建标签页": ("browser.playwright",),
    "切换标签页": ("browser.playwright",), "关闭标签页": ("browser.playwright",),
    "等待下载": ("browser.playwright",), "浏览器上传": ("browser.playwright",),
    "连接已开浏览器": ("browser.playwright",), "接管浏览器": ("browser.playwright",),
    "开始监听": ("browser.playwright",), "等待数据包": ("browser.playwright",),
    "停止监听": ("browser.playwright",), "启动浏览器录制": ("browser.playwright",),
    # PaddleOCR.dll 后端 (可选)
    "识别文字": ("ocr.paddle_dll",), "等待文字": ("ocr.paddle_dll",),
    "点击文字": ("ocr.paddle_dll",),
    # DD 内核输入 (可选, engine 缺失时回退 PyAutoGUI)
    "写入": ("input.dd",),
    # ── L2 动作库扩充 (方案文档 §5 L2 的 requires 列) ──
    "结束进程": ("proc.kill",),
    "HTTP 请求": ("net.http",),
    "电源操作": ("power",),
    "音量控制": ("audio",),
    "设置窗口样式": ("win32",),
}

for _n, _r in _EXTERNAL_REQUIRES.items():
    if _n in _schemas:                      # 仅覆盖已注册命令, 避免笔误写错
        _requires[_n] = tuple(_r)

# 未显式声明的已注册命令统一补 ("core",) (register 缺省只写空元组)。
for _n, _d, _p, _h in list(_registry):
    if not _requires.get(_n):
        _requires[_n] = ("core",)


def _set_handler(name, handler):
    """Update a registered command's handler (called by engine)."""
    global _registry
    for i, (n, d, p, _) in enumerate(_registry):
        if n == name:
            _registry[i] = (n, d, p, handler)
            return


# ══════════════════════════════════════════════════════════════════════
# L3 变量插值 —— 禁插值白名单 (方案文档 §5 L3 / §8.6 安全)
# ══════════════════════════════════════════════════════════════════════
# 为什么需要这份白名单: 插值一旦对**全部**参数生效, 脚本就能用 ${x} 让参数值
# 在运行时才确定 —— 这对数据类参数是好事, 但对下面三类参数是**降级**:
#   ① 变量名类参数: 插值会把「变量名」替换成「变量的值」, 命令直接失效;
#   ② 安全闸门参数: 插值会让 ${x} 动态关掉二次确认/回收站/白名单 → 削弱安全;
#   ③ 求值/解析通道参数 (模式/算法/方法/排序/源类型/动作/面板): 值在运行期漂移
#      会让行为不可审计, 也容易被市场脚本利用。
# 值语义:
#   ()/(0, 1, ...) —— 仅这些 **0 基参数下标** (0 = 该命令的「参数1」) 禁止插值;
#   None           —— **整条命令禁插值**。
# 88 条在册命令**全量登记** (含 6 条块结构命令 —— 它们本来就不经 execute(),
# 仍显式登记为 None 以表明「永不做 {var:} 插值」的设计意图)。
NO_INTERP = {
    # ── 基础输入 / 图像 (参数都是"数据", 全部可插值) ──
    "找图": (), "区域找图": (), "点图": (), "区域点图": (),
    "按键": (), "热键": (), "输入": (), "写入": (2,),      # 写入: 模式(auto/direct/simulate)
    "等待": (), "坐标": (), "悬停": (), "拖拽": (), "滚轮": (), "相移": (),
    "按下": (), "释放": (), "复制": (), "粘贴": (), "截屏": (),
    "代码": (),
    "Python": (0,),          # 「代码」禁插值: 插值结果不得进入 exec 通道

    # ── 块结构 (整条禁插值): 条件/循环参数只走 safe_eval 白名单, 不开新求值通道 ──
    "如果": None, "否则": None, "结束如果": None,
    "循环开始": None, "循环结束": None, "跳出循环": None,

    # ── 窗口与变量族 ──
    "激活窗口": (), "关闭窗口": (), "最小化窗口": (), "最大化窗口": (),
    "获取窗口位置": (1, 2, 3, 4),      # X/Y/宽/高 变量名
    "等待窗口": (),                    # 存在/不存在 属行为开关, 非安全闸门
    "窗口坐标": (),
    "设置变量": (0,),                  # 变量名
    "读取剪贴板": (0,),                # 变量名
    "字符串处理": (0, 3),              # 源变量名 / 目标变量名
    "数学运算": (0, 1),                # 表达式 + 结果变量名 (表达式只走 safe_eval)

    # ── OCR / AI / 工作流 ──
    "识别文字": (4,),                  # 变量名
    "等待文字": (),
    "点击文字": (),
    "AI找图": (),
    "AI识别界面": (0,),                # 变量名前缀
    "AI优化建议": (0, 1),              # 模式(log/save/var) + 变量名
    "运行工作流": (),
    "工作流变量": (0,),                # 变量名

    # ── 浏览器自动化 ──
    "打开网页": (), "浏览器点击": (), "浏览器输入": (),
    "等待元素": (),                    # 出现/消失 属行为开关
    "浏览器截图": (),
    "浏览器执行JS": (0, 1),            # 脚本(代码) + 目标变量名
    "执行JS": (0, 1),                  # 同上 (别名)
    "浏览器读取Cookie": (0,),          # 目标变量名
    "浏览器设置Cookie": (0,),          # 来源可能是变量名
    "切换框架": (), "返回主框架": (), "新建标签页": (),
    "切换标签页": (), "关闭标签页": (),
    "等待下载": (3,),                  # 写回变量
    "浏览器上传": (),
    "连接已开浏览器": (), "接管浏览器": (), "开始监听": (),
    "等待数据包": (3,),                # 写回变量
    "停止监听": (), "启动浏览器录制": (),

    # ── L2 文件 / 目录族 ──
    "复制文件": (2, 3),                # 覆盖 + 目标变量名
    "移动文件": (2, 3),                # 覆盖 + 目标变量名
    "删除文件": (1, 2),                # 递归 + 送回收站 (安全闸门)
    "创建目录": (),
    "列出目录": (2, 3),                # 排序 + 目标变量名
    "读取文本文件": (2,),              # 目标变量名
    "写入文本文件": (2,),              # 模式(覆盖/追加)
    "文件是否存在": (1,),              # 目标变量名

    # ── L2 系统 / 网络族 ──
    "启动程序": (3, 4),                # 等待结束 + 隐藏窗口
    "结束进程": (1, 2),                # 强制 + 保护白名单 (安全闸门)
    "HTTP 请求": (0, 5, 6),            # 方法 + 响应变量名 + 状态码变量名
    "JSON 取值": (3,),                 # 目标变量名
    "Base64 编解码": (0, 2),           # 模式 + 目标变量名
    "URL 编解码": (0, 2),              # 模式 + 目标变量名
    "哈希值": (0, 1, 3),               # 算法 + 源类型 + 目标变量名
    "打开系统面板": (0,),              # 面板 (白名单枚举)
    "电源操作": (0, 2, 3),             # 动作 + 强制 + 预演 (安全闸门)
    "音量控制": (0,),                  # 动作
    "磁盘空间": (1, 2, 3),             # 总容量/可用容量/使用率 变量名
    "设置窗口样式": (2, 3),            # 置顶 + 隐藏
}

# 未登记命令的哨兵 (区分「没登记」与「登记为 None = 整条禁插值」)
_NO_INTERP_UNREGISTERED = object()


def get_no_interp(name):
    """→ ``(整条禁插值?, 禁插值的参数下标 frozenset)``。

    供 ``engine._render_args`` 的单一 choke point 调用。
    索引是 **0 基参数下标** (0 = 该命令的「参数1」, 与 ``_arg(row, 0)`` 同源)。

    失败开放策略: 未登记的命令 (含插件新增命令) 一律返回"全部参数可插值" ——
    最坏结果是某个数据参数被插值, 不会出现"命令突然不可用";
    需要保护的命令请显式登记到 :data:`NO_INTERP`。
    """
    spec = NO_INTERP.get(name, _NO_INTERP_UNREGISTERED)
    if spec is _NO_INTERP_UNREGISTERED:
        return False, frozenset()
    if spec is None:
        return True, frozenset()
    try:
        return False, frozenset(spec)
    except TypeError:
        return False, frozenset()
