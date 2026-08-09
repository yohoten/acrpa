"""
AI 能力深化模块 — 5 大 AI 增强功能

1. 视觉理解:  截屏 → AI 分析界面元素 → 自动生成定位参数
2. 智能重试:  失败时 AI 分析截图 → 自动调整参数重试
3. 异常检测:  AI 实时监控执行过程，检测异常并处理
4. 流程优化:  分析历史执行数据，自动优化脚本参数
5. 自然语言调试: 用户问"为什么第X行没执行?" → AI 分析日志回答

依赖: ai_client.py (AIClient), state.py (API_KEY, API_MODEL)
"""
import os, sys, time, datetime, json, re, base64, threading
import state
from utils import log1

# ── Lazy imports ──
_pyautogui = None


def _get_pa():
    global _pyautogui
    if _pyautogui is None:
        import pyautogui
        _pyautogui = pyautogui
    return _pyautogui


# ── 工具函数 ──

def _screenshot_to_base64(region=None):
    """截取屏幕并编码为 base64 字符串 (JPEG)"""
    import io
    pa = _get_pa()
    img = pa.screenshot(region=region) if region else pa.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _get_ai_client():
    """延迟创建 AI 客户端（检测 API 配置）"""
    if not state.API_KEY:
        log1("AI 增强功能需要配置 API Key", "warning")
        return None
    try:
        from ai_client import create_client, APIError
        return create_client(state.API_KEY, state.API_MODEL)
    except Exception as e:
        log1("创建 AI 客户端失败: {}".format(e), "error")
        return None


def _call_ai(messages, temperature=0.3, max_tokens=2000, timeout=90):
    """通用 AI 调用封装"""
    client = _get_ai_client()
    if not client:
        return None
    try:
        resp = client.chat_completions(
            model=state.API_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log1("AI 调用失败: {}".format(e), "error")
        return None


def _call_vision(prompt, screenshot_base64=None, temperature=0.3, max_tokens=2000):
    """调用视觉模型（带图片的 AI 请求）"""
    if not state.API_KEY:
        return None

    messages = []
    if screenshot_base64:
        # 构建多模态消息（兼容 OpenAI Vision / Qwen-VL 等）
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,{}".format(screenshot_base64),
                    "detail": "high",
                },
            },
        ]
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})

    try:
        # 统一走 AIClient（复用会话/错误处理/base_url 逻辑，避免两套 HTTP 路径）
        client = _get_ai_client()
        if not client:
            return None
        resp = client.chat_completions(
            model=state.API_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=90,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log1("视觉 AI 调用失败: {}".format(e), "error")
        return None


# ======================================================================
# 1. 视觉理解 — AI 分析界面元素
# ======================================================================

def ai_find_element(description, region=None, confidence=0.8):
    """
    用自然语言描述要找的 UI 元素，AI 分析截图返回坐标。

    Args:
        description: 自然语言描述，如 "登录按钮"、"搜索输入框"、"确认支付按钮，蓝色"
        region: 可选 (left, top, width, height) 限定区域
        confidence: 置信度阈值 (0-1)

    Returns:
        dict 或 None: {"x": int, "y": int, "w": int, "h": int, "label": str, "confidence": float}
    """
    log1("AI 视觉分析: 寻找 '{}' ...".format(description))

    screenshot_b64 = _screenshot_to_base64(region)

    prompt = """你是一个精确的 UI 元素定位器。请分析截图中所有可见的 UI 元素，找到与下面描述最匹配的元素。

用户要找的 UI 元素描述：「{}」

请严格按以下 JSON 格式返回结果（只返回 JSON，不要其他文字）：
{{
  "found": true/false,
  "x": 元素中心X坐标(像素),
  "y": 元素中心Y坐标(像素),
  "w": 元素宽度(像素),
  "h": 元素高度(像素),
  "label": "元素上显示的文本（如果有）",
  "confidence": 0.0-1.0 之间的匹配置信度,
  "reason": "简要说明为什么你认为这是匹配的元素"
}}

如果找不到匹配的元素，返回: {{"found": false, "reason": "未找到的原因"}}

重要提示：
- 坐标是相对于截图左上角的像素坐标
- 如果描述是按钮/输入框/文本等，精确指出它的中心位置
- 如果描述可以看到多个候选，选择最明显的一个""".format(description)

    result_text = _call_vision(prompt, screenshot_b64, temperature=0.2)

    if not result_text:
        return None

    # 解析 JSON 响应
    try:
        # 清理可能的 Markdown 包裹
        cleaned = re.sub(r"```(?:json)?\s*", "", result_text)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        result = json.loads(cleaned)
        if result.get("found") and result.get("confidence", 0) >= confidence:
            log1("AI 视觉定位成功: {} (置信度: {:.2f}, 坐标: {},{})".format(
                description, result["confidence"], result["x"], result["y"]))
            return {
                "x": int(result["x"]),
                "y": int(result["y"]),
                "w": int(result.get("w", 50)),
                "h": int(result.get("h", 20)),
                "label": result.get("label", ""),
                "confidence": result["confidence"],
            }
        else:
            log1("AI 视觉定位: 未找到或置信度过低 '{}' — {}".format(
                description, result.get("reason", "未知")), "warning")
            return None
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log1("AI 视觉响应解析失败: {} — 原始: {}".format(e, result_text[:200]), "error")
        return None


def ai_describe_screen(region=None):
    """
    AI 分析截屏，返回界面上所有可交互元素的列表。
    用于脚本录制辅助和界面分析。

    Returns:
        list[dict]: [{"label": "登录按钮", "type": "button", "x":100, "y":200}, ...]
    """
    log1("AI 分析界面结构...")
    screenshot_b64 = _screenshot_to_base64(region)

    prompt = """分析这个截图，列出所有可见的可交互 UI 元素。返回 JSON 数组：

[
  {
    "type": "button/input/link/text/dropdown/checkbox/menu/tab/icon/other",
    "label": "元素上可见的文字",
    "x": 中心X坐标,
    "y": 中心Y坐标,
    "w": 宽度,
    "h": 高度,
    "description": "元素的功能描述"
  }
]

只返回 JSON 数组，不要其他文字。越精确越好。"""

    result_text = _call_vision(prompt, screenshot_b64, temperature=0.2)
    if not result_text:
        return []

    try:
        cleaned = re.sub(r"```(?:json)?\s*", "", result_text)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        elements = json.loads(cleaned)
        log1("AI 界面分析完成: 发现 {} 个元素".format(len(elements)))
        return elements
    except (json.JSONDecodeError, ValueError) as e:
        log1("AI 界面分析解析失败: {}".format(e), "error")
        return []


# ======================================================================
# 2. 智能重试 — 失败时 AI 分析并调整参数
# ======================================================================

def ai_smart_retry(cmd_type, cmd_args, error_msg, script_dir, attempt, max_attempts):
    """
    执行失败时，AI 分析失败截图，智能调整参数并返回修正建议。

    Args:
        cmd_type: 失败的命令类型
        cmd_args: 原始参数列表
        error_msg: 错误信息
        script_dir: 脚本目录
        attempt: 当前尝试次数
        max_attempts: 最大尝试次数

    Returns:
        dict 或 None: {"action": "retry/modify/skip", "modified_args": [...], "reason": "..."}
    """
    if not state.API_KEY:
        return None

    log1("AI 智能重试分析: {} (尝试 {}/{})".format(cmd_type, attempt, max_attempts))

    # 失败截图
    try:
        fail_img = _screenshot_to_base64()
    except Exception:
        fail_img = None

    # 当前命令类型和所有可用命令参考
    command_ref = """可用命令及参数格式:
找图: 图片名, 置信度(0.8-0.98)
点图: 图片名, 置信度(0.8-0.98)
区域找图: 图片名, 置信度, 左, 上, 宽, 高, 灰度(True/False)
区域点图: 图片名, 置信度, 左, 上, 宽, 高, 灰度, 按键(左/右), 次数
坐标: X, Y, 按键(左/右), 次数, 间隔
等待: 秒数
按键: 键名, 次数, 间隔
热键: 修饰键, 功能键
输入: 文本内容
激活窗口: 窗口标题
等待窗口: 窗口标题, 超时, 存在/不存在
识别文字: 左, 上, 宽, 高, 变量名
点击文字: 目标文字, 置信度, 按键, 左, 上, 宽, 高
设置变量: 变量名, 值
"""

    prompt = """你是一个 RPA 自动化调试专家。一个自动化命令执行失败了，请分析失败截图并给出修正建议。

## 失败的命令
- 命令: {}
- 参数: {}
- 错误: {}

## 已尝试次数
{} / {}

{}

## 请你:
1. 分析截图，判断失败的可能原因（界面没加载完? 元素位置变了? 图片没匹配? 窗口没激活?）
2. 给出具体的修正方案

## 返回 JSON 格式:
{{
  "analysis": "失败原因分析（简短）",
  "action": "retry" | "modify" | "skip" | "wait_and_retry",
  "wait_seconds": 等待秒数（action=wait_and_retry 时必填）,
  "modified_args": [修改后的参数列表，action=modify 时必填],
  "reason": "修改理由",
  "alternative_command": {{"type": "建议的替代命令", "args": [替代参数列表]}}
}}

只返回 JSON，不要其他文字。""".format(cmd_type, cmd_args, error_msg, attempt + 1, max_attempts, command_ref)

    result_text = None
    if fail_img:
        result_text = _call_vision(prompt, fail_img, temperature=0.3)
    else:
        result_text = _call_ai([{"role": "user", "content": prompt}], temperature=0.3)

    if not result_text:
        return None

    try:
        cleaned = re.sub(r"```(?:json)?\s*", "", result_text)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        plan = json.loads(cleaned)
        log1("AI 智能分析: {} → {}".format(plan.get("analysis", ""), plan.get("action", "")))
        return plan
    except json.JSONDecodeError:
        log1("AI 重试分析响应格式错误", "warning")
        return None


# ======================================================================
# 3. 异常检测 — AI 实时监控
# ======================================================================

class AIAnomalyDetector:
    """AI 驱动的执行异常检测器"""

    def __init__(self):
        self._enabled = False
        self._history = []  # 最近 N 条执行记录
        self._check_interval = 10  # 每 N 条命令检测一次
        self._anomaly_count = 0
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return self._enabled and bool(state.API_KEY)

    def enable(self):
        if not state.API_KEY:
            log1("AI 异常检测需要配置 API Key", "warning")
            return False
        self._enabled = True
        self._history.clear()
        self._anomaly_count = 0
        log1("AI 异常检测已启用")
        return True

    def disable(self):
        self._enabled = False
        log1("AI 异常检测已禁用")

    def record(self, cmd_type, elapsed_ms, success, row_num):
        """记录一条执行记录"""
        if not self._enabled:
            return
        with self._lock:
            self._history.append({
                "row": row_num,
                "cmd": cmd_type,
                "elapsed_ms": round(elapsed_ms, 1),
                "success": success,
                "time": time.time(),
            })
            # 只保留最近 200 条
            if len(self._history) > 200:
                self._history = self._history[-200:]

            # 每 N 条触发检测
            if len(self._history) >= self._check_interval and \
               len(self._history) % self._check_interval == 0:
                self._check_anomalies()

    def _check_anomalies(self):
        """后台线程执行 AI 异常检测"""
        recent = self._history[-self._check_interval:]
        failures = [r for r in recent if not r["success"]]
        total_time = sum(r["elapsed_ms"] for r in recent)
        avg_time = total_time / len(recent) if recent else 0

        # 快速规则检测 — 严重问题直接报
        failure_rate = len(failures) / len(recent) if recent else 0
        if failure_rate > 0.5:
            log1("AI 异常检测: 最近{}条命令失败率 {:.0%}，请检查!".format(
                len(recent), failure_rate), "error")

        # AI 深度分析（仅在有异常迹象时调用，节省 token）
        if failure_rate > 0.2 or (avg_time > 5000 and len(recent) >= 5):
            self._ai_deep_analysis(recent, failures, avg_time)

    def _ai_deep_analysis(self, recent, failures, avg_time):
        """AI 深度分析执行异常"""
        summary_lines = ["最近 {} 条命令执行记录:".format(len(recent))]
        for r in recent:
            status = "✗" if not r["success"] else "✓"
            summary_lines.append("  {} 行{} {} {}ms".format(
                status, r["row"], r["cmd"], r["elapsed_ms"]))

        prompt = """你是一个 RPA 自动化运维专家。请分析以下执行日志，判断是否存在异常模式。

## 执行记录
{}

## 统计
- 失败率: {:.0%}
- 平均耗时: {:.0f}ms
- 失败数: {}

## 请判断:
1. 是否存在异常模式（重复失败、性能退化、逻辑错误）？
2. 如果存在异常，可能的原因是什么？
3. 建议用户如何解决？

## 返回 JSON:
{{
  "anomaly_detected": true/false,
  "severity": "low/medium/high/critical",
  "pattern": "异常模式描述",
  "cause": "可能的原因",
  "suggestion": "建议的解决措施",
  "affected_rows": [问题行号列表]
}}

只返回 JSON。""".format("\n".join(summary_lines), len(failures) / len(recent),
                         avg_time, len(failures))

        result = _call_ai([{"role": "user", "content": prompt}], temperature=0.2)
        if not result:
            return

        try:
            cleaned = re.sub(r"```(?:json)?\s*", "", result)
            cleaned = re.sub(r"```\s*$", "", cleaned).strip()
            analysis = json.loads(cleaned)
            if analysis.get("anomaly_detected"):
                self._anomaly_count += 1
                severity = analysis.get("severity", "medium")
                log_level = "error" if severity in ("high", "critical") else "warning"
                log1("AI 异常检测 [{}]: {} — {}".format(
                    severity.upper(),
                    analysis.get("pattern", "未知异常"),
                    analysis.get("suggestion", "请手动检查")
                ), log_level)
        except json.JSONDecodeError:
            pass


# 全局异常检测器实例
anomaly_detector = AIAnomalyDetector()


# ======================================================================
# 4. 流程优化 — 分析历史数据优化脚本
# ======================================================================

def ai_optimize_script(exec_timings, variables_snapshot=None):
    """
    分析执行耗时数据，给出脚本优化建议。

    Args:
        exec_timings: state._exec_timings 字典 {row_num: {cmd, elapsed, success, time}}
        variables_snapshot: 可选，执行后的变量快照

    Returns:
        str: AI 优化建议文本
    """
    if not exec_timings:
        return "没有可用的执行数据"

    if not state.API_KEY:
        return "需要配置 API Key 才能使用 AI 优化功能"

    log1("AI 流程优化分析: {} 条执行记录...".format(len(exec_timings)))

    # 构建执行摘要
    sorted_rows = sorted(exec_timings.items(), key=lambda x: x[0])
    lines = []
    total_time = 0.0
    slowest = []
    failures = []

    for row_num, tdata in sorted_rows:
        elapsed = tdata.get("elapsed", 0)
        cmd = tdata.get("cmd", "?")
        success = tdata.get("success", True)
        total_time += elapsed
        lines.append("行{}: {} {:.2f}s {}".format(
            row_num, cmd, elapsed, "✓" if success else "✗"))
        slowest.append((row_num, cmd, elapsed))
        if not success:
            failures.append((row_num, cmd))

    slowest.sort(key=lambda x: x[2], reverse=True)
    slowest = slowest[:5]

    prompt = """你是 RPA 脚本优化专家。分析以下脚本执行数据，提出优化建议。

## 执行概况
- 总命令数: {}
- 总耗时: {:.1f}s
- 失败命令: {}

## 最耗时的 5 个命令
{}

## 失败的命令
{}

## 完整执行记录
{}

## 优化方向
- 减少等待时间（合并等待、用窗口等待替代固定等待）
- 用区域找图替代全屏找图
- 用窗口管理替代图像识别
- 优化变量计算效率
- 合并相邻同类操作
- 减少不必要的截图

## 请返回具体的优化建议（Markdown 格式），包括:
1. 可立即实施的优化（改动参数即可）
2. 需要重构脚本的优化（改动逻辑）
3. 预估优化后的时间节省

直接输出可读的建议文本，不要 JSON。""".format(
        len(sorted_rows), total_time,
        ", ".join("行{} {}".format(r, c) for r, c in failures) if failures else "无",
        "\n".join("  - 行{}: {} ({:.1f}s)".format(r, c, e) for r, c, e in slowest),
        "\n".join("  - 行{}: {} ✗".format(r, c) for r, c in failures) if failures else "无",
        "\n".join(lines),
    )

    result = _call_ai([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=3000)
    if result:
        log1("AI 流程优化分析完成")
    return result or "AI 分析未返回结果"


# ======================================================================
# 5. 自然语言调试 — AI 分析日志回答用户问题
# ======================================================================

def ai_debug_explain(question, exec_timings=None, log_buffer=None, variables=None,
                     script_rows=None):
    """
    自然语言调试：用户用自然语言提问，AI 分析执行日志回答。

    Args:
        question: 用户问题，如 "为什么第5行没执行?"
        exec_timings: state._exec_timings
        log_buffer: 最近的日志列表 [(msg, tag), ...]
        variables: engine.variables 字典
        script_rows: 当前脚本的 ScriptData 列表

    Returns:
        str: AI 分析结果
    """
    if not state.API_KEY:
        return "需要配置 API Key 才能使用 AI 调试功能"

    log1("AI 自然语言调试: '{}'".format(question))

    # 构建上下文
    context_parts = []

    if exec_timings:
        sorted_rows = sorted(exec_timings.items(), key=lambda x: x[0])
        context_parts.append("## 执行记录")
        for row_num, tdata in sorted_rows:
            status = "✓" if tdata.get("success", True) else "✗"
            context_parts.append("行{}: {} ({:.2f}s) {}".format(
                row_num, tdata.get("cmd", "?"), tdata.get("elapsed", 0), status))

    if script_rows:
        context_parts.append("\n## 当前脚本")
        for i, sd in enumerate(script_rows):
            args_str = ", ".join(str(a) for a in sd.args[:4] if a)
            context_parts.append("行{}: {}({})".format(i + 1, sd.cmd_type, args_str))

    if variables:
        context_parts.append("\n## 变量")
        for k, v in variables.items():
            val_str = str(v)[:100]
            context_parts.append("  {} = {}".format(k, val_str))

    if log_buffer:
        recent_logs = log_buffer[-30:] if len(log_buffer) > 30 else log_buffer
        context_parts.append("\n## 最近日志")
        for msg, tag in recent_logs:
            context_parts.append("  [{}] {}".format(tag or "info", msg[:200]))

    prompt = """你是 ACRPA RPA 调试专家。用户正在调试一个自动化脚本，请根据提供的执行上下文回答用户的问题。

{}

---

## 用户问题
{}

请分析以上数据，用专业但易懂的方式回答用户的问题。如果信息不足，请说明需要哪些额外信息。
直接输出回答文本。""".format("\n".join(context_parts), question)

    result = _call_ai([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=2000)
    if result:
        log1("AI 调试分析完成")
    return result or "AI 分析未返回结果"


# ======================================================================
# ── Engine 集成接口 ──
# ======================================================================

def _get_engine():
    """获取全局 engine 实例（避免循环导入）"""
    try:
        import engine as eng_mod
        return eng_mod.engine
    except Exception:
        return None


def _handler_ai_find(row, script_dir):
    """
    新命令: AI找图 — 用自然语言描述要找的东西，AI 分析截图并点击

    参数:
        params[0]: 自然语言描述，如 "登录按钮"
        params[1]: 置信度(0-1)，默认 0.8
        params[2]: 操作类型: click/hover/find，默认 click
        params[3]: 按键(左/右)，默认左
    """
    eng = _get_engine()
    if not eng:
        return
    eng._chk()

    # 解析参数
    if hasattr(row, 'args'):
        description = row.args[0] if len(row.args) > 0 else ""
        confidence = float(row.args[1]) if len(row.args) > 1 and row.args[1] else 0.8
        action = str(row.args[2]).strip() if len(row.args) > 2 and row.args[2] else "click"
        button = "右" if (len(row.args) > 3 and str(row.args[3]) == "右") else "左"
    else:
        description = str(row[1].value) if row[1].value else ""
        confidence = float(row[2].value) if row[2].value and row[2].value != "None" else 0.8
        action = str(row[3].value).strip() if row[3].value and row[3].value != "None" else "click"
        button = "右" if (row[4].value and str(row[4].value) == "右") else "左"

    if not description:
        log1("AI找图: 未指定查找描述", "error")
        return

    if action not in ("click", "hover", "find"):
        action = "click"

    result = ai_find_element(description, confidence=confidence)
    if result:
        pa = _get_pa()
        x, y = result["x"], result["y"]
        if action == "click":
            pa.click(x, y, button=button)
            log1("AI找图: 点击 '{}' 位置 ({},{})".format(description, x, y))
        elif action == "hover":
            pa.moveTo(x, y)
            log1("AI找图: 悬停 '{}' 位置 ({},{})".format(description, x, y))
        elif action == "find":
            log1("AI找图: 找到 '{}' 位置 ({},{})".format(description, x, y))

        # 将结果存入变量
        eng.variables["ai_x"] = x
        eng.variables["ai_y"] = y
        eng.variables["ai_w"] = result["w"]
        eng.variables["ai_h"] = result["h"]
        eng.variables["ai_label"] = result.get("label", "")
    else:
        log1("AI找图: 未找到 '{}'".format(description), "warning")


def _handler_ai_describe(row, script_dir):
    """
    新命令: AI识别界面 — 分析当前屏幕并将结果存入变量

    参数:
        params[0]: 变量名前缀，默认 "ui"，结果存入 ${prefix}_elements (JSON数组)
    """
    eng = _get_engine()
    if not eng:
        return
    eng._chk()

    if hasattr(row, 'args'):
        prefix = row.args[0] if len(row.args) > 0 and row.args[0] else "ui"
    else:
        prefix = str(row[1].value) if row[1].value and row[1].value != "None" else "ui"

    elements = ai_describe_screen()
    if elements:
        eng.variables["{}_elements".format(prefix)] = json.dumps(elements, ensure_ascii=False)
        eng.variables["{}_count".format(prefix)] = len(elements)
        log1("AI识别界面: 发现 {} 个元素 → 变量 ${}_elements".format(len(elements), prefix))
    else:
        eng.variables["{}_elements".format(prefix)] = "[]"
        eng.variables["{}_count".format(prefix)] = 0
        log1("AI识别界面: 未发现元素", "warning")


def _handler_ai_optimize(row, script_dir):
    """
    新命令: AI优化建议 — 在运行时输出优化建议到日志

    参数:
        params[0]: 操作模式: log(仅日志) / save(保存到文件) / var(存入变量)
        params[1]: 变量名(模式=var时)
    """
    eng = _get_engine()
    if not eng:
        return
    eng._chk()

    if hasattr(row, 'args'):
        mode = str(row.args[0]).strip() if len(row.args) > 0 and row.args[0] else "log"
        var_name = row.args[1] if len(row.args) > 1 and row.args[1] else "optimize_result"
    else:
        mode = str(row[1].value).strip() if row[1].value and row[1].value != "None" else "log"
        var_name = str(row[2].value) if row[2].value and row[2].value != "None" else "optimize_result"

    timings = getattr(state, '_exec_timings', {})
    if not timings:
        log1("AI优化: 没有执行数据可供分析", "warning")
        return

    result = ai_optimize_script(timings, eng.variables)
    if result:
        if mode == "var":
            eng.variables[var_name] = result
            log1("AI优化建议已存入变量 ${}".format(var_name))
        elif mode == "save":
            try:
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                fp = os.path.join(os.path.dirname(state.CONFIG_PATH),
                                  "optimize_{}.md".format(ts))
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(result)
                log1("AI优化建议已保存: {}".format(fp))
            except Exception as e:
                log1("保存优化建议失败: {}".format(e), "error")
        else:
            log1("\n" + "=" * 60)
            log1("AI 流程优化建议:")
            log1("-" * 60)
            for line in result.split("\n"):
                log1(line)
            log1("=" * 60)
