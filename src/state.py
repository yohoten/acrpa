"""Shared state for ACRPA — no tkinter/pyautogui dependencies."""
import os, sys, json, threading, ctypes

# ── Execution state ──
running = False; quit2 = False; quit3 = False; has_script = False
filename = None; script_dir = None
folded = False  # Mini Bar 折叠模式运行时状态
pause_event = threading.Event(); pause_event.set()
exec_state = {"loop":0,"total_loops":0,"row":0,"total_rows":0,"start_time":0,"elapsed":0}

# ── Recording state ──
recording = False; record_stop = False; recorded_actions = []
_closing = False

# ── Config schema: (key, default, type) — add new keys here only ──
_config_schema = [
    ("dark_mode",       False,          bool),
    ("retry_max",       3,              int),
    ("retry_interval",  1.0,            float),
    ("image_timeout",   5.0,            float),
    ("api_key",         "",             str),
    ("api_model",       "deepseek-v4-flash", str),
    ("app_protect",     True,           bool),
    ("check_update",    True,           bool),
    ("use_dd_driver",   False,          bool),
    ("dd_dll_path",     "",             str),
    ("sched_enabled",   False,          bool),
    ("sched_hour",      9,              int),
    ("sched_minute",    0,              int),
    ("sched_repeat_mode", "once",       str),
    ("sched_weekdays",  [False]*7,      list),
    # AI 增强配置
    ("ai_smart_retry",  False,          bool),   # AI 智能重试
    ("ai_anomaly_detect", False,        bool),   # AI 异常检测
    # 计划任务增强 
    ("sched_tasks",     [],             list),   # 多任务列表 [{name,script,period,enabled,...}]
    ("sched_poll_interval", 30,         int),    # 轮询间隔(秒)
    # 系统增强 
    ("auto_start",      False,          bool),   # 开机自启动
    ("minimize_to_tray", False,         bool),   # 关闭时最小化到托盘
    ("mini_bar_enabled", True,          bool),   # 启用折叠 Mini Bar 模式
    # UI 细节 (窗口/Tab 记忆)
    ("last_tab",       0,              int),    # 上次所在 Tab 索引 (重启恢复)
    ("win_geometry",   "",             str),    # 主窗口位置尺寸 (空=默认)
    ("hotkey_run",      "",             str),    # 运行脚本快捷键
    ("hotkey_pause",    "",             str),    # 暂停/恢复快捷键
    ("hotkey_stop",     "",             str),    # 停止脚本快捷键
    ("failsafe",        True,           bool),   # PyAutoGUI fail-safe开关
    # 执行增强 ( AutomationOperation 设计)
    ("max_execution_minutes", 0,        int),    # 最大执行时间(分钟), 0=不限
    ("bound_window_title",    "",       str),    # 全局绑定窗口标题(空=不绑定)
    ("stop_on_error",         True,     bool),   # 脚本出错时立即停止
    # 日志增强 (  + 结构化日志)
    ("log_level",             1,        int),    # 日志级别: 0=DEBUG, 1=INFO, 2=WARNING, 3=ERROR
    ("enable_log_saving",     True,     bool),   # 启用日志文件保存
    ("log_retention_days",    7,        int),    # 日志文件保留天数(超过自动删除)
    # OCR 增强 (PaddleOCR 可选后端)
    ("ocr_preferred_backend", "auto",   str),    # OCR后端偏好: auto/paddle/winrt/tesseract
    ("ocr_paddle_dir",        "",       str),    # PaddleOCR自定义模型目录(空=自动下载)
    # 浏览器自动化 (Playwright 可选后端)
    ("browser_headless",      True,     bool),   # 浏览器无头模式(True=不显示窗口)
    ("browser_slow_mo",       0,        int),    # 浏览器操作慢放(ms, 0=最快)
    # 录制增强
    ("recording_mode",        "absolute", str),  # 录制模式: absolute(绝对坐标) / relative(相对窗口)
    ("recording_stop_hotkey", "Ctrl+Alt+F12", str),  # 停止录制快捷键
    # OCR 增强
    ("ocr_preload",           False,     bool),  # 启动时后台预热 OCR 引擎
    ("ocr_threads",           8,         int),   # OCR CPU 线程数
    # 执行模式
    ("input_mode",            "sendinput", str), # 输入模式: sendinput/dd/sendmessage
    # Mini Bar 定制
    ("mini_bar_width",        430,       int),   # Mini Bar 宽度 (px)
    ("mini_bar_opacity",      80,        int),   # Mini Bar 透明度 (%)
]

# ── Config globals (initialised from schema defaults) ──
for _key, _default, _type in _config_schema:
    globals()[_key.upper()] = _default
del _key, _default, _type


# ═══════════════════════════════════════════════════════════════════════
# Config 类型化访问层 (P3) — 与模块级 globals 双向同步
# 新代码: from state import config; config.retry_max = 5
# 旧代码: state.RETRY_MAX = 5  —— 两者等效，逐步迁移
# ═══════════════════════════════════════════════════════════════════════

def _build_config_class():
    """基于 _config_schema 动态构建 Config 类（property 双向同步 globals）。"""
    attrs = {"__doc__": "类型化配置访问层 — 属性与模块级全局变量双向同步"}
    for _k, _d, _t in _config_schema:
        key = _k

        def _make_props(k):
            def getter(self):
                return globals()[k.upper()]

            def setter(self, v):
                globals()[k.upper()] = v

            return property(getter, setter)

        attrs[key] = _make_props(key)
    return type("Config", (), attrs)


config = _build_config_class()()  # 全局单例


def config_to_dict():
    """导出全部配置为 dict（供 save_config 使用）。"""
    return {key: globals()[key.upper()] for key, _, _ in _config_schema}

# ── Model 层变更通知机制 ──
_change_listeners = []  # [(callback, keys_tuple), ...] — keys_tuple=None 监听所有变更

def on_config_change(callback, keys=None):
    """注册配置变更监听回调。callback(config_dict, changed_keys) 在 save_config() 时被调用。"""
    if (callback, keys) not in _change_listeners:
        _change_listeners.append((callback, keys))

def remove_config_change(callback):
    """移除配置变更监听"""
    global _change_listeners
    _change_listeners = [(cb, k) for cb, k in _change_listeners if cb is not callback]

# ── Scheduler runtime state (config defaults are set via schema above) ──
SCHED_NEXT_RUN = ""
_sched_thread_active = False

# ── Editor state ──
_editor_rows = []
_editor_clipboard = []  # Ctrl+C/V row clipboard
_undo_stack = []        # 撤销栈: [(rows_snapshot, breakpoints_snapshot), ...]
_redo_stack = []        # 重做栈
_MAX_UNDO = 30          # 最大撤销步数
breakpoints = set()     # Breakpoint row indices (1-based, matching exec_state["row"])
highlight_row = 0       # Current executing row (0=none)
_editor_modified = False  # 编辑器是否有未保存修改

# ── UI 状态 (窗口位置/Tab 记忆，由 ACRPA 读写) ──
LAST_TAB = 0             # 上次所在 Tab 索引 (重启恢复)
WIN_GEOMETRY = ""        # 主窗口位置尺寸 (如 "500x625+400+80"，空=默认)

# ── Debugger state ──
debug_mode = False          # 调试模式开关
step_mode = False           # 单步执行模式
variables_watch = {}        # 变量监视字典 {var_name: value}
debug_pause_requested = False  # 用户请求暂停（用于单步）

# Debugger Enhancement: new signal flags
_skip_next_row = False      # 跳过下一行执行 (右键菜单)
_run_to_row = 0             # 运行到指定行 (0=无)
_exec_timings = {}          # 行执行耗时记录 {row_num: {cmd, elapsed, success, time}}

# ── Paths ──
if getattr(sys, "frozen", False):
    CONFIG_PATH = os.path.join(os.path.dirname(sys.executable), "config.json")
else:
    CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "config.json")


# ═══════════════════════════════════════════════════════════════════════
# API Key 安全存储 — Windows Credential Manager (纯 ctypes, 零第三方依赖)
# ═══════════════════════════════════════════════════════════════════════

_CRED_TARGET = "ACRPA/api_key"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


class _CREDENTIALW(ctypes.Structure):
    """Windows CREDENTIALW 结构（仅声明用到的字段）。"""
    _fields_ = [
        ("Flags", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", ctypes.c_longlong),
        ("CredentialBlobSize", ctypes.c_ulong),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", ctypes.c_ulong),
        ("AttributeCount", ctypes.c_ulong),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


_advapi32 = ctypes.windll.advapi32
_advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), ctypes.c_ulong]
_advapi32.CredWriteW.restype = ctypes.c_int
_advapi32.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                ctypes.POINTER(ctypes.c_void_p)]
_advapi32.CredReadW.restype = ctypes.c_int
_advapi32.CredFree.argtypes = [ctypes.c_void_p]


def _cred_write(secret):
    """写入 Windows 凭据库；失败返回 False。"""
    try:
        if not secret:
            return False
        blob = ctypes.create_unicode_buffer(secret)
        cred = _CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = _CRED_TARGET
        cred.UserName = "ACRPA"
        cred.CredentialBlobSize = len(secret) * 2  # UTF-16 字节数
        cred.CredentialBlob = ctypes.cast(blob, ctypes.c_void_p)
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        return bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))
    except Exception:
        return False


def _cred_read():
    """从 Windows 凭据库读取密钥；不存在或失败返回 None。"""
    try:
        pcred = ctypes.c_void_p()
        ok = _advapi32.CredReadW(_CRED_TARGET, _CRED_TYPE_GENERIC, 0,
                                 ctypes.byref(pcred))
        if not ok or not pcred.value:
            return None
        try:
            cred = ctypes.cast(pcred, ctypes.POINTER(_CREDENTIALW)).contents
            size = cred.CredentialBlobSize // 2
            secret = ctypes.string_at(cred.CredentialBlob,
                                      cred.CredentialBlobSize)
            return secret.decode("utf-16-le", errors="ignore")[:size]
        finally:
            _advapi32.CredFree(pcred)
    except Exception:
        return None


def _cred_delete():
    """从 Windows 凭据库删除密钥。"""
    try:
        _advapi32.CredDeleteW(_CRED_TARGET, _CRED_TYPE_GENERIC, 0)
    except Exception:
        pass


def load_config():
    """Load configuration from CONFIG_PATH, falling back to schema defaults.

    API Key 优先从 Windows 凭据库读取；旧版 config.json 中的明文 key
    首次加载时自动迁移到凭据库。
    """
    c = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                c = json.load(f)
    except Exception: pass
    for key, default, _ in _config_schema:
        globals()[key.upper()] = c.get(key, default)
    # API Key 安全迁移: 凭据库优先，旧明文回退并迁移
    if "api_key" in c and c.get("api_key"):
        _cred_write(c["api_key"])
    globals()["API_KEY"] = _cred_read() or ""
    if "api_key" in c and c.get("api_key"):
        _save_config_plain("api_key", "")  # 迁移后立即清空明文


def save_config():
    """Persist current config to CONFIG_PATH and notify listeners.

    API Key 不写入 config.json（明文风险），改存 Windows 凭据库。
    """
    try:
        data = {key: globals()[key.upper()] for key, _, _ in _config_schema}
        api_key = data.pop("api_key", "")
        data["api_key"] = ""
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
        if api_key:
            _cred_write(api_key)
        else:
            _cred_delete()
        # 通知所有变更监听器
        for callback, keys in _change_listeners:
            try:
                if keys is None:
                    callback(data, None)
                else:
                    changed = {k: data.get(k) for k in keys}
                    callback(data, changed)
            except Exception:
                pass
    except Exception:
        pass


def _save_config_plain(key, value):
    """仅更新 config.json 中单个键（不触发监听器，用于密钥迁移后清空明文）。"""
    try:
        c = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                c = json.load(f)
        c[key] = value
        with open(CONFIG_PATH, "w") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
