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
register("热键",     "组合键操作",                  "按键1, 按键2")
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
