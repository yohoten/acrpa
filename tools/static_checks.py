# -*- coding: utf-8 -*-
"""静态门禁统一入口 (docs/项目结构优化建议.md §4 L0-1 / L0-2)。

本命令在**不 import 任何业务模块、不建任何窗口**的前提下，静态校验以下护栏：

1. **L0-2 惰性 import 环冻结**：扫描 ``src/**/*.py``，找出「函数体内 import 本项目模块」
   且构成回边（闭合循环依赖）的边；与冻结基线 ``KNOWN_LAZY_CYCLE_EDGES`` 比对。
   发现**新增**的惰性环边 → 失败；发现**消失**的（环被拆掉）→ 仅提示（进度）。
   节点按**完整模块名**（如 ``ui.workflow_view``、``netlink.node``）区分，避免把
   整个 ``ui`` 包塌缩成一个节点而产生虚假自环。
2. **打包规范门禁**：委托 ``tools/_check_spec_excludes.py``（hiddenimports 覆盖 /
   excludes 误排 / 自相矛盾），此前它因命名不是 ``_test_`` 前缀而从未进 CI。

用法::

    python tools/static_checks.py            # 通过 → 退出码 0
    python tools/static_checks.py --report   # 打印当前惰性环边（用于更新基线）

设计：全部用 ``ast`` 静态解析源码文本，零运行期副作用，可在无 tkinter 环境执行。
"""
import ast
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
for _p in (ROOT, SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 冻结基线：当前已存在的「函数体内 import 且闭合循环」的边 ──────────
# 格式：((源完整模块名, 目标完整模块名), ...)。「冻结增量」白名单 —— 只禁止新增。
KNOWN_LAZY_CYCLE_EDGES = frozenset({
    ('acrpa_api', 'engine'),
    ('ai_enhance', 'engine'),
    ('browser_backend', 'engine'),
    ('browser_backend', 'recorder'),
    ('commands', 'engine'),
    ('dialogs', 'help_window'),
    ('dialogs', 'ui.settings.window'),
    ('engine', 'acrpa_api'),
    ('engine', 'ai_enhance'),
    ('engine', 'browser_backend'),
    ('engine', 'plugins'),
    ('engine', 'workflow'),
    ('help_window', 'dialogs'),
    ('plugins', 'commands'),
    ('recorder', 'browser_backend'),
    ('single_instance', 'ACRPA'),
    ('tray', 'single_instance'),
    ('ui.settings.cards.quick', 'dialogs'),
    ('ui.settings.cards.sched', 'dialogs'),
    ('ui.settings.window', 'ui.settings.cards.quick'),
    ('ui.settings.window', 'ui.settings.cards.sched'),
    ('ui.theme', 'utils'),
    ('utils', 'ui.theme'),
    ('workflow', 'commands'),
    ('workflow', 'engine'),
    ('workflow', 'scriptdata'),
})

_PASS, _FAIL, _INFO, _WARN = [], [], [], []


def _check(cond, msg):
    (_PASS if cond else _FAIL).append(msg)
    print("[{}] {}".format("OK  " if cond else "FAIL", msg))


def _info(msg):
    _INFO.append(msg)
    print("[INFO] {}".format(msg))


def _warn(msg):
    """告警: **只提示不判失败** (不写入 _FAIL, 不影响退出码)。"""
    _WARN.append(msg)
    print("[WARN] {}".format(msg))


# ── 模块名解析 ────────────────────────────────────────────────────────
def _module_name(relpath):
    """src 相对路径 → 完整可导入模块名。"""
    parts = relpath.replace(os.sep, "/").split("/")
    if parts[-1] == "__init__.py":
        return ".".join(parts[:-1])
    name = parts[-1][:-3] if parts[-1].endswith(".py") else parts[-1]
    if len(parts) == 1:
        return name
    return ".".join(parts[:-1] + [name])


def _collect_src_modules():
    """→ (project_modules:set, module_of_file:dict)。遍历 src/ 下所有 .py。"""
    project_modules = set()
    module_of_file = {}
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            mod = _module_name(os.path.relpath(path, SRC))
            project_modules.add(mod)
            module_of_file[path] = mod
    return project_modules, module_of_file


def _resolve_target(node, project_modules):
    """→ 该 import 节点实际加载的本项目完整模块名集合（非本项目 → 空）。"""
    if isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.level != 0 or not node.module:
            return set()
        names = []
        base = node.module
        for al in node.names:
            if al.name == "*":
                names.append(base)
            else:
                names.append(base + "." + al.name)
                names.append(base)
    else:
        return set()

    out = set()
    for n in names:
        parts = n.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in project_modules:
                out.add(cand)
                break
    return out


# ── 导入图扫描 ────────────────────────────────────────────────────────
def _is_import_guard_handler(t):
    """判断 except 子句是否吞掉导入失败（bare / ImportError / ModuleNotFoundError / Exception）。"""
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in ("ImportError", "ModuleNotFoundError", "Exception")
    if isinstance(t, ast.Tuple):
        return any(_is_import_guard_handler(e) for e in t.elts)
    return False


def _try_is_guarded(node):
    """try/except ImportError 守卫：body 内的 import 是「可延迟/可失败」的。"""
    return any(_is_import_guard_handler(h.type) for h in node.handlers)


def _scan_edges(project_modules, module_of_file):
    """→ [(源模块名, 目标模块名, 是否延迟), ...]。

    「延迟」= 函数体内 import 或 try/except ImportError 守卫内的 import；
    这两类都不会在导入期硬失败，是掩盖运行期环的元凶。
    """
    edges = []
    for path, mod in module_of_file.items():
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read(), filename=path)
        except Exception:
            continue

        def visit(node, deferred):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _resolve_target(node, project_modules):
                    edges.append((mod, target, deferred))
                return
            if isinstance(node, ast.Try):
                guarded = _try_is_guarded(node)
                for child in node.body:
                    visit(child, deferred or guarded)
                for child in node.orelse:
                    visit(child, deferred)
                for child in node.finalbody:
                    visit(child, deferred)
                for h in node.handlers:
                    for child in h.body:
                        visit(child, True)
                return
            new_deferred = deferred or isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for child in ast.iter_child_nodes(node):
                visit(child, new_deferred)

        visit(tree, False)
    return edges


def _lazy_cycle_edges(edges):
    """返回闭合循环的惰性边集合 {(源, 目标)}。"""
    adj = {}
    for u, v, _ in edges:
        adj.setdefault(u, set()).add(v)

    def _reachable(start, target):
        seen = {start}
        stack = [start]
        while stack:
            u = stack.pop()
            for v in adj.get(u, ()):
                if v == target:
                    return True
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        return False

    out = set()
    for u, v, lazy in edges:
        if lazy and _reachable(v, u):
            out.add((u, v))
    return out


# ── L0-2 检查 ─────────────────────────────────────────────────────────
def check_lazy_cycles():
    project_modules, module_of_file = _collect_src_modules()
    edges = _scan_edges(project_modules, module_of_file)
    current = _lazy_cycle_edges(edges)
    new = current - set(KNOWN_LAZY_CYCLE_EDGES)
    removed = set(KNOWN_LAZY_CYCLE_EDGES) - current

    _check(not new,
           "无新增惰性 import 环 (当前 {} 条, 基线 {} 条)".format(
               len(current), len(KNOWN_LAZY_CYCLE_EDGES)))
    for u, v in sorted(new):
        _check(False, "新增惰性环边: {} -> {}".format(u, v))
    for u, v in sorted(removed):
        _info("惰性环边已消除 (进度): {} -> {}".format(u, v))
    return current


# ── L0-3 图片/截图 API 主线程门禁 (v5 §4.4; 本项目按约定只报 WARN) ──
# 判定: `Image.open(` / `ImageGrab.grab(` / `.resize(` 出现在**非 worker 路径**。
# 「worker 路径」= 所在函数名命中下面任一规则 (后缀 worker/thread/bg、前缀 _grab_/_do_、
# 或显式白名单 —— 白名单都是「已知在主线程也安全」的位置: 合成/缓存/尺寸计算等,
# 真正的解码/缩放已经由缓存或后台池承担)。
_IMG_FUNC_PREFIX = ("_grab_", "_do_", "_shot_")
_IMG_FUNC_SUFFIX = ("_worker", "_thread", "_bg", "_save")
_IMG_FUNC_ALLOW = frozenset({
    # bg_image: 全部是「缓存命中才执行」的合成/缩放, 首次调用发生在主线程但成本已被缓存摊薄
    "load_bg_photo", "load_icon_photo", "_cached_tile", "_cover_resize",
    "_fit_no_upscale", "_open_rgba", "_trim_transparent", "_tile_to_size",
    "_pick_resample", "resolve_icon_path", "_cached_source",
    # mini_bar: 两级缓存后的图标缩放 (3~8 ms, 且第二次起 0 次重采样)
    "_mb_set_icon", "_mb_icon_photo",
    # 日志导出: 纯文本拼装/写盘, 与图片无关 (仅因 `.resize` 名字撞规则, 若有)
    "_log_export_write",
    # 远端截图: 解码在后台池, PhotoImage 主线程 (函数本身只做目标尺寸计算)
    "_shot_pick_resample", "_shot_target_size", "_shot_pil_try",
    # 后台池里的解码闭包 (提交给 ui.bg_task 执行, 名字不含 worker 后缀)
    "_decode", "_crop_save", "_do_export", "_grab_region",
})


def _img_api_hits(path):
    """→ [(lineno, funcname, api)]: 文件内命中的图片/截图 API 调用。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except Exception:
        return []
    out = []

    def _api_of(call):
        func = getattr(call, "func", None)
        if not isinstance(func, ast.Attribute):
            return ""
        if func.attr == "open" and isinstance(func.value, ast.Name) \
                and func.value.id == "Image":
            return "Image.open"
        if func.attr == "grab":
            return "ImageGrab.grab"
        if func.attr == "resize":
            return ".resize"
        return ""

    def _visit(node, stack):
        for child in ast.iter_child_nodes(node):
            fns = stack
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fns = stack + [child.name]
            if isinstance(child, ast.Call):
                api = _api_of(child)
                if api:
                    out.append((child.lineno,
                                fns[-1] if fns else "<module>", api))
            _visit(child, fns)

    _visit(tree, [])
    return out


def _img_func_is_worker(name):
    name = str(name or "")
    if name.endswith(_IMG_FUNC_SUFFIX):
        return True
    if name.startswith(_IMG_FUNC_PREFIX):
        return True
    return name in _IMG_FUNC_ALLOW


def check_img_main_thread():
    """L0-3: 扫描 src/ 下的图片/截图 API, 非 worker 路径只报 WARN (不判失败)。

    为什么只 WARN: 静态判定无法区分「这确实在主线程」与「这已被缓存摊薄」,
    真正的红线由 v5 §2.1 T4 / §6.3（worker 里禁止碰 Tk）在运行期保证; 门禁若
    FAIL 会把整个 CI 卡死在一个无法 100% 静态判定的规则上 (方案 §4.4 原为 FAIL,
    按本批约定降级为 WARN)。
    """
    hits = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            for lineno, fname, api in _img_api_hits(path):
                if _img_func_is_worker(fname):
                    continue
                hits.append((os.path.relpath(path, ROOT), lineno, fname, api))
    if not hits:
        _info("L0-3 图片/截图 API 主线程门禁: 未发现非 worker 路径调用")
        return []
    for rel, lineno, fname, api in hits:
        _warn("L0-3 图片 API 可能占用主线程: {}:{} ({} → {})".format(
            rel, lineno, fname, api))
    _info("L0-3 告警 {} 处 (仅提示, 不阻塞) —— 建议确认该路径已被缓存或已提交后台池".format(
        len(hits)))
    return hits


# ── 打包规范门禁 ──────────────────────────────────────────────────────
def check_spec_excludes():
    import _check_spec_excludes as gate
    rc, problems = gate.check()
    _check(rc == 0, "打包规范门禁通过 (hiddenimports 覆盖 / excludes 无误排)")
    for p in problems:
        _check(False, p)
    return rc


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    print("=" * 70)
    print("ACRPA 静态门禁 (L0-1 统一入口 + L0-2 惰性环冻结)")
    print("=" * 70)

    print("\n── L0-2 惰性 import 环冻结 ──")
    current = check_lazy_cycles()
    if "--report" in argv:
        print("\n当前惰性环边（复制进 KNOWN_LAZY_CYCLE_EDGES）：")
        print("KNOWN_LAZY_CYCLE_EDGES = frozenset({")
        for u, v in sorted(current):
            print("    ({!r}, {!r}),".format(u, v))
        print("})")

    print("\n── L0-3 图片/截图 API 主线程门禁 (WARN, 不计入退出码) ──")
    check_img_main_thread()

    print("\n── 打包规范门禁 ──")
    check_spec_excludes()

    print("\n" + "=" * 70)
    print("通过 {} / 失败 {} / 提示 {} / 告警 {}".format(
        len(_PASS), len(_FAIL), len(_INFO), len(_WARN)))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
