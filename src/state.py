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
    # DD 内核驱动的 DLL 哈希固定（可选）：非空时加载前强制校验，不符即拒绝加载。
    # 留空 = 只做"安装目录内 + PE 头 + 体积"静态预检（见 dd_backend._preflight）。
    ("dd_dll_sha256",   "",             str),
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
    ("win_geometry",   "",             str),    # 设置窗口位置尺寸 (空=默认)
    ("main_geometry",  "",             str),    # 主窗口几何 (空=默认居中; 仿 win_geometry)
    ("main_maximized", False,          bool),   # 主窗口最大化状态 (启动恢复)
    ("compact_mode",   False,          bool),   # 紧凑模式: True=500x625 旧布局
    # ── 片段库 (离线可用) ──
    ("snippet_dir",        "",    str),   # 片段库主目录 (空=<app_root>/template)
    ("snippet_extra_dirs", [],    list),  # 片段库追加目录列表
    ("snippet_last_cat",   "全部", str),  # 片段库 UI 记忆: 上次分类
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
    # OCR 原生 DLL 后端 (lib/paddle_ocr/PaddleOCR.dll) — 默认关闭 = 行为不变
    ("paddle_dll_enabled",    False,    bool),   # 启用 PaddleOCR.dll 原生后端
    ("paddle_dll_dir",        "",       str),    # 含 PaddleOCR.dll 的目录(空=lib/paddle_ocr)
    ("paddle_dll_model_dir",  "",       str),    # 模型目录(需含 inference.json/pdiparams)
    ("paddle_dll_proto_init", "json5",  str),    # 初始化原型 Initializejson(det,cls,rec,keys,json)
    ("paddle_dll_proto_detect", "ptr_byte", str),  # 识别原型 DetectByte(enginePtr,字节流,长度)
    ("paddle_dll_config",     "",       str),    # 参数 JSON 覆盖(字符串, 可空)
    ("paddle_dll_license",    "",       str),    # libaddLicense 许可证串(可空)
    # 浏览器自动化 (Playwright 可选后端)
    ("browser_headless",      True,     bool),   # 浏览器无头模式(True=不显示窗口)
    ("browser_slow_mo",       0,        int),    # 浏览器操作慢放(ms, 0=最快)
    # 浏览器后端增强 P0 (默认值不改变现有行为)
    ("browser_wait_timeout",       15.0,  float),  # 通用等待超时(秒)
    ("browser_poll_interval",      0.2,   float),  # waiter 轮询间隔(秒)
    ("browser_full_page_screenshot", False, bool), # 截图缺省整页开关(默认与现状一致=False)
    ("browser_js_timeout",         15.0,  float),  # 执行 JS 超时(秒)
    ("browser_retry",              1,     int),    # facade 内部瞬态重试次数
    ("browser_retry_interval",     0.5,   float),  # 内部重试间隔(秒)
    ("browser_silent",             False, bool),   # 静默模式(元素缺失/超时不判失败)
    # 浏览器后端增强 P1 (默认值不改变现有行为)
    ("browser_download_dir",       "",    str),    # 下载目录(空=CONFIG_PATH 同级 downloads)
    ("browser_download_timeout",   15.0,  float),  # 等待下载缺省超时(秒)
    ("browser_download_overwrite", True,  bool),   # 同名下载文件是否覆盖
    ("browser_screenshot_dir",     "",    str),    # 截图目录覆盖(空=沿用 screenshots)
    # 浏览器后端增强 P2 (默认值不改变现有行为)
    ("browser_cdp_endpoint",           "",   str),   # CDP 接管地址(空=须由命令参数给出)
    ("browser_listen_max",             200,  int),   # 监听队列上限(超出丢弃最旧)
    ("browser_listen_default_timeout", 15.0, float), # 等待数据包缺省超时(秒)
    ("browser_user_agent",             "",   str),   # 自定义UA(预留)
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
    # ── NetLink 多设备互联 ──
    ("netlink_enabled",     False,          bool),   # 启用设备互联
    ("netlink_port",        19710,          int),    # TCP 监听端口
    ("netlink_device_name", "",             str),    # 本机显示名(空=自动取主机名)
    ("netlink_perm_level",  "observe",      str),    # 权限: observe/control/script
    ("netlink_peers",       [],             list),   # 已配对设备白名单
    ("netlink_static_peers",[],             list),   # 手动填写的 IP:PORT(发现失败时兜底)
    ("netlink_autodiscover",True,           bool),   # UDP 自动发现开关
    ("netlink_discovery_port",19711,        int),    # UDP 发现端口
    ("netlink_tls",         False,          bool),   # TLS 可选档(Phase4)
    ("netlink_ui_window",   "",             str),    # 设备互联窗口几何记忆
    ("netlink_require_auth", True,          bool),   # 是否启用配对认证(默认开启)
    ("netlink_pin_ttl",      600,           int),    # 配对码有效期(秒)
    ("netlink_confirm_control", True,       bool),   # 首次远程操控需被控端弹窗确认
    ("netlink_confirmed_peers", [],         list),   # 已确认过操控的设备指纹(记住此设备)
    ("netlink_script_dir",      "",         str),    # 允许远程运行的脚本目录(空=程序目录/scripts)
    ("netlink_audit_days",      0,          int),    # 审计日志保留天数(0=跟随 LOG_RETENTION_DAYS)
    ("netlink_web_enabled", False,        bool),   # 启用浏览器只读监控面板
    ("netlink_web_port",    19712,        int),    # 面板监听端口
    ("netlink_web_bind",    "0.0.0.0",    str),    # 监听地址(0.0.0.0=允许同网段访问)
    # ── 网页面板「有限控制」(Phase4-2 演进，默认全关 = 行为完全不变) ──
    # ⚠ 控制 PIN 明文/哈希不在此列：只进 Windows 凭据库
    #   (target=ACRPA/netlink/web-control-pin)，绝不写入 config.json。
    ("netlink_web_control", False,        bool),   # 启用面板有限控制(run/stop)
    ("netlink_web_control_ttl", 300,      int),    # 控制会话 TTL(秒)
    ("netlink_web_tls",     False,        bool),   # 面板 HTTPS(控制开启时强制)
    ("netlink_web_confirm_control", False, bool),  # 本地面板控制是否也需桌面弹窗确认
    ("netlink_web_allow_remote_control", False, bool),  # 控制开启且 bind=0.0.0.0 时是否允许局域网控制(默认收窄 127.0.0.1)
    ("netlink_tls_cert",  "",  str),   # TLS 服务端证书 PEM 路径(启用 TLS 时必填)
    ("netlink_tls_key",   "",  str),   # TLS 服务端私钥 PEM 路径(启用 TLS 时必填)
    ("netlink_tls_pins",  [],  list),  # 已固定的对端证书指纹(sha256 hex，TOFU)
    # 默认 30 → 28: Mini Bar 内容已改为铺满整高 (去掉 outer/inner 各 1px 上下留白),
    # 28px 即可容纳 ACRPA_BUTTON(9pt) 文本 + 1px 描边, 视觉更紧凑。
    ("mini_bar_height", 28,  int),   # Mini Bar 高度 24-48（步进 4）
    ("mini_bar_pos",    "",  str),   # Mini Bar 最近位置 "x+y"
    # ── AI 提供商/模型自定义 ──
    ("ai_provider",         "",  str),   # 提供商 id（空=按模型/URL 自动推断）
    ("ai_base_url",         "",  str),   # 自定义 BaseURL（空=用预设）
    ("ai_model",            "",  str),   # 覆盖模型名（空=用 api_model）
    ("ai_custom_providers", [],  list),  # 自定义提供商 [{id,name,base_url,models:[...]}]
    # ── Python 扩展 ──
    ("python_default_perm", "sandbox", str),   # 默认权限 sandbox/trusted/full
    ("python_full_enabled", False,     bool),  # 是否允许 full 权限（默认关闭）
    ("python_timeout",      30,        int),   # 单段代码超时(秒)
    # 过渡开关（§1.2）：True = 「代码」命令回退旧的受限内建 exec 路径（兼容存量
    # 脚本，文档公告一个版本周期后移除）；False（默认）= 与 Python 命令统一走
    # py_sandbox 沙箱内核（AST 预检 / 超时 / 审计 / AcrpaAPI / print 转发）。
    ("legacy_code_command", False,     bool),
    ("ui_scale", 1.0, float),   # 界面缩放 0.8-1.5
    # 脚本编辑区「Excel 表格」独立字号增量 (Ctrl+滚轮 / Ctrl+± / Ctrl+0 复位)。
    # 与 ui_scale 分开: 用户常想让表格比其它界面更密或更大, 且它是唯一的高频阅读区。
    ("editor_zoom", 0, int),
    # ── 脚本市场 v2 (全部非敏感) ──
    # ⚠ token 不在此列：只进 Windows 凭据库 (复用 cred_write/cred_read/cred_delete)，
    #   与 api_key 同款安全策略，绝不写入 config.json、绝不写日志。
    ("market_provider",          "gitee",   str),  # 上次登录/上传所用 provider
    ("market_username",          "",        str),  # 展示用登录名(非密钥)
    ("market_auto_check_update", True,      bool), # 打开市场时后台查更新
    ("market_install_dir",       "",        str),  # 自定义安装根目录(空=CONFIG_PATH 同级)
    ("market_last_category",     "全部",    str),  # UI 记忆: 上次分类筛选
    ("market_index_cache_ttl",   3600,      int),  # 索引缓存秒数(覆盖 CACHE_TTL 常量)
    # ── 帮助系统渲染层 (阶段1-2) ──
    ("help_geometry",           "",     str),   # 帮助窗口几何记忆 (空=默认居中)
    ("help_maximized",          False,  bool),  # 帮助窗口最大化状态 (启动恢复)
    ("help_modal",              False,  bool),  # 帮助窗口是否模态 (默认非模态)
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
WIN_GEOMETRY = ""        # 设置窗口位置尺寸 (如 "680x680+450+60"，空=默认)
MAIN_GEOMETRY = ""       # 主窗口几何 (如 "1000x680+100+50"，空=默认居中)
MAIN_MAXIMIZED = False   # 主窗口是否最大化 (启动恢复)
COMPACT_MODE = False     # 紧凑模式 (500x625 旧布局, 照顾小屏/便携)
# 帮助窗口渲染层 (阶段1-2) — 与 _config_schema 同名大写别名
HELP_GEOMETRY = ""       # 帮助窗口几何 (如 "900x640+200+120"，空=默认居中)
HELP_MAXIMIZED = False   # 帮助窗口是否最大化 (启动恢复)
HELP_MODAL = False       # 帮助窗口是否模态 (False=非模态, 可边看边操作)

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
_advapi32.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
_advapi32.CredDeleteW.restype = ctypes.c_int


def cred_write(target, secret):
    """写入 Windows 凭据库（指定 target）；失败返回 False，不抛。

    NetLink 配对 token 等敏感数据一律走此函数（绝不落 config.json）。
    """
    try:
        if not secret or not target:
            return False
        blob = ctypes.create_unicode_buffer(secret)
        cred = _CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.UserName = "ACRPA"
        cred.CredentialBlobSize = len(secret) * 2  # UTF-16 字节数
        cred.CredentialBlob = ctypes.cast(blob, ctypes.c_void_p)
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        return bool(_advapi32.CredWriteW(ctypes.byref(cred), 0))
    except Exception:
        return False


def cred_read(target):
    """从 Windows 凭据库读取（指定 target）；不存在或失败返回 None，不抛。"""
    try:
        pcred = ctypes.c_void_p()
        ok = _advapi32.CredReadW(target, _CRED_TYPE_GENERIC, 0,
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


def cred_delete(target):
    """从 Windows 凭据库删除（指定 target）；不抛。"""
    try:
        _advapi32.CredDeleteW(target, _CRED_TYPE_GENERIC, 0)
    except Exception:
        pass


# ── 原 API Key 专用薄封装（行为与输出保持不变）──
def _cred_write(secret):
    """写入 API Key 到 Windows 凭据库；失败返回 False。"""
    return cred_write(_CRED_TARGET, secret)


def _cred_read():
    """从 Windows 凭据库读取 API Key；不存在或失败返回 None。"""
    return cred_read(_CRED_TARGET)


def _cred_delete():
    """从 Windows 凭据库删除 API Key。"""
    cred_delete(_CRED_TARGET)


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
