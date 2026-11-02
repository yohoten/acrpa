# -*- coding: utf-8 -*-
"""S1-03 回归: ``ocr_consts`` 单一定义源 + ``paddle_dll.self_check()`` + 零副作用。

断言
----
  O1 三处同源   : ``src/ocr_consts.py`` 的字面量 == ``capabilities.PADDLE_*``;
                  子进程里 ``paddle_dll.DEP_DLLS is capabilities.PADDLE_DEP_DLLS``
                  且 ``ocr_consts.DEP_DLLS is paddle_dll.DEP_DLLS``（**同一对象**,
                  不是"逐项相等"）
  O2 唯一清单   : ``src/`` 下**只有** ``ocr_consts.py`` 含依赖/模型清单的
                  tuple/list 字面量（``capabilities`` / ``paddle_dll`` 已改为 import）
  O3 self_check : 裸 ``PaddleOCR.dll`` → ``("broken", 原因, 依赖+模型文件名)``;
                  依赖缺失而模型齐全 → ``missing`` 恰为 4 个依赖名;
                  依赖+模型齐全 + ASCII 路径 → ``("ready", …, [])``;
                  非 ASCII 目录 → ``("broken", 原因含 ASCII)``;
                  目录里没有 ``PaddleOCR.dll`` → ``("missing", …)``
  O4 不加载 DLL : 运行期把 ``ctypes.WinDLL/CDLL/OleDLL/windll/LoadLibrary`` 全换成
                  "一调就抛", 上述分支仍全部正常返回; 外加 ast 静态证明
  O5 逐项清单   : ``self_check_report()`` 逐文件 ok 标记与目录实况一致
  O6 零副作用   : ``import ocr_consts`` / ``import capabilities`` 后 ``sys.modules``
                  无 ``tkinter`` / ``pyautogui`` / ``engine`` / ``paddle_dll``

约束: 构造环境只用 ``tempfile`` 临时目录 + 注入路径, 绝不改动仓库真实 ``lib/``
下任何文件; 全程**不加载**任何 DLL。

用法: python -X utf8 tools\\_test_ocr_selfcheck.py
退出码: 0=全部通过, 1=存在失败
"""
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import capabilities                                        # noqa: E402
import ocr_consts                                          # noqa: E402

_PASS, _FAIL, _SKIP = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def skip(msg, detail=""):
    _SKIP.append(msg)
    print("[SKIP] {}{}".format(msg, "  ({})".format(detail) if detail else ""))


def _child(code, args=(), timeout=90):
    """在子进程里跑一段 python 代码 → (rc, stdout, stderr)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-X", "utf8", "-c", code] + [str(a) for a in args]
    try:
        p = subprocess.run(cmd, cwd=BASE, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT after {}s".format(timeout)
    return (p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


def _write(path, data=b"PADDLE-PLACEHOLDER"):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "wb") as f:
        f.write(data)
    return path


_MARK = "@@RESULT@@"

# 子进程脚本: ① 三处同源(同一对象) ② self_check 各分支(加载入口已全部禁用)
# ③ self_check_report 逐项清单。
_CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import ctypes
import capabilities
import ocr_consts

out = {}

pairs = [("DLL_NAME", "PADDLE_DLL_NAME"),
         ("DEP_DLLS", "PADDLE_DEP_DLLS"),
         ("MODEL_FILES", "PADDLE_MODEL_FILES"),
         ("MODEL_FILES_LEGACY", "PADDLE_MODEL_FILES_LEGACY")]
ident = {}
ident["consts_vs_caps"] = all(
    getattr(ocr_consts, a) is getattr(capabilities, b) for a, b in pairs)
try:
    import paddle_dll
    ident["consts_vs_paddle"] = all(
        getattr(ocr_consts, a) is getattr(paddle_dll, a) for a, _ in pairs)
    ident["paddle_vs_caps"] = all(
        getattr(paddle_dll, a) is getattr(capabilities, b) for a, b in pairs)

    def _boom(*a, **k):
        raise AssertionError("self_check 内不允许加载 DLL")

    ctypes.WinDLL = _boom
    ctypes.CDLL = _boom
    ctypes.windll = _boom
    ctypes.LoadLibrary = _boom
    try:
        ctypes.OleDLL = _boom
    except Exception:
        pass

    cases = json.loads(sys.argv[2])
    got = {}
    for name, kw in cases.items():
        try:
            st, reason, missing = paddle_dll.self_check(**kw)
            got[name] = {"state": st, "reason": reason, "missing": list(missing)}
        except Exception as e:
            got[name] = {"error": "%s: %s" % (type(e).__name__, e)}
    out["self_check"] = got

    for tag in ("full", "deps_missing"):
        try:
            rep = paddle_dll.self_check_report(**cases[tag])
            out["report_" + tag] = {
                "state": rep["state"],
                "reason": rep["reason"],
                "items": [[i["kind"], i["name"], bool(i["ok"])]
                          for i in rep["items"]],
            }
        except Exception as e:
            out["report_" + tag] = {"error": "%s: %s" % (type(e).__name__, e)}
except Exception as e:
    ident["error"] = "%s: %s" % (type(e).__name__, e)

out["ident"] = ident
print("@@RESULT@@" + json.dumps(out, ensure_ascii=False))
'''


# ══════════════════════════════════════════════════════════════════════
# O1 / O2 单一定义源
# ══════════════════════════════════════════════════════════════════════
def _module_literals(path, wanted):
    """ast 静态解析 → {name: literal}（只收字面量赋值）。"""
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
    return found


def t_single_source():
    print("\n── O1 三处同源: ocr_consts / capabilities / paddle_dll ──")
    wanted = ("DLL_NAME", "DEP_DLLS", "MODEL_FILES", "MODEL_FILES_LEGACY")
    lit = _module_literals(os.path.join(SRC, "ocr_consts.py"), wanted)
    check(set(lit) == set(wanted), "ocr_consts.py 有 4 个清单字面量",
          "缺: {}".format(sorted(set(wanted) - set(lit))))
    check(lit.get("DLL_NAME") == capabilities.PADDLE_DLL_NAME,
          "ocr_consts.DLL_NAME == capabilities.PADDLE_DLL_NAME",
          repr(lit.get("DLL_NAME")))
    check(tuple(lit.get("DEP_DLLS") or ()) == tuple(capabilities.PADDLE_DEP_DLLS),
          "ocr_consts.DEP_DLLS == capabilities.PADDLE_DEP_DLLS",
          "{} vs {}".format(lit.get("DEP_DLLS"), capabilities.PADDLE_DEP_DLLS))
    check(tuple(lit.get("MODEL_FILES") or ()) ==
          tuple(capabilities.PADDLE_MODEL_FILES),
          "ocr_consts.MODEL_FILES == capabilities.PADDLE_MODEL_FILES")
    check(tuple(lit.get("MODEL_FILES_LEGACY") or ()) ==
          tuple(capabilities.PADDLE_MODEL_FILES_LEGACY),
          "ocr_consts.MODEL_FILES_LEGACY == capabilities.PADDLE_MODEL_FILES_LEGACY")
    # 进程内已经是同一对象（capabilities 直接 import 了 ocr_consts）
    check(capabilities.PADDLE_DEP_DLLS is ocr_consts.DEP_DLLS,
          "进程内 capabilities.PADDLE_DEP_DLLS is ocr_consts.DEP_DLLS")
    check(capabilities.PADDLE_MODEL_FILES is ocr_consts.MODEL_FILES,
          "进程内 capabilities.PADDLE_MODEL_FILES is ocr_consts.MODEL_FILES")


def t_no_duplicate_literals():
    print("\n── O2 全库唯一字面量清单（src/ 下不得有第二份） ──")
    dep_set = set(ocr_consts.DEP_DLLS)
    model_set = set(ocr_consts.MODEL_FILES) | set(ocr_consts.MODEL_FILES_LEGACY)
    offenders = []
    scanned = 0
    for root, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            scanned += 1
            try:
                with io.open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=path)
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Tuple, ast.List)):
                    continue
                names = [e.value for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                dep_hit = len([n for n in names if n in dep_set])
                mod_hit = len([n for n in names if n in model_set])
                if dep_hit >= 2 or mod_hit >= 2:
                    rel = os.path.relpath(path, BASE)
                    offenders.append("{}(:{})".format(rel, getattr(node, "lineno", "?")))
    only_consts = bool(offenders) and all(
        o.startswith("src\\ocr_consts.py") or o.startswith("src/ocr_consts.py")
        for o in offenders)
    check(only_consts,
          "只有 src/ocr_consts.py 含清单字面量（扫描 {} 个 .py）".format(scanned),
          "命中: {}".format(", ".join(offenders)))


# ══════════════════════════════════════════════════════════════════════
# O3 / O4 / O5 self_check 各分支（临时目录构造 + 加载入口全禁用）
# ══════════════════════════════════════════════════════════════════════
def _make_env(root, name, with_dll=True, with_deps=True, with_model=True,
              legacy=False):
    d = os.path.join(root, name)
    os.makedirs(d)
    if with_dll:
        _write(os.path.join(d, ocr_consts.DLL_NAME))
    if with_deps:
        for dep in ocr_consts.DEP_DLLS:
            _write(os.path.join(d, dep))
    if with_model:
        for f in (ocr_consts.MODEL_FILES_LEGACY if legacy else
                  ocr_consts.MODEL_FILES):
            _write(os.path.join(d, f))
    return d


def t_self_check(tmp):
    print("\n── O3 self_check 分支（构造临时目录，不含仓库 lib/） ──")
    full = _make_env(tmp, "full_ocr")
    deps_missing = _make_env(tmp, "deps_missing_ocr", with_deps=False)
    bare = _make_env(tmp, "bare_ocr", with_deps=False, with_model=False)
    legacy = _make_env(tmp, "legacy_ocr", legacy=True)
    nonascii = _make_env(tmp, "OCR中文路径", legacy=False)
    nodll = _make_env(tmp, "nodll_ocr", with_dll=False, with_deps=False,
                      with_model=False)

    cases = {
        "full": {"dll_dir": full, "model_dir": full},
        "deps_missing": {"dll_dir": deps_missing, "model_dir": deps_missing},
        "bare": {"dll_dir": bare, "model_dir": bare},
        "legacy": {"dll_dir": legacy, "model_dir": legacy},
        "nonascii": {"dll_dir": nonascii, "model_dir": nonascii},
        "nodll": {"dll_dir": nodll, "model_dir": nodll},
    }
    rc, out, err = _child(_CHILD, (SRC, json.dumps(cases, ensure_ascii=False)))
    check(rc == 0, "子进程执行 self_check 正常退出 (rc={})".format(rc),
          (err or "").strip()[-300:])
    idx = out.find(_MARK)
    if idx < 0:
        check(False, "子进程返回结果标记", (out or "").strip()[-200:])
        return {}
    res = json.loads(out[idx + len(_MARK):])

    # ── 三处同源（子进程内的对象身份） ──
    ident = res.get("ident") or {}
    check(ident.get("consts_vs_caps") is True,
          "子进程: ocr_consts 与 capabilities 同一批对象", repr(ident.get("consts_vs_caps")))
    check(ident.get("consts_vs_paddle") is True,
          "子进程: ocr_consts 与 paddle_dll 同一批对象", repr(ident.get("consts_vs_paddle")))
    check(ident.get("paddle_vs_caps") is True,
          "子进程: paddle_dll.DEP_DLLS is capabilities.PADDLE_DEP_DLLS 等 4 项成立",
          repr(ident.get("paddle_vs_caps")) + (ident.get("error") or ""))

    got = res.get("self_check") or {}
    for name in cases:
        if "error" in (got.get(name) or {}):
            check(False, "self_check({}) 未抛异常".format(name), got[name]["error"])

    full_r = got.get("full") or {}
    check(full_r.get("state") == "ready",
          "依赖+模型齐全 + ASCII 路径 → ready",
          "{!r} / {}".format(full_r.get("state"), (full_r.get("reason") or "")[:120]))
    check(full_r.get("missing") == [], "ready 时 missing 为空",
          repr(full_r.get("missing")))

    dm = got.get("deps_missing") or {}
    check(dm.get("state") == "broken", "缺 4 个依赖（模型齐全）→ broken",
          "{!r} / {}".format(dm.get("state"), (dm.get("reason") or "")[:120]))
    check(tuple(dm.get("missing") or ()) == tuple(ocr_consts.DEP_DLLS),
          "missing 恰为 4 个依赖文件名", repr(dm.get("missing")))
    check(all(d in (dm.get("reason") or "") for d in ocr_consts.DEP_DLLS),
          "reason 逐个列出缺失的依赖名")

    bare_r = got.get("bare") or {}
    check(bare_r.get("state") == "broken",
          "只有裸 PaddleOCR.dll → broken", "{!r}".format(bare_r.get("state")))
    bmiss = list(bare_r.get("missing") or [])
    check(all(d in bmiss for d in ocr_consts.DEP_DLLS),
          "裸 DLL 的 missing 含 4 个依赖名", repr(bmiss))
    check(all(f in bmiss for f in ocr_consts.MODEL_FILES),
          "裸 DLL 的 missing 同时含模型文件名（模型也确实没有）", repr(bmiss))

    lg = got.get("legacy") or {}
    check(lg.get("state") == "ready", "旧版模型文件（.pdmodel/.pdiparams/.yml）→ ready",
          "{!r} / {}".format(lg.get("state"), (lg.get("reason") or "")[:120]))

    na = got.get("nonascii") or {}
    check(na.get("state") == "broken", "非 ASCII 目录 → broken",
          "{!r}".format(na.get("state")))
    check("ASCII" in (na.get("reason") or ""),
          "非 ASCII 的 reason 可读（含「ASCII」与修复建议）",
          (na.get("reason") or "")[:160])
    check(na.get("missing") == [], "非 ASCII 时 missing 不含文件（文件本身都在）",
          repr(na.get("missing")))

    nd = got.get("nodll") or {}
    check(nd.get("state") == "missing", "目录里没有 PaddleOCR.dll → missing",
          "{!r}".format(nd.get("state")))
    check(list(nd.get("missing") or []) == [ocr_consts.DLL_NAME],
          "missing 状态列出缺失的主模块文件名", repr(nd.get("missing")))

    # ── O5 逐项清单 ──
    print("\n── O5 self_check_report 逐项清单 ──")
    rf = res.get("report_full") or {}
    items = rf.get("items") or []
    kinds = [i[0] for i in items]
    check(rf.get("state") == "ready", "ready 环境 report.state == ready",
          repr(rf.get("state")))
    check(all(i[2] for i in items if i[0] in ("dll", "dep", "model")),
          "ready 环境: dll/dep/model 逐项 ok=True", repr(items))
    check(kinds.count("dep") == len(ocr_consts.DEP_DLLS),
          "清单逐个列出 {} 个依赖".format(len(ocr_consts.DEP_DLLS)), repr(kinds))
    check(kinds.count("model") == len(ocr_consts.MODEL_FILES),
          "清单逐个列出模型文件")
    check(kinds.count("legacy") == len(ocr_consts.MODEL_FILES_LEGACY),
          "清单同时列出旧版兼容文件（不计入 missing）")

    rd = res.get("report_deps_missing") or {}
    ditems = rd.get("items") or []
    check(rd.get("state") == "broken", "缺依赖环境 report.state == broken",
          repr(rd.get("state")))
    check(all(not i[2] for i in ditems if i[0] == "dep"),
          "缺依赖环境: dep 逐项 ok=False")
    check(all(i[2] for i in ditems if i[0] == "model"),
          "缺依赖环境: model 逐项 ok=True", repr(ditems))
    return res


# ══════════════════════════════════════════════════════════════════════
# O4 不加载 DLL（静态 ast 证明，运行期证明见 O3 子进程）
# ══════════════════════════════════════════════════════════════════════
def t_no_dll_load_ast():
    print("\n── O4 静态证明: self_check 相关函数内无 DLL 加载调用 ──")
    path = os.path.join(SRC, "paddle_dll.py")
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    targets = ("self_check", "self_check_report", "_resolve_dll_dir",
               "_resolve_model_dir", "_find_model_file", "_ascii_problem")
    seen = []
    bad = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in targets:
            continue
        seen.append(node.name)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr in (
                    "WinDLL", "CDLL", "OleDLL", "windll", "LoadLibrary"):
                bad.append("{}:{}:{}".format(node.name, sub.attr, sub.lineno))
            elif isinstance(sub, ast.Name) and sub.id in (
                    "LoadLibrary", "LoadLibraryExW"):
                bad.append("{}:{}:{}".format(node.name, sub.id, sub.lineno))
    check(set(seen) == set(targets), "解析出 6 个 self_check 相关函数",
          "缺: {}".format(sorted(set(targets) - set(seen))))
    check(not bad, "6 个函数体内无 ctypes.WinDLL/CDLL/LoadLibrary 调用",
          ", ".join(bad))

    # 真实加载路径仍在（本卡只加"静态自检", 不替换子进程预检）
    with io.open(path, encoding="utf-8") as f:
        src = f.read()
    check("ctypes.WinDLL(dll_path)" in src,
          "真实加载仍只在 PaddleDllEngine.load() 里（未被本卡改动）")
    check("def preflight_child(" in src, "子进程预检路径仍在")


# ══════════════════════════════════════════════════════════════════════
# O6 零副作用
# ══════════════════════════════════════════════════════════════════════
def t_side_effect_free():
    print("\n── O6 import 零副作用（子进程验证） ──")
    for mod in ("ocr_consts", "capabilities"):
        code = (
            "import sys; sys.path.insert(0, {src!r}); import {mod};"
            "print('TK=%s' % ('tkinter' in sys.modules));"
            "print('PA=%s' % ('pyautogui' in sys.modules));"
            "print('ENGINE=%s' % ('engine' in sys.modules));"
            "print('PADDLE=%s' % ('paddle_dll' in sys.modules))"
        ).format(src=SRC, mod=mod)
        rc, out, err = _child(code, timeout=60)
        check(rc == 0, "子进程 import {} 正常退出 (rc={})".format(mod, rc),
              (err or "").strip()[-200:])
        check("TK=False" in out, "import {} 未加载 tkinter".format(mod),
              out.strip().replace("\n", " "))
        check("PA=False" in out, "import {} 未加载 pyautogui".format(mod))
        check("ENGINE=False" in out, "import {} 未加载 engine".format(mod))
        check("PADDLE=False" in out, "import {} 未加载 paddle_dll".format(mod))


def main():
    print("=" * 68)
    print("S1-03 回归: ocr_consts 单一定义源 + paddle_dll.self_check + 零副作用")
    print("=" * 68)
    tmp = tempfile.mkdtemp(prefix="acrpa_ocrsc_")
    try:
        t_single_source()
        t_no_duplicate_literals()
        t_self_check(tmp)
        t_no_dll_load_ast()
        t_side_effect_free()
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
