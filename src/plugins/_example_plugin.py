"""
ACRPA 示例插件 — 演示如何编写自定义命令

将此文件复制为 my_plugin.py 并修改即可使用。
复制的插件会被自动发现和加载。
"""
import time
from utils import log1, _safe_get

# ── 插件元信息 ──
PLUGIN_NAME = "示例插件 v1.0"

# ── 命令处理器 ──

def handler_message_box(row, script_dir):
    """
    命令: 弹窗提示 — 在运行时弹出消息框（测试/调试用）

    参数:
        params[0]: 消息文本
        params[1]: 标题（选填，默认"ACRPA 插件"）
    """
    text = str(row[1].value) if row[1].value else "Hello from plugin!"
    title = str(row[2].value) if row[2].value else "ACRPA 插件"
    import tkinter.messagebox as mb
    mb.showinfo(title, text)
    log1("插件弹窗: {}".format(text))


def handler_beep(row, script_dir):
    """
    命令: 蜂鸣 — 播放系统提示音

    参数:
        params[0]: 次数（选填，默认1）
    """
    count = _safe_get(row[1].value, 1, int)
    import ctypes
    for _ in range(min(count, 10)):
        ctypes.windll.user32.MessageBeep(0)
        time.sleep(0.3)
    log1("蜂鸣 {} 次".format(count))


def handler_log_mark(row, script_dir):
    """
    命令: 日志标记 — 在日志中插入一条醒目标记

    参数:
        params[0]: 标记文本
    """
    text = str(row[1].value) if row[1].value else "--- MARK ---"
    log1("=" * 40)
    log1("  [MARK] {}".format(text))
    log1("=" * 40)


# ── 注册命令 ──
# 注意: 在模块顶层直接调用 commands.register()
# 插件加载后会自动注册，engine 会通过 _set_handler 绑定

import commands
commands.register("弹窗提示", "弹出消息框（插件）", "消息文本, 标题", handler_message_box)
commands.register("蜂鸣",     "播放系统提示音（插件）", "次数(默认1)",    handler_beep)
commands.register("日志标记", "日志插入醒目标记（插件）", "标记文本",     handler_log_mark)

# ── 导出命令列表（供 plugin 管理器参考） ──
_EXPORTED_COMMANDS = [
    ("弹窗提示", "弹出消息框（插件）", "消息文本, 标题", handler_message_box),
    ("蜂鸣",     "播放系统提示音（插件）", "次数(默认1)",    handler_beep),
    ("日志标记", "日志插入醒目标记（插件）", "标记文本",     handler_log_mark),
]
