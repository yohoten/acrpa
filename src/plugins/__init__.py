"""
ACRPA 插件系统 — 自动发现 plugins/ 目录下的 .py 文件并注册命令。

插件编写规范:
    1. 创建 src/plugins/my_plugin.py
    2. 定义 handler 函数: def handler(row, script_dir): ...
    3. 模块顶层调用: commands.register("命令名", "描述", "参数说明", handler)
    4. 可选: 设置 PLUGIN_NAME = "我的插件" 用于日志

依赖: 同项目环境 (pyautogui, pyperclip, xlrd, Pillow 等)
"""
import os
import sys
import traceback
from utils import log1

# 已加载的插件模块列表
_loaded_plugins = []


def discover_and_load(plugins_dir=None):
    """
    扫描 plugins/ 目录，加载所有 .py 插件模块。
    在 engine.py 注册完内置命令后调用，避免覆盖内置 handler。
    """
    if plugins_dir is None:
        plugins_dir = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(plugins_dir):
        return

    # 确保 plugins 目录在 sys.path 中
    parent_dir = os.path.dirname(plugins_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    for filename in sorted(os.listdir(plugins_dir)):
        if filename.startswith("_") or not filename.endswith(".py"):
            continue
        mod_name = "plugins.{}".format(filename[:-3])
        try:
            # 动态导入模块 (其顶层 register 会自动注册命令)
            import importlib
            mod = importlib.import_module(mod_name)
            name = getattr(mod, "PLUGIN_NAME", filename[:-3])
            log1("插件已加载: {}".format(name), "success")
            _loaded_plugins.append(mod)
        except Exception:
            log1("插件加载失败 {}: {}".format(filename, traceback.format_exc()), "error")


def reload_plugins():
    """重新加载所有插件（开发调试用）"""
    import importlib
    for mod in _loaded_plugins[:]:
        try:
            importlib.reload(mod)
            name = getattr(mod, "PLUGIN_NAME", mod.__name__)
            log1("插件已重载: {}".format(name))
        except Exception:
            log1("插件重载失败: {}".format(mod.__name__), "error")
            _loaded_plugins.remove(mod)


def list_plugins():
    """返回已加载插件信息列表 [(name, cmds), ...]

    cmds 从 commands 注册表反查该插件模块注册的命令。
    若插件显式声明了 _EXPORTED_COMMANDS 则优先使用该列表。
    """
    import commands
    result = []
    for mod in _loaded_plugins:
        name = getattr(mod, "PLUGIN_NAME", mod.__name__)
        exported = mod.__dict__.get("_EXPORTED_COMMANDS")
        if exported:
            cmds = [n for n, _, _, _ in exported]
        else:
            # 反查：该插件模块定义过哪些 handler 函数 → 映射到注册表命令名
            mod_handlers = {
                getattr(mod, attr) for attr in dir(mod)
                if callable(getattr(mod, attr, None))
            }
            cmds = [n for n, _, _, h in commands.list_all()
                    if h is not None and h in mod_handlers]
        result.append((name, cmds))
    return result
