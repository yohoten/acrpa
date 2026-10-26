# -*- coding: utf-8 -*-
"""单实例互斥 + 二次启动唤起 + 本机 IPC (方案文档 §5 L5 #1 / #6, §8.6)。

三件事，全部纯 ``ctypes`` + 标准库，零后台线程：

1. **互斥**: ``CreateMutexW(None, False, "Global\\ACRPA_SingleInstance_v1")`` →
   ``GetLastError() == ERROR_ALREADY_EXISTS(183)`` 判定「已有一个实例」。
   ``Global\\`` 只在当前用户会话内互斥（本程序的正常场景: 同一登录用户双击两次）;
   若因权限拿不到 Global 命名空间（极少数受限会话），自动降级为 ``Local\\``。
2. **唤起 / 转发**: 第二实例把本次命令行动作 (restore / exec_task / workflow)
   通过 ``WM_COPYDATA`` 发给**第一实例**隐藏的 IPC 窗口，然后以退出码 0 退出。
   第一实例收到后经 ``root.after`` **回流主线程**执行（绝不跨线程碰 tkinter）。
3. **来源校验**（§8.6「单实例 IPC 被伪造」）:
   · 载荷必须 ≤ ``MAX_PAYLOAD`` 字节的 UTF-8 JSON 对象;
   · 必须携带与接收方一致的会话 token（``<数据目录>/.ipc_key``，随首次启动
     生成、权限继承用户目录 ACL，仅当前用户可读）;
   · ``action`` 必须在白名单内（``restore`` / ``exec_task`` / ``workflow``），
     且只接受**结构化参数**（任务 ID / 工作流路径），绝不拼接 shell/exec 字符串
     —— 执行一律走既有的 ``scheduler.run_task_now`` 与 ``workflow`` 解释器。

协议格式（COPYDATASTRUCT）::

    dwData = 0x41435250 ('ACRP' 魔术字, 其余值直接忽略)
    cbData = len(payload_utf8)        # ≤ MAX_PAYLOAD
    lpData -> b'{"v":1,"token":"<hex>","action":"exec_task","args":{...}}'
"""
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import hmac
import json
import os
import threading

# ══════════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════════

MUTEX_NAME = "Global\\ACRPA_SingleInstance_v1"
MUTEX_NAME_LOCAL = "Local\\ACRPA_SingleInstance_v1"

# IPC 窗口标题 —— 第二实例用 FindWindowW(None, 标题) 定位接收方
IPC_WINDOW_TITLE = "ACRPA_IPC_CH_v1"

WM_COPYDATA = 0x004A
GWLP_WNDPROC = -4

COPYDATA_MAGIC = 0x41435250          # 'ACRP'
MAX_PAYLOAD = 64 * 1024              # 64 KB 上限 (§8.6 要求校验 cbData)
PROTOCOL_VERSION = 1

# 动作白名单 —— 只能是这些, 其它一律 2 (拒绝)
ACTION_RESTORE = "restore"
ACTION_EXEC_TASK = "exec_task"
ACTION_WORKFLOW = "workflow"
ACTIONS = (ACTION_RESTORE, ACTION_EXEC_TASK, ACTION_WORKFLOW)

# 发送结果 → 退出码 (0/1/2 与 CLI 既有语义对齐)
RC_OK = 0            # 已投递
RC_NOT_FOUND = 1     # 任务不存在 / 目标不存在
RC_REJECTED = 2      # 载荷非法 (超长 / 非法 JSON / 未知动作 / token 不符)
RC_NO_CHANNEL = 3    # 找不到第一实例的 IPC 窗口 (调用方决定回退策略)

SW_RESTORE = 9


class COPYDATASTRUCT(ctypes.Structure):
    """Windows COPYDATASTRUCT (与 SDK 布局一致)。"""
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", wintypes.DWORD),
        ("lpData", ctypes.c_void_p),
    ]


_WNDPROC_TYPE = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,          # LRESULT
    ctypes.c_void_p,            # HWND
    wintypes.UINT,              # UINT msg
    ctypes.c_ulonglong,         # WPARAM
    ctypes.c_longlong,          # LPARAM
)

# 模块级状态
_MUTEX_HANDLE = None
_MUTEX_ACQUIRED = False
_TOKEN = ""
_RECV_WIN = None
_RECV_HWND = None
_RECV_WNDPROC = None
_RECV_ORIG_WNDPROC = None
_INBOX = []                 # 已通过校验、待主线程执行的动作
_INBOX_LOCK = threading.Lock()
_PENDING = []               # 首实例启动后要补执行的动作 (--exec-task 无实例时)


# ══════════════════════════════════════════════════════════════════════
# token (会话密钥)
# ══════════════════════════════════════════════════════════════════════

def token_path():
    """→ token 文件路径 (<数据目录>/.ipc_key)。"""
    try:
        import state
        return os.path.join(state.DATA_ROOT, ".ipc_key")
    except Exception:
        return os.path.join(os.getcwd(), ".ipc_key")


def get_token(create=True):
    """读取（必要时生成）本机会话 token；失败返回空串。

    安全性说明: 文件位于当前用户的数据目录 (U 盘便携模式则在程序目录),
    默认 ACL 只允许当前用户与管理员读取；伪造 IPC 需要先能读到这个文件，
    而这等价于已经能读写用户的配置与任务库，不再是「IPC 引入的新面」。
    """
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    path = token_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                value = (f.read() or "").strip()
            if len(value) >= 16:
                _TOKEN = value
                return _TOKEN
        if not create:
            return ""
        value = hashlib.sha256(
            ("{}|{}|{}".format(os.getpid(), os.path.abspath(path), _now())
             ).encode("utf-8")).hexdigest()
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(value)
        _TOKEN = value
        return _TOKEN
    except Exception:
        return ""


def _now():
    import time
    return time.time()


def _token_ok(given):
    """常量时间比较 token（空 token 一律拒绝）。"""
    expect = get_token(create=False)
    if not expect or not given:
        return False
    try:
        return hmac.compare_digest(str(given), str(expect))
    except Exception:
        return str(given) == str(expect)


# ══════════════════════════════════════════════════════════════════════
# 互斥
# ══════════════════════════════════════════════════════════════════════

def acquire(name=None):
    """尝试成为**第一实例** → True 表示抢到锁；False 表示已有实例在跑。

    幂等: 同一进程重复调用返回首次结果（不重复创建句柄）。
    """
    global _MUTEX_HANDLE, _MUTEX_ACQUIRED
    if _MUTEX_ACQUIRED:
        return True
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.SetLastError(0)
    except Exception:
        pass
    for candidate in ([name] if name else [MUTEX_NAME, MUTEX_NAME_LOCAL]):
        try:
            handle = kernel32.CreateMutexW(None, False, candidate)
        except Exception:
            handle = None
        if not handle:
            continue
        err = 0
        try:
            err = int(kernel32.GetLastError() or 0)
        except Exception:
            err = 0
        if err == 183:                       # ERROR_ALREADY_EXISTS
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            # Global 命名空间被别人占了 → 直接判定「已有实例」
            return False
        _MUTEX_HANDLE = handle
        _MUTEX_ACQUIRED = True
        return True
    # 连互斥体都建不出来（极端受限环境）→ 不让程序无法启动
    return False


def is_primary():
    """本进程是否持有单实例互斥体。"""
    return bool(_MUTEX_ACQUIRED)


def release():
    """释放互斥体（测试/退出用；进程退出后内核自动释放）。"""
    global _MUTEX_HANDLE, _MUTEX_ACQUIRED
    if _MUTEX_HANDLE:
        try:
            ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
        except Exception:
            pass
    _MUTEX_HANDLE = None
    _MUTEX_ACQUIRED = False


def _probe_mutex(name):
    """仅探测「名字是否已存在」（不持有锁），供单测复现二次获取判定。"""
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, name)
        err = int(kernel32.GetLastError() or 0)
        if handle:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
        return err == 183
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════
# 载荷构造 / 校验 (纯函数, 可单测)
# ══════════════════════════════════════════════════════════════════════

def build_payload(action, args=None, token=None):
    """→ UTF-8 JSON bytes。``action`` 非法时抛 ValueError（调用方自行兜底）。"""
    if action not in ACTIONS:
        raise ValueError("未知动作: {}".format(action))
    body = {
        "v": PROTOCOL_VERSION,
        "token": token if token is not None else get_token(),
        "action": action,
        "args": dict(args or {}),
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def validate_payload(raw, token=None, max_bytes=None):
    """校验载荷 → ``(rc, action, args)``。

    rc: ``RC_OK`` / ``RC_REJECTED`` / ``RC_NOT_FOUND``（exec_task 任务不存在）。

    只做**结构性**校验: 不执行、不导入 tkinter、不碰引擎。超长 / 非法 JSON /
    未知动作 / token 不符一律 ``RC_REJECTED``。
    """
    limit = MAX_PAYLOAD if max_bytes is None else int(max_bytes)
    if raw is None:
        return RC_REJECTED, "", {}
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    if not isinstance(raw, (bytes, bytearray)):
        return RC_REJECTED, "", {}
    if len(raw) == 0 or len(raw) > limit:
        return RC_REJECTED, "", {}
    try:
        data = json.loads(bytes(raw).decode("utf-8", "strict"))
    except Exception:
        return RC_REJECTED, "", {}
    if not isinstance(data, dict):
        return RC_REJECTED, "", {}
    action = str(data.get("action") or "")
    if action not in ACTIONS:
        return RC_REJECTED, "", {}
    if token is None:
        if not _token_ok(data.get("token")):
            return RC_REJECTED, "", {}
    elif str(data.get("token") or "") != str(token):
        return RC_REJECTED, "", {}
    args = data.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return RC_REJECTED, action, {}
    clean = {}
    if action == ACTION_EXEC_TASK:
        task_id = str(args.get("task_id") or "").strip()
        if not task_id or len(task_id) > 128:
            return RC_REJECTED, action, {}
        clean["task_id"] = task_id
        if not task_exists(task_id):
            return RC_NOT_FOUND, action, clean
    elif action == ACTION_WORKFLOW:
        path = str(args.get("path") or "").strip()
        if not path or len(path) > 1024 or "\n" in path or "\0" in path:
            return RC_REJECTED, action, {}
        clean["path"] = path
    return RC_OK, action, clean


def task_exists(task_id):
    """任务 ID 是否存在于当前任务库（只读一次 state.SCHED_TASKS）。"""
    try:
        import state
        for task in (getattr(state, "SCHED_TASKS", None) or []):
            try:
                if str(task.get("id") or "") == str(task_id):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════════════════════════════
# 发送端
# ══════════════════════════════════════════════════════════════════════

def find_receiver():
    """→ 第一实例 IPC 窗口句柄 (0 = 找不到)。"""
    try:
        return int(ctypes.windll.user32.FindWindowW(None, IPC_WINDOW_TITLE) or 0)
    except Exception:
        return 0


def send_payload(payload, hwnd=None, timeout_ms=3000):
    """把载荷发给第一实例 → ``(rc, detail)``。

    rc 取 ``RC_OK`` / ``RC_NOT_FOUND`` / ``RC_REJECTED`` / ``RC_NO_CHANNEL``。
    返回值直接映射退出码 (见模块头注释)。
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not payload or len(payload) > MAX_PAYLOAD:
        return RC_REJECTED, "载荷非法 (空或超过 {} 字节)".format(MAX_PAYLOAD)
    target = int(hwnd or 0) or find_receiver()
    if not target:
        return RC_NO_CHANNEL, "未找到正在运行的 ACRPA 实例"
    try:
        buf = ctypes.create_string_buffer(bytes(payload), len(payload))
        cds = COPYDATASTRUCT()
        cds.dwData = ctypes.c_void_p(COPYDATA_MAGIC)
        cds.cbData = len(payload)
        cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
        user32 = ctypes.windll.user32
        # 第 7 个参数是 PDWORD_PTR (x64 为 8 字节)，必须用 c_size_t 承载
        out = ctypes.c_size_t(0)
        user32.SendMessageTimeoutW.restype = ctypes.c_size_t
        user32.SendMessageTimeoutW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_void_p,
            ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
        ok = user32.SendMessageTimeoutW(
            ctypes.c_void_p(target), WM_COPYDATA, 0,
            ctypes.byref(cds), 0x0002,      # SMTO_ABORTIFHUNG
            int(timeout_ms), ctypes.byref(out))
        if not ok:
            return RC_NO_CHANNEL, "第一实例未响应 (超时或窗口已销毁)"
        code = int(out.value or 0)
        if code in (RC_OK, RC_NOT_FOUND, RC_REJECTED):
            return code, ""
        return RC_NO_CHANNEL, "第一实例返回未知结果 {}".format(code)
    except Exception as e:
        return RC_NO_CHANNEL, "发送失败: {}: {}".format(type(e).__name__, e)


def notify_restore():
    """请第一实例把主窗前置；返回 (rc, detail)。"""
    try:
        payload = build_payload(ACTION_RESTORE)
    except Exception as e:
        return RC_REJECTED, str(e)
    return send_payload(payload)


# ══════════════════════════════════════════════════════════════════════
# 接收端 (第一实例)
# ══════════════════════════════════════════════════════════════════════

def _set_api_signatures():
    """固定 64 位签名，避免 HVND/指针被截断成 int32。"""
    user32 = ctypes.windll.user32
    try:
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_void_p]
        user32.CallWindowProcW.restype = ctypes.c_longlong
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_uint, ctypes.c_ulonglong,
                                           ctypes.c_longlong]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
    except Exception:
        pass


def handle_copydata(lparam, hwnd=None):
    """在窗口过程里调用：解析 + 校验 + 入队 → 返回 rc (同步给发送方)。

    **只做**「拷贝字节 + 校验 + 入队 + root.after」，耗时的执行全部回到主线程；
    因此不会在 SendMessage 期间长时间阻塞发送方。
    """
    try:
        cds = ctypes.cast(int(lparam), ctypes.POINTER(COPYDATASTRUCT)).contents
    except Exception:
        return RC_REJECTED
    try:
        if cds.dwData and int(cds.dwData) != COPYDATA_MAGIC:
            return RC_REJECTED
        size = int(cds.cbData or 0)
        if size <= 0 or size > MAX_PAYLOAD or not cds.lpData:
            return RC_REJECTED
        raw = ctypes.string_at(cds.lpData, size)
    except Exception:
        return RC_REJECTED
    rc, action, args = validate_payload(raw)
    if rc != RC_OK:
        return rc
    with _INBOX_LOCK:
        _INBOX.append((action, args))
    _schedule_drain()
    return RC_OK


def _schedule_drain():
    """把「消费收件箱」排到主线程 (root.after)。"""
    root = _recv_root()
    if root is None:
        return
    try:
        root.after(0, drain_inbox)
    except Exception:
        pass


_ROOT_REF = None


def _recv_root():
    if _ROOT_REF is not None:
        return _ROOT_REF
    try:
        import tkinter
        return getattr(tkinter, "_default_root", None)
    except Exception:
        return None


def install_receiver(root):
    """创建隐藏 IPC 窗口并子类化其窗口过程（幂等）。返回 hwnd 或 0。

    必须由**主线程**调用一次（app.build() 内）。失败不影响启动。
    """
    global _RECV_WIN, _RECV_HWND, _RECV_WNDPROC, _RECV_ORIG_WNDPROC, _ROOT_REF
    if _RECV_HWND:
        return _RECV_HWND
    try:
        import tkinter
    except Exception:
        return 0
    _ROOT_REF = root
    _set_api_signatures()
    try:
        win = tkinter.Toplevel(root)
        win.withdraw()
        win.title(IPC_WINDOW_TITLE)
        win.update_idletasks()
        tk_id = win.winfo_id()
        hwnd = ctypes.windll.user32.GetParent(ctypes.c_void_p(tk_id)) or tk_id
    except Exception:
        return 0

    @_WNDPROC_TYPE
    def _wnd_proc(h, msg, wparam, lparam):
        if msg == WM_COPYDATA:
            try:
                return ctypes.c_longlong(handle_copydata(lparam, h))
            except Exception:
                return ctypes.c_longlong(RC_REJECTED)
        return ctypes.windll.user32.CallWindowProcW(
            _RECV_ORIG_WNDPROC, h, msg, wparam, lparam)

    try:
        _RECV_WNDPROC = _wnd_proc
        _RECV_ORIG_WNDPROC = ctypes.windll.user32.SetWindowLongPtrW(
            ctypes.c_void_p(int(hwnd)), GWLP_WNDPROC, _wnd_proc)
        _RECV_WIN = win
        _RECV_HWND = int(hwnd)
        return _RECV_HWND
    except Exception:
        try:
            win.destroy()
        except Exception:
            pass
        return 0


def receiver_hwnd():
    return _RECV_HWND


def uninstall_receiver():
    """恢复原窗口过程并销毁隐藏窗口（退出路径 / 测试收尾）。"""
    global _RECV_WIN, _RECV_HWND, _RECV_WNDPROC, _RECV_ORIG_WNDPROC
    if _RECV_HWND and _RECV_ORIG_WNDPROC:
        try:
            ctypes.windll.user32.SetWindowLongPtrW(
                ctypes.c_void_p(int(_RECV_HWND)), GWLP_WNDPROC, _RECV_ORIG_WNDPROC)
        except Exception:
            pass
        _RECV_ORIG_WNDPROC = None
    if _RECV_WIN is not None:
        try:
            _RECV_WIN.withdraw()
            _RECV_WIN.destroy()
        except Exception:
            pass
    _RECV_WIN = None
    _RECV_HWND = 0
    _RECV_WNDPROC = None


def drain_inbox():
    """在主线程消费收件箱（由 root.after 调用）。"""
    with _INBOX_LOCK:
        items = list(_INBOX)
        _INBOX.clear()
    for action, args in items:
        try:
            handle_payload(action, args)
        except Exception:
            try:
                import crashlog
                crashlog.log_once("ipc.handle", "IPC 动作执行失败: {}".format(action),
                                  "error")
            except Exception:
                pass


def _main_run_fn():
    """→ 宿主执行脚本入口 (ACRPA.main_run)，取不到返回 None。"""
    try:
        import ACRPA
        fn = getattr(ACRPA, "main_run", None)
        return fn if callable(fn) else None
    except Exception:
        return None


def restore_main_window(root=None):
    """把主窗前置（复用 ACRPA._restore_from_tray，它已处理 Mini Bar 折叠态）。"""
    root = root or _recv_root()
    try:
        import ACRPA
        fn = getattr(ACRPA, "_restore_from_tray", None)
        if callable(fn):
            fn()
    except Exception:
        pass
    try:
        if root is not None:
            root.deiconify()
            root.lift()
            try:
                root.focus_force()
            except Exception:
                pass
            hwnd = ctypes.windll.user32.GetParent(
                ctypes.c_void_p(int(root.winfo_id()))) or root.winfo_id()
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(int(hwnd)), SW_RESTORE)
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(int(hwnd)))
    except Exception:
        pass


def _run_workflow_async(path):
    """无头执行一次工作流（与 scheduler._run_workflow 同一条执行入口）。"""
    def _work():
        try:
            import workflow as wf_mod
            import state as st
            wf_def = wf_mod.parse_workflow(path)
            st.quit2 = False
            st.quit3 = True
            wf_mod.workflow_engine.run_workflow(wf_def, os.path.dirname(path))
        except Exception as e:
            try:
                import crashlog
                crashlog.log_once("ipc.workflow", "工作流执行失败: {}".format(e), "error")
            except Exception:
                pass
    threading.Thread(target=_work, name="acrpa-ipc-workflow", daemon=True).start()


def handle_payload(action, args, root=None):
    """主线程执行一个已校验的动作。返回 True 表示已投递。"""
    root = root or _recv_root()
    if action == ACTION_RESTORE:
        restore_main_window(root)
        return True
    if action == ACTION_EXEC_TASK:
        task_id = str((args or {}).get("task_id") or "")
        try:
            import scheduler
            fn = _main_run_fn()
            ok = scheduler.run_task_now(task_id, fn) if fn else scheduler.run_task_now(task_id)
            if not ok:
                try:
                    import utils
                    utils.log1("IPC 触发任务失败 (任务不存在或未接线): {}".format(task_id),
                               "warning")
                except Exception:
                    pass
            return bool(ok)
        except Exception as e:
            try:
                import crashlog
                crashlog.log_once("ipc.exec_task", "IPC 触发任务异常: {}".format(e), "error")
            except Exception:
                pass
            return False
    if action == ACTION_WORKFLOW:
        path = str((args or {}).get("path") or "")
        if not os.path.isfile(path):
            return False
        _run_workflow_async(path)
        return True
    return False


# ══════════════════════════════════════════════════════════════════════
# 首实例「启动后补执行」(--exec-task / --workflow 且当时无实例)
# ══════════════════════════════════════════════════════════════════════

def set_pending(action, args=None):
    """登记「GUI 起来后立刻执行一次」的动作（进程内，不落盘）。"""
    if action not in ACTIONS:
        return False
    _PENDING.append((action, dict(args or {})))
    return True


def dispatch_pending(root=None, delay_ms=600):
    """把已登记的动作排到主线程执行（app.build() 之后调用一次）。"""
    if not _PENDING:
        return 0
    root = root or _recv_root()
    count = len(_PENDING)
    if root is None:
        # 没有 Tk 可回流 → 直接在当前线程执行（CLI 场景本就单线程）
        while _PENDING:
            action, args = _PENDING.pop(0)
            handle_payload(action, args, root=None)
        return count
    for action, args in list(_PENDING):
        root.after(int(delay_ms), lambda a=action, g=args: handle_payload(a, g, root))
    _PENDING.clear()
    return count
