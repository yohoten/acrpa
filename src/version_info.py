"""ACRPA 版本号唯一事实来源 (Single Source of Truth).

所有模块应通过 `from version_info import VERSION` 获取版本号，
避免在多个文件中硬编码导致版本号不同步。

版本号保存在项目根目录 VERSION 文件第一行:
    0.1.24
    第二行: 下载地址 (可选, 供 updater 使用)

修改版本号只需编辑 VERSION 文件 (或运行 tools/bump_version.py)。
"""
import os


# ── 内置回退版本 (仅当 VERSION 文件缺失/损坏时使用) ──
_FALLBACK_VERSION = "0.1.24"


def _get_root():
    """定位项目根目录 (version_info.py 位于 src/ 下)。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_version():
    """读取 VERSION 文件第一行作为版本号；失败时回退内置版本。"""
    try:
        with open(os.path.join(_get_root(), "VERSION"), encoding="utf-8") as f:
            line = f.readline().strip()
            if line:
                return line
    except Exception:
        pass
    return _FALLBACK_VERSION


def get_download_url():
    """读取 VERSION 文件第二行作为下载地址 (可能为空)。"""
    try:
        with open(os.path.join(_get_root(), "VERSION"), encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            return lines[1] if len(lines) > 1 else ""
    except Exception:
        return ""


# ── 模块级常量: 模块导入时即确定当前版本 ──
VERSION = get_version()
