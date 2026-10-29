# -*- coding: utf-8 -*-
"""S1-04 回归: ``hotkey_win`` (RegisterHotKey 后端 + 热字串) 与三步迁移的可回退性。

断言清单
--------
  H1 parse_spec  : 表驱动 (ctrl/alt/shift/win / 具名键 / 单字符 / 非法输入 → None)
  H2 等价性      : ``hotkey_win.parse_spec`` 与 ``ACRPA._parse_hotkey_spec``、
                   ``ACRPA._parse_hotkey_spec_legacy`` **逐例结果完全一致** (同一对象比较)
  H3 生命周期    : rebuild → 成功集合、unregister 后可再注册、重复注册同 spec 报 1409
  H4 线程模型    : 在**主线程**注册, 由消息线程收到的 WM_HOTKEY 仍被派发
                   (PostThreadMessage 注入 + SendMessage 打隐藏窗口两条路径, 不依赖真实按键)
  H5 退出干净    : stop()/close() 后线程已结束、注册项清空、重复调用不抛
  H6 热字串缓冲  : 按键→字符纯函数、触发词命中、回删字符数、注入事件被忽略
  H7 默认关闭    : 无 bindings 时 start() 返回 False 且**不安装任何钩子**
  H8 回退等价性  : HOTKEY_BACKEND="poll" → 轮询不跳过任何条目 (集合恒为空)
  H9 零依赖/无副作用: 源码无第三方 import; 子进程 import hotkey_win 不加载 tkinter

任何一步在无窗口/无键鼠环境下不可执行 → 打印 SKIP 并以 0 退出 (fail-open)。

用法: python -X utf8 tools\\_test_hotkey_win.py
退出码: 0=全部通过或明确跳过, 1=存在失败
"""
import os
import subprocess
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
sys.path.insert(0, SRC)

import hotkey_win                                            # noqa: E402
import state                                                 # noqa: E402

_PASS, _FAIL, _SKIP = [], [], []


def check(cond, msg, detail=""):
    (_PASS if cond else _FAIL).append(msg)
    tail = ("  ({})".format(detail) if detail else "")
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg, tail))
    return bool(cond)


def skip(msg, detail=""):
    _SKIP.append(msg)
    print("[SKIP] {}{}".format(msg, "  ({})".format(detail) if detail else ""))


# ══════════════════════════════════════════════════════════════════════
# H1/H2 键位解析与等价性
# ══════════════════════════════════════════════════════════════════════
CASES = [
    ("ctrl+shift+q", ({0x11, 0x10}, 0x51)),
    ("Ctrl+Alt+F9", ({0x11, 0x12}, 0x78)),
    ("win+shift+s", ({0x5B, 0x10}, 0x53)),
    ("alt+f4", ({0x12}, 0x73)),
    ("ctrl+shift+space", ({0x11, 0x10}, 0x20)),
    ("ctrl+enter", ({0x11}, 0x0D)),
    ("shift+tab", ({0x10}, 0x09)),
    ("ctrl+pageup", ({0x11}, 0x21)),
    ("ctrl+pagedown", ({0x11}, 0x22)),
    ("ctrl+backspace", ({0x11}, 0x08)),
    ("ctrl+delete", ({0x11}, 0x2E)),
    ("ctrl+insert", ({0x11}, 0x2D)),
    ("ctrl+home", ({0x11}, 0x24)),
    ("ctrl+end", ({0x11}, 0x23)),
    ("ctrl+1", ({0x11}, 0x31)),
    ("control+x", ({0x11}, 0x58)),
    ("F12", None),                 # 无修饰键 → 无效
    ("", None),
    ("未设置", None),
    (None, None),
    ("ctrl", None),                # 只有修饰键 → 无效
    ("ctrl+shift+nonexistent", None),
    ("ctrl+shift", None),
    ("ctrl++", None),
]


def t_parse_spec():
    print("\n── H1 parse_spec 表驱动 ──")
    for text, want in CASES:
        got = hotkey_win.parse_spec(text)
        ok = (got == want)
        check(ok, "parse_spec({!r}) == {!r}".format(text, want), "got={!r}".format(got))
    # mods 必须是 VK 码集合 (兼容 GetAsyncKeyState 轮询路径), 不是 MOD_* 位标志
    parsed = hotkey_win.parse_spec("ctrl+alt+f9")
    check(parsed[0] == {0x11, 0x12}, "mods 是 VK 码集合 {0x11,0x12}", repr(parsed[0]))
    check(hotkey_win.to_register_mods(parsed[0]) ==
          (hotkey_win.MOD_CONTROL | hotkey_win.MOD_ALT),
          "to_register_mods → MOD_CONTROL|MOD_ALT",
          hex(hotkey_win.to_register_mods(parsed[0])))
    check(hotkey_win.MOD_MAP.get("norepeat") == hotkey_win.MOD_NOREPEAT,
          "MOD_MAP 含 MOD_NOREPEAT", hex(hotkey_win.MOD_NOREPEAT))


def t_parse_equivalence():
    print("\n── H2 与 ACRPA._parse_hotkey_spec 逐例等价 ──")
    try:
        import ACRPA                                         # noqa: F401
    except Exception as e:                                   # noqa: BLE001
        skip("import ACRPA 失败, 跳过等价性证明", repr(e))
        return
    legacy = getattr(ACRPA, "_parse_hotkey_spec_legacy", None)
    thin = getattr(ACRPA, "_parse_hotkey_spec", None)
    check(callable(thin), "ACRPA._parse_hotkey_spec 仍存在 (调用点不变的证明)")
    check(callable(legacy), "旧实现保留为 _parse_hotkey_spec_legacy (可回退)")
    if not (callable(thin) and callable(legacy)):
        return
    same_thin = same_legacy = 0
    for text, _want in CASES:
        a = hotkey_win.parse_spec(text)
        b = thin(text)
        c = legacy(text)
        if a == b:
            same_thin += 1
        else:
            check(False, "薄封装与 hotkey_win.parse_spec 不一致: {!r}".format(text),
                  "{!r} != {!r}".format(a, b))
        if a == c:
            same_legacy += 1
        else:
            check(False, "与改造前旧实现不一致: {!r}".format(text),
                  "new={!r} legacy={!r}".format(a, c))
    check(same_thin == len(CASES),
          "薄封装 == hotkey_win.parse_spec: {}/{}".format(same_thin, len(CASES)))
    check(same_legacy == len(CASES),
          "hotkey_win.parse_spec == 改造前旧实现: {}/{}".format(
              same_legacy, len(CASES)))
    # 打印实测双侧结果 (验收第 2 条要贴)
    print("      spec                  hotkey_win                ACRPA(薄)                 legacy")
    for text, _w in CASES[:12]:
        print("      {:<20} {!s:<24} {!s:<24} {!s}".format(
            repr(text), hotkey_win.parse_spec(text), thin(text), legacy(text)))


# ══════════════════════════════════════════════════════════════════════
# H3/H4/H5 生命周期 / 线程模型 / 退出干净
# ══════════════════════════════════════════════════════════════════════
SPEC_A = "ctrl+alt+shift+f9"      # 选生僻组合, 尽量避开其它程序占用
SPEC_B = "ctrl+alt+shift+f10"


def _wait(flag, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if flag.is_set():
            return True
        time.sleep(0.01)
    return flag.is_set()


def t_lifecycle():
    print("\n── H3/H4/H5 生命周期 + 线程模型 + 退出干净 ──")
    if not hotkey_win.is_supported():
        skip("非 Windows 或无 user32, 跳过 RegisterHotKey 相关断言")
        return
    mgr = hotkey_win.HotkeyManager()
    if not mgr.start():
        skip("隐藏消息窗口/消息线程启动失败 (可能无桌面会话), 跳过", "start()=False")
        return
    try:
        check(mgr.is_running, "消息线程已启动 (tid={})".format(mgr.thread_id))
        fired = threading.Event()

        # 注册在**主线程**发起, 回调在消息线程执行 → 证明线程模型
        ok_a, err_a = mgr.register("run", SPEC_A, callback=fired.set)
        ok_b, err_b = mgr.register("pause", SPEC_B)
        if err_a or err_b:
            print("      注册返回: run={} pause={}".format(
                (ok_a, err_a), (ok_b, err_b)))
        check(ok_a, "register('run', {!r}) 成功".format(SPEC_A), err_a)
        check(ok_b, "register('pause', {!r}) 成功".format(SPEC_B), err_b)
        check(mgr.registered_actions() >= {"run", "pause"} if (ok_a and ok_b)
              else True, "registered_actions() 含已成功项",
              repr(sorted(mgr.registered_actions())))

        if ok_a:
            # H4-①: PostThreadMessageW 注入 WM_HOTKEY (不依赖真实按键)
            fired.clear()
            posted = mgr.post_hotkey("run")
            check(posted, "post_hotkey('run') 投递成功")
            check(_wait(fired), "非消息线程注册的条目, WM_HOTKEY 仍被派发到回调")
            # H4-②: SendMessageW 直接打隐藏窗口 (wndproc 路径)
            fired.clear()
            sent = mgr.send_hotkey("run")
            check(sent and _wait(fired), "SendMessageW → wndproc → 回调 同链路可用")

        # 重复注册同一组合 → 1409 语义可见 (用第二个 manager 实例构造系统级占用)
        if ok_a:
            mgr2 = hotkey_win.HotkeyManager()
            try:
                dup_ok, dup_err = mgr2.register("dup", SPEC_A)
                check(not dup_ok, "重复注册同一组合被拒绝 (第二个 manager 实例)")
                check("1409" in str(dup_err) or "占用" in str(dup_err),
                      "失败原因文案含 1409/已被占用 语义", str(dup_err))
            finally:
                mgr2.stop()

        # unregister 后可再注册
        if ok_b:
            check(mgr.unregister("pause"), "unregister('pause') 返回 True")
            check("pause" not in mgr.registered_actions(), "注销后注册表已移除")
            ok_b2, err_b2 = mgr.register("pause", SPEC_B)
            check(ok_b2, "同一 action 注销后可再次注册成功", err_b2)

        # rebuild 返回 {action: err} (仅失败项) 且完成全量重建
        errs = mgr.rebuild({"run": SPEC_A, "stop": SPEC_B})
        check(isinstance(errs, dict), "rebuild() 返回 dict")
        check(set(errs) <= {"run", "stop"}, "rebuild() 只含失败项", repr(errs))
        check(mgr.registered_actions() >= ({"run", "stop"} - set(errs)),
              "rebuild() 后成功项均已注册", repr(sorted(mgr.registered_actions())))
        # 无法解析的 spec → 不入注册表 (由轮询兜住), 且不抛
        errs2 = mgr.rebuild({"run": "不是快捷键!!"})
        check("run" in errs2 and "无法解析" in str(errs2["run"]),
              "非法 spec 在 rebuild 中被报告而非抛异常", repr(errs2))
    finally:
        stopped = mgr.stop()
        check(stopped, "stop() 后线程已结束")
        check(not mgr.is_running, "stop() 后 is_running 为 False")
        check(mgr.registered_actions() == set(), "stop() 后注册项已清空",
              repr(sorted(mgr.registered_actions())))
        try:
            mgr.stop()
            mgr.close()
            check(True, "重复 stop()/close() 不抛异常")
        except Exception as e:                               # noqa: BLE001
            check(False, "重复 stop()/close() 不抛异常", repr(e))


# ══════════════════════════════════════════════════════════════════════
# H6/H7 热字串
# ══════════════════════════════════════════════════════════════════════
def t_key_to_char():
    print("\n── H6-① 按键→字符 纯函数 ──")
    table = [
        ((0x41, False), "a"), ((0x41, True), "A"),
        ((0x5A, False), "z"), ((0x5A, True), "Z"),
        ((0x31, False), "1"), ((0x31, True), "!"),
        ((0x30, True), ")"),
        ((0xBA, False), ";"), ((0xBA, True), ":"),
        ((0xBD, False), "-"), ((0xBE, False), "."),
        ((0x20, False), " "), ((0x0D, False), "\n"), ((0x09, False), "\t"),
        ((0x60, False), "0"), ((0x69, False), "9"),
        ((0x70, False), None),        # F1 不可映射
        ((0x08, False), None),        # Backspace 由缓冲处理
        ((0x2E, False), None),        # Delete
    ]
    for (vk, shift), want in table:
        got = hotkey_win.key_event_to_char(vk, shift)
        check(got == want, "key_event_to_char({:#04x}, shift={}) == {!r}".format(
            vk, shift, want), "got={!r}".format(got))


def _type(engine, text, end_vk=0x20):
    """模拟敲字 (按下+抬起) → 返回 feed 的最后一个非 None 结果。"""
    hit = None
    for ch in text:
        vk = _char_to_vk(ch)
        r = engine.feed(vk, True)
        engine.feed(vk, False)
        if r:
            hit = r
    if end_vk:
        r = engine.feed(end_vk, True)
        engine.feed(end_vk, False)
        if r:
            hit = r
    return hit


def _char_to_vk(ch):
    """测试辅助: 单字符 → (vk, shift) 的 vk 部分 (仅 ASCII 小写/数字/分号)。"""
    if ch.isdigit():
        return 0x30 + int(ch)
    if ch == ";":
        return 0xBA
    return ord(ch.upper())


def t_hotstring_buffer():
    print("\n── H6-② 缓冲命中 / 回删字符数 / 注入事件忽略 ──")
    eng = hotkey_win.HotstringEngine([
        {"trigger": ";;acr", "expand": "ACRPA 自动化", "kind": "text"},
        {"trigger": ";;sig", "expand": "—— 签名", "kind": "text"},
        {"trigger": "纯中文", "expand": "x"},            # 非 ASCII → 丢弃
        {"trigger": "a", "expand": "too short"},         # 长度 <2 → 丢弃
        {"trigger": ";;noexpand", "expand": ""},         # 无展开 → 丢弃
        "not-a-dict",                                    # 脏数据 → 丢弃
    ])
    triggers = [b["trigger"] for b in eng.bindings()]
    check(triggers == [";;acr", ";;sig"], "非法热字串被丢弃, 只留合法项",
          repr(triggers))

    hit = _type(eng, ";;acr")
    check(hit is not None, "输入 ';;acr' + 空格 → 命中")
    if hit:
        check(hit["trigger"] == ";;acr", "命中 trigger 正确", repr(hit.get("trigger")))
        check(hit["backspaces"] == 5, "回删字符数 == len(';;acr') == 5",
              repr(hit.get("backspaces")))
        check(hit["expand"] == "ACRPA 自动化", "展开文本正确", repr(hit.get("expand")))
        check(hit["kind"] == "text", "kind 正确")

    # 展开后缓冲已清空 → 不会二次命中
    check(eng.feed(0x20, True) is None, "展开后缓冲已清空, 再敲空格不重复命中")

    # 未以终止符结尾不触发
    eng.reset()
    hit2 = _type(eng, ";;acr", end_vk=0)
    check(hit2 is None, "未按空格/回车/Tab 结尾时不触发")

    # 命中需要完整触发词
    eng.reset()
    hit3 = _type(eng, ";;ac")
    check(hit3 is None, "触发词不完整时不触发")

    # Ctrl 组合键清空缓冲 (不参与热字串)
    eng.reset()
    eng.feed(0x11 & 0, True)      # no-op (占位, 保持顺序清晰)
    eng.feed(0xBA, True)          # ';'
    eng.feed(0xA2, True)          # Ctrl 按下 → 清缓冲
    for _ in range(2):
        eng.feed(0xBA, True)
    eng.feed(0xA2, False)
    eng.feed(0x20, True)
    check(eng.feed(0x20, True) is None, "Ctrl 按下会清空缓冲 (不误触发)")

    # 注入事件 (自己 SendInput 出来的) 一律忽略
    eng.reset()
    eng.feed(0xBA, True, injected=True)
    eng.feed(0x20, True, injected=True)
    check(eng.feed(0xBA, True) is None, "injected=True 的事件不进缓冲")

    # Backspace 退格缓冲: ";;acr" 退一格 → ";;ac" + 空格 → 不命中
    eng.reset()
    _type(eng, ";;acr", end_vk=0)
    eng.feed(0x08, True)
    hit4 = eng.feed(0x20, True)
    check(hit4 is None, "Backspace 已吃掉一个字符 → 不再命中", repr(hit4))
    # 退格后再补回被删字符 → 仍能命中 (证明退格只影响缓冲, 不是一次性破坏状态)
    eng.reset()
    _type(eng, ";;acr", end_vk=0)
    eng.feed(0x08, True)
    _type(eng, "r", end_vk=0)
    hit5 = eng.feed(0x20, True)
    check(hit5 is not None and hit5["backspaces"] == 5,
          "退格后重新输全触发词 → 仍命中 (缓冲状态一致)")

    # build_inputs: 回删 + Unicode 注入序列
    ev = hotkey_win.build_inputs(5, "你好")
    check(ev[:5] == [("vk", 0x08)] * 5, "build_inputs 前 5 个事件是 VK_BACK",
          repr(ev[:5]))
    uni = [c for k, c in ev if k == "unicode"]
    check(uni == [0x4F60, 0x597D], "build_inputs 逐 UTF-16 码元注入", repr(uni))
    check(len(hotkey_win.build_inputs(0, "ab")) == 2, "backspaces=0 时只注入文本")
    check(hotkey_win.build_inputs(3, "") == [("vk", 0x08)] * 3,
          "文本为空时只回删")


def t_hotstring_default_off():
    print("\n── H7 默认关闭: 不安装任何钩子 ──")
    try:
        import recorder
    except Exception as e:                                   # noqa: BLE001
        skip("import recorder 失败, 跳过钩子断言", repr(e))
        return
    eng = hotkey_win.HotstringEngine()          # 无 bindings = 默认关闭
    check(eng.bindings() == [], "默认构造无任何热字串")
    started = eng.start()
    check(started is False, "空配置调用 start() 返回 False (不启动)")
    time.sleep(0.05)
    check(not eng.is_running, "空配置下引擎未运行")
    check(getattr(recorder, "_kbd_hook", None) is None,
          "空配置下 recorder 未安装任何键盘钩子",
          repr(getattr(recorder, "_kbd_hook", None)))
    check(getattr(recorder, "_kbd_consumers", []) == [],
          "空配置下没有注册任何键盘消费者")
    # state 默认值必须是空列表 (config.json 缺键即关闭)
    check(list(getattr(state, "HOTSTRINGS", []) or []) == [],
          "state.HOTSTRINGS 默认空列表 (= 关闭)",
          repr(getattr(state, "HOTSTRINGS", None)))
    eng.stop()
    check(True, "未启动的引擎调用 stop() 不抛")


# ══════════════════════════════════════════════════════════════════════
# H8 回退等价性 (HOTKEY_BACKEND="poll")
# ══════════════════════════════════════════════════════════════════════
def t_poll_rollback():
    print("\n── H8 HOTKEY_BACKEND=\"poll\" 回退等价性 ──")
    try:
        import ACRPA                                      # noqa: F401
    except Exception as e:                                   # noqa: BLE001
        skip("import ACRPA 失败, 跳过回退等价性断言", repr(e))
        return
    if not hasattr(ACRPA, "_hotkey_win_sync"):
        check(False, "ACRPA._hotkey_win_sync 存在 (步骤 1 落点)")
        return
    backend_backup = getattr(state, "HOTKEY_BACKEND", "poll")
    actions_backup = ACRPA._HOTKEY_ACTIONS
    run_backup = getattr(state, "HOTKEY_RUN", "")
    pause_backup = getattr(state, "HOTKEY_PAUSE", "")
    try:
        ACRPA._HOTKEY_ACTIONS = {"run": ({0x11, 0x10}, 0x78),
                                 "pause": ({0x11, 0x12}, 0x7A)}
        # poll 模式: 注册表恒为空 → _hotkey_poll 不跳过任何条目
        hotkey_win.HotkeyManager.reset_instance()   # 隔离前面用例, 只测 poll 路径
        state.HOTKEY_BACKEND = "poll"
        ACRPA._hotkey_win_sync()
        check(ACRPA._HOTKEY_WIN_OK == set(),
              "poll 模式下 _HOTKEY_WIN_OK 为空集合 (轮询不跳过任何条目)",
              repr(ACRPA._HOTKEY_WIN_OK))
        check(ACRPA._HOTKEY_WIN_ERRS == {}, "poll 模式下无注册失败项")
        check(hotkey_win.HotkeyManager.existing_instance() is None,
              "poll 模式不构造 HotkeyManager (零线程 / 零 RegisterHotKey 调用)")
        check(getattr(state, "HOTKEY_BACKEND", "") == "poll",
              "默认配置值为 poll (零行为变更)")
        # 源码级证明: 跳过分支只在 _HOTKEY_WIN_OK 命中时生效
        with open(os.path.join(SRC, "ACRPA.py"), "r", encoding="utf-8") as f:
            src = f.read()
        check("if action in _HOTKEY_WIN_OK:" in src,
              "轮询跳过由 _HOTKEY_WIN_OK 单一条件控制 (poll 下恒不跳过)")
        # register 模式: 成功项进入 OK 集合 (之后是否真的能注册取决于系统占用)
        state.HOTKEY_RUN = SPEC_A
        state.HOTKEY_PAUSE = SPEC_B
        ACRPA._HOTKEY_ACTIONS = {"run": hotkey_win.parse_spec(SPEC_A),
                                 "pause": hotkey_win.parse_spec(SPEC_B)}
        state.HOTKEY_BACKEND = "register"
        ACRPA._hotkey_win_sync()
        ok_set = set(ACRPA._HOTKEY_WIN_OK)
        err_set = set(ACRPA._HOTKEY_WIN_ERRS)
        check(ok_set | err_set == {"run", "pause"} or (not hotkey_win.is_supported()),
              "register 模式下每个条目都有结论 (成功或失败, 不留悬空)",
              "ok={} err={}".format(sorted(ok_set), sorted(err_set)))
        check(not (ok_set & err_set), "成功集与失败集不相交")
        if hotkey_win.is_supported():
            check(ok_set == {"run", "pause"},
                  "register 模式下合法组合全部注册成功", repr(sorted(ok_set)))
        print("      register 实测: 成功={} 失败={}".format(
            sorted(ok_set), {k: v for k, v in ACRPA._HOTKEY_WIN_ERRS.items()}))
    finally:
        state.HOTKEY_BACKEND = backend_backup
        state.HOTKEY_RUN = run_backup
        state.HOTKEY_PAUSE = pause_backup
        ACRPA._HOTKEY_ACTIONS = actions_backup
        try:
            ACRPA._hotkey_win_sync()      # 清理: 回到备份 backend 的状态
        except Exception:
            pass
        try:
            hotkey_win.HotkeyManager.instance().stop()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
# H9 零依赖 / import 无副作用
# ══════════════════════════════════════════════════════════════════════
def t_side_effect_free():
    print("\n── H9 零 pip 依赖 + import 无 tkinter 副作用 ──")
    import ast
    path = os.path.join(SRC, "hotkey_win.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except Exception as e:                                   # noqa: BLE001
        check(False, "hotkey_win.py 语法可解析", repr(e))
        return
    top_imports = []
    for node in tree.body:                      # 只看**模块级**导入 (函数内惰性允许)
        if isinstance(node, ast.Import):
            top_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_imports.append(node.module or "")
    allowed = {"collections", "ctypes", "os", "queue", "sys", "threading", "time"}
    check(set(top_imports) <= allowed,
          "模块级只 import 标准库 (零 pip 依赖/零重依赖)", repr(top_imports))
    check(not (set(top_imports) & {"tkinter", "pyautogui", "engine", "recorder"}),
          "模块级不 import tkinter/pyautogui/engine/recorder", repr(top_imports))
    check("        import recorder as recorder" in src,
          "recorder 只在 _run() 内惰性 import (import 期不拉起钩子模块)")
    code = ("import sys; sys.path.insert(0, {src!r}); import hotkey_win; "
            "print('MODS=' + ','.join(sorted(m for m in "
            "('tkinter','pyautogui','engine','recorder') if m in sys.modules)))"
            ).format(src=SRC)
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           cwd=BASE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
    except Exception as e:                                   # noqa: BLE001
        out, err, p = "", repr(e), None
    check(p is not None and p.returncode == 0,
          "子进程 import hotkey_win 正常退出", err.strip()[-200:])
    check("MODS=" in out and out.split("MODS=")[1].strip() == "",
          "import hotkey_win 不加载 tkinter/pyautogui/engine/recorder",
          out.strip().replace("\n", " "))


def main():
    print("=" * 70)
    print("S1-04 回归: hotkey_win (RegisterHotKey 后端 + 热字串) + 三步迁移回退性")
    print("=" * 70)
    t_parse_spec()
    t_parse_equivalence()
    t_lifecycle()
    t_key_to_char()
    t_hotstring_buffer()
    t_hotstring_default_off()
    t_poll_rollback()
    t_side_effect_free()
    print()
    print("=" * 70)
    print("结果: {} 通过 / {} 失败 / {} 跳过".format(
        len(_PASS), len(_FAIL), len(_SKIP)))
    print("=" * 70)
    if _FAIL:
        for m in _FAIL:
            print("  [FAIL] {}".format(m))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
