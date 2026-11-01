"""UI 加载/显示性能基准 (docs/UI优化方案-v3.md §5.3)。

默认跑「纯逻辑」项 (不需要窗口):
  - 日志批量渲染: 1000 条消息 → insert/update/tag_add 调用次数与耗时
  - 表格列策略: 640/800/1024/1440 宽度下的可见列与总宽度
带 --gui 时额外跑「实机」项 (需要可用的窗口会话):
  - 主窗口构建耗时 (crashlog 启动阶段)
  - 表格全量重建耗时 (500 / 2000 行)

用法::

    python tools/_bench_ui.py
    python tools/_bench_ui.py --gui

输出为可直接粘贴进方案文档的 Markdown 表格；GUI 项不可用时标 SKIP，退出码仍为 0。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ROWS = []


def _row(name, before, after, note=""):
    ROWS.append((name, str(before), str(after), note))


class _FakeText:
    """最小 Text 替身: 只统计调用次数/字符量, 让日志 flush 可在无窗口下度量。"""

    def __init__(self):
        self.inserts = 0
        self.updates = 0
        self.sees = 0
        self.tag_adds = 0
        self.chars = 0

    def winfo_exists(self):
        return True

    def index(self, spec=None):
        return "1.0"

    def insert(self, index, text):
        self.inserts += 1
        self.chars += len(text)

    def tag_add(self, *args):
        self.tag_adds += 1

    def see(self, *args):
        self.sees += 1

    def update_idletasks(self):
        self.updates += 1


def bench_log(n=1000):
    """日志批量渲染: N 条消息推进队列后一次 flush 的调用次数与耗时。"""
    import utils

    w = _FakeText()
    tlog = utils.ThreadSafeLog(w, log_dir=None)
    for i in range(n):
        tlog.put("基准日志行 {} 内容测试".format(i), "info", "bench.py", "bench_log", utils.LOG_INFO)
    t0 = time.perf_counter()
    tlog.flush(None)
    ms = (time.perf_counter() - t0) * 1000

    # 队列未清空的余量 (受 GUI_BATCH_MAX 限制): 继续冲干, 便于统计总量
    while not tlog._q.empty():
        tlog.flush(None)

    _row("日志 {} 条 · Text.insert 次数".format(n), "{}".format(n), w.inserts, "越多越慢")
    _row("日志 {} 条 · update_idletasks 次数".format(n), "{}".format(n), w.updates, "旧实现为 update()")
    _row("日志 {} 条 · flush 耗时(ms)".format(n), "-", "{:.1f}".format(ms))
    _row("日志 {} 条 · 字符总量".format(n), "-", w.chars)


def bench_columns():
    """列策略: 不同窗口宽度下的可见数据列。"""
    import ACRPA

    for width in (640, 760, 800, 1024, 1440):
        plan = ACRPA._tree_column_plan(width)
        total = 32 + (100 if "cmd" in plan else 0) + 65 * (len(plan) - 1)
        _row("列策略 @{}px".format(width), "9 参数列固定",
             "{} 列 ({}px)".format(len(plan), total), "含 #0")


def bench_gui():
    """实机: 构建耗时 + 表格重建耗时。"""
    try:
        import tkinter
        probe = tkinter.Tk()
        probe.destroy()
    except Exception as e:
        _row("GUI 基准", "-", "SKIP", str(e)[:60])
        return
    try:
        import ACRPA
        import state
        from scriptdata import ScriptData
    except Exception as e:
        _row("GUI 基准", "-", "SKIP", "import: {}".format(str(e)[:50]))
        return

    t0 = time.perf_counter()
    try:
        ACRPA.build_app()
        build_ms = (time.perf_counter() - t0) * 1000
        _row("主窗口构建耗时(ms)", "-", "{:.0f}".format(build_ms))
    except Exception as e:
        _row("主窗口构建耗时(ms)", "-", "SKIP", str(e)[:60])
        return

    try:
        # 预热: 首次重建含 tree/style 懒初始化, 直接计入会污染 500 行样本
        state._editor_rows = [ScriptData("等待", ["0"]) for _ in range(50)]
        ACRPA._editor_sync_to_tree()
        ACRPA.root.update_idletasks()
        for n in (500, 2000):
            samples = []
            for _ in range(2):      # 取第二次, 排除首轮树结构扩张的一次性成本
                state._editor_rows = [ScriptData("等待", ["{:.2f}".format(i)])
                                      for i in range(n)]
                t1 = time.perf_counter()
                ACRPA._editor_sync_to_tree()
                ACRPA.root.update_idletasks()
                samples.append((time.perf_counter() - t1) * 1000)
            ms = min(samples)
            _row("表格 {} 行重建(ms)".format(n), "-", "{:.0f}".format(ms),
                 "{:.0f}μs/行".format(ms * 1000 / n))
    except Exception as e:
        _row("表格重建", "-", "SKIP", str(e)[:60])

    # 待机背景合成: 改造前发生在首帧之前 (阻塞首帧), 现在轮到 idle 之后 (不阻塞首帧)
    try:
        import ui.bg_image as _bg
        for size in ((900, 500), (1400, 800)):
            t2 = time.perf_counter()
            photo = _bg.load_bg_photo(ACRPA.RES_DIR, ACRPA._IDLE_BG_NAME,
                                      alpha=ACRPA._IDLE_BG_ALPHA,
                                      bg_color="#FFFFFF", size=size,
                                      master=ACRPA.root)
            ms = (time.perf_counter() - t2) * 1000
            _row("待机背景合成 {}x{}(ms)".format(size[0], size[1]), "首帧内",
                 "{:.0f}".format(ms), "无图" if photo is None else "idle 后")
    except Exception as e:
        _row("待机背景合成", "首帧内", "SKIP", str(e)[:60])

    try:
        import crashlog
        for stage, cost, _detail in crashlog.startup_marks():
            _row("启动阶段 {}".format(stage), "-", "{}ms".format(cost))
    except Exception:
        pass

    try:
        ACRPA.root.destroy()
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    bench_columns()
    bench_log()
    if "--gui" in args:
        bench_gui()
    else:
        _row("实机基准 (构建/表格)", "-", "SKIP", "加 --gui 启用")

    print("| 用例 | 改造前 | 改造后 | 备注 |")
    print("| --- | --- | --- | --- |")
    for name, before, after, note in ROWS:
        print("| {} | {} | {} | {} |".format(name, before, after, note))
    print("\n提示: 「改造前」列在本轮实施前未留档的项显示为 '-'，对比请用 git 回溯旧版本重跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
