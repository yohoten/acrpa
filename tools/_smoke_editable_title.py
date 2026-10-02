# -*- coding: utf-8 -*-
"""_smoke_editable_title.py  (功能实现子任务：顶部标题双击可编辑 验证)

验证 ACRPA 界面顶部标题 Label「A/C RPA Automation Workflow」的双击内联编辑功能：
  1) 启动后标题文本 == 默认文案
  2) 双击 <Double-Button-1> 后出现同字体/同前景背景色、无边框的编辑 Entry
  3) <Return> 提交后 Label 文本更新为新值, 编辑控件消失, 外观恢复
  4) 会话变量已更新, 但 config.json 内容/md5/mtime 未被本次编辑改动
  5) 空/纯空白文本 → 回退为默认文案 (经 <FocusOut> 自动保存)
  6) <Escape> 放弃本次修改, 保持编辑前文本
  7) 另起干净子进程重新 import 应用 → 标题回到默认 (证明「重启回默认」)

判定: 全部通过 → 退出码 0; 任一失败 → 1
输出落盘 tools\\_smoke_result_phase2\\_smoke_editable_title.out
"""

import os
import sys
import time
import hashlib
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (ROOT, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
OUTDIR = os.path.join(ROOT, "tools", "_smoke_result_phase2")
os.makedirs(OUTDIR, exist_ok=True)
REPORT = os.path.join(OUTDIR, "_smoke_editable_title.out")

DEFAULT = "A/C RPA Automation Workflow"

lines = []
_results = []  # (name, ok, detail)


def emit(m=""):
    print(m)
    lines.append(m)


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    emit("[%s] %s %s" % ("OK" if ok else "FAIL", name, detail))
    return bool(ok)


def file_state(path):
    """返回 (存在, 内容 md5, 字节数, mtime); 文件不存在时其余为 None。"""
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            data = f.read()
        return (True, hashlib.md5(data).hexdigest(), len(data), st.st_mtime)
    except Exception:
        return (False, None, None, None)


def main():
    emit("=== ACRPA 顶部标题双击可编辑 验证 ===")
    emit("解释器: %s" % sys.executable)
    emit("默认文案: %r" % (DEFAULT,))

    emit("[IMPORT] import ACRPA ...")
    import ACRPA
    import state as state_mod

    root = ACRPA.root
    title_lbl = ACRPA.title_lbl
    title_bar = ACRPA.title_bar
    emit("[IMPORT] ACRPA 导入完成, root=%r" % (root,))

    # 让布局完成 (winfo_x / winfo_width 才有效)
    root.deiconify()
    for _ in range(6):
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            pass
        time.sleep(0.05)

    cfg_path = getattr(state_mod, "CONFIG_PATH", None)
    emit("[CFG] config.json 路径: %r" % (cfg_path,))

    def find_edit_entry():
        return getattr(ACRPA, "_title_edit_entry", None)

    def find_entry_child():
        for w in title_bar.winfo_children():
            try:
                if w.winfo_class() == "Entry":
                    return w
            except Exception:
                pass
        return None

    def dblclick_edit():
        # Tk 不允许直接 event_generate("<Double-Button-1>") (Double 修饰符被拒),
        # 因此先连点两次 <Button-1> 尝试由 Tk 自行合成双击; 若未触发则直接调用
        # 已绑定的回调 (绑定存在性已在断言 2a-binding 单独校验)。
        try:
            title_lbl.event_generate("<Button-1>", when="now")
            title_lbl.event_generate("<Button-1>", when="now")
        except Exception:
            pass
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
        ent = find_edit_entry()
        if ent is None:
            ACRPA._start_title_edit()
            root.update_idletasks()
            root.update()
            time.sleep(0.08)
            ent = find_edit_entry()
        return ent

    # ---- 1. 启动后标题 == 默认 ----
    emit("-" * 64)
    t0 = str(title_lbl.cget("text"))
    check("1). 启动标题为默认文案", t0 == DEFAULT, "实际=%r" % (t0,))

    # 记录编辑前 config.json 状态
    cfg_before = file_state(cfg_path) if cfg_path else (False, None, None, None)
    emit("[CFG] 编辑前: %r" % (cfg_before,))

    # 记录标题 Label 原始字体/前景/背景 (用于比对编辑控件外观)
    lbl_font = str(title_lbl.cget("font"))
    lbl_fg = str(title_lbl.cget("fg"))
    lbl_bg = str(title_lbl.cget("bg"))
    emit("[LBL] font=%r fg=%r bg=%r" % (lbl_font, lbl_fg, lbl_bg))

    # ---- 2. 双击 → 出现同字体/同色无边框 Entry ----
    emit("-" * 64)
    bscript = title_lbl.bind("<Double-Button-1>")
    check("2a-binding). 标题已注册 <Double-Button-1> 双击绑定",
          bool(bscript), "bind=%r" % (bscript,))
    ent = dblclick_edit()
    child_ent = find_entry_child()
    check("2a). 双击后产生编辑控件 (Entry)",
          ent is not None and child_ent is not None,
          "entry=%r child=%r" % (ent, child_ent))
    if ent is not None:
        e_font = str(ent.cget("font"))
        e_fg = str(ent.cget("fg"))
        e_bg = str(ent.cget("bg"))
        e_bw = int(ent.cget("borderwidth"))
        e_hl = int(ent.cget("highlightthickness"))
        check("2b). 编辑控件字体与标题一致", e_font == lbl_font,
              "entry=%r lbl=%r" % (e_font, lbl_font))
        check("2c). 编辑控件前景/背景与标题一致",
              e_fg == lbl_fg and e_bg == lbl_bg,
              "entry(fg=%r,bg=%r) lbl(fg=%r,bg=%r)"
              % (e_fg, e_bg, lbl_fg, lbl_bg))
        check("2d). 编辑控件无可见边框", e_bw == 0 and e_hl == 0,
              "borderwidth=%d highlightthickness=%d" % (e_bw, e_hl))
        check("2e). 编辑控件预填当前文本", str(ent.get()) == t0,
              "entry=%r" % (str(ent.get()),))

    # ---- 3. 修改文本 + <Return> → 保存/恢复 ----
    emit("-" * 64)
    NEW = "自定义标题 ABC-123"
    if ent is not None:
        ent.delete(0, "end")
        ent.insert(0, NEW)
        try:
            ent.focus_force()
        except Exception:
            pass
        ent.event_generate("<Return>", when="now")
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
    t1 = str(title_lbl.cget("text"))
    check("3a). <Return> 后 Label 更新为新值", t1 == NEW, "实际=%r" % (t1,))
    check("3b). <Return> 后编辑控件消失",
          find_edit_entry() is None and find_entry_child() is None,
          "entry=%r child=%r" % (find_edit_entry(), find_entry_child()))
    check("3c). 会话变量已更新",
          getattr(ACRPA, "_title_text_session", None) == NEW,
          "session=%r" % (getattr(ACRPA, "_title_text_session", None),))

    # ---- 4. config.json 未被本次编辑改动 ----
    emit("-" * 64)
    cfg_after = file_state(cfg_path) if cfg_path else (False, None, None, None)
    emit("[CFG] 编辑后: %r" % (cfg_after,))
    check("4). config.json 内容/md5/mtime 未被编辑改动",
          cfg_before == cfg_after,
          "before=%r after=%r" % (cfg_before, cfg_after))

    # ---- 5. 空/纯空白 → 回退默认 (FocusOut 自动保存) ----
    emit("-" * 64)
    ent2 = dblclick_edit()
    if ent2 is not None:
        ent2.delete(0, "end")
        ent2.insert(0, "   ")  # 纯空白
        try:
            ent2.focus_force()
        except Exception:
            pass
        ent2.event_generate("<FocusOut>", when="now")
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
    t2 = str(title_lbl.cget("text"))
    check("5a). 空/纯空白回退为默认文案", t2 == DEFAULT, "实际=%r" % (t2,))
    check("5b). FocusOut 后编辑控件消失",
          find_edit_entry() is None and find_entry_child() is None,
          "entry=%r child=%r" % (find_edit_entry(), find_entry_child()))
    check("5c). 会话变量回退为默认",
          getattr(ACRPA, "_title_text_session", None) == DEFAULT,
          "session=%r" % (getattr(ACRPA, "_title_text_session", None),))

    # ---- 6. <Escape> 放弃本次修改 ----
    emit("-" * 64)
    ent3 = dblclick_edit()
    if ent3 is not None:
        ent3.delete(0, "end")
        ent3.insert(0, "将被放弃的标题")
        try:
            ent3.focus_force()
        except Exception:
            pass
        ent3.event_generate("<Escape>", when="now")
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
    t3 = str(title_lbl.cget("text"))
    check("6). <Escape> 放弃修改, 保持编辑前文本", t3 == DEFAULT, "实际=%r" % (t3,))

    # ---- 7. 干净子进程重新 import → 标题回默认 ----
    emit("-" * 64)
    if os.path.exists(PY):
        code = (
            "import sys, os;"
            "sys.path.insert(0, r'{root}');"
            "sys.path.insert(0, r'{src}');"
            "import ACRPA;"
            "print('CHILD_TITLE=' + str(ACRPA.title_lbl.cget('text')));"
            "print('CHILD_SESSION=' + str(ACRPA._title_text_session));"
            "sys.stdout.flush();"
            "os._exit(0)"
        ).format(root=ROOT, src=SRC)
        try:
            r = subprocess.run([PY, "-X", "utf8", "-c", code],
                               cwd=ROOT, capture_output=True, text=True,
                               timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            emit("[CHILD] rc=%s out=%s" % (r.returncode, out.replace("\n", " ")))
            ok = ("CHILD_TITLE=" + DEFAULT) in out
            check("7). 干净子进程重新 import → 标题为默认 (重启回默认)", ok,
                  "rc=%s" % (r.returncode,))
        except Exception as e:
            check("7). 干净子进程重新 import → 标题为默认 (重启回默认)", False,
                  "异常: %r" % (e,))
    else:
        check("7). 干净子进程重新 import → 标题为默认 (重启回默认)", False,
              "未找到解释器 %s" % PY)

    # ---- 收尾 ----
    emit("-" * 64)
    try:
        root.destroy()
    except Exception:
        pass

    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    emit("=== 结果: %s  通过 %d/%d, 失败 %d ==="
         % ("OK" if failed == 0 else "FAIL", passed, total, failed))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
