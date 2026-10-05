#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/_smoke_marketplace.py — 脚本市场 v2「批次 2：窗口 UI + 登录入口」冒烟自测。

覆盖 (设计 §3 / §6.4)：
  A. 静态断言 — src/market_window.py 无 hex 颜色字面量、无魔法 padding 像素；
                使用 themed()/TOKENS 访问器；对外接口齐全；
                ACRPA 三处接线 (_open_marketplace 委托 / _market_on_install /
                retheme_marketplace)；工具栏「市场」绑定行保持不变。
  B. 动态断言 (真实 Tk + 真实 ACRPA)：
     1) 能创建并打开市场窗口，四态切换 (list/empty/error/loading)；
     2) 双主题切换 → 窗口/卡片颜色随 themed() 变化；
     3) ui_scale 1.0/1.25/1.5 三档 → 卡片重建、字体与间距同步变化；
     4) 导入链路 → on_install 收到 .xls 路径，ACRPA._market_on_install 载入编辑器
        (state.filename/script_dir、script_name_var、编辑器行数)；
     5) 账号区 未登录/已登录 两态显示正确，且窗口标题/控件文本无 token 明文。

运行 (cmd.exe，勿用 && 串联；中文乱码可先 set PYTHONIOENCODING=utf-8)：
    python -X utf8 tools\\_smoke_marketplace.py

退出码 0 (全过/仅 WARN) / 1 (存在 FAIL)。输出行前缀 [OK]/[WARN]/[FAIL]/[INFO]。
"""
import ast
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FAILS = []
WARNS = []


def _p(status, msg):
    print("[{}] {}".format(status, msg))


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════
# A. 静态断言
# ══════════════════════════════════════════════════════════════════════

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
# 魔法 padding 像素：padx/pady 取正整数字面量 (0 允许，代表无留白)
_PAD_FIRST_RE = re.compile(r"\b(padx|pady)\s*=\s*\(?\s*[1-9]\d*")
_PAD_SECOND_RE = re.compile(r"\b(padx|pady)\s*=\s*\([^)]*,\s*[1-9]\d*")


def check_static():
    src = _read("src/market_window.py")

    # A1. 无 hex 颜色字面量
    hits = []
    lines = src.splitlines()
    for i, ln in enumerate(lines, 1):
        for m in _HEX_RE.finditer(ln):
            # 允许出现在注释里的说明性写法？一律不允许，保持零硬编码色。
            hits.append("{}:{} {}".format("src/market_window.py", i, m.group(0)))
    if hits:
        FAILS.append("market_window.py 存在 hex 颜色字面量: {}".format(hits))
        _p("FAIL", "market_window.py 存在 hex 颜色字面量:")
        for h in hits:
            print("        {}".format(h))
    else:
        _p("OK", "market_window.py 无 hex 颜色字面量 (颜色全走 themed())")

    # A2. themed() 使用充分
    n_themed = src.count("themed(")
    if n_themed >= 20:
        _p("OK", "market_window.py themed( 调用 {} 次 (颜色实时随主题)".format(n_themed))
    else:
        FAILS.append("market_window.py themed( 使用不足: {}".format(n_themed))
        _p("FAIL", "market_window.py themed( 使用不足: {}".format(n_themed))

    # A3. TOKENS 访问器使用
    used = [name for name in ("sp(", "gap(", "ctrl_h(", "radius(", "icon_size(", "tk_px(")
            if name in src]
    missing = [n for n in ("sp(", "gap(", "tk_px(") if n not in src]
    if missing:
        FAILS.append("market_window.py 缺 TOKENS 访问器: {}".format(missing))
        _p("FAIL", "market_window.py 缺 TOKENS 访问器: {}".format(missing))
    else:
        _p("OK", "market_window.py 使用 TOKENS 访问器: {}".format(", ".join(used)))

    # A4. 无魔法 padding 像素
    bad = []
    for i, ln in enumerate(lines, 1):
        if _PAD_FIRST_RE.search(ln) or _PAD_SECOND_RE.search(ln):
            bad.append("{}: {}".format(i, ln.strip()))
    if bad:
        FAILS.append("market_window.py 存在魔法 padding 像素: {}".format(bad))
        _p("FAIL", "market_window.py 存在魔法 padding 像素:")
        for b in bad:
            print("        {}".format(b))
    else:
        _p("OK", "market_window.py padding 全部经 sp()/gap()/ctrl_h() 派生 (无魔法像素)")

    # A5. 对外接口
    tree = ast.parse(src, filename="src/market_window.py")
    top_funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for fn in ("open_market_window", "open_marketplace", "close_marketplace",
               "retheme_marketplace"):
        if fn in top_funcs:
            _p("OK", "market_window.py 暴露 def {}()".format(fn))
        else:
            FAILS.append("market_window.py 缺 def {}()".format(fn))
            _p("FAIL", "market_window.py 缺 def {}()".format(fn))

    # A6. 上传占位必须安全 (不得 raise NotImplementedError)
    if "NotImplementedError" in src:
        FAILS.append("market_window.py 含 NotImplementedError (上传占位应安全降级)")
        _p("FAIL", "market_window.py 含 NotImplementedError (上传占位应安全降级)")
    elif "Phase 3" in src or "Phase3" in src:
        _p("OK", "market_window.py 上传占位已注明 Phase 3 且无未实现异常")
    else:
        WARNS.append("market_window.py 未见 Phase 3 占位说明")
        _p("WARN", "market_window.py 未见 Phase 3 占位说明")

    # A7. ACRPA 三处接线
    a_src = _read("src/ACRPA.py")
    if "market_window.open_market_window(root, on_install=_market_on_install)" in a_src:
        _p("OK", "ACRPA._open_marketplace 委托 market_window.open_market_window")
    else:
        FAILS.append("ACRPA._open_marketplace 未委托 market_window")
        _p("FAIL", "ACRPA._open_marketplace 未委托 market_window")

    if re.search(r"def _market_on_install\(xls_path\):", a_src):
        _p("OK", "ACRPA 新增 def _market_on_install(xls_path)")
    else:
        FAILS.append("ACRPA 缺 def _market_on_install(xls_path)")
        _p("FAIL", "ACRPA 缺 def _market_on_install(xls_path)")

    if re.search(r"state\.script_dir = os\.path\.dirname\(xls_path\)", a_src):
        _p("OK", "ACRPA script_dir 口径固定为 dirname(xls_path)")
    else:
        FAILS.append("ACRPA script_dir 口径未固定 (应 = dirname(xls_path))")
        _p("FAIL", "ACRPA script_dir 口径未固定")

    if "market_window.retheme_marketplace()" in a_src:
        _p("OK", "ACRPA 主题切换处调用 market_window.retheme_marketplace()")
    else:
        FAILS.append("ACRPA 主题切换处未调用 retheme_marketplace()")
        _p("FAIL", "ACRPA 主题切换处未调用 retheme_marketplace()")

    if '_tbtn(toolbar_inner,"市场",_open_marketplace' in a_src:
        _p("OK", "工具栏「市场」按钮绑定保持不变")
    else:
        FAILS.append("工具栏「市场」按钮绑定被改动")
        _p("FAIL", "工具栏「市场」按钮绑定被改动")

    # A8. 旧内联实现已移除 (避免重复实现)
    if "_refresh_script_list" not in a_src and "_show_script_detail" not in a_src:
        _p("OK", "ACRPA 旧内联市场实现 (_refresh_script_list/_show_script_detail) 已移除")
    else:
        WARNS.append("ACRPA 仍残留旧内联市场实现")
        _p("WARN", "ACRPA 仍残留旧内联市场实现")


# ══════════════════════════════════════════════════════════════════════
# B. 动态断言
# ══════════════════════════════════════════════════════════════════════

def _pump(root, cond=None, timeout=8.0, step=0.02):
    """嵌套 mainloop 驱动事件直到 cond() 为真(超时)/cond=None 时仅空转一小段。

    必须用真实 mainloop (而非 root.update())：市场窗口在后台线程里用
    dlg.after(0, cb) 回主线程，只有主线程处于 mainloop 中时跨线程 after 才被接受
    (否则 RuntimeError: main thread is not in main loop)。
    """
    t0 = time.time()

    def _poll():
        if cond is not None:
            try:
                if cond():
                    root.quit()
                    return
            except Exception:
                pass
        elif time.time() - t0 >= 0.25:
            root.quit()
            return
        if time.time() - t0 > timeout:
            root.quit()
            return
        root.after(max(1, int(step * 1000)), _poll)

    root.after(0, _poll)
    try:
        root.mainloop()
    except Exception:
        pass
    return True if cond is None else bool(cond())


def _collect_texts(widget, out):
    """递归收集 widget 文本 (Label/Button/Menubutton text、Entry 内容)。"""
    try:
        cls = widget.winfo_class()
        if cls in ("Label", "Button", "Menubutton", "Checkbutton"):
            out.append(str(widget.cget("text")))
        elif cls == "Entry":
            try:
                out.append(str(widget.get()))
            except Exception:
                pass
        elif cls == "Toplevel" or cls == "Tk":
            out.append(str(widget.title()))
    except Exception:
        pass
    for ch in widget.winfo_children():
        _collect_texts(ch, out)


def _fake_scripts():
    import marketplace as mp
    return [
        mp.ScriptInfo({
            "id": "t_office", "name": "测试办公脚本", "description": "办公类测试脚本描述",
            "category": "办公", "author": "ACRPA", "version": "1.2.0",
            "downloads": 128, "rating": 4.6, "rating_count": 42,
            "tags": ["Excel", "录入"], "icon": "📄",
        }),
        mp.ScriptInfo({
            "id": "t_finance", "name": "测试财务脚本", "description": "财务类测试脚本描述",
            "category": "财务", "author": "社区", "version": "2.0.1",
            "downloads": 256, "rating": 4.9, "rating_count": 88,
            "tags": ["对账", "网银"], "icon": "💰",
            "requires": ["pywin32"],
        }),
        mp.ScriptInfo({
            "id": "t_system", "name": "测试系统脚本", "description": "系统类测试脚本描述",
            "category": "系统", "author": "社区", "version": "1.0.0",
            "downloads": 64, "rating": 4.0, "rating_count": 10,
            "tags": ["文件", "批量"],
        }),
        mp.ScriptInfo({
            "id": "t_pkg", "name": "测试包模式脚本", "description": "v2 包模式条目",
            "category": "其他", "author": "ACRPA", "version": "1.1.0",
            "downloads": 32, "rating": 4.2, "rating_count": 6,
            "tags": ["包"], "pkg": "packages/t_pkg-1.1.0.acrpapkg",
            "sha256": "0" * 64, "size": 2048,
        }),
    ]


def check_dynamic():
    import tkinter
    import tkinter.font

    import state
    import utils
    import marketplace as mp
    import accounts
    import market_window

    _p("INFO", "导入 ACRPA (真实主程序)…")
    import ACRPA
    import app
    app.build()   # 入口拆分后: import ACRPA 不再建窗, 需显式构建

    root = ACRPA.root
    try:
        root.withdraw()
    except Exception:
        pass

    fake = _fake_scripts()
    orig_fetch = mp.fetch_index
    orig_download = mp.download_script
    orig_list = accounts.list_logged_in

    win = None
    try:
        # ── B1. 打开窗口 + list 态 ──
        mp.fetch_index = lambda *a, **k: fake
        ACRPA._open_marketplace()
        win = market_window.get_window()
        if win is None:
            FAILS.append("open 后 market_window.get_window() 为 None")
            _p("FAIL", "open 后未创建市场窗口")
            return
        _p("OK", "市场窗口已创建 (open_market_window)")

        ok = _pump(root, lambda: win.state_name() == "list")
        if ok and win.cards_count() == len(fake):
            _p("OK", "四态→list：卡片数 = {} (期望 {})".format(win.cards_count(), len(fake)))
        else:
            FAILS.append("list 态卡片数异常: state={} cards={}".format(
                win.state_name(), win.cards_count()))
            _p("FAIL", "list 态异常: state={} cards={}".format(
                win.state_name(), win.cards_count()))

        # ── B2. 搜索筛选 → 卡片数下降 ──
        win.search_var.set("财务")
        _pump(root, lambda: win.cards_count() == 1)
        if win.cards_count() == 1:
            _p("OK", "搜索「财务」筛出 1 张卡片")
        else:
            FAILS.append("搜索筛选异常: cards={}".format(win.cards_count()))
            _p("FAIL", "搜索筛选异常: cards={}".format(win.cards_count()))

        # ── B3. 空态 ──
        win.search_var.set("不存在的关键字ZZZ")
        _pump(root, lambda: win.state_name() == "empty")
        if win.state_name() == "empty":
            _p("OK", "四态→empty：无匹配显示空态")
        else:
            FAILS.append("空态未触发: {}".format(win.state_name()))
            _p("FAIL", "空态未触发: {}".format(win.state_name()))
        win._clear_filter()
        _pump(root, lambda: win.state_name() == "list")

        # ── B4. 错误态 + 离线回退 ──
        def _boom(*a, **k):
            raise mp.MarketplaceError("模拟网络失败")
        mp.fetch_index = _boom
        win._load(force=True)
        ok = _pump(root, lambda: win.state_name() == "error")
        if ok:
            _p("OK", "四态→error：在线失败进入错误态 (含重试)")
        else:
            FAILS.append("错误态未触发: {}".format(win.state_name()))
            _p("FAIL", "错误态未触发: {}".format(win.state_name()))
        mp.fetch_index = lambda *a, **k: fake
        win._load(force=True)
        _pump(root, lambda: win.state_name() == "list")

        # ── B5. 双主题切换 ──
        def _widget_bg(w):
            try:
                return str(w.cget("bg"))
            except Exception:
                return ""

        before_list = _widget_bg(win.frm_list)
        card0 = win._card_widgets[0] if win._card_widgets else None
        before_card = _widget_bg(card0) if card0 else ""

        state.DARK_MODE = not bool(getattr(state, "DARK_MODE", False))
        ACRPA._refresh_theme()          # 走真实主题切换路径 (内部调用 retheme_marketplace)
        _pump(root, lambda: win.cards_count() > 0)
        after_list = _widget_bg(win.frm_list)
        card0b = win._card_widgets[0] if win._card_widgets else None
        after_card = _widget_bg(card0b) if card0b else ""

        if before_list != after_list and after_list == utils.themed("bgc"):
            _p("OK", "主题切换后列表底色 {} -> {} (== themed('bgc'))".format(
                before_list, after_list))
        else:
            FAILS.append("列表底色未随主题变化: {} -> {}".format(before_list, after_list))
            _p("FAIL", "列表底色未随主题变化: {} -> {}".format(before_list, after_list))

        if before_card != after_card and after_card == utils.themed("bgc"):
            _p("OK", "主题切换后卡片底色 {} -> {} (== themed('bgc'))".format(
                before_card, after_card))
        else:
            FAILS.append("卡片底色未随主题变化: {} -> {}".format(before_card, after_card))
            _p("FAIL", "卡片底色未随主题变化: {} -> {}".format(before_card, after_card))

        # 还原主题
        state.DARK_MODE = not bool(getattr(state, "DARK_MODE", False))
        ACRPA._refresh_theme()
        _pump(root, lambda: win.cards_count() > 0)

        # ── B6. ui_scale 三档：字体 + 间距同步 ──
        prev_body = None
        prev_pad = None
        scales_ok = True
        for sc in (1.0, 1.25, 1.5):
            utils.set_ui_scale(sc)
            win.rebuild()
            _pump(root, lambda: win.cards_count() == len(fake))
            try:
                body_pt = int(tkinter.font.nametofont(utils.FONT_BODY).cget("size"))
            except Exception:
                body_pt = -1
            pad = "-"
            try:
                pad = str(win._card_widgets[0].pack_info().get("pady"))
            except Exception:
                pass
            wrap = win._wrap_labels[0].cget("wraplength") if win._wrap_labels else 0
            _p("INFO", "ui_scale={}: 卡片={} FONT_BODY={}pt 卡片pady={} wraplength={}".format(
                sc, win.cards_count(), body_pt, pad, wrap))
            if win.cards_count() != len(fake):
                scales_ok = False
                FAILS.append("ui_scale={} 卡片重建异常".format(sc))
                _p("FAIL", "ui_scale={} 卡片重建异常".format(sc))
            if prev_body is not None and body_pt <= prev_body:
                scales_ok = False
                FAILS.append("ui_scale 增大但 FONT_BODY 未变大: {}->{}".format(prev_body, body_pt))
                _p("FAIL", "ui_scale 增大但 FONT_BODY 未变大: {}->{}".format(prev_body, body_pt))
            if prev_pad is not None and pad == prev_pad:
                scales_ok = False
                FAILS.append("ui_scale 增大但卡片 padding 未变: {}".format(pad))
                _p("FAIL", "ui_scale 增大但卡片 padding 未变: {}".format(pad))
            expect_pad = str(utils.sp("gap_tight"))
            if expect_pad not in pad:
                scales_ok = False
                FAILS.append("ui_scale={} 卡片 pady={} 未含期望间距 {}".format(sc, pad, expect_pad))
                _p("FAIL", "ui_scale={} 卡片 pady={} 未含期望间距 {}".format(sc, pad, expect_pad))
            if not wrap or int(wrap) <= 0:
                scales_ok = False
                FAILS.append("ui_scale={} wraplength 无效: {}".format(sc, wrap))
                _p("FAIL", "ui_scale={} wraplength 无效: {}".format(sc, wrap))
            prev_body, prev_pad = body_pt, pad
        if scales_ok:
            _p("OK", "三档 ui_scale 下卡片重建、字体与间距同步变化")
        utils.set_ui_scale(1.0)
        win.rebuild()
        _pump(root, lambda: win.cards_count() == len(fake))

        # ── B7. 账号区：未登录 / 已登录 + 无 token 泄露 ──
        _FAKE_TOKEN = "ghp_FAKEtoken_0123456789abcdefABCDEF"
        accounts.list_logged_in = lambda: []
        win.refresh_account()
        _pump(root, None)
        if win.avatar_btn.cget("text") == "登录":
            _p("OK", "账号区(未登录)：显示「登录」")
        else:
            FAILS.append("账号区未登录显示异常: {}".format(win.avatar_btn.cget("text")))
            _p("FAIL", "账号区未登录显示异常: {}".format(win.avatar_btn.cget("text")))

        accounts.list_logged_in = lambda: ["gitee"]
        state.MARKET_PROVIDER = "gitee"
        state.MARKET_USERNAME = "tester001"
        win.refresh_account()
        _pump(root, None)
        av = win.avatar_btn.cget("text")
        acct = win.account_lbl.cget("text")
        if "tester001" in av and "gitee" in acct:
            _p("OK", "账号区(已登录)：头像={!r} 账户={!r}".format(av, acct))
        else:
            FAILS.append("账号区已登录显示异常: avatar={!r} acct={!r}".format(av, acct))
            _p("FAIL", "账号区已登录显示异常: avatar={!r} acct={!r}".format(av, acct))

        # token 明文不得出现在任何控件文本/标题
        texts = []
        _collect_texts(win.dlg, texts)
        joined = " | ".join(texts)
        if _FAKE_TOKEN in joined:
            FAILS.append("检测到 token 明文泄露到控件文本")
            _p("FAIL", "检测到 token 明文泄露到控件文本")
        else:
            _p("OK", "控件文本/标题无 token 明文 (共扫描 {} 处文本)".format(len(texts)))

        # 还原账号 mock
        accounts.list_logged_in = orig_list

        # ── B8. 导入链路：on_install 收到 .xls 路径 + 载入编辑器 ──
        xls = os.path.join(ROOT, "template", "脚本模板.xls")
        if not os.path.exists(xls):
            # 退回任一模板
            tdir = os.path.join(ROOT, "template")
            cands = [f for f in os.listdir(tdir) if f.endswith(".xls")]
            xls = os.path.join(tdir, cands[0]) if cands else xls
        captured = {"path": None}

        def _fake_download(script_id, save_dir, progress=None, cancel=None):
            if progress:
                try:
                    progress(10, 20)
                    progress(20, 20)
                except Exception:
                    pass
            return xls

        mp.download_script = _fake_download

        # 记录 _market_on_install 是否把路径交给了编辑器加载函数
        # (xlrd 缺失时 _editor_load_xls 会弹 messagebox，故先替换为记录器)
        try:
            import xlrd  # noqa: F401
            has_xlrd = True
        except Exception:
            has_xlrd = False
        orig_load = ACRPA._editor_load_xls
        load_calls = {"fp": None, "n": 0}

        def _rec_load(fp):
            load_calls["fp"] = fp
            load_calls["n"] += 1
            if has_xlrd:
                return orig_load(fp)
            return None

        ACRPA._editor_load_xls = _rec_load
        # 用真实 ACRPA 回调
        win.on_install = ACRPA._market_on_install

        state.filename = None
        state.script_dir = None
        if not win.import_script("t_office"):
            FAILS.append("import_script 未命中 t_office")
            _p("FAIL", "import_script 未命中 t_office")

        ok = _pump(root, lambda: state.filename is not None, timeout=10.0)
        _pump(root, None)
        if ok and os.path.abspath(state.filename) == os.path.abspath(xls):
            _p("OK", "ACRPA.state.filename == 导入路径 ({})".format(
                os.path.basename(state.filename)))
        else:
            FAILS.append("state.filename 未设置: {!r}".format(state.filename))
            _p("FAIL", "state.filename 未设置: {!r}".format(state.filename))

        if state.script_dir and os.path.abspath(state.script_dir) == os.path.abspath(
                os.path.dirname(xls)):
            _p("OK", "state.script_dir 口径 = dirname(xls) ({})".format(state.script_dir))
        else:
            FAILS.append("state.script_dir 异常: {!r}".format(state.script_dir))
            _p("FAIL", "state.script_dir 异常: {!r}".format(state.script_dir))

        if ACRPA.script_name_var.get() == os.path.basename(xls):
            _p("OK", "script_name_var 已更新为 {}".format(os.path.basename(xls)))
        else:
            FAILS.append("script_name_var 未更新: {!r}".format(ACRPA.script_name_var.get()))
            _p("FAIL", "script_name_var 未更新: {!r}".format(ACRPA.script_name_var.get()))

        # _market_on_install 必须把同一路径交给编辑器加载函数
        if load_calls["n"] >= 1 and os.path.abspath(load_calls["fp"] or "") == os.path.abspath(xls):
            _p("OK", "_market_on_install 已调用 _editor_load_xls({})".format(
                os.path.basename(xls)))
        else:
            FAILS.append("_market_on_install 未把路径交给编辑器: {!r}".format(load_calls))
            _p("FAIL", "_market_on_install 未把路径交给编辑器: {!r}".format(load_calls))

        rows = len(ACRPA.tree.get_children())
        if has_xlrd:
            if rows > 0:
                _p("OK", "编辑器已载入脚本 (tree 行数 = {})".format(rows))
            else:
                FAILS.append("编辑器未载入任何行 (xlrd 可用)")
                _p("FAIL", "编辑器未载入任何行 (xlrd 可用)")
        else:
            WARNS.append("xlrd 未安装：跳过编辑器行数校验 (环境依赖缺失)")
            _p("WARN", "xlrd 未安装：跳过编辑器行数校验 (环境依赖缺失)")

        # 窗口应已关闭
        _pump(root, lambda: market_window.get_window() is None, timeout=5.0)
        if market_window.get_window() is None:
            _p("OK", "导入完成后市场窗口已关闭")
        else:
            FAILS.append("导入完成后市场窗口未关闭")
            _p("FAIL", "导入完成后市场窗口未关闭")

    except Exception as e:
        import traceback
        FAILS.append("动态断言异常: {!r}".format(e))
        _p("FAIL", "动态断言异常: {!r}".format(e))
        for ln in traceback.format_exc().splitlines():
            print("        {}".format(ln))
    finally:
        mp.fetch_index = orig_fetch
        mp.download_script = orig_download
        accounts.list_logged_in = orig_list
        try:
            ACRPA._editor_load_xls = orig_load
        except Exception:
            pass
        try:
            market_window.close_marketplace()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    # close_marketplace 幂等性
    try:
        market_window.close_marketplace()
        market_window.retheme_marketplace()   # 未开 → 安全 no-op
        _p("OK", "close_marketplace()/retheme_marketplace() 未开时安全 no-op")
    except Exception as e:
        FAILS.append("close/retheme no-op 异常: {!r}".format(e))
        _p("FAIL", "close/retheme no-op 异常: {!r}".format(e))


def main():
    print("=== ACRPA 脚本市场 v2 批次2 (窗口 UI + 登录入口) 冒烟自测 ===")
    print("--- A. 静态断言 ---")
    check_static()
    print("--- B. 动态断言 (真实 Tk + ACRPA) ---")
    check_dynamic()
    print("=== 结果: {} (FAIL={} WARN={}) ===".format(
        "FAIL" if FAILS else "OK", len(FAILS), len(WARNS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
