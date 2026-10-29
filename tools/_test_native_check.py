# -*- coding: utf-8 -*-
"""S1-01/S1-02 回归: ``native_check`` 静态检查 + 四个 probe 的"假阳性 READY"消除。

背景
----
能力探测曾出现"假就绪": 只要 ``PaddleOCR.dll`` / ``dd63330.dll`` 在盘上就 READY,
但第一次真正调用必然失败 (缺 4 个原生依赖 / 缺模型目录 / 路径含中文 / DLL 是 32 位)。
本测试同时锁两件事:

  ① ``src/native_check.py`` 的四个公开函数按契约返回 (且**绝不加载 DLL**);
  ② ``src/capabilities.py`` 的四个 probe 在**构造的残缺环境**里不再假 READY。

断言
----
  N1 pe_machine  : 真实 x64 PE → 0x8664；纯文本/空文件/不存在/目录/伪 MZ → None
  N2 is_x64      : x64 / 32 位构造 / 非法文件 三分支文案
  N3 dll_loadable: 缺依赖列出文件名；齐全 → True；路径不存在 → False
  N4 不加载 DLL  : 源码无 LoadLibrary/WinDLL/CDLL/ctypes 调用 (静态证明)
  N5 dir_ascii   : 中文路径判假、ASCII 路径判真
  N6 probe 构造  : 裸 PaddleOCR.dll → BROKEN 且 reason 含 4 个缺文件名;
                   32 位 dd63330.dll → BROKEN; 冻结环境 cv.match reason 含
                   「冻结版不含 cv2」; playwright 包在但浏览器未装 → BROKEN
  N7 零副作用    : 子进程 import capabilities 后 sys.modules 无
                   tkinter/pyautogui/engine/paddle_dll
  N8 常量同步    : ast 静态解析 src/paddle_dll.py, 与 capabilities 的只读清单逐项相等

约束: **构造环境只用临时目录 + 注入路径/属性**, 绝不改动仓库里真实的 ``lib/`` 文件。

用法: python -X utf8 tools\\_test_native_check.py
退出码: 0=全部通过, 1=存在失败
"""
import ast
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import capabilities                                        # noqa: E402
import native_check                                        # noqa: E402

_PASS, _FAIL, _SKIP = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def skip(msg, detail=""):
    _SKIP.append(msg)
    print("[SKIP] {}{}".format(msg, "  ({})".format(detail) if detail else ""))


_MISSING = object()


def _set_attrs(mod, **kv):
    """临时设置模块属性 → 返回可用于还原的 dict。"""
    old = {}
    for k, v in kv.items():
        old[k] = getattr(mod, k, _MISSING)
        setattr(mod, k, v)
    return old


def _restore_attrs(mod, old):
    for k, v in old.items():
        if v is _MISSING:
            try:
                delattr(mod, k)
            except Exception:
                pass
        else:
            setattr(mod, k, v)


def _make_pe(path, machine):
    """构造最小 PE 头 (MZ + e_lfanew→PE\\0\\0 + Machine), 仅够 pe_machine 解析。"""
    e_lfanew = 0x80
    buf = bytearray(b"\x00" * 0x100)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf[e_lfanew:e_lfanew + 4] = b"PE\x00\x00"
    struct.pack_into("<H", buf, e_lfanew + 4, machine)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "wb") as f:
        f.write(bytes(buf))
    return path


# ══════════════════════════════════════════════════════════════════════
# N1 / N2  PE 头
# ══════════════════════════════════════════════════════════════════════
def t_pe_machine(tmp):
    print("\n── N1 pe_machine: 真实 x64 PE / 非法输入 ──")
    real = os.path.join(BASE, "lib", "dd_driver", "dd63330.dll")
    m = native_check.pe_machine(real)
    check(m == native_check.PE_MACHINE_X64,
          "真实 x64 PE lib/dd_driver/dd63330.dll → Machine 0x8664",
          "实际 {}".format("None" if m is None else hex(m)))

    txt = os.path.join(tmp, "plain.txt")
    with io.open(txt, "w", encoding="utf-8") as f:
        f.write("这不是 PE 文件\n")
    check(native_check.pe_machine(txt) is None, "纯文本文件 → None (不抛异常)")

    empty = os.path.join(tmp, "empty.bin")
    open(empty, "wb").close()
    check(native_check.pe_machine(empty) is None, "空文件 → None")

    check(native_check.pe_machine(os.path.join(tmp, "no_such.dll")) is None,
          "不存在的路径 → None")
    check(native_check.pe_machine(tmp) is None, "目录路径 → None")

    mz_only = os.path.join(tmp, "mz_only.dll")
    with open(mz_only, "wb") as f:
        f.write(b"MZ" + b"\x00" * 30)
    check(native_check.pe_machine(mz_only) is None, "只有 MZ 头 (不足 0x40) → None")

    fake_sig = os.path.join(tmp, "bad_sig.dll")
    buf = bytearray(b"\x00" * 0x100)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)      # 指向 0x80, 但那里不是 "PE\0\0"
    with open(fake_sig, "wb") as f:
        f.write(bytes(buf))
    check(native_check.pe_machine(fake_sig) is None,
          "e_lfanew 指向处无 PE 签名 → None")

    print("\n── N2 is_x64: 三分支 ──")
    ok, why = native_check.is_x64(real)
    check(ok is True and "x64" in why, "x64 真实文件 → (True, 含 x64)", why)

    p32 = _make_pe(os.path.join(tmp, "fake32.dll"), native_check.PE_MACHINE_X86)
    ok32, why32 = native_check.is_x64(p32)
    check(ok32 is False and "32 位" in why32,
          "32 位构造 PE → (False, 含「32 位」)", why32)

    okn, whyn = native_check.is_x64(txt)
    check(okn is False and "不是有效的 PE" in whyn,
          "非法文件 → (False, 含「不是有效的 PE」)", whyn)

    pa, wha = native_check.is_x64(_make_pe(os.path.join(tmp, "arm64.dll"),
                                          native_check.PE_MACHINE_ARM64))
    check(pa is False and "ARM64" in wha,
          "ARM64 构造 PE → (False, 含 ARM64 说明)", wha)


# ══════════════════════════════════════════════════════════════════════
# N3 / N4  dll_loadable + 绝不加载 DLL
# ══════════════════════════════════════════════════════════════════════
def t_dll_loadable(tmp):
    print("\n── N3 dll_loadable: 只做静态存在性检查 ──")
    d = os.path.join(tmp, "dll_dir")
    os.makedirs(d)
    main = _make_pe(os.path.join(d, "PaddleOCR.dll"), native_check.PE_MACHINE_X64)
    deps = ("paddle_inference.dll", "tbb12.dll", "yaml-cpp.dll", "opencv_world470.dll")

    ok, why = native_check.dll_loadable(main, deps)
    check(ok is False, "缺全部依赖 → False")
    check(all(x in why for x in deps),
          "reason 逐个列出 4 个缺失文件名", why)

    partial = os.path.join(d, "tbb12.dll")
    _make_pe(partial, native_check.PE_MACHINE_X64)
    ok2, why2 = native_check.dll_loadable(d, deps)
    check(ok2 is False and "tbb12.dll" not in why2
          and "paddle_inference.dll" in why2,
          "补上 tbb12.dll 后只报其余 3 个", why2)

    for name in ("paddle_inference.dll", "yaml-cpp.dll", "opencv_world470.dll"):
        _make_pe(os.path.join(d, name), native_check.PE_MACHINE_X64)
    ok3, why3 = native_check.dll_loadable(d, deps)
    check(ok3 is True, "依赖齐全 → True", why3)
    check(native_check.dll_loadable(os.path.join(tmp, "nope"), deps)[0] is False,
          "路径不存在 → False")
    check(native_check.dll_loadable(d, ())[0] is True, "无依赖清单 → True")

    print("\n── N4 静态证明: 全程未加载 DLL ──")
    # 用 ast 而不是子串扫描: 注释/文档里出现 "LoadLibrary" 是在**说明**为何不加载,
    # 真正要证明的是"代码里没有调用/引用"。Import 名单 + Name/Attribute 集合即可定论。
    with io.open(os.path.join(SRC, "native_check.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="native_check.py")
    mods, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    check("ctypes" not in mods,
          "native_check 未 import ctypes (无加载 DLL 的能力)", str(sorted(mods)))
    hits = sorted(names & {"LoadLibrary", "LoadLibraryW", "LoadLibraryA",
                           "WinDLL", "CDLL", "windll", "ctypes"})
    check(not hits, "AST 中无 LoadLibrary/WinDLL/CDLL 调用或引用", str(hits))
    check("ctypes" not in native_check.__dict__,
          "运行时 native_check 未持有 ctypes 名字")

    print("\n── N5 dir_ascii_only: 中文路径 / ASCII 路径 ──")
    cn = os.path.join(tmp, "中文目录")
    os.makedirs(cn, exist_ok=True)
    ok_cn, why_cn = native_check.dir_ascii_only(cn)
    check(ok_cn is False and "非 ASCII" in why_cn,
          "中文路径 → (False, 含「非 ASCII」)", why_cn)
    ok_en, why_en = native_check.dir_ascii_only(d)
    check(ok_en is True, "ASCII 路径 → True", why_en)
    check(native_check.dir_ascii_only("")[0] is False, "空路径 → False")


# ══════════════════════════════════════════════════════════════════════
# N6  四个 probe 的"假阳性"构造环境 (仅临时目录 / 注入属性)
# ══════════════════════════════════════════════════════════════════════
def t_probe_paddle(tmp):
    print("\n── N6-a 只放裸 PaddleOCR.dll → ocr.paddle_dll 应为 BROKEN ──")
    import state
    dll_dir = os.path.join(tmp, "paddle_ocr")
    _make_pe(os.path.join(dll_dir, "PaddleOCR.dll"), native_check.PE_MACHINE_X64)
    old = _set_attrs(state, PADDLE_DLL_ENABLED=True,
                     PADDLE_DLL_DIR=dll_dir, PADDLE_DLL_MODEL_DIR="")
    try:
        capabilities.refresh("ocr.paddle_dll")
        r = capabilities.probe("ocr.paddle_dll")
    finally:
        _restore_attrs(state, old)
        capabilities.refresh("ocr.paddle_dll")
    check(r.state == capabilities.CapState.BROKEN,
          "裸 DLL (无依赖/无模型) → BROKEN", "实际 {}".format(r.state))
    lacked = [d for d in capabilities.PADDLE_DEP_DLLS if d not in r.reason]
    check(not lacked, "reason 列出全部 4 个缺失依赖文件名", "漏: {}".format(lacked))
    check("模型" in r.reason, "reason 同时指出模型不可用")
    print("      reason: {}".format(r.reason))


def t_probe_dd(tmp):
    print("\n── N6-b 32 位 dd63330.dll → input.dd 应为 BROKEN ──")
    import state
    app = os.path.join(tmp, "app32")
    _make_pe(os.path.join(app, "lib", "dd_driver", "dd63330.dll"),
             native_check.PE_MACHINE_X86)
    old_state = _set_attrs(state, INPUT_MODE="dd", USE_DD_DRIVER=True)
    old_app = capabilities._APP_DIR
    try:
        capabilities._APP_DIR = app
        capabilities.refresh("input.dd")
        r32 = capabilities.probe("input.dd")
    finally:
        capabilities._APP_DIR = old_app
        _restore_attrs(state, old_state)
        capabilities.refresh("input.dd")
    check(r32.state == capabilities.CapState.BROKEN,
          "32 位同名 DLL → BROKEN", "实际 {}".format(r32.state))
    check("32 位" in r32.reason, "reason 含「32 位」", r32.reason)
    print("      reason: {}".format(r32.reason))

    # 对照: 换成 x64 构造文件 → 不再 BROKEN (证明不是"一律 BROKEN")
    app64 = os.path.join(tmp, "app64")
    _make_pe(os.path.join(app64, "lib", "dd_driver", "dd63330.dll"),
             native_check.PE_MACHINE_X64)
    old_state = _set_attrs(state, INPUT_MODE="dd", USE_DD_DRIVER=True)
    try:
        capabilities._APP_DIR = app64
        capabilities.refresh("input.dd")
        r64 = capabilities.probe("input.dd")
    finally:
        capabilities._APP_DIR = old_app
        _restore_attrs(state, old_state)
        capabilities.refresh("input.dd")
    check(r64.state == capabilities.CapState.READY,
          "同目录换 x64 构造文件 → READY (位数校验真的在生效)",
          "实际 {}".format(r64.state))


def t_probe_cv_frozen():
    print("\n── N6-c 冻结环境 → cv.match reason 应含「冻结版不含 cv2」──")
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", _MISSING)
    old_meipass = getattr(sys, "_MEIPASS", _MISSING)
    try:
        sys.frozen = True
        capabilities.refresh("cv.match")
        r = capabilities.probe("cv.match")
    finally:
        if old_frozen is _MISSING:
            try:
                del sys.frozen
            except Exception:
                pass
        else:
            sys.frozen = old_frozen
        if old_meipass is _MISSING:
            try:
                del sys._MEIPASS
            except Exception:
                pass
        else:
            sys._MEIPASS = old_meipass
        capabilities.refresh("cv.match")
    if "由扩展" in r.reason:
        skip("冻结环境 cv.match 断言 (本机装了 cv.match 扩展, 走到①分支)", r.reason)
        return
    check("冻结版不含 cv2" in r.reason, "冻结环境 reason 含「冻结版不含 cv2」", r.reason)
    print("      state/reason: {} / {}".format(r.state.value, r.reason))


def t_probe_playwright(tmp):
    print("\n── N6-d playwright 包装了但浏览器未装 → BROKEN ──")
    fake_sync = types.ModuleType("playwright.sync_api")
    fake_pw = types.ModuleType("playwright")
    fake_pw.sync_api = fake_sync
    saved_mods = {k: sys.modules.get(k, _MISSING)
                  for k in ("playwright", "playwright.sync_api")}
    empty = os.path.join(tmp, "pw_empty")
    os.makedirs(empty, exist_ok=True)
    saved_env = {k: os.environ.get(k, _MISSING)
                 for k in ("PLAYWRIGHT_BROWSERS_PATH", "LOCALAPPDATA",
                           "HOME", "USERPROFILE")}
    try:
        sys.modules["playwright"] = fake_pw
        sys.modules["playwright.sync_api"] = fake_sync
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = empty
        os.environ["LOCALAPPDATA"] = empty
        os.environ["HOME"] = empty
        os.environ["USERPROFILE"] = empty
        capabilities.refresh("browser.playwright")
        r = capabilities.probe("browser.playwright")
    finally:
        for k, v in saved_mods.items():
            if v is _MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        for k, v in saved_env.items():
            if v is _MISSING:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        capabilities.refresh("browser.playwright")
    check(r.state == capabilities.CapState.BROKEN,
          "包可导入但无浏览器 → BROKEN", "实际 {}".format(r.state))
    check("playwright install chromium" in r.reason,
          "reason 给出 `playwright install chromium` 修复指引", r.reason)
    print("      reason: {}".format(r.reason))

    # 对照: 目录里出现 chromium-* → 静态判定为已安装
    have = os.path.join(tmp, "pw_have")
    os.makedirs(os.path.join(have, "chromium-9999"), exist_ok=True)
    saved_env = {k: os.environ.get(k, _MISSING)
                 for k in ("PLAYWRIGHT_BROWSERS_PATH", "LOCALAPPDATA",
                           "HOME", "USERPROFILE")}
    try:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = have
        os.environ["LOCALAPPDATA"] = have
        os.environ["HOME"] = have
        os.environ["USERPROFILE"] = have
        ok, where = capabilities._playwright_browser_present()
    finally:
        for k, v in saved_env.items():
            if v is _MISSING:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check(ok is True and "chromium-9999" in where,
          "目录含 chromium-9999 → 判为已安装", where)


# ══════════════════════════════════════════════════════════════════════
# N7 / N8  零副作用 + 常量同步
# ══════════════════════════════════════════════════════════════════════
def t_side_effect_free():
    print("\n── N7 import capabilities 后 sys.modules 干净 ──")
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import capabilities; "
            "print('TK=%s' % ('tkinter' in sys.modules)); "
            "print('PA=%s' % ('pyautogui' in sys.modules)); "
            "print('ENGINE=%s' % ('engine' in sys.modules)); "
            "print('PADDLE=%s' % ('paddle_dll' in sys.modules)); "
            "print('DD=%s' % ('dd_backend' in sys.modules))").format(src=SRC)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=BASE,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=60)
    out = (p.stdout or b"").decode("utf-8", "replace")
    check(p.returncode == 0, "子进程 import capabilities 正常退出 (rc={})".format(
        p.returncode), (p.stderr or b"").decode("utf-8", "replace")[-300:])
    check("TK=False" in out, "无 tkinter", out.strip().replace("\n", " "))
    check("PA=False" in out, "无 pyautogui")
    check("ENGINE=False" in out, "无 engine")
    check("PADDLE=False" in out, "无 paddle_dll (探测不拉起重依赖)")
    check("DD=False" in out, "无 dd_backend")


def t_consts_in_sync():
    """N8 三处同源（S1-03 口径调整）。

    旧口径: ast 解析 ``paddle_dll.py`` 的 4 个**字面量**, 与 ``capabilities`` 逐项比对
    （"两处各写一份, 靠测试防漂移"）。
    新口径: 权威字面量**只在** ``src/ocr_consts.py`` 一处; ``paddle_dll`` 与
    ``capabilities`` 都 ``import`` 同一批对象。因此本测试改为锁定三件事:
      ① ``ocr_consts`` 的 4 个字面量与 ``capabilities.PADDLE_*`` 逐项相等;
      ② ``paddle_dll.py`` **不再自持**这 4 个名字的字面量, 且必须从 ``ocr_consts``
         import 它们（防止有人把清单又抄回去）;
      ③ 运行时 ``paddle_dll.DEP_DLLS is capabilities.PADDLE_DEP_DLLS``（**同一对象**,
         而不是值相等）—— 子进程验证, 避免把 tkinter 拉进本测试进程。
    """
    print("\n── N8 依赖清单三处同源 (ocr_consts 单一定义源, S1-03) ──")
    wanted = ("DLL_NAME", "DEP_DLLS", "MODEL_FILES", "MODEL_FILES_LEGACY")

    # ① 权威字面量在 src/ocr_consts.py
    path = os.path.join(SRC, "ocr_consts.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            name = getattr(tgt, "id", None)
            if name in wanted:
                try:
                    found[name] = ast.literal_eval(node.value)
                except Exception:
                    found[name] = None
    check(set(found) == set(wanted), "ocr_consts.py 解析出 4 个清单常量",
          "缺: {}".format(sorted(set(wanted) - set(found))))
    check(found.get("DLL_NAME") == capabilities.PADDLE_DLL_NAME,
          "DLL_NAME 同步", "{} vs {}".format(found.get("DLL_NAME"),
                                          capabilities.PADDLE_DLL_NAME))
    check(tuple(found.get("DEP_DLLS") or ()) == tuple(capabilities.PADDLE_DEP_DLLS),
          "DEP_DLLS 同步", "{} vs {}".format(found.get("DEP_DLLS"),
                                          capabilities.PADDLE_DEP_DLLS))
    check(tuple(found.get("MODEL_FILES") or ()) ==
          tuple(capabilities.PADDLE_MODEL_FILES),
          "MODEL_FILES 同步", "{} vs {}".format(found.get("MODEL_FILES"),
                                             capabilities.PADDLE_MODEL_FILES))
    check(tuple(found.get("MODEL_FILES_LEGACY") or ()) ==
          tuple(capabilities.PADDLE_MODEL_FILES_LEGACY),
          "MODEL_FILES_LEGACY 同步",
          "{} vs {}".format(found.get("MODEL_FILES_LEGACY"),
                            capabilities.PADDLE_MODEL_FILES_LEGACY))

    # ② paddle_dll 不得再把清单抄回自己文件里
    pdll_path = os.path.join(SRC, "paddle_dll.py")
    with io.open(pdll_path, encoding="utf-8") as f:
        ptree = ast.parse(f.read(), filename=pdll_path)
    imported, literals = set(), []
    for node in ptree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "ocr_consts":
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                name = getattr(tgt, "id", None)
                if name in wanted:
                    literals.append(name)
    check(set(wanted) <= imported,
          "paddle_dll.py 从 ocr_consts import 四个名字",
          "缺: {}".format(sorted(set(wanted) - imported)))
    check(not literals,
          "paddle_dll.py 不再有第二份清单字面量（旧口径的漂移面已消除）",
          "仍在: {}".format(literals))

    # ③ 运行时同一对象（子进程: paddle_dll 经 utils 拉 tkinter, 不污染本进程）
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import capabilities, ocr_consts, paddle_dll; "
            "pairs=[('DLL_NAME','PADDLE_DLL_NAME'),"
            "('DEP_DLLS','PADDLE_DEP_DLLS'),"
            "('MODEL_FILES','PADDLE_MODEL_FILES'),"
            "('MODEL_FILES_LEGACY','PADDLE_MODEL_FILES_LEGACY')]; "
            "print('SAME=' + ','.join(a for a, b in pairs "
            "if getattr(paddle_dll, a) is getattr(capabilities, b) "
            "and getattr(ocr_consts, a) is getattr(paddle_dll, a)))").format(src=SRC)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=BASE,
                           env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        rc = p.returncode
        err = (p.stderr or b"").decode("utf-8", "replace")
    except Exception as e:                 # noqa: BLE001
        out, rc, err = "", -1, repr(e)
    same = ""
    for tok in out.split():
        if tok.startswith("SAME="):
            same = tok[len("SAME="):]
    check(rc == 0, "子进程 import paddle_dll/capabilities 正常退出 (rc={})".format(rc),
          err.strip()[-200:])
    check(same == ",".join(wanted),
          "paddle_dll / capabilities / ocr_consts 三处是同一批对象 (is)",
          "同一={!r}".format(same))


def t_probe_contracts():
    print("\n── N9 四个 probe 均不抛异常并返回 ProbeResult ──")
    for cid in ("ocr.paddle_dll", "input.dd", "cv.match", "browser.playwright"):
        try:
            capabilities.refresh(cid)
            r = capabilities.probe(cid)
            ok = isinstance(r, capabilities.ProbeResult)
            msg = ""
        except Exception as e:                     # noqa: BLE001
            ok, r, msg = False, None, repr(e)
        check(ok, "probe({!r}) 返回 ProbeResult".format(cid), msg)


def main():
    print("=" * 68)
    print("S1-01/S1-02 回归: native_check + 四个 probe 假阳性消除")
    print("=" * 68)
    tmp = tempfile.mkdtemp(prefix="acrpa_nc_")
    try:
        t_pe_machine(tmp)
        t_dll_loadable(tmp)
        t_probe_paddle(tmp)
        t_probe_dd(tmp)
        t_probe_cv_frozen()
        t_probe_playwright(tmp)
        t_side_effect_free()
        t_consts_in_sync()
        t_probe_contracts()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("=" * 68)
    print("结果: {} 通过 / {} 失败 / {} 跳过".format(
        len(_PASS), len(_FAIL), len(_SKIP)))
    print("=" * 68)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
