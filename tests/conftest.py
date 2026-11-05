# -*- coding: utf-8 -*-
"""pytest 适配层 (方案文档 S4)。

tests/_test_*.py 均为「独立脚本」：自插 src、自带 check()/main()，退出码 0=过/1=失败。
本 conftest 用子进程把每个脚本包成一个 pytest Item，复用退出码契约且不污染全局状态。

marker 三档：gui(需真实窗口) / slow(网络·e2e·打包·重计算) / 默认(纯逻辑 headless)。
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

GUI_TESTS = {
    "command_palette", "editor_view_split", "exec_bar_split",
    "flow_canvas_gradient", "flow_canvas_interact", "flow_canvas_render_perf",
    "log_dock_split", "rounded_controls", "scroll_bind_recursive", "toast_fade",
    "ui_a11y_tokens", "ui_l4", "ui_zoom_shortcuts", "workflow_library",
    "workflow_view_split",
}

SLOW_TESTS = {
    "ai_generation_flow", "ai_provider",
    "browser_captcha", "browser_cdp", "browser_cookie", "browser_download",
    "browser_frame_tab", "browser_listen", "browser_locator",
    "browser_locator_chain", "browser_manage", "browser_waiter",
    "cli_headless", "cv_match_ext", "l5_engineering", "market_upload",
    "netlink_2node", "netlink_auth", "netlink_control", "netlink_e2e",
    "netlink_phase2_e2e", "netlink_phase3_e2e", "netlink_screenshot",
    "netlink_tls", "netlink_transfer", "netlink_webui",
    "netlink_webui_control", "netlink_webui_control_core",
    "ocr_selfcheck", "packaging_imports", "updater_flow",
}


def _markers(stem):
    ms = []
    if stem in GUI_TESTS:
        ms.append("gui")
    if stem in SLOW_TESTS:
        ms.append("slow")
    return ms


class ScriptItem(pytest.Item):
    """一个 _test_*.py 脚本 = 一个用例 (子进程运行，断言退出码 0)。"""

    def runtest(self):
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(self.path)],
            cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        if r.returncode != 0:
            tail = "\n".join((out + "\n" + err).strip().splitlines()[-60:])
            pytest.fail("脚本退出码 {}\n{}".format(r.returncode, tail), pytrace=False)

    def reportinfo(self):
        return self.path, 0, "[script] {}".format(self.name)


class ScriptFile(pytest.File):
    def collect(self):
        name = self.path.name
        if name.startswith("_test_") and name.endswith(".py"):
            stem = name[len("_test_"):-3]
            item = ScriptItem.from_parent(parent=self, name=stem)
            for m in _markers(stem):
                item.add_marker(m)
            yield item


def pytest_collect_file(file_path, parent):
    n = file_path.name
    if n.startswith("_test_") and n.endswith(".py"):
        return ScriptFile.from_parent(parent, path=file_path)
    return None
