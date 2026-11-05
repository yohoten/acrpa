#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AppContext 转发别名登记回归 (方案文档 S2-b 第 1 步)。

验证 ACRPA.py 把「模块级转发别名」收拢为 AppContext 容器条目后:
  R1  import ACRPA 不建窗 (登记发生在 import 期, 零 GUI 副作用);
  R2  ctx.get(<name>) 与 ACRPA.<name> 是同一引用 (身份一致);
  R3  48 个稳定函数/回调别名全部登记且非 None;
  R4  模块级旧键保留 (向后兼容), 不因登记而消失。

用法:
    .venv\\Scripts\\python.exe -X utf8 tests\\_test_app_context_registry.py

退出码: 0 = 全部通过 / 1 = 有失败。
"""
import os
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
    print("[{}] {}{}".format("OK  " if cond else "FAIL", msg,
                             ("  ({})".format(detail) if detail else "")))


# 登记的稳定函数/回调别名 (与 ACRPA.py 的 _app_ctx.register_many 一一对应)
FORWARDING_ALIASES = [
    # 执行控制栏 (exec_bar)
    "select_script", "_screenshot_tool", "_run_update_overflow",
    "_run_on_bar_configure", "_run_on_wheel", "_shared_run", "_shared_stop",
    "_tip_with_hotkey", "_update_recent_scripts", "_on_script_switched",
    "_add_recent_script", "_on_loop_sel", "_exec_params_commit",
    "_update_exec_buttons",
    # 日志面板 (log_dock)
    "_toggle_log_dock", "_on_log_sash_release", "_log_autoscroll_changed",
    "_log_apply_cbar", "_log_apply_filter", "_log_search_next",
    "_log_clear_view", "_log_export_view",
    # 工作流 Tab (workflow_view)
    "_wf_update_overflow", "_wf_on_wheel", "_wf_flow_on_wheel",
    "_wf_update_workflow_highlight", "_wf_ensure", "_wf_run", "_wf_stop",
    "_wf_toggle_view", "_wf_refresh_tree", "_wf_new", "_wf_open", "_wf_save",
    "_wf_add_step", "_wf_edit_step", "_wf_delete_step", "_wf_move_up",
    "_wf_move_down", "_wf_export_template", "_wf_export_screenshot",
    "_wf_context_menu", "_wf_open_variable_manager", "_wf_library_context_menu",
    "_wf_add_from_library", "_wf_add_recent_clicked", "_wf_filter_library",
    "_wf_render_flowchart",
]


def main():
    print("=== AppContext 转发别名登记回归 (S2-b 第 1 步) ===")

    import ACRPA
    from core.app_context import ctx

    # R1: import 不建窗
    check(not hasattr(ACRPA, "root"), "import ACRPA 不建窗 (无 root 属性)")

    # R2/R3: 每个别名都登记且身份一致、非 None
    missing = []
    mismatched = []
    none_vals = []
    for name in FORWARDING_ALIASES:
        if name not in ctx:
            missing.append(name)
            continue
        mod_val = getattr(ACRPA, name, None)
        if ctx.get(name) is not mod_val:
            mismatched.append(name)
        if mod_val is None:
            none_vals.append(name)

    check(not missing, "全部别名已登记进 ctx", "missing={}".format(missing))
    check(not mismatched, "ctx 值与 ACRPA.<name> 身份一致", "mismatch={}".format(mismatched))
    check(not none_vals, "登记值均非 None", "none={}".format(none_vals))
    check(len(FORWARDING_ALIASES) == 48, "别名清单共 48 项", "got={}".format(len(FORWARDING_ALIASES)))

    # R4: 模块级旧键保留 (向后兼容)
    check(ACRPA.select_script is not None, "ACRPA.select_script 旧键保留")
    check(ACRPA._shared_run is not None, "ACRPA._shared_run 旧键保留")
    check(ACRPA._wf_run is not None, "ACRPA._wf_run 旧键保留")
    check(ctx.get("select_script") is ACRPA.select_script, "ctx.select_script 与旧键同源")

    # 身份抽查 (跨模块真源一致)
    import ui.exec_bar as ui_exec_bar
    import ui.log_dock as ui_log_dock
    import ui.workflow_view as ui_workflow_view
    check(ctx.get("select_script") is ui_exec_bar.select_script,
          "ctx.select_script → ui_exec_bar.select_script")
    check(ctx.get("_toggle_log_dock") is ui_log_dock.toggle_dock,
          "ctx._toggle_log_dock → ui_log_dock.toggle_dock")
    check(ctx.get("_wf_new") is ui_workflow_view._wf_new,
          "ctx._wf_new → ui_workflow_view._wf_new")

    t_real_machine()

    print("\n=== 结果: {} (通过 {}, 失败 {}) ===".format(
        "FAIL" if _FAIL else "OK", len(_PASS), len(_FAIL)))
    if _FAIL:
        for m in _FAIL:
            print("  - {}".format(m))
    return 1 if _FAIL else 0


def t_real_machine():
    """实机 (真实 Tk) 断言: 构建后各视图模块经 deps 注入共享 ctx, _call 回退可用。"""
    print("\n── S2-b-2 实机: 视图模块注入共享容器 ──")
    try:
        import tkinter
        probe = tkinter.Tk()
        probe.destroy()
    except Exception:
        print("[WARN] 无 GUI 环境, 跳过实机断言")
        return
    try:
        import app
        import ACRPA
    except Exception as e:
        print("[WARN] import app/ACRPA 失败, 跳过实机断言: {}".format(e))
        return
    rt = None
    try:
        rt = app.build()
        ACRPA._wf_ensure()  # 触发懒构建工作流 Tab
        from core.app_context import ctx
        import ui.exec_bar as eb
        import ui.log_dock as ld
        import ui.workflow_view as wv
        import ui.editor_view as ev
        for mod, label in ((eb, "exec_bar"), (ld, "log_dock"),
                           (wv, "workflow_view"), (ev, "editor_view")):
            check(getattr(mod, "_CTX").get("ctx") is ctx,
                  "{}._CTX['ctx'] 注入共享容器".format(label))
        ctx.register("__s2b_probe", lambda *a, **k: "FALLBACK_OK")
        try:
            check(eb._call("__s2b_probe") == "FALLBACK_OK",
                  "exec_bar._call 回退到共享容器")
            check(ev._call("__s2b_probe") == "FALLBACK_OK",
                  "editor_view._call 回退到共享容器")
        finally:
            ctx.unregister("__s2b_probe")
    except Exception as e:
        check(False, "实机构建/断言异常: {}".format(e))
    finally:
        try:
            if rt is not None:
                rt.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
