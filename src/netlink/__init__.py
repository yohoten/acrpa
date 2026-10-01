# -*- coding: utf-8 -*-
"""ACRPA NetLink — 局域网多设备互联（纯标准库，线程安全，零 UI 依赖）。

本文件是门面：保留协议常量 re-export，并对外提供进程级单例入口
（get_bus / is_running / start_netlink / stop_netlink / get_node）。

所有重对象（NetBus / NetLinkNode）均通过函数内懒 import 创建，
保证 `import netlink` 在无 GUI / 无 tkinter 环境下也能安全导入。
"""
import threading

from .protocol import (
    PROTOCOL_VERSION, DEFAULT_PORT, DEFAULT_DISCOVERY_PORT,
    encode_frame, frame_message, FrameReader, make_msg, parse_msg, T_NAMES,
)

__all__ = [
    "PROTOCOL_VERSION", "DEFAULT_PORT", "DEFAULT_DISCOVERY_PORT",
    "encode_frame", "frame_message", "FrameReader", "make_msg", "parse_msg",
    "T_NAMES",
    "get_bus", "is_running", "start_netlink", "stop_netlink", "get_node",
    "set_control_hooks", "send_command",
    "list_remote_scripts", "push_script", "push_script_many",
    "get_transfer_topic",
    "request_screenshot", "get_screenshot_topic",
    "start_webui", "stop_webui", "is_webui_running",
    "webui_url", "webui_token", "rotate_webui_token",
]


_bus = None
_node = None
_webui = None
_lock = threading.Lock()
_webui_lock = threading.Lock()
# 操控钩子缓存：节点尚未启动时先登记，start_netlink 创建节点后补注入
_control_hooks = {"run": None, "stop": None}


def get_bus():
    """返回进程级 NetBus 单例（首次调用时创建）。"""
    global _bus
    try:
        if _bus is None:
            with _lock:
                if _bus is None:
                    from .bus import NetBus
                    _bus = NetBus()
        return _bus
    except Exception:
        return None


def is_running():
    """NetLink 节点是否处于运行态。"""
    node = _node
    if node is None:
        return False
    try:
        return bool(getattr(node, "_started", False))
    except Exception:
        return False


def start_netlink(root=None):
    """按 state.NETLINK_ENABLED 启动节点；重复调用返回当前状态，不重复启动。"""
    global _node
    try:
        import state
        if not getattr(state, "NETLINK_ENABLED", False):
            return False
    except Exception:
        return False
    try:
        if is_running():
            return True
        bus = get_bus()
        from .node import NetLinkNode
        with _lock:
            if _node is not None and getattr(_node, "_started", False):
                return True
            node = NetLinkNode(root=root, bus=bus)
            ok = node.start()
            if not ok:
                try:
                    node.stop()
                except Exception:
                    pass
                return False
            _node = node
        try:
            node.set_control_hooks(run=_control_hooks.get("run"),
                                   stop=_control_hooks.get("stop"))
        except Exception:
            pass
        return True
    except Exception as e:
        try:
            from .connection import _nl_log
            _nl_log("start_netlink failed: {}".format(e), "ERROR")
        except Exception:
            pass
        return False


def stop_netlink():
    """幂等停止节点（捕获全部异常），并把 _node/_bus 置 None（可再次 start）。

    Phase4-2：连带停止浏览器只读面板，避免停止互联后端口泄漏。
    """
    global _node, _bus
    try:
        stop_webui()
    except Exception:
        pass
    node = None
    try:
        with _lock:
            node = _node
            _node = None
            _bus = None
        if node is not None:
            node.stop()
    except Exception:
        pass


def get_node():
    """返回当前 NetLinkNode（未启动时为 None）。"""
    return _node


def set_control_hooks(run=None, stop=None):
    """登记被控端执行钩子（run(loops=None) / stop()）。

    懒 import node；若节点尚未启动则先缓存，start_netlink 创建节点后自动补注入。
    """
    try:
        if run is not None:
            _control_hooks["run"] = run
        if stop is not None:
            _control_hooks["stop"] = stop
        node = _node
        if node is not None:
            try:
                node.set_control_hooks(run=run, stop=stop)
            except Exception:
                pass
        return True
    except Exception:
        return False


def list_remote_scripts(peer_node_id):
    """控制端：请求远端脚本列表；找到连接并发出返回 True，否则 False。"""
    try:
        node = _node
        if node is None:
            return False
        return bool(node.list_remote_scripts(peer_node_id))
    except Exception:
        return False


def push_script(peer_node_id, local_path, remote_name=None, run=False):
    """控制端：把本地脚本推送到该 peer；返回 tid 或 None。"""
    try:
        node = _node
        if node is None:
            return None
        return node.push_script(peer_node_id, local_path, remote_name, run)
    except Exception:
        return None


def push_script_many(peer_node_ids, local_path, remote_name=None, run=False):
    """控制端：把同一脚本批量下发到多个 peer（Phase3-2）。

    返回 {"ok": {peer_node_id: tid, ...}, "skip": {peer_node_id: <原因>, ...}}；
    节点未启动 / 异常时返回 {"ok": {}, "skip": {}}（不抛）。
    """
    try:
        node = _node
        if node is None:
            return {"ok": {}, "skip": {}}
        return node.push_script_many(peer_node_ids, local_path, remote_name, run)
    except Exception:
        return {"ok": {}, "skip": {}}


def get_transfer_topic():
    """返回脚本分发进度主题（便于 UI 不硬编码）。"""
    try:
        from .transfer import TOPIC_TRANSFER
        return TOPIC_TRANSFER
    except Exception:
        return "netlink.transfer"


def request_screenshot(peer_node_id, max_width=None, quality=None):
    """控制端：请求远端截图；找到连接并发出返回 True，否则 False。"""
    try:
        node = _node
        if node is None:
            return False
        return bool(node.request_screenshot(peer_node_id, max_width, quality))
    except Exception:
        return False


def get_screenshot_topic():
    """返回远程截图事件主题（便于 UI 不硬编码）。"""
    try:
        from .node import TOPIC_SCREENSHOT
        return TOPIC_SCREENSHOT
    except Exception:
        return "netlink.screenshot"


def send_command(peer_node_id, t, data=None):
    """按 node_id 找到已认证的目标连接并发送一条消息；找不到/未启动返回 False。

    供 Phase 2-2b 的 UI 使用（控制端视角）。
    """
    try:
        node = _node
        if node is None:
            return False
        from .protocol import make_msg
        msg = make_msg(t, data if data is not None else {})
        nid = str(peer_node_id or "")
        try:
            with node._lock:
                conns = list(node._conns.items())
        except Exception:
            return False
        for conn, rec in conns:
            try:
                if str((rec or {}).get("node_id") or "") == nid:
                    return bool(node.send(conn, msg))
            except Exception:
                continue
        return False
    except Exception:
        return False


# ── Phase4-2 浏览器只读监控面板（门面）──────────────────────────────────
def start_webui():
    """按 state.NETLINK_WEB_ENABLED 启动只读面板；需先 start_netlink 且 enabled。

    返回 True 表示面板可访问；配置关闭 / 互联未启动 / 绑定失败均返回 False。
    """
    global _webui
    try:
        import state
        if not getattr(state, "NETLINK_WEB_ENABLED", False):
            return False
    except Exception:
        return False
    if not is_running():
        return False
    try:
        with _webui_lock:
            if _webui is not None and getattr(_webui, "running", False):
                return True
            node = _node
            if node is None:
                return False
            from .webui import WebUI, DEFAULT_PORT
            port = getattr(state, "NETLINK_WEB_PORT", DEFAULT_PORT)
            bind = getattr(state, "NETLINK_WEB_BIND", "0.0.0.0")
            ui = WebUI(node, port=port, bind=bind)
            if not ui.start():
                try:
                    ui.stop()
                except Exception:
                    pass
                return False
            _webui = ui
            return True
    except Exception as e:
        try:
            from .connection import _nl_log
            _nl_log("start_webui failed: {}".format(e), "ERROR")
        except Exception:
            pass
        return False


def stop_webui():
    """幂等停止面板（端口释放，可再次 start）。"""
    global _webui
    ui = None
    try:
        with _webui_lock:
            ui = _webui
            _webui = None
        if ui is not None:
            ui.stop()
    except Exception:
        pass


def is_webui_running():
    """面板是否运行中。"""
    ui = _webui
    if ui is None:
        return False
    try:
        return bool(ui.running)
    except Exception:
        return False


def webui_url():
    """面板访问地址；未运行时返回空串。"""
    ui = _webui
    if ui is None:
        return ""
    try:
        return ui.url() if ui.running else ""
    except Exception:
        return ""


def webui_token():
    """当前面板令牌（不存在则生成并写入凭据库）。"""
    try:
        from .webui import get_token
        return get_token()
    except Exception:
        return ""


def rotate_webui_token():
    """重新生成面板令牌（写凭据库）并返回新值。"""
    ui = _webui
    try:
        if ui is not None:
            return ui.rotate_token()
    except Exception:
        pass
    try:
        from .webui import rotate_token
        return rotate_token()
    except Exception:
        return ""
