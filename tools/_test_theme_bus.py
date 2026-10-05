#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ThemeBus / 语义角色 回归自测 (路线图 §9 阶段二第 2 项: `ui/theme.py` + ThemeBus)。

用法:
    .venv\\Scripts\\python.exe -X utf8 tools\\_test_theme_bus.py

覆盖:
  T1  模块存在性   : src/ui/theme.py 存在且导出 colors/apply_theme/subscribe/
                     unsubscribe/publish/subscriber_count/set_role/get_role
  T2  无 GUI 副作用: 子进程 `import ui.theme` 不建窗、不阻塞 (秒退)
  T3  订阅去重     : subscribe 同函数两次 → subscriber_count 只增 1; unsubscribe 生效
  T4  发布隔离     : 某个订阅者抛异常不影响其它订阅者被调用 (try/except 逐个隔离)
  T5  发布契约     : publish 以 (dark, colors, prev) 三个位置参数派发给每个订阅者
  T6  色板契约     : colors(dark=True/False) 键集一致且取值不同; utils._colors 委托一致
  T7  去扇出(静态) : ACRPA._refresh_theme 内**不再**具名调用
                     settings_window.refresh_theme / dialogs.C = C /
                     netlink_window.refresh_theme / help_window.refresh_theme /
                     market_window.retheme_marketplace / _bind_minibar,
                     且改为一次 ui_theme.publish(...) (AST 判定, 不受注释干扰)
  T8  去嗅探(静态) : ACRPA.py / settings_window.py 内不再出现
                     「读控件文本再按中文词判色」(`"●/运行/就绪" in txt`);
                     全 src/ 内 `_semantic_bg_for` / `_SEMANTIC_GROUPS` 已绝迹
  T9  角色登记(静态): 语义按钮/标签在创建处登记了角色 (roled/set_role + 角色键名);
                     执行四键随执行栏迁至 src/ui/exec_bar.py (阶段二第 3 项第 2 步),
                     故其角色登记断言改为「exec_bar 内登记 + ACRPA 不再具名登记」;
                     各独立窗口 claim_window 自管换肤, dialogs/settings 已 subscribe
  T10 订阅幂等     : 同一回调重复 subscribe 不产生重复调用

退出码: 0 = 全部通过 / 1 = 有失败。
纯逻辑 + 静态断言, 不创建任何真实窗口 (无 GUI 依赖, 可进 CI)。
"""
import ast
import io
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

_PASS, _FAIL = [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))


def _read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _parse(rel):
    return ast.parse(_read(rel), filename=rel)


def _src_files():
    out = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dirpath, fn), ROOT)
                           .replace(os.sep, "/"))
    return sorted(out)


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _calls_of(fn_node):
    """收集函数体内「具名调用」: Name(...) 与 X.attr(...) 两种形态。"""
    names = set()
    for n in ast.walk(fn_node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            names.add(f.id)
        elif isinstance(f, ast.Attribute):
            names.add(f.attr)
            if isinstance(f.value, ast.Name):
                names.add("{}.{}".format(f.value.id, f.attr))
    return names


# ── T1 / T2 ──────────────────────────────────────────────────────────

def t_module_and_side_effect():
    print("\n── T1 模块存在性 / T2 无 GUI 副作用 ──")
    path = os.path.join(SRC, "ui", "theme.py")
    check(os.path.exists(path), "src/ui/theme.py 存在")
    check(os.path.exists(os.path.join(SRC, "ui", "__init__.py")), "src/ui/__init__.py 存在")

    if not os.path.exists(path):
        return
    try:
        import ui.theme as theme
    except Exception as e:
        check(False, "import ui.theme 失败: {}".format(e))
        return

    for nm in ("colors", "apply_theme", "subscribe", "unsubscribe", "publish",
               "subscriber_count", "set_role", "get_role", "clear_role", "roled",
               "claim_window", "is_claimed"):
        check(hasattr(theme, nm), "ui.theme 导出 {}".format(nm))

    # T2: 子进程导入不得建窗/阻塞 (只验「秒退 + 无副作用」)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    code = ("import sys; sys.path.insert(0, {src!r}); "
            "import ui.theme as t; "
            "print('HAS_TK_ROOT=%s' % ('tkinter' in sys.modules)); "
            "print('OK=%s' % hasattr(t, 'colors'))").format(src=SRC)
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=20)
        out = (p.stdout or b"").decode("utf-8", "replace")
        check(p.returncode == 0, "子进程 import ui.theme 秒退 (rc={})".format(p.returncode),
              (p.stderr or b"").decode("utf-8", "replace").strip()[-200:])
        check("OK=True" in out, "子进程内 ui.theme 可用", out.strip().replace("\n", " "))
    except subprocess.TimeoutExpired:
        check(False, "子进程 import ui.theme 超时 (疑似副作用阻塞)")
    except Exception as e:
        check(False, "子进程探测异常: {}".format(e))


# ── T3 / T4 / T5 / T10 ───────────────────────────────────────────────

def t_bus_semantics():
    print("\n── T3 去重/注销 · T4 异常隔离 · T5 派发契约 · T10 幂等 ──")
    try:
        import ui.theme as theme
    except Exception as e:
        check(False, "import ui.theme 失败: {}".format(e))
        return

    theme.clear_subscribers()
    check(theme.subscriber_count() == 0, "clear_subscribers 后计数归零")

    calls = []

    def a(dark, colors, prev):
        calls.append(("a", dark, colors, prev))

    def b(dark, colors, prev):
        calls.append(("b", dark, colors, prev))

    theme.subscribe(a)
    theme.subscribe(a)
    check(theme.subscriber_count() == 1, "同一回调重复 subscribe 只登记一次 (T3/T10)",
          "count={}".format(theme.subscriber_count()))

    theme.publish(True, {"ac": "#111111"}, {"ac": "#222222"})
    called = [c[0] for c in calls]
    check(called == ["a"], "publish 每个订阅者只被调用一次", "calls={}".format(called))
    check(calls and calls[0][1] is True and calls[0][2] == {"ac": "#111111"}
          and calls[0][3] == {"ac": "#222222"},
          "publish 契约 (dark, colors, prev) 三参数原样派发", "got={}".format(calls[:1]))

    # T4: 中间订阅者抛异常, 前后订阅者都必须被调用
    calls = []
    boom_hit = []

    def boom(dark, colors, prev):
        boom_hit.append(1)
        raise RuntimeError("订阅者内部异常 (应被隔离)")

    def c(dark, colors, prev):
        calls.append("c")

    theme.clear_subscribers()
    theme.subscribe(a)
    theme.subscribe(boom)
    theme.subscribe(b)
    theme.subscribe(c)
    try:
        theme.publish(False, {"ac": "#333333"}, None)
        check(True, "publish 不因订阅者抛异常而中断")
    except Exception as e:
        check(False, "publish 把订阅者异常抛给了调用方: {}".format(e))

    after = [c[0] if isinstance(c, tuple) else c for c in calls]
    check(boom_hit == [1], "抛异常的订阅者确实被调用过")
    check(after == ["a", "b", "c"], "异常订阅者前后订阅者均被调用 (异常隔离)",
          "calls={}".format(after))

    theme.unsubscribe(boom)
    check(theme.subscriber_count() == 3, "unsubscribe 生效", "count={}".format(theme.subscriber_count()))
    theme.unsubscribe(boom)
    check(theme.subscriber_count() == 3, "unsubscribe 不存在的回调不报错")
    theme.clear_subscribers()


# ── T6 ───────────────────────────────────────────────────────────────

def t_palette():
    print("\n── T6 色板契约 / utils 委托一致 ──")
    try:
        import ui.theme as theme
        import utils
    except Exception as e:
        check(False, "import ui.theme/utils 失败: {}".format(e))
        return

    light, dark = theme.colors(False), theme.colors(True)
    check(set(light.keys()) == set(dark.keys()),
          "light/dark 键集一致 ({}/{})".format(len(light), len(dark)))
    check(light["ac"] != dark["ac"] and light["sc"] != dark["sc"],
          "light/dark 关键色不同 (ac={} sc={})".format(light["ac"], dark["ac"]))
    check("hlbg" in light and "hlbg" in dark and light["hlbg"] != dark["hlbg"],
          "hlbg 主题键在两侧齐备且随主题变化")

    u = utils._colors()
    check(u == light or u == dark, "utils._colors 委托 ui.theme.colors (取值一致)")
    check(set(u.keys()) == set(light.keys()), "utils._colors 键集 == ui.theme.colors 键集")
    check(hasattr(utils, "themed") and hasattr(utils, "apply_theme"),
          "utils.themed / utils.apply_theme 向后兼容仍可用")


# ── T7 ───────────────────────────────────────────────────────────────

BANNED_CALLS = ("settings_window.refresh_theme", "netlink_window.refresh_theme",
                "help_window.refresh_theme", "market_window.retheme_marketplace",
                "_bind_minibar", "dialogs.refresh_theme")


def t_no_fanout():
    print("\n── T7 ACRPA._refresh_theme 去手工扇出 (AST) ──")
    tree = _parse("src/ACRPA.py")
    rt = _find_func(tree, "_refresh_theme")
    if rt is None:
        check(False, "未找到 ACRPA._refresh_theme")
        return

    calls = _calls_of(rt)
    hits = sorted(n for n in BANNED_CALLS if n in calls)
    check(not hits, "ACRPA._refresh_theme 无对独立窗口的具名刷新调用", "残留={}".format(hits))
    check("dialogs.C" not in {  # 旧的 dialogs.C = C 扇出
        "{}.{}".format(t.value.id, t.attr)
        for n in ast.walk(rt) if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)},
        "ACRPA._refresh_theme 不再同步 dialogs.C (改由订阅回调)")
    check("ui_theme.publish" in calls,
          "ACRPA._refresh_theme 改为一次 ui_theme.publish(...)",
          "具名调用={}".format(sorted(calls)))
    n_publish = 0
    for n in ast.walk(rt):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "publish" and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "ui_theme":
            n_publish += 1
    check(n_publish == 1, "publish 广播唯一 (恰 1 处, 取代 6 处扇出)",
          "count={}".format(n_publish))

    # publish 必须在 apply_theme 之后 (订阅方的配色不被本窗口回填覆盖) —— 行号顺序断言
    lines = {}
    for n in ast.walk(rt):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "publish" and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "ui_theme":
            lines["publish"] = n.lineno
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "apply_theme":
            lines["apply_theme"] = n.lineno
    check(lines.get("publish") and lines.get("apply_theme")
          and lines["apply_theme"] < lines["publish"],
          "publish 位于 apply_theme 之后 (订阅方结果不被覆盖)",
          "apply_theme={} publish={}".format(lines.get("apply_theme"), lines.get("publish")))


# ── T8 ───────────────────────────────────────────────────────────────

SNIFF_WORDS = ("●", "运行", "就绪")


def _sniff_hits(rel):
    """AST 级判定: 是否存在 `"<中文词>" in <Name|Attr>` 形态的判定。"""
    hits = []
    for n in ast.walk(_parse(rel)):
        if not isinstance(n, ast.Compare):
            continue
        for op, cmp_ in zip(n.ops, n.comparators):
            if not isinstance(op, ast.In):
                continue
            for left in [n.left] + []:
                if isinstance(left, ast.Constant) and isinstance(left.value, str) \
                        and any(w in left.value for w in SNIFF_WORDS) \
                        and isinstance(cmp_, (ast.Name, ast.Attribute)):
                    hits.append((n.lineno, left.value))
    return hits


def t_no_text_sniff():
    print("\n── T8 去文本嗅探 / 去旧值反推 (静态) ──")
    for rel in ("src/ACRPA.py", "src/settings_window.py"):
        hits = _sniff_hits(rel)
        check(not hits, "{} 不含「读文本按中文词判色」".format(rel), "命中={}".format(hits))
        src = _read(rel)
        check('cget("text")' not in src or "w.cget" not in src,
              "{} 未读取控件文本用于着色".format(rel))

    bad = []
    for rel in _src_files():
        try:
            tree = _parse(rel)
        except SyntaxError as e:
            bad.append("{} 语法错误 {}".format(rel, e))
            continue
        for n in ast.walk(tree):
            nm = None
            if isinstance(n, ast.Name):
                nm = n.id
            elif isinstance(n, ast.Attribute):
                nm = n.attr
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nm = n.name
            if nm in ("_semantic_bg_for", "_SEMANTIC_GROUPS"):
                bad.append("{}:{}".format(rel, nm))
    check(not bad, "全 src/ 内 _semantic_bg_for / _SEMANTIC_GROUPS 已绝迹", "残留={}".format(bad))


# ── T9 ───────────────────────────────────────────────────────────────

def t_roles_and_subscriptions():
    print("\n── T9 语义角色登记 / 自管换肤声明 / 订阅接入 (静态) ──")
    a = _read("src/ACRPA.py")
    flat = a.replace(" ", "")
    # 阶段二第 3 项第 2 步: 执行四键随执行控制栏迁至 src/ui/exec_bar.py ——
    # 角色登记断言移到 exec_bar (创建处), ACRPA 侧断言「不再具名登记四键」。
    eb = _read("src/ui/exec_bar.py")
    eflat = eb.replace(" ", "")
    # btn_x = ui_theme.roled(tkinter.Button(...), "<token>")
    for name, tok in (("btn_run", "sc"), ("btn_pause", "wn"),
                      ("btn_step", "ac"), ("btn_stop", "dg")):
        pat = '{}=ui_theme.roled('.format(name)
        check(pat in eflat and '"{}")'.format(tok) in eflat,
              "exec_bar.{} 登记语义角色 \"{}\"".format(name, tok))
    check(eb.count("ui_theme.roled(") >= 5,
          "src/ui/exec_bar.py 多处 roled(...) 登记语义角色 (选择/四键)",
          "count={}".format(eb.count("ui_theme.roled(")))
    for name in ("btn_run", "btn_pause", "btn_step", "btn_stop"):
        check('{}=ui_theme.roled('.format(name) not in flat,
              "ACRPA.py 不再具名登记 {} (已迁 exec_bar)".format(name))
    check("ui_theme.subscribe(refresh_theme)" in eb,
          "src/ui/exec_bar.py 订阅 ThemeBus (执行四键角色回填)")
    check("status_dot=ui_theme.roled(" in flat,
          "状态标签 status_dot 登记语义角色 (替代「就绪」文案嗅探)")
    check("_subscribe_theme_bus()" in flat and "ui_theme.subscribe(_fn)" in flat,
          "ACRPA 通过 _subscribe_theme_bus() 订阅 mini_bar/netlink/help/market")

    d = _read("src/dialogs.py")
    check("_ui_theme.subscribe(refresh_theme)" in d, "dialogs 订阅 ThemeBus")
    check("def refresh_theme(" in d, "dialogs 提供 refresh_theme(dark, colors, prev)")
    check("_ui_theme.set_role(" in d or "_ui_theme.roled(" in d,
          "dialogs 语义按钮登记角色")

    s = _read("src/settings_window.py")
    check("_ui_theme.subscribe(_on_theme_publish)" in s, "settings_window 订阅 ThemeBus")
    check("def refresh_theme(prev=None):" in s,
          "settings_window.refresh_theme(prev) 签名保持 (兼容既有自测)")

    # 阶段二第 4 项: 窗口创建/claim_window 迁至 src/ui/settings/window.py
    # (src/settings_window.py 退化为薄壳, 只保留 subscribe + refresh_theme 字面量)。
    # 定位随之更新, 检查强度不降: 仍断言「设置窗口声明自管换肤 (claim_window)」。
    for rel, owner in (("src/ui/settings/window.py", "settings_window"),
                       ("src/mini_bar.py", "mini_bar"),
                       ("src/netlink_window.py", "netlink_window"),
                       ("src/help_window.py", "help_window"),
                       ("src/market_window.py", "market_window")):
        src = _read(rel)
        check("claim_window(" in src and owner in src,
              "{} 声明自管换肤 (claim_window)".format(rel))


# ── T11: 角色键名覆盖检查 (语义按钮 token 一致性) ──

def t_token_convention():
    print("\n── T11 token→角色 约定 (工厂解析) ──")
    u = _read("src/utils.py")
    check("resolve_bg(" in u, "utils._btn 经 ui.theme.resolve_bg 解析 色板键名/字面色")
    check('_theme.set_role(btn, _role)' in u, "utils._btn 按解析出的角色登记 set_role")
    a = _read("src/ACRPA.py")
    check('_role, bg_c = ui_theme.resolve_bg(bg_c, C)' in a
          or "ui_theme.resolve_bg(bg_c, C)" in a,
          "ACRPA._tbtn 同样经 resolve_bg 解析并登记角色")
    check('_tbtn(toolbar_inner,"+ 添加",_cmd_add_row,"ac","white"' in a,
          "_tbtn 调用点改为传色板键名 (声明语义角色)")


def main():
    print("=== ThemeBus / 语义角色 回归自测 (路线图 §9 阶段二第 2 项) ===")
    t_module_and_side_effect()
    t_bus_semantics()
    t_palette()
    t_no_fanout()
    t_no_text_sniff()
    t_roles_and_subscriptions()
    t_token_convention()
    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
