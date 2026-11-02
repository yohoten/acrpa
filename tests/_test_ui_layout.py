"""UI 布局 / 加载优化的纯逻辑回归 (docs/UI优化方案-v3.md §5.1 P0-7)。

不建窗口, 只断言可离线验证的部分:

  1. 列策略      : 宽度 → 可见数据列数量/顺序 (窄屏不再 9 列全显)
  2. 行 tags     : 命令配色 tag 只在命中时挂载; 斑马纹按行号
  3. 日志批量刷  : N 条消息 → insert/update 次数有上界, 且文本顺序不变
  4. 图例判定    : 按当前脚本命令集合收窄/隐藏
  5. 静态断言    : 关键实现不被回退 (无循环内 tag 查询、标题走常量)

用法: python tools/_test_ui_layout.py  → 退出码 0 表示全绿。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_fails = 0
_checks = 0


def check(name, cond, detail=None):
    global _fails, _checks
    _checks += 1
    if cond:
        print("[OK]   {}".format(name))
    else:
        _fails += 1
        print("[FAIL] {}  {}".format(name, "" if detail is None else repr(detail)))


class _FakeText:
    """记录文本与调用次数的 Text 替身。"""

    def __init__(self):
        self.text = []
        self.inserts = 0
        self.updates = 0
        self.sees = 0
        self.tag_adds = 0

    def winfo_exists(self):
        return True

    def index(self, spec=None):
        return "1.0"

    def insert(self, index, text):
        self.inserts += 1
        self.text.append(text)

    def tag_add(self, *args):
        self.tag_adds += 1

    def see(self, *args):
        self.sees += 1

    def update_idletasks(self):
        self.updates += 1


def t_column_plan(A):
    print("\n== 1. 列策略: 宽度 → 可见数据列 ==")
    cases = [(640, 4), (759, 4), (760, 7), (1023, 7), (1024, 10), (1920, 10)]
    ok = True
    detail = []
    for width, expect in cases:
        plan = A._tree_column_plan(width)
        detail.append((width, len(plan)))
        if len(plan) != expect or plan[0] != "cmd":
            ok = False
    check("1a. 各断点可见列数符合设计", ok, detail)

    narrow = A._tree_column_plan(640)
    check("1b. 窄屏隐藏尾部参数列",
          "p4" not in narrow and "p9" not in narrow and "p3" in narrow, narrow)
    wide = A._tree_column_plan(1440)
    check("1c. 宽屏 9 个参数列全显",
          wide == A._TREE_DATA_COLS, wide)
    check("1d. 列策略单调不减",
          len(A._tree_column_plan(640)) <= len(A._tree_column_plan(800))
          <= len(A._tree_column_plan(1440)))

    check("1e. 640px 下总宽不再溢出最小窗口",
          32 + 100 + 65 * (len(narrow) - 1) <= 640,
          (32 + 100 + 65 * (len(narrow) - 1)))
    check("1f. 逻辑列 0..9 与列名一一对应",
          A._TREE_DATA_COLS == ("cmd",) + tuple("p{}".format(i) for i in range(1, 10))
          and len(A._TREE_COL_INDEX) == 10,
          A._TREE_DATA_COLS)


def t_row_tags(A):
    print("\n== 2. 行 tags: 配色命中 + 斑马纹 ==")
    check("2a. 命中命令带配色 tag", A._row_tags("找图", 1) == ("找图",),
          A._row_tags("找图", 1))
    check("2b. 偶数行追加 even", A._row_tags("找图", 0) == ("找图", "even"),
          A._row_tags("找图", 0))
    check("2c. 未登记命令不挂配色 tag", A._row_tags("不存在的命令", 1) == (),
          A._row_tags("不存在的命令", 1))
    check("2d. 空命令仅斑马纹", A._row_tags("", 0) == ("even",),
          A._row_tags("", 0))


def t_log_batch():
    print("\n== 3. 日志批量渲染: 调用次数有上界 + 顺序不变 ==")
    import utils

    n = 1000
    w = _FakeText()
    tlog = utils.ThreadSafeLog(w, log_dir=None)
    for i in range(n):
        tlog.put("行-{:04d}".format(i), "info", "t.py", "t", utils.LOG_INFO)
    tlog.flush(None)
    while not tlog._q.empty():
        tlog.flush(None)

    check("3a. 单批 insert 次数远小于消息数", w.inserts <= 3, w.inserts)
    check("3b. update_idletasks 次数远小于消息数", w.updates <= 3, w.updates)
    check("3c. see 次数远小于消息数", w.sees <= 3, w.sees)

    joined = "".join(w.text)
    first = joined.split("\n")[0]
    check("3d. 首行格式仍为 [时间] [级别] 消息",
          first.startswith("[") and "] [INFO] 行-0000" in first, first)
    idx = [joined.find("行-{:04d}".format(i)) for i in range(n)]
    check("3e. 1000 条全部落地", all(i >= 0 for i in idx), idx[:5])
    check("3f. 顺序与写入顺序一致", idx == sorted(idx))

    check("3g. 内存缓冲同步累积 (导出不受影响)",
          len(tlog._buffer) == n, len(tlog._buffer))

    # 控件已销毁: 不得抛异常, 也不得丢文件侧写入
    w2 = _FakeText()

    class _Gone(_FakeText):
        def winfo_exists(self):
            return False

    tlog2 = utils.ThreadSafeLog(_Gone(), log_dir=None)
    tlog2.put("关窗后的日志", None, "t.py", "t", utils.LOG_INFO)
    ok = True
    try:
        tlog2.flush(None)
    except Exception as e:
        ok = False
        detail = repr(e)
    else:
        detail = None
    check("3h. 控件销毁后 flush 不抛异常", ok, detail)


def t_legend():
    print("\n== 4. 图例按需显示判定 ==")
    import ACRPA
    import state
    from scriptdata import ScriptData

    old = getattr(state, "_editor_rows", None)
    try:
        state._editor_rows = [ScriptData("找图"), ScriptData("等待")]
        used = ACRPA._legend_used_names()
        check("4a. 收集脚本用到的命令类型", used == {"找图", "等待"}, used)

        state._editor_rows = []
        check("4b. 空脚本 → 无命中 (整条图例可隐藏)",
              ACRPA._legend_used_names() == set())

        names = tuple(n for n in "找图/区域找图".split("/") if n)
        check("4c. 分组名拆分正确", names == ("找图", "区域找图"), names)
    finally:
        if old is not None:
            state._editor_rows = old

    check("4d. 图例分组容器存在 (build 期填充)", isinstance(ACRPA._LEGEND_GROUPS, list))


def t_static(A):
    print("\n== 5. 静态断言: 关键实现不被回退 ==")
    src = os.path.join(SRC, "ACRPA.py")
    with open(src, "r", encoding="utf-8") as f:
        code = f.read()
    check("5a. 表格重建不再循环内查询 tags",
          'tree.item(item, "tags")' not in code)
    check("5b. 列策略使用 displaycolumns",
          "displaycolumns=plan" in code)
    check("5c. <Configure> 走去抖入口",
          'tree.bind("<Configure>", _tree_columns_on_configure, add=True)' in code)
    check("5d. 标题栏文案引用常量",
          "text=TITLE_DEFAULT_TEXT" in code)
    check("5e. 背景合成改为首帧后调度",
          "root.after_idle(_sync_idle_bg)" in code)
    check("5f. init_fonts 只调用一次",
          code.count("utils.init_fonts(root)") == 1,
          code.count("utils.init_fonts(root)"))
    check("5g. 命令配色 tag 一次性预配置入口存在",
          "def _preconfig_cmd_tags(" in code)
    # v3 §7: 三个回退开关必须真实存在 (置 False 即恢复旧行为), 否则回退承诺是空的
    check("5h. 回退开关 _TREE_AUTO_COLS / _LEGEND_AUTO / _IDLE_BG_DEFER 齐备",
          all(k in code for k in ("_TREE_AUTO_COLS = True",
                                  "_LEGEND_AUTO = True",
                                  "_IDLE_BG_DEFER = True")))
    check("5i. 背景合成确实受 _IDLE_BG_DEFER 控制",
          "if _IDLE_BG_DEFER:" in code)


def t_window_shell(A):
    print("\n== 6. 从窗口骨架 (ui/window_shell) ==")
    try:
        from ui.window_shell import shell_size
    except Exception as e:
        check("6a. 可导入 ui.window_shell", False, e)
        return
    import utils as _u
    md = shell_size("md")
    check("6a. size 语义取 TOKENS dlg_md_*", md == (_u.TOKENS["dlg_md_w"],
                                                    _u.TOKENS["dlg_md_h"]), md)
    check("6b. size 返回设计 px (不预缩放)",
          shell_size("lg") == (_u.TOKENS["dlg_lg_w"], _u.TOKENS["dlg_lg_h"])
          and shell_size((600, 550)) == (600, 550), shell_size((600, 550)))
    check("6c. 未知 size 回退 md", shell_size("nope") == md, shell_size("nope"))
    check("6d. 缩放口径统一: 尺寸交给 place_dialog 缩放 (不在此处 sp)",
          'shell_size(size, C)' not in open(
              os.path.join(SRC, "ui", "window_shell.py"), encoding="utf-8").read())

    shell_src = open(os.path.join(SRC, "ui", "window_shell.py"), encoding="utf-8").read()
    check("6e. 骨架留白走 utils.sp/token", "utils.sp(" in shell_src)
    check("6f. 骨架统一居中/钳制走 place_dialog", "utils.place_dialog(" in shell_src)

    dlg_src = open(os.path.join(SRC, "dialogs.py"), encoding="utf-8").read()
    check("6g. dialogs 试点已套骨架", "apply_window_shell(" in dlg_src)
    check("6h. 版本历史窗口不再硬编码 minsize",
          "minsize(500, 350)" not in dlg_src)


def t_bg_cache(A):
    print("\n== 7. 待机背景中间尺寸缓存 (P1) ==")
    try:
        import ui.bg_image as B
        from PIL import Image  # noqa: F401
    except Exception as e:
        check("7a. PIL / ui.bg_image 可用", False, e)
        return
    if not B._load_pil():
        check("7a. PIL / ui.bg_image 可用", False, B.get_last_error() or "PIL 不可用")
        return
    check("7a. PIL / ui.bg_image 可用", True)

    res_dir = os.path.join(ROOT, "res")
    name, alpha, bgc = "bg-ac.ico", 0.55, "#FFFFFF"
    path = B.resolve_icon_path(res_dir, name)
    if not path:
        check("7b. 背景资源可解析", False, res_dir)
        return
    check("7b. 背景资源可解析", True)

    captured = {}

    class _StubPhoto:
        def __init__(self, img, master=None):
            captured["img"] = img

    real_photo = B.ImageTk.PhotoImage
    B.ImageTk.PhotoImage = _StubPhoto
    try:
        def legacy(size, mode):
            src = B._trim_transparent(B._open_rgba(path))
            img = (B._tile_to_size(src, size) if mode == "tile"
                   else B._contain_center(src, size))
            ch = img.getchannel("A").point(lambda v: int(round(v * alpha)))
            img.putalpha(ch)
            base = Image.new("RGBA", size, bgc)
            return Image.alpha_composite(base, img).convert("RGB")

        for size, mode in (((900, 520), "contain"), ((200, 150), "tile")):
            B.clear_cache()
            B.load_bg_photo(res_dir, name, alpha=alpha, bg_color=bgc,
                            size=size, mode=mode)
            new = captured.get("img")
            same = (new is not None
                    and new.tobytes() == legacy(size, mode).tobytes())
            check("7c. 缓存路径与旧实现逐像素一致 {} {}".format(size, mode), same)

        # 同宽度、高度微调: 不应再产生新的图块 (尺寸未变 → 命中)
        B.clear_cache()
        B.load_bg_photo(res_dir, name, alpha=alpha, bg_color=bgc, size=(1200, 600))
        tiles_after_first = len(B._tile_cache)
        B.load_bg_photo(res_dir, name, alpha=alpha, bg_color=bgc, size=(1200, 620))
        check("7d. 同宽高度微调复用图块缓存",
              len(B._tile_cache) == tiles_after_first and tiles_after_first >= 1,
              (tiles_after_first, len(B._tile_cache)))
        check("7e. 源图只解析一次", len(B._src_cache) == 1, len(B._src_cache))

        B.clear_cache()
        check("7f. clear_cache 同时清空三级缓存",
              not B._cache and not B._src_cache and not B._tile_cache)
    finally:
        B.ImageTk.PhotoImage = real_photo


def _scrollbar_calls(text):
    """返回所有 `...Scrollbar(` 调用括号内的参数文本 (平衡括号扫描, 不误伤后续控件)。"""
    out = []
    start = 0
    key = "Scrollbar("
    while True:
        i = text.find(key, start)
        if i < 0:
            return out
        j = i + len(key)
        depth, buf = 1, []
        while j < len(text) and depth > 0:
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(ch)
            j += 1
        out.append("".join(buf))
        start = i + len(key)


def t_ui_details():
    """§5.2 D 清单: 滚动条厚度 / tooltip 延迟的「口径唯一化」是否被守住。"""
    print("\n== 8. D 清单口径 (滚动条 8px / tooltip 400ms) ==")
    import re

    with open(os.path.join(SRC, "utils.py"), encoding="utf-8") as fp:
        utils_code = fp.read()
    with open(os.path.join(SRC, "ui", "window_shell.py"), encoding="utf-8") as fp:
        shell_code = fp.read()
    with open(os.path.join(SRC, "ui", "theme.py"), encoding="utf-8") as fp:
        theme_code = fp.read()

    check("8a. TOKENS 含 sb_w=8 / sb_w_thin=6",
          '"sb_w": 8, "sb_w_thin": 6' in utils_code)
    check("8b. window_shell.sb_width() 提供统一取厚入口",
          "def sb_width(" in shell_code and '"sb_w"' in shell_code
          and '"sb_w_thin"' in shell_code)

    # 全仓扫描: Scrollbar 构造里不允许再出现字面量宽度 (6/7/8/16 都是历史遗留)
    offenders = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as fp:
                    text = fp.read()
            except Exception:
                continue
            for args in _scrollbar_calls(text):
                # (?<![A-Za-z_]) 排除 elementborderwidth=0 / borderwidth=1 这类同名尾巴
                if re.search(r"(?<![A-Za-z_])width\s*=\s*[\d.]+", args):
                    offenders.append(os.path.relpath(path, SRC))
                    break
    check("8c. 全仓滚动条无字面量宽度 (一律 token 派生)",
          not offenders, offenders[:4])

    check("8d. ttk 滚动条样式压到 sp(sb_w)",
          'style.configure("TScrollbar"' in theme_code
          and 'width=_sb' in theme_code)
    check("8e. tooltip 统一延迟 400ms",
          "TOOLTIP_DELAY_MS = 400" in utils_code
          and "widget.after(" in utils_code)
    check("8f. attach_tooltip 允许逐处覆盖延迟",
          "def attach_tooltip(widget, text, delay=None)" in utils_code)
    check("8g. 离开/重复进入会取消待弹定时器",
          "_cancel_tip_job" in utils_code and "after_cancel" in utils_code)

    # D1: 字体一律走角色名, 不允许再出现 font=("Family", size, "weight") 字面量
    font_offenders = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as fp:
                    text = fp.read()
            except Exception:
                continue
            if re.search(r"""font\s*=\s*\(\s*['"]""", text):
                font_offenders.append(os.path.relpath(path, SRC))
    check("8h. 全仓无内联字族字面量 (字体走命名字体角色)",
          not font_offenders, font_offenders[:4])
    check("8i. 大号等宽数字有了字体角色 FONT_PIN",
          "FONT_PIN" in utils_code and '"ACRPA_PIN"' in utils_code)

    # §2.1 高度断点: 窗口变矮 → 日志面板默认折叠 (单入口 + 150ms 去抖 + 可回退)
    with open(os.path.join(SRC, "ACRPA.py"), encoding="utf-8") as fp:
        app_code = fp.read()
    with open(os.path.join(SRC, "ui", "log_dock.py"), encoding="utf-8") as fp:
        dock_code = fp.read()
    check("8j. 高度断点阈值 _LOG_BREAK_H=560 且经 sp() 换算",
          "_LOG_BREAK_H = 560" in app_code and "utils.sp(_LOG_BREAK_H)" in app_code)
    check("8k. 断点走单一入口 + 150ms 去抖",
          "def _apply_log_auto_collapse(" in app_code
          and '"<Configure>", _log_breakpoint_on_configure' in app_code
          and "root.after(_TREE_COLS_DEBOUNCE" in app_code)
    check("8l. log_dock 提供幂等 set_visible/is_visible",
          "def is_visible(" in dock_code
          and "def set_visible(" in dock_code
          and "if want == is_visible():" in dock_code)
    check("8m. 回退开关 _LOG_AUTO_COLLAPSE 存在",
          "_LOG_AUTO_COLLAPSE = True" in app_code)


def main():
    try:
        import ACRPA
    except Exception as e:
        print("[FAIL] 无法 import ACRPA: {}".format(e))
        return 1
    t_column_plan(ACRPA)
    t_row_tags(ACRPA)
    t_log_batch()
    t_legend()
    t_static(ACRPA)
    t_window_shell(ACRPA)
    t_bg_cache(ACRPA)
    t_ui_details()

    print("\n=== {} checks, FAIL={} ===".format(_checks, _fails))
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
