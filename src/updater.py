"""Lightweight update checker — no new dependencies."""
import os
import threading
import requests

# 版本号统一从 version_info 获取 (VERSION 文件为唯一事实来源)
from version_info import VERSION

UPDATE_URL = "https://gitee.com/yohoten/acrpa/raw/master/VERSION"

# Result cache — only check once per session
_result = None  # None=not checked, False=no update, (ver, url)=update available
_checking = False


def _compare_versions(current, latest):
    """Compare semver-like strings. Returns True if latest > current."""
    def parse(v):
        try:
            return tuple(int(x) for x in v.lstrip("v").split("."))
        except Exception:
            return (0,)
    return parse(latest) > parse(current)


def check_async(callback=None):
    """Check for updates in a background thread. Calls callback(version, url) if update found."""
    global _checking, _result
    if _checking or _result is not None:
        return
    _checking = True

    def _run():
        global _result
        try:
            resp = requests.get(UPDATE_URL, timeout=8)
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
            latest_ver = lines[0].strip()
            download_url = lines[1].strip() if len(lines) > 1 else ""
            if _compare_versions(VERSION, latest_ver):
                _result = (latest_ver, download_url)
                if callback:
                    callback(latest_ver, download_url)
            else:
                _result = False
        except Exception:
            _result = False
        finally:
            _checking = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def get_result():
    return _result
