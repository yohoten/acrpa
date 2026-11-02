# -*- coding: utf-8 -*-
"""入口拆分回归: `import ACRPA` 必须零副作用 (路线图 §9 阶段二第 1 项)。

背景:
    旧实现把整个 GUI 构建写死在 src/ACRPA.py 的模块级 —— `import ACRPA` 即建 Tk 根窗口、
    注册 after 定时器并启动后台线程, 导致 engine/commands/scriptdata 等纯逻辑无法在
    无头环境里被测试 (导入期即建窗 → 一切测试都被迫建窗)。
    现方案: ACRPA.py 退化为纯定义模块, 全部副作用收敛进 `ACRPA.build_app()`;
    `src/app.py` 为薄入口 (build()/main()), run.py 走 app.main()。

断言:
    I1 无副作用   : 子进程 `import ACRPA` 后 hasattr(ACRPA, 'root') 为 False (没建窗)
    I2 不阻塞     : 该 import 子进程在超时内正常退出 (不再建窗阻塞 / 不再挂定时器)
    I3 纯定义可用 : import 后 ACRPA 具备 build_app / main_run / stop_execution 等纯定义符号
    I4 薄入口     : src/app.py 提供 build()/main(); 仅 import app 不触发构建 (无 root)
    I5/I6 纯函数  : import script_validate / capabilities 不加载 tkinter/pyautogui
    I7 extensions : import extensions 及其子模块不加载 tkinter/pyautogui/script_package
                    (阶段二新增项②: 纯逻辑扩展管理器须 import 无副作用)

用法: .venv\\Scripts\\python.exe -X utf8 tools\\_test_import_side_effect_free.py
退出码: 0=全部通过, 1=存在失败
"""
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
PY = sys.executable

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _child(code, timeout=30):
    """在子进程里执行一段 python 代码 → (rc|None(超时), stdout, stderr)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([PY, "-X", "utf8", "-c", code],
                           cwd=BASE, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT after {}s".format(timeout)
    return (p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


def t_side_effect_free():
    print("\n── I1/I2  `import ACRPA` 无副作用且不阻塞 ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import ACRPA; "
            "print('HAS_ROOT=%s' % hasattr(ACRPA, 'root'))").format(src=SRC)
    rc, out, err = _child(code, timeout=30)
    check(rc == 0, "子进程 import ACRPA 在 30s 内正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    check("HAS_ROOT=False" in out,
          "import ACRPA 后无 root 属性 (未建 Tk 窗口)", out.strip())
    print("      child stdout: {}".format(out.strip().replace("\n", " ") or "(空)"))


def t_pure_defs_available():
    print("\n── I3  import 后纯定义符号可用 ──")
    # 执行核心 (main_run/stop_execution) 与执行栏转发别名 (阶段二第 3 项第 2 步)
    # 均须在 `import ACRPA` 后即可见: 执行核心仍定义于本文件, 转发别名指向 ui.exec_bar。
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import ACRPA; "
            "names=['build_app','main_run','stop_execution','_load_pil','_get_pa',"
            "'toggle_pause','_step_once','_fmt_dur','_update_exec_buttons',"
            "'select_script','_screenshot_tool','_shared_run','_shared_stop',"
            "'validate_script_cb']; "
            "print('MISSING=' + ','.join(n for n in names "
            "if not hasattr(ACRPA, n)))").format(src=SRC)
    rc, out, err = _child(code, timeout=30)
    check(rc == 0, "子进程探测纯定义符号正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    missing = ""
    for tok in out.split():
        if tok.startswith("MISSING="):
            missing = tok[len("MISSING="):]
    check(missing == "",
          "build_app / main_run / stop_execution / _load_pil / _get_pa / "
          "toggle_pause / _step_once / _fmt_dur / 执行栏转发别名 齐全",
          "缺失={!r}".format(missing))


def t_script_validate_pure():
    print("\n── I5  script_validate 纯函数: 不加载 GUI/pyautogui ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import script_validate; "
            "print('TK=%s' % ('tkinter' in sys.modules)); "
            "print('PA=%s' % ('pyautogui' in sys.modules))").format(src=SRC)
    rc, out, err = _child(code, timeout=30)
    check(rc == 0, "子进程 import script_validate 正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    check("TK=False" in out, "import script_validate 未加载 tkinter", out.strip())
    check("PA=False" in out, "import script_validate 未加载 pyautogui", out.strip())
    print("      child stdout: {}".format(out.strip().replace("\n", " ") or "(空)"))


def t_capabilities_pure():
    print("\n── I6  capabilities 纯函数: 不加载 GUI ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import capabilities; "
            "print('TK=%s' % ('tkinter' in sys.modules)); "
            "print('PA=%s' % ('pyautogui' in sys.modules))").format(src=SRC)
    rc, out, err = _child(code, timeout=30)
    check(rc == 0, "子进程 import capabilities 正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    check("TK=False" in out, "import capabilities 未加载 tkinter", out.strip())
    check("PA=False" in out, "import capabilities 未加载 pyautogui", out.strip())


def t_extensions_pure():
    print("\n── I7  extensions 纯逻辑: 不加载 GUI / script_package ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import extensions; "
            "import extensions.manifest, extensions.package, extensions.manager; "
            "print('TK=%s' % ('tkinter' in sys.modules)); "
            "print('PA=%s' % ('pyautogui' in sys.modules)); "
            "print('SP=%s' % ('script_package' in sys.modules)); "
            "print('HAS_INSTALL=%s' % callable(getattr(extensions, 'install', None)))").format(src=SRC)
    rc, out, err = _child(code, timeout=30)
    check(rc == 0, "子进程 import extensions 正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    check("TK=False" in out, "import extensions 未加载 tkinter", out.strip())
    check("PA=False" in out, "import extensions 未加载 pyautogui", out.strip())
    check("SP=False" in out, "import extensions 未加载 script_package (惰性复用)", out.strip())
    check("HAS_INSTALL=True" in out, "extensions.install 顶层 API 可用")
    print("      child stdout: {}".format(out.strip().replace("\n", " ") or "(空)"))


def t_thin_entry():
    print("\n── I4  薄入口 src/app.py ──")
    app_py = os.path.join(SRC, "app.py")
    check(os.path.exists(app_py), "src/app.py 存在")
    if os.path.exists(app_py):
        with open(app_py, encoding="utf-8") as f:
            src = f.read()
        check("def build(" in src, "app.py 提供 build()")
        check("def main(" in src, "app.py 提供 main()")
        check("build_app()" in src, "app.py 的 build() 调用 ACRPA.build_app()")

    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import app; "
            "print('APP_HAS_ACRPA=%s' % hasattr(app, 'ACRPA')); "
            "print('ACRPA_HAS_BUILD=%s' % hasattr(app.ACRPA, 'build_app')); "
            "print('APP_ROOT_ATTR=%s' % hasattr(app.ACRPA, 'root'))").format(src=SRC)
    rc, out, err = _child(code, timeout=30)
    check(rc == 0, "子进程 import app 正常退出 (rc={})".format(rc),
          (err or "").strip()[-200:])
    check("APP_HAS_ACRPA=True" in out, "app.ACRPA 可访问")
    check("ACRPA_HAS_BUILD=True" in out, "app.ACRPA.build_app 存在")
    check("APP_ROOT_ATTR=False" in out, "仅 import app 不建窗 (构建仍需显式 build())")


def main():
    print("=== import ACRPA 无副作用 回归 (入口拆分 / §9 阶段二第 1 项) ===")
    print("解释器: {}".format(PY))
    t_side_effect_free()
    t_pure_defs_available()
    t_script_validate_pure()
    t_capabilities_pure()
    t_extensions_pure()
    t_thin_entry()
    print("\n=== 结果: {} 通过 / {} 失败 ===".format(len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
