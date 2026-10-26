# -*- coding: utf-8 -*-
"""ui.tray_menu — 托盘右键菜单「规格层」（方案文档 §5 L4 #1）。

把**菜单长什么样**与**怎么弹出来**解耦：

* 本模块只产出 dict 规格，零 tkinter 副作用（`import ui.tray_menu` 不建窗）；
* 真正的 ``tkinter.Menu`` 构建、勾选变量、``tk_popup`` 与事件分发留在
  ``src/tray.py``；
* 因此「菜单结构是否齐全」「每个菜单项是否有绑定的回调」都可以**纯逻辑单测**
  （见 ``tools/_test_ui_l4.py``），不需要真实弹出菜单。

规格节点字段
------------
``id``        稳定标识（测试 / 日志用）
``type``      ``command`` / ``separator`` / ``submenu`` / ``check`` / ``status``
``label``     显示文案（已过 ``i18n.t()``）
``action``    回调键；``separator`` / ``status`` 为 ``None``
``children``  仅 ``submenu``：子节点列表
``checked``   仅 ``check``：初始勾选状态

文案约定：全部使用 ACRPA 自己的中文措辞（**不**拷贝 zTasker 文案），
承重串走 ``i18n.t()``，英文目录已有对应翻译。
"""
import i18n

__all__ = [
    "TYPE_COMMAND", "TYPE_SEPARATOR", "TYPE_SUBMENU", "TYPE_CHECK", "TYPE_STATUS",
    "build_spec", "iter_items", "action_keys", "menu_ids", "find_item",
    "ACTION_KEYS",
]

TYPE_COMMAND = "command"
TYPE_SEPARATOR = "separator"
TYPE_SUBMENU = "submenu"
TYPE_CHECK = "check"
TYPE_STATUS = "status"

# 电源类动作名（与 sys_ops.POWER_ACTIONS 同源，这里只作为**回调键后缀**使用）
POWER_ACTIONS = ("关机", "重启", "注销", "锁屏", "待机", "休眠")


def _t(key, **fmt):
    """i18n 取文案（失败回退 key，绝不抛）。"""
    try:
        return i18n.t(key, **fmt)
    except Exception:                       # noqa: BLE001
        return key


def _cmd(item_id, label, action):
    return {"id": item_id, "type": TYPE_COMMAND, "label": label,
            "action": action, "children": None, "checked": None}


def _sep(item_id="sep"):
    return {"id": item_id, "type": TYPE_SEPARATOR, "label": "-",
            "action": None, "children": None, "checked": None}


def _status(item_id, label):
    return {"id": item_id, "type": TYPE_STATUS, "label": label,
            "action": None, "children": None, "checked": None}


def _submenu(item_id, label, children):
    return {"id": item_id, "type": TYPE_SUBMENU, "label": label,
            "action": None, "children": children, "checked": None}


def _check(item_id, label, action, checked=False):
    return {"id": item_id, "type": TYPE_CHECK, "label": label,
            "action": action, "children": None, "checked": bool(checked)}


def build_spec(ctx=None):
    """→ 托盘菜单规格（``list[dict]``）。

    ``ctx`` 可选项（缺省全部有安全默认）：
        ``running`` / ``recording``        执行状态（决定状态行与「执行/取消」）
        ``status_label``                   状态行文案（给了就直接用）
        ``power_delay_sec``                电源类默认延迟秒数（默认 60）
        ``power_cancel_sec``               还在倒计时则传入剩余秒数，否则 None
        ``sched_enabled`` / ``auto_start`` 只读状态行
        ``netlink_enabled``                「允许远程连接」勾选态
    """
    ctx = dict(ctx or {})
    running = bool(ctx.get("running"))
    recording = bool(ctx.get("recording"))

    status_label = ctx.get("status_label")
    if not status_label:
        if running:
            status_label = _t("tray.status_running")
        elif recording:
            status_label = _t("tray.status_recording")
        else:
            status_label = _t("tray.status_idle")

    try:
        delay = int(ctx.get("power_delay_sec") or 60)
    except Exception:                       # noqa: BLE001
        delay = 60
    delay = max(0, min(86400, delay))

    left = ctx.get("power_cancel_sec")
    if left in (None, ""):
        cancel_label = _t("tray.power_cancel")
    else:
        try:
            cancel_label = _t("tray.power_cancel_left", sec=int(left))
        except Exception:                   # noqa: BLE001
            cancel_label = _t("tray.power_cancel")

    reminder_items = [
        _cmd("reminder_5", _t("tray.reminder_min", min=5), "reminder_5"),
        _cmd("reminder_15", _t("tray.reminder_min", min=15), "reminder_15"),
        _cmd("reminder_30", _t("tray.reminder_min", min=30), "reminder_30"),
        _sep("sep_reminder"),
        _cmd("reminder_custom", _t("tray.reminder_custom"), "reminder_custom"),
    ]

    power_items = [
        _cmd("power_shutdown_delay",
             _t("tray.power_shutdown_delay", sec=delay), "power_shutdown_delay"),
        _sep("sep_power1"),
        _cmd("power_shutdown", _t("tray.power_shutdown"), "power_shutdown"),
        _cmd("power_restart", _t("tray.power_restart"), "power_restart"),
        _cmd("power_logoff", _t("tray.power_logoff"), "power_logoff"),
        _cmd("power_lock", _t("tray.power_lock"), "power_lock"),
        _cmd("power_sleep", _t("tray.power_sleep"), "power_sleep"),
        _cmd("power_hibernate", _t("tray.power_hibernate"), "power_hibernate"),
        _sep("sep_power2"),
        _cmd("power_cancel", cancel_label, "power_cancel"),
    ]

    spec = [
        _status("status", status_label),
        _sep("sep_top"),
        _cmd("show_main", _t("tray.show_main"), "restore"),
        _sep("sep_run"),
    ]
    if running or recording:
        spec.append(_cmd("stop_exec", _t("tray.stop_exec"), "stop"))
    else:
        spec.append(_cmd("run_all", _t("tray.run_all"), "run"))

    spec.extend([
        _sep("sep_quick"),
        _submenu("quick_reminder", _t("tray.quick_reminder"), reminder_items),
        _submenu("power", _t("tray.power"), power_items),
        _sep("sep_mute"),
        _cmd("mute_toggle", _t("tray.mute_toggle"), "mute_toggle"),
        _sep("sep_open"),
        _cmd("open_settings", _t("tray.settings"), "settings"),
        _cmd("open_tasks", _t("tray.tasks"), "tasks"),
        _cmd("open_about", _t("tray.about"), "about"),
        _sep("sep_status"),
        _status("sched_status",
                _t("tray.sched_on") if ctx.get("sched_enabled") else _t("tray.sched_off")),
        _status("autostart_status",
                _t("tray.autostart_on") if ctx.get("auto_start") else _t("tray.autostart_off")),
        _check("netlink_toggle", _t("tray.netlink"), "netlink_toggle",
               bool(ctx.get("netlink_enabled"))),
        _sep("sep_bottom"),
        _cmd("quit", _t("tray.quit"), "quit"),
    ])
    return spec


def iter_items(spec):
    """深度优先遍历规格树（含子菜单），逐个 yield 节点。"""
    for node in spec or []:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if children:
            for child in iter_items(children):
                yield child


def action_keys(spec=None):
    """→ 规格里出现过的全部 action 键（去重、保持出现顺序）。"""
    out = []
    for node in iter_items(spec if spec is not None else build_spec({})):
        act = node.get("action")
        if act and act not in out:
            out.append(act)
    return out


def menu_ids(spec=None):
    """→ 规格里出现过的全部节点 id（扁平列表，便于断言）。"""
    return [str(node.get("id") or "") for node in
            iter_items(spec if spec is not None else build_spec({}))]


def find_item(spec, item_id):
    """按 id 找节点；找不到返回 ``None``。"""
    for node in iter_items(spec):
        if str(node.get("id") or "") == str(item_id):
            return node
    return None


# 默认规格（无运行时上下文）下的全部回调键 —— 供 tray.py 做「回调必须齐全」自检，
# 也供单测断言「菜单项与回调绑定存在」。见 SystemTray.ACTION_METHODS。
ACTION_KEYS = tuple(action_keys(build_spec({})))
