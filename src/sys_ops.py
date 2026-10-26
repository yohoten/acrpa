# -*- coding: utf-8 -*-
"""sys_ops — 系统交互类动作的纯逻辑实现（L2 动作库扩充：启动程序 / 结束进程 /
系统面板 / 电源 / 音量 / 磁盘 / 窗口样式）。

设计约束（务必保持）
--------------------
* **纯 stdlib + ctypes**: 不引入 psutil / pycaw / comtypes；音量优先走
  ``IAudioEndpointVolume``（手写 COM vtable，见 ``_open_endpoint_volume``），
  失败回退 ``keybd_event`` 音量键（精度受限，见 ``volume()`` 文档）；
* **import 期零副作用**: ``ctypes.windll`` / ``os.startfile`` 只在函数体内访问，
  因此可被无头环境安全 import；
* **不抛异常**: 统一返回 ``(ok, message, value)``，消息为中文可读；
* **安全底线**:
    - ``结束进程`` 内置**硬白名单**（explorer/winlogon/csrss/lsass/services/
      svchost/System 等系统关键进程）不可绕过，并拒绝结束本程序自身；
    - ``电源操作`` 必须经 ``confirm`` 二次确认，支持 ``dry_run`` 预演；
    - ``打开系统面板`` 只查白名单表，绝不接受任意命令串。

对外接口
--------
    start_program(program, args, workdir, wait, hidden, script_dir, chk)
    list_processes()
    kill_process(name, force, protect, confirm)
    open_panel(panel)
    power_action(action, delay, force, dry_run, confirm)
    get_volume() / set_volume(v) / change_volume(d) / volume(action, value)
    disk_space(drive)
    find_window(title, finder) / window_style(title, opacity, topmost, visibility, finder)
"""
import os
import re
import subprocess
import sys
import time

# ── 常量 ─────────────────────────────────────────────────────────────
CREATE_NO_WINDOW = 0x08000000
PROCESS_TERMINATE = 0x0001
TH32CS_SNAPPROCESS = 0x00000002

#: 音量键虚拟码
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002

#: 一次音量键步进大约改变 2%（Windows 默认步进）
_VOLUME_STEP = 2

#: **硬白名单**：系统关键进程，任何情况下都拒绝结束（对齐 zTasker chkProcSafe）
HARD_PROTECT = frozenset({
    "explorer.exe", "winlogon.exe", "csrss.exe", "lsass.exe", "services.exe",
    "svchost.exe", "smss.exe", "wininit.exe", "spoolsv.exe",
    "system", "system idle process", "registry", "memory compression",
})

#: **软白名单**：属于系统体验进程，``保护白名单=是``（默认）时同样拒绝
SOFT_PROTECT = frozenset({
    "dwm.exe", "sihost.exe", "taskhostw.exe", "runtimebroker.exe",
    "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "textinputhost.exe", "ctfmon.exe", "audiodg.exe",
})

#: 系统面板白名单（只有表内项，不接受任意命令）→ (启动方式, 目标)
PANELS = {
    "任务管理器": ("exe", "taskmgr.exe"),
    "服务": ("open", "services.msc"),
    "注册表": ("open", "regedit.exe"),
    "控制面板": ("open", "control.exe"),
    "设备管理器": ("open", "devmgmt.msc"),
    "网络连接": ("open", "ncpa.cpl"),
    "电源选项": ("open", "powercfg.cpl"),
    "系统属性": ("open", "sysdm.cpl"),
    "回收站": ("open", "shell:RecycleBinFolder"),
    "计算器": ("exe", "calc.exe"),
    "记事本": ("exe", "notepad.exe"),
}

#: 支持的电源动作
POWER_ACTIONS = ("关机", "重启", "注销", "锁屏", "待机", "休眠")

#: 参数切分：支持 "a b" 与 "a \"b c\"" 两种写法（不经过 shell）
_ARG_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')


def _clean(value):
    """归一化入参 → 去空白字符串；``None`` / ``empty`` / ``none`` 一律视为空。"""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "null", "-"):
        return ""
    return text


def _to_int(value, default=0):
    """宽松整数解析（失败返回 default，不抛）。"""
    try:
        return int(float(_clean(value) or default))
    except Exception:
        return default


def _to_float(value, default=0.0):
    try:
        return float(_clean(value) or default)
    except Exception:
        return default


def _flag(value, default=False):
    """是/否 → bool；空值返回 default。"""
    text = _clean(value).lower()
    if not text:
        return default
    return text in ("是", "1", "true", "yes", "y", "on", "√")


def _is_windows():
    return os.name == "nt"


# ══════════════════════════════════════════════════════════════════════
# 启动程序
# ══════════════════════════════════════════════════════════════════════
def _split_args(text):
    """把参数串切成列表（去引号，不经过 shell，天然免疫命令注入）。"""
    raw = _clean(text)
    if not raw:
        return []
    out = []
    for token in _ARG_RE.findall(raw):
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        if token:
            out.append(token)
    return out


def _looks_like_shell_target(program):
    """URL / 文档 → 交给 ``os.startfile``（由系统关联程序打开）。"""
    low = program.lower()
    if low.startswith(("http://", "https://", "file://", "mailto:", "shell:")):
        return True
    ext = os.path.splitext(low)[1]
    return ext not in ("", ".exe", ".com", ".bat", ".cmd")


def start_program(program, args="", workdir="", wait=False, hidden=False,
                  script_dir="", chk=None):
    """启动外部程序/文档/URL → (ok, msg, pid 或 returncode)。

    * **禁止 shell=True**：命令以列表形式传给 ``Popen``，因此参数里的
      ``&`` / ``|`` / ``$(...)`` 都只是普通字符，不构成命令注入面；
    * ``wait=True`` 时轮询等待结束，轮询体每次都调用 ``chk``（引擎的停止/暂停
      闸门），于是长等待可被「停止」立刻打断（``chk`` 抛 ``AbortSignal``）；
    * URL / 文档（无参数、非可执行扩展名）走 ``os.startfile``，立即返回。
    """
    prog = _clean(program)
    if not prog:
        return False, "程序路径为空：请先填写要启动的程序/文档/URL", ""
    if not os.path.isabs(prog) and _clean(script_dir) and not _looks_like_shell_target(prog):
        candidate = os.path.join(_clean(script_dir), prog)
        if os.path.exists(candidate):
            prog = candidate

    # 文档 / URL：交给系统关联程序打开，无需 Popen
    if _looks_like_shell_target(prog) and not _clean(args):
        try:
            os.startfile(prog)             # noqa: S606 — 仅打开用户显式给出的目标
        except Exception as e:             # noqa: BLE001
            return False, "打开失败: {}".format(e), ""
        if wait:
            return True, "已请求系统打开: {}（此类目标无法等待退出）".format(prog), ""
        return True, "已请求系统打开: {}".format(prog), ""

    if not os.path.exists(prog):
        return False, "找不到可执行文件: {}".format(prog), ""

    cmd = [prog] + _split_args(args)
    cwd = _clean(workdir) or None
    if cwd and _clean(script_dir) and not os.path.isabs(cwd):
        cwd = os.path.join(_clean(script_dir), cwd)
    if cwd and not os.path.isdir(cwd):
        return False, "工作目录不存在: {}".format(cwd), ""

    popen_kw = {}
    if _is_windows():
        popen_kw["creationflags"] = CREATE_NO_WINDOW if hidden else 0
        if hidden:
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0          # SW_HIDE
                popen_kw["startupinfo"] = si
            except Exception:
                pass
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, close_fds=True, **popen_kw)
    except Exception as e:                 # noqa: BLE001
        return False, "启动失败: {}".format(e), ""

    if not wait:
        return True, "已启动 {} (PID {})".format(os.path.basename(prog), proc.pid), proc.pid

    # 等待结束：每次轮询都过一遍中断闸门
    while True:
        if callable(chk):
            chk()                          # 停止 → 抛 AbortSignal，向上传播
        code = proc.poll()
        if code is not None:
            break
        time.sleep(0.2)
    return True, "已结束: {} (返回码 {})".format(os.path.basename(prog), code), code


# ══════════════════════════════════════════════════════════════════════
# 进程：枚举 / 结束（纯 ctypes，无 psutil）
# ══════════════════════════════════════════════════════════════════════
class _PROCESSENTRY32W(object):
    """仅在 Windows 下实例化；用工厂函数延迟构造 ctypes.Structure。"""

    _cls = None

    @classmethod
    def build(cls):
        if cls._cls is not None:
            return cls._cls
        import ctypes
        from ctypes import wintypes

        class _Entry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        cls._cls = _Entry
        return _Entry


def list_processes():
    """→ [(pid, exe 名)]；非 Windows / 失败返回空列表（绝不抛）。"""
    if not _is_windows():
        return []
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        try:
            handle_value = int(snapshot) if snapshot is not None else 0
        except Exception:
            handle_value = 0
        if not snapshot or handle_value in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            return []
        entry_cls = _PROCESSENTRY32W.build()
        entry = entry_cls()
        entry.dwSize = ctypes.sizeof(entry_cls)
        out = []
        try:
            ok = k32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
            guard = 0
            while ok and guard < 20000:
                guard += 1
                name = entry.szExeFile
                if name:
                    out.append((int(entry.th32ProcessID), str(name)))
                ok = k32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        finally:
            k32.CloseHandle(ctypes.c_void_p(snapshot))
        return out
    except Exception:                      # noqa: BLE001
        return []


def _self_names():
    """本程序自身的可执行名（小写集合），用于「拒绝结束自身」。"""
    names = {"acrpa.exe"}
    try:
        exe = os.path.basename(sys.executable or "").lower()
        if exe:
            names.add(exe)
    except Exception:
        pass
    return names


def _normalize_exe(name):
    text = _clean(name).lower()
    if not text:
        return ""
    return text if "." in text else text + ".exe"


def kill_process(name, force=False, protect=True, confirm=None):
    """按进程名结束进程 → (ok, msg, 结束的进程数)。

    安全规则（不可绕过）：
      ① ``HARD_PROTECT`` 内的系统关键进程**一律拒绝**，与 ``protect`` 取值无关；
      ② 拒绝结束本程序自身（按 PID 与可执行名双重判定）；
      ③ ``protect=True``（默认）时 ``SOFT_PROTECT`` 也拒绝；
      ④ 非白名单进程默认需要二次确认；``force=True`` 表示用户在脚本里显式
         声明了强制结束，可跳过确认；无确认界面时（无 GUI）由 ④ 决定是否失败。
    """
    target = _normalize_exe(name)
    if not target:
        return False, "进程名为空：请先填写要结束的进程名（如 notepad.exe）", 0

    if target in HARD_PROTECT:
        return False, ("拒绝执行：'{}' 属系统关键进程（内置白名单，不可绕过）"
                       .format(target)), 0
    if protect and target in SOFT_PROTECT:
        return False, ("拒绝执行：'{}' 属系统体验进程（保护白名单已开启；"
                       "如确需结束请把『保护白名单』设为 否）".format(target)), 0
    if target in _self_names():
        return False, "拒绝执行：不能结束本程序自身 ({})".format(target), 0

    procs = [p for p in list_processes() if p[1].lower() == target]
    own_pid = os.getpid()
    procs = [p for p in procs if p[0] != own_pid]
    if not procs:
        return False, "未找到进程: {}".format(target), 0

    if not force:
        if not callable(confirm):
            return (False, "结束进程需二次确认：当前无确认界面，"
                           "如确需执行请把『强制』设为 是", 0)
        try:
            approved = bool(confirm("即将结束 {} 个进程：{}\n是否继续？".format(
                len(procs), target)))
        except Exception as e:             # noqa: BLE001
            return False, "二次确认失败: {}".format(e), 0
        if not approved:
            return False, "用户取消结束进程", 0

    if not _is_windows():
        return False, "非 Windows 平台不支持按进程名结束进程", 0
    killed = 0
    errors = []
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        for pid, _nm in procs:
            handle = k32.OpenProcess(PROCESS_TERMINATE, 0, pid)
            if not handle:
                errors.append("PID {} 打开失败(错误码 {})".format(
                    pid, int(k32.GetLastError())))
                continue
            try:
                if k32.TerminateProcess(ctypes.c_void_p(handle), 1):
                    killed += 1
                else:
                    errors.append("PID {} 结束失败(错误码 {})".format(
                        pid, int(k32.GetLastError())))
            finally:
                k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception as e:                 # noqa: BLE001
        return False, "结束进程失败: {}".format(e), killed

    if killed:
        msg = "已结束 {} 个 {} 进程".format(killed, target)
        if errors:
            msg += "（部分失败: {}）".format("; ".join(errors))
        return True, msg, killed
    return False, "结束进程失败: {}".format("; ".join(errors) or "未知原因"), 0


# ══════════════════════════════════════════════════════════════════════
# 系统面板
# ══════════════════════════════════════════════════════════════════════
def open_panel(panel):
    """打开系统面板 → (ok, msg, 目标)。**只接受白名单内的面板名**。"""
    key = _clean(panel)
    if not key:
        return False, "面板类型为空：可选 {}".format("/".join(PANELS.keys())), ""
    if key not in PANELS:
        return False, "不支持的面板: {!r}（可选 {}）".format(
            key, "/".join(PANELS.keys())), ""
    mode, target = PANELS[key]
    try:
        if mode == "open":
            os.startfile(target)           # noqa: S606 — 目标来自固定白名单
        else:
            subprocess.Popen([target], close_fds=True)
    except Exception as e:                 # noqa: BLE001
        return False, "打开『{}』失败: {}".format(key, e), target
    return True, "已打开{}".format(key), target


# ══════════════════════════════════════════════════════════════════════
# 电源
# ══════════════════════════════════════════════════════════════════════
def _shutdown_exe():
    root = _clean(os.environ.get("SystemRoot")) or "C:\\Windows"
    return os.path.join(root, "System32", "shutdown.exe")


def power_action(action, delay=60, force=False, dry_run=False, confirm=None):
    """关机/重启/注销/锁屏/待机/休眠 → (ok, msg, detail dict)。

    安全语义（对齐方案文档 §8.6）：
      ① ``dry_run=True`` 只做参数校验与预演，**绝不真正执行**；
      ② 真实执行**必须**经 ``confirm`` 二次确认，无确认回调一律拒绝；
      ③ 延迟秒数钳制在 0–86400，默认 60 秒（给用户留取消余量）。
    """
    act = _clean(action)
    if act not in POWER_ACTIONS:
        return False, "不支持的动作: {!r}（可选 {}）".format(
            action, "/".join(POWER_ACTIONS)), None
    seconds = _to_int(delay, 60)
    seconds = max(0, min(86400, seconds))
    detail = {"action": act, "delay": seconds, "force": bool(force),
              "dry_run": bool(dry_run)}

    if dry_run:
        return True, "预演：将于 {} 秒后执行『{}』（未真正执行）".format(seconds, act), detail

    if not callable(confirm):
        return (False, "危险操作需二次确认：当前无确认界面。"
                       "自动化测试请用『预演=是』", detail)
    try:
        approved = bool(confirm("即将执行『{}』（延迟 {} 秒，期间可用 shutdown /a 取消）。"
                                "\n确定继续？".format(act, seconds)))
    except Exception as e:                 # noqa: BLE001
        return False, "二次确认失败: {}".format(e), detail
    if not approved:
        return False, "用户取消电源操作", detail

    try:
        if act in ("关机", "重启", "注销"):
            flag = {"关机": "/s", "重启": "/r", "注销": "/l"}[act]
            cmd = [_shutdown_exe(), flag]
            if act != "注销":
                # shutdown.exe 的 /t 只对 /s /r 生效；注销不支持延迟（文档已说明）
                cmd += ["/t", str(seconds)]
            if force:
                cmd.append("/f")
            subprocess.Popen(cmd, close_fds=True)
            if act == "注销":
                return True, "已提交注销命令（注销不支持延迟，已忽略 {} 秒）".format(
                    seconds), detail
            return True, "已提交{}命令（延迟 {} 秒）".format(act, seconds), detail
        if not _is_windows():
            return False, "非 Windows 平台不支持『{}』".format(act), detail
        import ctypes
        if act == "锁屏":
            ok = bool(ctypes.windll.user32.LockWorkStation())
            return (True, "已锁屏", detail) if ok else (False, "锁屏失败", detail)
        if act == "待机":
            ok = bool(ctypes.windll.powrprof.SetSuspendState(0, 1, 0))
            return (True, "已进入待机", detail) if ok else (False, "待机失败", detail)
        ok = bool(ctypes.windll.powrprof.SetSuspendState(1, 0, 0))
        return (True, "已进入休眠", detail) if ok else (False, "休眠失败", detail)
    except Exception as e:                 # noqa: BLE001
        return False, "电源操作失败: {}".format(e), detail


def cancel_power():
    """取消已排定的关机/重启（对应 ``shutdown /a``）。"""
    try:
        subprocess.Popen([_shutdown_exe(), "/a"], close_fds=True)
        return True, "已请求取消关机", ""
    except Exception as e:                 # noqa: BLE001
        return False, "取消失败: {}".format(e), ""


# ══════════════════════════════════════════════════════════════════════
# 音量（COM: IAudioEndpointVolume，失败回退 keybd_event）
# ══════════════════════════════════════════════════════════════════════
_CLSID_MMDEVICE_ENUM = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDEVICE_ENUM = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAUDIO_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

# COM vtable 下标（IUnknown 占 0/1/2）
_VT_GET_DEFAULT_ENDPOINT = 4        # IMMDeviceEnumerator::GetDefaultAudioEndpoint
_VT_DEVICE_ACTIVATE = 3             # IMMDevice::Activate
_VT_SET_VOLUME_SCALAR = 7           # IAudioEndpointVolume::SetMasterVolumeLevelScalar
_VT_GET_VOLUME_SCALAR = 9           # IAudioEndpointVolume::GetMasterVolumeLevelScalar
_VT_SET_MUTE = 14                   # IAudioEndpointVolume::SetMute
_VT_GET_MUTE = 15                   # IAudioEndpointVolume::GetMute


class _GUID(object):
    """GUID 结构工厂（延迟构造，避免 import 期绑定 ctypes 类型）。"""

    _cls = None

    @classmethod
    def build(cls):
        if cls._cls is not None:
            return cls._cls
        import ctypes

        class _G(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong),
                        ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        cls._cls = _G
        return _G


def _guid(text):
    """字符串 → GUID 结构（CLSIDFromString）。失败抛 OSError。"""
    import ctypes
    guid_cls = _GUID.build()
    guid = guid_cls()
    ole32 = ctypes.windll.ole32
    ole32.CLSIDFromString.restype = ctypes.c_long
    ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(guid_cls)]
    hr = ole32.CLSIDFromString(text, ctypes.byref(guid))
    if hr != 0:
        raise OSError("CLSIDFromString({}) 失败: 0x{:08X}".format(
            text, hr & 0xFFFFFFFF))
    return guid


def _vt_call(ptr, index, restype, argtypes, *args):
    """调用 COM 接口指针 ``ptr`` 的第 ``index`` 个虚函数。"""
    import ctypes
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, *argtypes)
    return proto(vtable[index])(*args)


def _com_release(ptr):
    """Release()；失败静默（避免析构路径抛异常）。"""
    try:
        import ctypes
        _vt_call(ptr, 2, ctypes.c_ulong, [ctypes.c_void_p], ptr)
    except Exception:
        pass


def _open_endpoint_volume():
    """拿到默认输出设备的 IAudioEndpointVolume 接口指针（失败抛异常）。"""
    import ctypes
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

    clsid = _guid(_CLSID_MMDEVICE_ENUM)
    iid_enum = _guid(_IID_IMMDEVICE_ENUM)
    enumerator = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.cast(ctypes.byref(clsid), ctypes.c_void_p), None, 1,   # CLSCTX_INPROC_SERVER
        ctypes.cast(ctypes.byref(iid_enum), ctypes.c_void_p),
        ctypes.byref(enumerator))
    if hr != 0 or not enumerator:
        raise OSError("CoCreateInstance(MMDeviceEnumerator) 失败: 0x{:08X}".format(
            hr & 0xFFFFFFFF))

    device = ctypes.c_void_p()
    try:
        hr = _vt_call(enumerator, _VT_GET_DEFAULT_ENDPOINT, ctypes.c_long,
                      [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                       ctypes.POINTER(ctypes.c_void_p)],
                      enumerator, 0, 0, ctypes.byref(device))       # eRender / eConsole
        if hr != 0 or not device:
            raise OSError("GetDefaultAudioEndpoint 失败: 0x{:08X}（可能无音频设备）".format(
                hr & 0xFFFFFFFF))
    finally:
        _com_release(enumerator)

    try:
        iid_vol = _guid(_IID_IAUDIO_ENDPOINT_VOLUME)
        iface = ctypes.c_void_p()
        hr = _vt_call(device, _VT_DEVICE_ACTIVATE, ctypes.c_long,
                      [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                      device, ctypes.cast(ctypes.byref(iid_vol), ctypes.c_void_p),
                      1, None, ctypes.byref(iface))
        if hr != 0 or not iface:
            raise OSError("IMMDevice::Activate 失败: 0x{:08X}".format(hr & 0xFFFFFFFF))
        return iface
    finally:
        _com_release(device)


def _epv_get_volume(iface):
    import ctypes
    level = ctypes.c_float(0.0)
    hr = _vt_call(iface, _VT_GET_VOLUME_SCALAR, ctypes.c_long,
                  [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float)],
                  iface, ctypes.byref(level))
    if hr != 0:
        raise OSError("GetMasterVolumeLevelScalar 失败: 0x{:08X}".format(hr & 0xFFFFFFFF))
    return int(round(float(level.value) * 100.0))


def _epv_set_volume(iface, percent):
    import ctypes
    hr = _vt_call(iface, _VT_SET_VOLUME_SCALAR, ctypes.c_long,
                  [ctypes.c_void_p, ctypes.c_float, ctypes.c_void_p],
                  iface, ctypes.c_float(max(0.0, min(1.0, percent / 100.0))), None)
    if hr != 0:
        raise OSError("SetMasterVolumeLevelScalar 失败: 0x{:08X}".format(hr & 0xFFFFFFFF))


def _epv_set_mute(iface, muted):
    import ctypes
    hr = _vt_call(iface, _VT_SET_MUTE, ctypes.c_long,
                  [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p],
                  iface, 1 if muted else 0, None)
    if hr != 0:
        raise OSError("SetMute 失败: 0x{:08X}".format(hr & 0xFFFFFFFF))


def _epv_get_mute(iface):
    import ctypes
    flag = ctypes.c_int(0)
    hr = _vt_call(iface, _VT_GET_MUTE, ctypes.c_long,
                  [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)],
                  iface, ctypes.byref(flag))
    if hr != 0:
        raise OSError("GetMute 失败: 0x{:08X}".format(hr & 0xFFFFFFFF))
    return bool(flag.value)


def _keyboard_volume(vk, times=1):
    """回退方案：模拟音量键（每次约 ±2%，精度受限）。"""
    if not _is_windows():
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        for _ in range(max(0, int(times))):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:                      # noqa: BLE001
        return False


def get_volume():
    """读取当前主音量 → (ok, msg, 0-100 或 None)。"""
    if not _is_windows():
        return False, "非 Windows 平台不支持音量控制", None
    try:
        iface = _open_endpoint_volume()
        try:
            level = _epv_get_volume(iface)
        finally:
            _com_release(iface)
        return True, "当前音量 {}%".format(level), level
    except Exception as e:                 # noqa: BLE001
        return False, "读取音量失败（IAudioEndpointVolume 不可用）: {}".format(e), None


def set_volume(percent):
    """设置主音量到 0-100 → (ok, msg, 实际生效值或 None)。

    COM 不可用时回退音量键：先按 50 次「音量-」归零，再按 ``percent/2`` 次
    「音量+」。**精度限制**：每次约 2%，且无法读回真实值，``value`` 返回请求值。
    """
    if not _is_windows():
        return False, "非 Windows 平台不支持音量控制", None
    target = max(0, min(100, _to_int(percent, 0)))
    try:
        iface = _open_endpoint_volume()
        try:
            _epv_set_volume(iface, target)
            actual = _epv_get_volume(iface)
        finally:
            _com_release(iface)
        return True, "音量已设为 {}%".format(actual), actual
    except Exception as e:                 # noqa: BLE001
        if not _keyboard_volume(VK_VOLUME_DOWN, 50) or \
                not _keyboard_volume(VK_VOLUME_UP, target // _VOLUME_STEP):
            return False, "设置音量失败: {}".format(e), None
        return (True,
                "音量键回退：目标 {}%（每次约 {}%，无法读回真实值）".format(
                    target, _VOLUME_STEP),
                target)


def change_volume(delta):
    """在当前音量上增减 → (ok, msg, 新值或 None)。"""
    percent, msg, value = get_volume()
    if not percent:
        # 读不到当前值 → 只能退化为按步进敲键
        steps = abs(_to_int(delta, 0)) // _VOLUME_STEP
        vk = VK_VOLUME_UP if _to_int(delta, 0) >= 0 else VK_VOLUME_DOWN
        if _keyboard_volume(vk, steps):
            return True, "音量键回退：调节 {} 步（无法读回真实值）".format(steps), None
        return False, "调节音量失败: {}".format(msg), None
    target = max(0, min(100, int(value) + _to_int(delta, 0)))
    return set_volume(target)


def volume(action, value=0):
    """音量命令分发 → (ok, msg, 0-100 或 None)。

    动作：设置 / 增加 / 减少 / 静音切换 / 静音开 / 静音关。
    """
    act = _clean(action) or "设置"
    if act == "设置":
        return set_volume(value)
    if act == "增加":
        return change_volume(abs(_to_int(value, 10)) or 10)
    if act == "减少":
        return change_volume(-abs(_to_int(value, 10)) or -10)
    if act in ("静音切换", "静音开", "静音关"):
        if not _is_windows():
            return False, "非 Windows 平台不支持静音控制", None
        want = {"静音切换": None, "静音开": True, "静音关": False}[act]
        try:
            iface = _open_endpoint_volume()
            try:
                current = _epv_get_mute(iface)
                target = (not current) if want is None else want
                _epv_set_mute(iface, target)
                now = _epv_get_mute(iface)
                level = _epv_get_volume(iface)
            finally:
                _com_release(iface)
            return True, "静音状态: {}".format("开" if now else "关"), level
        except Exception as e:             # noqa: BLE001
            if act == "静音切换" and _keyboard_volume(VK_VOLUME_MUTE, 1):
                return (True, "音量键回退：已切换静音（无法读回真实状态）", None)
            return False, "静音操作失败: {}".format(e), None
    return False, "不支持的动作: {!r}（可选 设置/增加/减少/静音切换/静音开/静音关）".format(action), None


# ══════════════════════════════════════════════════════════════════════
# 磁盘空间
# ══════════════════════════════════════════════════════════════════════
def _fmt_size(num):
    """字节 → 人类可读（MB/GB/TB）。"""
    try:
        value = float(num)
    except Exception:
        return str(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return "{:.1f} {}".format(value, unit) if unit != "B" else "{:.0f} B".format(value)
        value /= 1024.0
    return "{:.1f} TB".format(value)


def _normalize_drive(drive):
    """盘符归一化：``C`` / ``C:`` / ``C:\\`` / 任意路径 → 可直接查询的路径。"""
    text = _clean(drive)
    if not text:
        return ""
    if len(text) == 1 and text.isalpha():
        return text.upper() + ":\\"
    if len(text) == 2 and text[1] == ":":
        return text.upper() + "\\"
    return text


def disk_space(drive):
    """查询卷容量 → (ok, msg, {"total","free","used","percent"})，数值单位为字节。"""
    path = _normalize_drive(drive)
    if not path:
        return False, "盘符或路径为空：请填写如 C: 或 D:/data", None
    try:
        if not os.path.exists(path):
            return False, "路径不存在: {}".format(path), None
    except Exception as e:                 # noqa: BLE001
        return False, "路径无效: {}".format(e), None

    total = free = 0
    if _is_windows():
        try:
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            total_free = ctypes.c_ulonglong(0)
            ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(path), ctypes.byref(free_bytes),
                ctypes.byref(total_bytes), ctypes.byref(total_free))
            if ok:
                free = int(free_bytes.value)
                total = int(total_bytes.value)
        except Exception:
            total = free = 0
    if not total:
        try:                               # stdlib 回退（非 Windows 或 ctypes 失败）
            import shutil
            usage = shutil.disk_usage(path)
            total, free = int(usage.total), int(usage.free)
        except Exception as e:             # noqa: BLE001
            return False, "查询磁盘空间失败: {}".format(e), None

    used = max(0, total - free)
    percent = round(used * 100.0 / total, 2) if total else 0.0
    detail = {"total": total, "free": free, "used": used, "percent": percent}
    return (True,
            "{} 总容量 {} / 可用 {} / 已用 {} ({:.1f}%)".format(
                path, _fmt_size(total), _fmt_size(free), _fmt_size(used), percent),
            detail)


# ══════════════════════════════════════════════════════════════════════
# 窗口样式（复用引擎 _find_window_by_pattern，必要时 ctypes 回退）
# ══════════════════════════════════════════════════════════════════════
def _find_window_ctypes(title):
    """纯 ctypes 的窗口查找回退：先精确匹配，再大小写不敏感子串匹配。"""
    if not _is_windows():
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW(None, ctypes.c_wchar_p(title))
        if hwnd:
            return int(hwnd)
        found = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                return True
            length = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, length + 1)
            if title.lower() in (buf.value or "").lower():
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(enum_proc(_cb), 0)
        return found[0] if found else 0
    except Exception:                      # noqa: BLE001
        return 0


def find_window(title, finder=None):
    """按标题找窗口句柄 → hwnd（0 = 未找到）。

    ``finder`` 优先（由 ``commands.py`` 注入引擎既有的
    ``engine._find_window_by_pattern``，保持"一处实现"），失败再走 ctypes。
    """
    text = _clean(title)
    if not text:
        return 0
    if callable(finder):
        try:
            hwnd = int(finder(text) or 0)
            if hwnd:
                return hwnd
        except Exception:
            pass
    return _find_window_ctypes(text)


def window_style(title, opacity=None, topmost=None, visibility=None, finder=None):
    """设置窗口透明度/置顶/隐藏显示 → (ok, msg, hwnd)。

    * ``opacity``: 0-255（0=全透明、255=不透明；``None`` 表示不改）；
    * ``topmost``: True/False/None；
    * ``visibility``: "隐藏" / "显示" / None。
    """
    hwnd = find_window(title, finder)
    if not hwnd:
        return False, "未找到窗口: {}".format(_clean(title) or "(空)"), 0
    if not _is_windows():
        return False, "非 Windows 平台不支持窗口样式设置", hwnd

    applied = []
    try:
        import ctypes
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_ALPHA = 0x2
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010

        if opacity is not None and _clean(opacity) != "":
            alpha = max(0, min(255, _to_int(opacity, 255)))
            getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            getter.restype = ctypes.c_ssize_t
            style = int(getter(ctypes.c_void_p(hwnd), GWL_EXSTYLE))
            setter(ctypes.c_void_p(hwnd), GWL_EXSTYLE, style | WS_EX_LAYERED)
            if not user32.SetLayeredWindowAttributes(
                    ctypes.c_void_p(hwnd), 0, alpha, LWA_ALPHA):
                return False, "设置透明度失败(错误码 {})".format(
                    int(ctypes.windll.kernel32.GetLastError())), hwnd
            applied.append("透明度={}".format(alpha))

        if topmost is not None and _clean(topmost) != "":
            enabled = _flag(topmost, False)
            insert_after = ctypes.c_void_p(-1 if enabled else -2)   # TOPMOST / NOTOPMOST
            if not user32.SetWindowPos(ctypes.c_void_p(hwnd), insert_after, 0, 0, 0, 0,
                                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE):
                return False, "设置置顶失败(错误码 {})".format(
                    int(ctypes.windll.kernel32.GetLastError())), hwnd
            applied.append("置顶={}".format("是" if enabled else "否"))

        vis = _clean(visibility)
        if vis:
            if vis == "隐藏":
                user32.ShowWindow(ctypes.c_void_p(hwnd), 0)         # SW_HIDE
                applied.append("已隐藏")
            elif vis == "显示":
                user32.ShowWindow(ctypes.c_void_p(hwnd), 5)         # SW_SHOW
                user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
                applied.append("已显示")
            else:
                return False, "『隐藏/显示』只能填 隐藏/显示（收到 {!r}）".format(visibility), hwnd

        if not applied:
            return False, "没有需要设置的项（透明度/置顶/隐藏显示 全为空）", hwnd
        return True, "已设置窗口 {}: {}".format(hwnd, ", ".join(applied)), hwnd
    except Exception as e:                 # noqa: BLE001
        return False, "设置窗口样式失败: {}".format(e), hwnd
