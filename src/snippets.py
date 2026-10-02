"""snippets.py — 统一「片段库」（离线可用，无需网络/API Key）。

三类片段统一编目（供「脚本编辑」工具栏「片段」按钮使用）：
  1. builtin : templates.py 内置动作片段（办公 / 系统 / 其他，见 TEMPLATE_METADATA）
  2. user    : 用户自定义片段（存 <app_root>/snippets_user.json）
  3. file    : 库目录中的 .xls 脚本文件（默认 <app_root>/template，可追加多个目录）

设计口径：
  * 默认主库目录 = 项目 root/template（可用 state.SNIPPET_DIR 覆盖）；
  * 追加目录 = state.SNIPPET_EXTRA_DIRS（列表）；
  * 行数据统一用 ScriptData；JSON 存储用紧凑 [cmd, a1..a9] 形式；
  * 不引入任何第三方依赖（xls 读取复用运行期已有的 xlrd，缺失时静默降级为空）。
"""
import os
import sys
import json


# ── 路径解析 ──────────────────────────────────────────────────────────

def app_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_primary_dir(auto_create=True):
    """主库目录: state.SNIPPET_DIR 优先, 空 → <app_root>/template。"""
    d = ""
    try:
        import state
        d = getattr(state, "SNIPPET_DIR", "") or ""
    except Exception:
        d = ""
    if not d:
        d = os.path.join(app_root(), "template")
    if auto_create:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    return d


def get_extra_dirs():
    try:
        import state
        return [d for d in (getattr(state, "SNIPPET_EXTRA_DIRS", []) or []) if d]
    except Exception:
        return []


def list_library_dirs():
    """[主库] + 追加库（去重且存在的目录）。"""
    dirs = [get_primary_dir()]
    for d in get_extra_dirs():
        if d and d not in dirs and os.path.isdir(d):
            dirs.append(d)
    return dirs


def add_extra_dir(path):
    """把目录加入追加库并落盘 (state.SNIPPET_EXTRA_DIRS)。返回是否新增。"""
    if not path or not os.path.isdir(path):
        return False
    try:
        import state
        cur = list(getattr(state, "SNIPPET_EXTRA_DIRS", []) or [])
        if path in cur or path == get_primary_dir(auto_create=False):
            return False
        cur.append(path)
        state.SNIPPET_EXTRA_DIRS = cur
        state.save_config()
        return True
    except Exception:
        return False


def remove_extra_dir(path):
    try:
        import state
        cur = [d for d in (getattr(state, "SNIPPET_EXTRA_DIRS", []) or []) if d != path]
        state.SNIPPET_EXTRA_DIRS = cur
        state.save_config()
        return True
    except Exception:
        return False


# ── 用户自定义片段 (JSON) ─────────────────────────────────────────────

def _user_file():
    return os.path.join(app_root(), "snippets_user.json")


def _load_user():
    try:
        with open(_user_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_user(items):
    try:
        with open(_user_file(), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def save_user_snippet(name, category, rows):
    """保存/覆盖一段用户片段。rows: list[ScriptData]。返回 id 或 None。"""
    name = (name or "").strip()
    if not name or not rows:
        return None
    items = [it for it in _load_user() if it.get("name") != name]
    items.append({
        "name": name,
        "category": (category or "我的").strip() or "我的",
        "rows": [[r.cmd_type] + list(r.args) for r in rows],
    })
    if _save_user(items):
        return "user:" + name
    return None


def delete_user_snippet(name):
    items = [it for it in _load_user() if it.get("name") != name]
    return _save_user(items)


# ── xls 片段文件加载 ──────────────────────────────────────────────────

def _load_xls_rows(path):
    try:
        import xlrd
    except Exception:
        return []
    try:
        from scriptdata import ScriptData
        wb = xlrd.open_workbook(path)
        s1 = wb.sheet_by_index(0)
        rows = []
        for i in range(2, s1.nrows):          # 跳过 2 行表头
            vals = s1.row_values(i)
            if not vals or not vals[0]:
                continue
            args = ["" if v is None else str(v) for v in vals[1:10]]
            while len(args) < 9:
                args.append("")
            rows.append(ScriptData(str(vals[0]), args))
        wb.release_resources()
        return rows
    except Exception:
        return []


# ── 统一编目 ──────────────────────────────────────────────────────────

def list_snippets():
    """返回列表: [{id,name,category,source,path,rows}]。rows: -1=未知(文件, 按需读)。"""
    out = []
    # 1) 内置
    try:
        import templates
        for name in templates.list_templates():
            info = templates.get_template_info(name) or {}
            out.append({
                "id": "builtin:" + name, "name": name,
                "category": info.get("category", "其他"),
                "source": "builtin", "path": "", "rows": int(info.get("rows", 0) or 0),
            })
    except Exception:
        pass
    # 2) 用户自定义
    for it in _load_user():
        nm = it.get("name", "")
        out.append({
            "id": "user:" + nm, "name": nm,
            "category": it.get("category", "我的"),
            "source": "user", "path": "", "rows": len(it.get("rows", [])),
        })
    # 3) 库目录中的 xls 文件
    for d in list_library_dirs():
        try:
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for fn in names:
            if not fn.lower().endswith(".xls"):
                continue
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                out.append({
                    "id": "file:" + p, "name": os.path.splitext(fn)[0],
                    "category": "文件", "source": "file", "path": p, "rows": -1,
                })
    return out


def categories():
    cats = ["全部"]
    seen = set()
    for s in list_snippets():
        c = s.get("category") or "其他"
        if c not in seen:
            seen.add(c)
            cats.append(c)
    return cats


def get_snippet_rows(sid):
    """按 id 返回 list[ScriptData]（失败返回 []）。"""
    sid = sid or ""
    if sid.startswith("builtin:"):
        try:
            import templates
            return templates.get_template(sid.split(":", 1)[1]) or []
        except Exception:
            return []
    if sid.startswith("user:"):
        nm = sid.split(":", 1)[1]
        try:
            from scriptdata import ScriptData
            for it in _load_user():
                if it.get("name") == nm:
                    return [ScriptData(r[0], list(r[1:10])) for r in it.get("rows", [])]
        except Exception:
            return []
        return []
    if sid.startswith("file:"):
        return _load_xls_rows(sid.split(":", 1)[1])
    return []


def is_placeholder(value):
    """判断参数是否为「待填」占位符（<...> 或全大写占位或 xxx）。"""
    v = (value or "").strip()
    if not v:
        return False
    if v.startswith("<") and v.endswith(">"):
        return True
    low = v.lower()
    if low in ("xxx", "todo", "tbd"):
        return True
    return False
