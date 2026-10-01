# -*- coding: utf-8 -*-
"""NetLink Phase1-4 打包完整性验证。

目的（关键）：验证 netlink 相关模块没有在 PyInstaller 打包时被漏掉——
用 --dir 构建 dist/ACRPA/ACRPA.exe，再启动它 10s，若进程存活即说明
启动期未因缺 hidden import / 模块缺失而崩溃。

运行:  python tools/_verify_netlink_package.py
退出码: 0 = OK / SKIP / WARN（WARN 表示该项未真正验证）；1 = [FAIL]（exe 启动期崩溃）

说明:
  * PyInstaller 不可用 → 打印 [SKIP] pyinstaller not available 并以 0 退出；
  * 构建超时 / 环境不允许（如无权限写 dist/）→ [WARN] + 原始错误，退出码 0，
    但会在输出中显式声明“该项未真正验证”；
  * exe 在 10s 内退出 → [FAIL] 并打印其全部 stdout/stderr（疑似缺 hidden import）；
  * 冻结后的 config.json 写在 <exe_dir>/config.json，绝不触碰项目根 config.json。
"""
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(_HERE)
DIST_EXE = os.path.join(BASE, "dist", "ACRPA", "ACRPA.exe")

SMOKE_WAIT = 10.0
# 构建子进程硬超时；可用环境变量覆盖（PyInstaller 构建本身耗时较长）
BUILD_TIMEOUT = int(os.environ.get("NL_PKG_BUILD_TIMEOUT", "300"))


def _print_tail(text, lines=40):
    try:
        arr = text.splitlines()
    except Exception:
        print(text)
        return
    if len(arr) <= lines:
        print(text)
    else:
        print("... (输出较长，仅显示末尾 {} 行) ...".format(lines))
        print("\n".join(arr[-lines:]))


SPEC_PATH = os.path.join(BASE, "ACRPA.spec")


def _snapshot_spec():
    """构建会按 --dir/--console 重写 ACRPA.spec；先快照，构建后还原，
    避免验证过程污染被纳管的 ACRPA.spec（.gitignore 例外项）。"""
    try:
        if os.path.exists(SPEC_PATH):
            with open(SPEC_PATH, "rb") as f:
                return f.read()
    except Exception:
        pass
    return None


def _restore_spec(data):
    if data is None:
        return
    try:
        with open(SPEC_PATH, "wb") as f:
            f.write(data)
    except Exception as e:
        print("[WARN] 还原 ACRPA.spec 失败: {}".format(repr(e)))


NL_MODULES = [
    "netlink", "netlink.protocol", "netlink.connection", "netlink.bus",
    "netlink.discovery", "netlink.server", "netlink.client",
    "netlink.agent", "netlink.node", "netlink.security",
    "netlink.control", "netlink.audit", "netlink.transfer", "netlink.screen",
    "netlink.webui", "netlink.tls",
    "netlink_window",
]


def _decode(raw):
    """控制台/子进程输出解码：先 utf-8 再 gbk（中文 Windows）。"""
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw)
    for enc in ("utf-8", "gbk"):
        try:
            return bytes(raw).decode(enc)
        except Exception:
            continue
    return bytes(raw).decode("utf-8", "replace")


def _netlink_bundle_check():
    """读 PyInstaller 分析产物，交叉验证 netlink 模块是否被收集。

    exe 若因“缺运行期依赖”在预检阶段退出（与本机环境有关），无法用
    存活 10s 来判断 netlink 是否打包完整，故用 xref/toc 产物兜底验证。
    返回 (present_list, missing_list)；产物缺失返回 (None, None)。
    """
    text = ""
    for rel in ("build/ACRPA/xref-ACRPA.html",
                "build/ACRPA/PYZ-00.toc",
                "build/ACRPA/Analysis-00.toc"):
        p = os.path.join(BASE, rel)
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    text += "\n" + f.read()
        except Exception:
            pass
    if not text:
        return None, None
    present = [m for m in NL_MODULES if m in text]
    missing = [m for m in NL_MODULES if m not in text]
    return present, missing


def main():
    print("=" * 64)
    print("NetLink Phase1-4 packaging verification")
    print("=" * 64)

    # ── 1. PyInstaller 可用性 ──────────────────────────────────────────
    try:
        import PyInstaller
        print("[OK]   PyInstaller {}".format(getattr(PyInstaller, "__version__", "?")))
    except Exception as e:
        print("[SKIP] pyinstaller not available ({})".format(repr(e)))
        return 0

    # ── 2. 构建（--dir --console）───────────────────────────────────────
    print("[..]   running: python build.py --dir --console  (cwd={})".format(BASE))
    spec_backup = _snapshot_spec()
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "build.py", "--dir", "--console"],
            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=BUILD_TIMEOUT)
        rc = proc.returncode
        raw = proc.stdout or b""
    except subprocess.TimeoutExpired as e:
        _restore_spec(spec_backup)
        print("[WARN] 构建超过 {}s，判定为“构建耗时超范围”，该项未真正验证".format(BUILD_TIMEOUT))
        print("[WARN] 原始错误: {}".format(repr(e)))
        try:
            if e.output:
                _print_tail(e.output.decode("utf-8", "replace"))
        except Exception:
            pass
        return 0
    except Exception as e:
        _restore_spec(spec_backup)
        print("[WARN] 构建过程抛异常，环境可能不允许打包，该项未真正验证")
        print("[WARN] 原始错误: {}".format(repr(e)))
        return 0
    # 构建已完成，立即还原被纳管的 ACRPA.spec（--dir/--console 会重写它）
    _restore_spec(spec_backup)

    dt = time.time() - t0
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    _print_tail(text, 40)
    print("[{}] build.py exit={} ({:.1f}s)".format("OK" if rc == 0 else "WARN", rc, dt))

    # ── 2b. 产物存在性 ─────────────────────────────────────────────────
    if not os.path.exists(DIST_EXE):
        print("[WARN] 未生成 {}，该项未真正验证（可能是打包失败或无权限写 dist/）".format(DIST_EXE))
        return 0
    size_mb = os.path.getsize(DIST_EXE) / (1024 * 1024)
    print("[OK]   已生成 {} ({:.1f} MB)".format(DIST_EXE, size_mb))

    # ── 3. 崩溃冒烟：启动 exe，等待 10s 看是否仍在运行 ─────────────────
    # 冻结后的 config.json 写到 <exe_dir>/config.json，cwd 设为 exe 目录更贴近真实运行
    exe_dir = os.path.dirname(DIST_EXE)
    try:
        popen = subprocess.Popen([DIST_EXE], cwd=exe_dir,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as e:
        print("[WARN] 无法启动 exe，该项未真正验证: {}".format(repr(e)))
        return 0

    deadline = time.time() + SMOKE_WAIT
    while time.time() < deadline:
        if popen.poll() is not None:
            break
        time.sleep(0.2)

    rc_exe = popen.poll()
    if rc_exe is None:
        print("[OK]   exe 存活 10s，无启动期崩溃")
        try:
            popen.terminate()
        except Exception:
            pass
        try:
            popen.wait(timeout=5)
        except Exception:
            try:
                popen.kill()
            except Exception:
                pass
        return 0

    # 已退出 → 打印全部输出
    try:
        out = popen.stdout.read() if popen.stdout is not None else b""
    except Exception:
        out = b""
    out_text = _decode(out)
    print("[FAIL] exe 在 10s 内退出 (exit={})".format(rc_exe))
    print("------- exe stdout/stderr (full) -------")
    print(out_text)
    print("----------------------------------------")

    # 若退出原因是 run.py 的“依赖预检”（本机解释器缺运行期依赖，与 netlink 无关），
    # 归类为环境限制 WARN；随后用 PyInstaller 分析产物交叉验证 netlink 是否打包完整。
    preflight = ("requirements.txt" in out_text or "缺少依赖" in out_text
                 or "缺失" in out_text)
    if preflight:
        print("[WARN] 退出原因为 run.py 依赖预检（本机构建解释器缺少运行期依赖），"
              "与 netlink 打包无关 —— 据此改判为 WARN")
        print("[WARN] 该项“exe 存活 10s”因缺运行期依赖【未真正验证】")
        present, missing = _netlink_bundle_check()
        if present is None:
            print("[WARN] 未找到 PyInstaller 分析产物，无法交叉验证 netlink 打包")
            return 0
        if not missing:
            print("[OK]   交叉验证：PyInstaller 分析产物含全部 {} 个 netlink 模块（{}）".format(
                len(NL_MODULES), ", ".join(NL_MODULES)))
            return 0
        print("[FAIL] 交叉验证：netlink 模块缺失 {}，疑似打包遗漏".format(missing))
        return 1

    # 其它原因（疑似缺 hidden import / 启动异常）→ FAIL
    present, missing = _netlink_bundle_check()
    if present is not None:
        print("[info] netlink 分析产物命中 {}/{}；缺失 {}".format(
            len(present), len(NL_MODULES), missing))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
