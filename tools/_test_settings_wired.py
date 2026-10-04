# -*- coding: utf-8 -*-
"""tools/_test_settings_wired.py — P0-7「三个看得见但不起作用的设置项」接线守护测试。

背景: recording_stop_hotkey / ocr_preload / market_auto_check_update 三项此前
只有 UI 写入 (settings_window) 与 state 默认值, `src/` 内**零消费者** —— 即
「死设置」。本测试用**静态消费者检查**守护: 只要谁把接线删了/改名了, 立即 FAIL。

检查项:
  1) 三项设置在 src/ 的**消费者**中各自至少命中一处
     (排除 state.py 的 schema 默认值 与 settings_window.py 的 UI 写入);
  2) 三处**真实接线点**仍然存在 (OCR preload 公开函数 + 启动 daemon 线程、
     市场 check_update 调用、录制停止热键的解析与边沿检测状态);
  3) 启动预热不得阻塞主循环: OCR 预热线程必须是 daemon;
  4) 运行时行为 (无窗口 / 无网络):
     - ocr_backend.preload() 可调用、返回 bool、不抛异常;
     - market_window 本地版本扫描能读出自建安装目录的 manifest.json,
       无清单/无 version 的目录被跳过;
     - _collect_updates 以 (script_id, local_version) 调用 check_update
       (此处桩化, 不联网) 并正确筛出可更新脚本;
     - 从 ACRPA.py 源码提取 _parse_hotkey_spec 纯函数验证解析/非法值兜底
       (ACRPA.py 模块级会建 Tk 窗口, 故不 import 它)。

退出码 0(全过) / 1(存在 FAIL)。输出行前缀: [OK] / [FAIL]。
运行：python tools/_test_settings_wired.py
(cmd.exe 下中文乱码可先单独执行 `set PYTHONIOENCODING=utf-8`，勿用 && 串联。)
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

# 这两个文件分别只是「默认值 schema」与「UI 写入」, 不算消费者 (否则死设置永远"绿")
_CONSUMER_EXCLUDE = ("state.py", "settings_window.py")

_FAILS = []


# ── 测试工具 ──

def ok(msg):
    print("[OK] " + msg)


def fail(msg):
    _FAILS.append(msg)
    print("[FAIL] " + msg)


def check(cond, msg):
    if cond:
        ok(msg)
    else:
        fail(msg)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _consumer_files():
    """→ [(相对名, 绝对路径)]: src/ 下的 .py, 排除 state.py / settings_window.py。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if not d.startswith("__pycache__")]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            if name in _CONSUMER_EXCLUDE:
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            out.append((rel.replace("\\", "/"), os.path.join(dirpath, name)))
    return out


# ── 用例 ──

def test_settings_have_consumers():
    """三项设置各自至少被一个「真消费者」文件读取。"""
    consumers = _consumer_files()
    check(len(consumers) > 0, "找到 src/ 消费者文件 {} 个".format(len(consumers)))

    texts = [(rel, _read(path)) for rel, path in consumers]

    # (设置名, [该设置的大写/小写两种出现形式])
    cases = (
        ("recording_stop_hotkey",
         ("RECORDING_STOP_HOTKEY", "recording_stop_hotkey")),
        ("ocr_preload", ("OCR_PRELOAD", "ocr_preload")),
        ("market_auto_check_update",
         ("MARKET_AUTO_CHECK_UPDATE", "market_auto_check_update")),
    )
    for setting, tokens in cases:
        hits = [rel for rel, txt in texts
                if any(t in txt for t in tokens)]
        check(bool(hits),
              "{} 存在真实消费者: {}".format(
                  setting, ", ".join(hits) if hits else "(无 !! 仍是死设置)"))


def test_ocr_preload_wiring():
    """ocr_preload 接线: ocr_backend 公开 preload() + 启动处 daemon 线程调用。"""
    ob = os.path.join(SRC, "ocr_backend.py")
    ac = os.path.join(SRC, "ACRPA.py")
    check(os.path.exists(ob) and os.path.exists(ac),
          "ocr_backend.py / ACRPA.py 均存在")
    ob_txt = _read(ob)
    ac_txt = _read(ac)

    check("def preload(" in ob_txt,
          "ocr_backend.py 提供公开预热入口 def preload(")
    check("def preload(" in ob_txt and "return False" in ob_txt,
          "preload() 带失败兜底 (返回 bool, 不抛异常)")
    check("preload" in ac_txt and "OCR_PRELOAD" in ac_txt,
          "ACRPA.py 依据 OCR_PRELOAD 调用 ocr_backend.preload")
    check("target=_ocr_preload_work" in ac_txt and "daemon=True" in ac_txt,
          "OCR 预热在工作线程中执行, 且线程为 daemon (不阻塞 Tk 主循环)")


def test_recording_stop_hotkey_wiring():
    """recording_stop_hotkey 接线: 解析 + 边沿检测 + 配置监听 + 硬编码保留。"""
    ac_txt = _read(os.path.join(SRC, "ACRPA.py"))

    check("_RECORD_STOP_SPEC" in ac_txt,
          "_hotkey_poll 使用 _RECORD_STOP_SPEC 承载配置热键解析结果")
    check("_recstop_prev" in ac_txt,
          "配置热键走独立边沿检测状态 _recstop_prev (按住不重复触发)")
    check("RECORDING_STOP_HOTKEY" in ac_txt and "_parse_hotkey_spec" in ac_txt,
          "复用 _parse_hotkey_spec 解析 state.RECORDING_STOP_HOTKEY")
    check("recording_stop_hotkey" in ac_txt,
          "recording_stop_hotkey 已进 on_config_change 监听 (改配置即时生效)")
    # 行为不回归: 硬编码 Ctrl+Shift+Q / Ctrl+Shift+S 仍在
    check("0x51" in ac_txt and "0x53" in ac_txt,
          "既有硬编码停止录制热键 (Q/S) 保留, 无行为回归")


def test_market_auto_check_update_wiring():
    """market_auto_check_update 接线: 打开市场后台遍历已装脚本查更新。"""
    mw_txt = _read(os.path.join(SRC, "market_window.py"))

    check("MARKET_AUTO_CHECK_UPDATE" in mw_txt,
          "market_window.py 读取 state.MARKET_AUTO_CHECK_UPDATE 作开关")
    check("mkt.check_update(" in mw_txt,
          "后台调用 marketplace.check_update(script_id, local_version)")
    check("manifest.json" in mw_txt or "MANIFEST_NAME" in mw_txt,
          "本地版本来自安装目录 manifest.json (有可靠来源, 非误报)")
    check("check_update" in mw_txt and "daemon=True" in mw_txt,
          "更新自检在 daemon 后台线程执行 (不阻塞主线程)")


def test_runtime_behaviour():
    """运行时验证: OCR 预热入口 + 市场本地版本来源 + 停止录制热键解析。"""
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    try:
        import ocr_backend as ob
        import market_window as mw
        import state
    except Exception as e:
        fail("运行时模块导入失败: {}".format(e))
        return

    # ── (a) OCR 预热入口: 可调用 / 返回 bool / 不抛异常 ──
    try:
        r = ob.preload()
        check(isinstance(r, bool),
              "ocr_backend.preload() 返回 bool (本机结果 {!r}, 无后端时应为 False)".format(r))
    except Exception as e:
        fail("ocr_backend.preload() 抛异常 (必须静默兜底): {}".format(e))

    # ── (b) 市场本地版本来源 + 更新判定 (桩化 check_update, 不联网) ──
    tmp = tempfile.mkdtemp(prefix="acrpa_p07_")
    old_dir = getattr(state, "MARKET_INSTALL_DIR", "")
    try:
        root = os.path.join(tmp, "market_scripts")
        os.makedirs(os.path.join(root, "demo.script"))
        with open(os.path.join(root, "demo.script", "manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"id": "demo.script", "version": "1.0.0"}, f)
        os.makedirs(os.path.join(root, "no_manifest"))          # 无清单 → 跳过
        os.makedirs(os.path.join(root, "no_version"))           # 无 version → 跳过
        with open(os.path.join(root, "no_version", "manifest.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"id": "no_version"}, f)

        state.MARKET_INSTALL_DIR = root
        got = mw._installed_local_versions(tmp)
        check(got == {"demo.script": "1.0.0"},
              "本地版本来源 = 安装目录 manifest.json, 异常目录被跳过: {}".format(got))

        calls = []
        orig = mw.mkt.check_update

        def _stub(sid, ver):
            calls.append((sid, ver))
            return sid == "demo.script"

        mw.mkt.check_update = _stub
        try:
            ups = mw._collect_updates(tmp)
        finally:
            mw.mkt.check_update = orig
        check(calls == [("demo.script", "1.0.0")],
              "check_update 以 (script_id, local_version) 被调用: {}".format(calls))
        check(ups == ["demo.script"],
              "可更新脚本被正确筛出 (不误报): {}".format(ups))

        # 安装目录不存在时必须安全返回空 (无网络/无已装脚本场景)
        state.MARKET_INSTALL_DIR = os.path.join(tmp, "not_exist")
        check(mw._installed_local_versions(tmp) == {},
              "安装目录不存在时返回空映射, 不抛异常")
    except Exception as e:
        fail("市场更新自检运行时验证异常: {}".format(e))
    finally:
        try:
            state.MARKET_INSTALL_DIR = old_dir
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    # ── (c) 停止录制热键解析 (提取 ACRPA.py 纯函数, 不启动 Tk) ──
    try:
        ac = _read(os.path.join(SRC, "ACRPA.py"))
        i = ac.index("def _parse_hotkey_spec")
        j = ac.index("def _rebuild_hotkey_specs")
        ns = {}
        exec(ac[i:j], ns)          # noqa: S102 - 仅提取自包含纯函数做断言
        fp = ns["_parse_hotkey_spec"]
        check(fp("Ctrl+Alt+F12") == ({0x11, 0x12}, 0x7B),
              "默认值 Ctrl+Alt+F12 可解析为 (mods={Ctrl,Alt}, F12)")
        check(fp("") is None and fp("未设置") is None and fp("bogus") is None,
              "空/非法值解析为 None → 退回仅硬编码热键 (不抛异常)")
    except Exception as e:
        fail("停止录制热键解析验证失败: {}".format(e))


def main():
    print("== P0-7 设置接线守护 ==")
    print("-- 静态消费者检查 --")
    test_settings_have_consumers()
    test_ocr_preload_wiring()
    test_recording_stop_hotkey_wiring()
    test_market_auto_check_update_wiring()
    print("-- 运行时行为检查 --")
    test_runtime_behaviour()

    if _FAILS:
        print("\n[FAIL] 共 {} 项失败".format(len(_FAILS)))
        return 1
    print("\n[OK] 全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # pragma: no cover - 兜底, 避免测试自身静默
        print("[FAIL] 测试自身异常: {}".format(e))
        sys.exit(1)
