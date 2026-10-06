# -*- coding: utf-8 -*-
"""i18n — UI 字符串目录 (路线图 §5.6「i18n 字符串目录」第一刀)。

本模块只做一件事: 给 UI 的**承重串**(状态文案 / 状态栏文案 / 执行控制文案)
建一份可翻译的目录, 并提供 ``t(key, **fmt)`` 取用。它是纯粹的**表现层**目录。

⚠  文本只是显示, 不得再作为状态 / 主题判定输入。
    —— 状态一律读 ``state.*`` (如 state.running / state.pause_event);
       主题一律读控件的**语义角色** (``ui.theme`` role)。
       任何「读控件文本再按中文词猜颜色 / 猜状态」的写法都是本目录要消除的对象
       (路线图 §5.6 第 2 点, 与 §5.2 去嗅探呼应)。
       把文案搬进本目录, 正是为了让「文案」与「状态」彻底分家: 改文案不再
       影响任何判定, 换语言也不再动状态机。

默认语言 zh, 且 zh 文案与抽取前**逐字一致** —— 本刀只搬家, 不改字 (否则会打破
既有 UI 断言)。

零副作用: ``import i18n`` 不 import tkinter、不建窗、不读屏幕、不写文件。

对外接口 (签名稳定, 供后续扩展)
--------------------------------
    LANGUAGES = ("zh", "en")            # 支持的语言
    DEFAULT_LANGUAGE = "zh"             # 默认 / 真源语言
    CATALOG                             # {lang: {key: template}} —— zh 为真源
    t(key, **fmt) -> str                # 查当前语言 → 回退 zh → 再回退返回 key
    set_language(lang) -> None          # 未知语言回退 DEFAULT_LANGUAGE (不抛)
    get_language() -> str
    keys() -> set                       # zh 目录全部键

key 命名: 点分命名空间, 如 ``status.ready`` / ``status.running`` /
``status.paused`` / ``status.stopped`` / ``status.rows`` / ``status.elapsed``。

插值: ``str.format(**fmt)``; 缺参时**不抛异常**, 回退为未插值模板。
"""

# ══════════════════════════════════════════════════════════════════════
# 语言集与默认值 (zh 为真源)
# ══════════════════════════════════════════════════════════════════════
LANGUAGES = ("zh", "en")
DEFAULT_LANGUAGE = "zh"


# ══════════════════════════════════════════════════════════════════════
# 目录: {lang: {key: template}}
#   · zh —— 真源, 与抽取前源码字面量逐字一致 (含首尾空格 / 全角标点 / '|')
#   · en —— 至少覆盖全部 key (英文直译; 占位符与 zh 一一对应)
# ══════════════════════════════════════════════════════════════════════
CATALOG = {
    "zh": {
        # ── 状态栏主串 (status_text) ──
        "status.ready": " 就绪{mod}{ai}  |  {rows} 行  |  请选择脚本文件开始",
        "status.running": " 运行中 — 正在执行自动化任务",
        "status.paused": " 已暂停 — 点击继续恢复执行",
        "status.paused_resume": " 已暂停 — 点击「继续」恢复执行",
        "status.stopped": " 已停止 — 点击开始运行重新启动",
        "status.recording": " ● 录制中 · {n} 个动作 · Ctrl+Shift+Q 停止",
        "status.update_available": " 新版本 v{ver} 可用 — 点击更新",
        "status.ready_initial": "就绪 — 请选择脚本文件开始",
        # ── 状态圆点 (status_dot) ──
        "status.dot_ready": "● 就绪",
        "status.dot_running": "● 运行中",
        "status.dot_paused": "● 已暂停",
        "status.dot_stopped": "● 已停止",
        "status.dot_recording": "● 录制",
        "status.dot_update": " 更新",
        "status.loaded_dot": "● 已加载",
        # ── 执行仪表盘片段 (循环 / 行 / 已用 / ETA / 分隔 / 计数) ──
        "status.dash_empty": "尚未运行 · 选择脚本后点击运行",
        "status.loop": "循环 {cur}/{total}",
        "status.rows": "行 {done}/{total}",
        "status.elapsed": "已用 {elapsed}",
        "status.eta": "ETA {eta}",
        "status.sep": "  ·  ",
        "status.counts": "  ·  成功 {ok}  失败 {fail}",
        # ── 执行控制: 四键 (注: 「⏸ 暂停」被 _test_netlink_phase2_e2e.py 断言, 未抽取) ──
        "exec.btn_validate": "✓ 校验",
        "exec.btn_run": "▶ 运行",
        "exec.btn_resume": "▶ 继续",
        "exec.btn_step": "⏭ 单步",
        "exec.btn_stop": "■ 停止",
        "exec.btn_debug": "⚑ 调试",
        "exec.btn_vars": "☰ 变量",
        "exec.loaded_text": " 脚本已加载 — 点击「开始运行」启动",
        # ── 执行参数条: 标签 / 值 ──
        "exec.label_script": "脚本:",
        "exec.label_choose": "选择脚本",
        "exec.no_file": "没有选择文件",
        "exec.label_loops": "次数:",
        "exec.loop_infinite": "无限循环",
        "exec.loop_custom": "自定义…",
        "exec.label_maxmin": "最长:",
        "exec.label_minute": "分",
        "exec.stop_on_error": "出错即停",
        "exec.dlg_loops_title": "运行次数",
        "exec.dlg_loops_prompt": "请输入运行次数:",
        "exec.tip_validate": "运行前校验 (静态检查脚本, 不执行)",
    },
    "en": {
        # ── Status bar (status_text) ──
        "status.ready": " Ready{mod}{ai}  |  {rows} rows  |  Choose a script file to start",
        "status.running": " Running — executing automation task",
        "status.paused": " Paused — click Resume to continue",
        "status.paused_resume": " Paused — click \"Resume\" to continue",
        "status.stopped": " Stopped — click Run to start again",
        "status.recording": " ● Recording · {n} actions · Ctrl+Shift+Q to stop",
        "status.update_available": " New version v{ver} available — click to update",
        "status.ready_initial": "Ready — choose a script file to start",
        # ── Status dot (status_dot) ──
        "status.dot_ready": "● Ready",
        "status.dot_running": "● Running",
        "status.dot_paused": "● Paused",
        "status.dot_stopped": "● Stopped",
        "status.dot_recording": "● Recording",
        "status.dot_update": " Update",
        "status.loaded_dot": "● Loaded",
        # ── Execution dashboard fragments ──
        "status.dash_empty": "Not running · choose a script then click Run",
        "status.loop": "Loop {cur}/{total}",
        "status.rows": "Row {done}/{total}",
        "status.elapsed": "Elapsed {elapsed}",
        "status.eta": "ETA {eta}",
        "status.sep": "  ·  ",
        "status.counts": "  ·  OK {ok}  Failed {fail}",
        # ── Execution control: four buttons ──
        "exec.btn_validate": "✓ Validate",
        "exec.btn_run": "▶ Run",
        "exec.btn_resume": "▶ Resume",
        "exec.btn_step": "⏭ Step",
        "exec.btn_stop": "■ Stop",
        "exec.btn_debug": "⚑ Debug",
        "exec.btn_vars": "☰ Variables",
        "exec.loaded_text": " Script loaded — click \"Run\" to start",
        # ── Execution parameter bar: labels / values ──
        "exec.label_script": "Script:",
        "exec.label_choose": "Choose script",
        "exec.no_file": "No file selected",
        "exec.label_loops": "Loops:",
        "exec.loop_infinite": "Infinite",
        "exec.loop_custom": "Custom…",
        "exec.label_maxmin": "Max:",
        "exec.label_minute": "min",
        "exec.stop_on_error": "Stop on error",
        "exec.dlg_loops_title": "Run count",
        "exec.dlg_loops_prompt": "Enter run count:",
        "exec.tip_validate": "Pre-run validation (static check, no execution)",
    },
}

# ── 当前语言 (模块级; 默认 zh) ──
_lang = DEFAULT_LANGUAGE


def get_language():
    """返回当前语言代码。"""
    return _lang


def set_language(lang):
    """设置当前语言; 未知语言回退 ``DEFAULT_LANGUAGE`` (不抛异常)。"""
    global _lang
    _lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def keys():
    """返回 zh 目录 (真源) 的全部键集合。"""
    return set(CATALOG.get(DEFAULT_LANGUAGE, {}).keys())


def t(key, **fmt):
    """取当前语言的文案。

    解析顺序: 当前语言 → 回退 ``zh`` → 仍无则原样返回 ``key``。
    有 ``fmt`` 时用 ``str.format(**fmt)`` 插值; 缺参 (KeyError/IndexError/ValueError)
    时 **不抛**, 回退为未插值模板。
    """
    tmpl = (CATALOG.get(_lang) or {}).get(key)
    if tmpl is None:
        tmpl = CATALOG.get(DEFAULT_LANGUAGE, {}).get(key)
    if tmpl is None:
        return key
    if not fmt:
        return tmpl
    try:
        return tmpl.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return tmpl
