# 帮助系统重构 · 阶段 1-2（渲染层与整合）修复报告

> 范围：新增渲染层 `src/help_window.py`，把 `dialogs.show_help_dialog()` 薄委托化，
> 接入主题/缩放/几何记忆，打包纳入 `docs`，新增 UI 冒烟。
> 依据：`docs/帮助窗口重构设计.md` 与已确认决策（Markdown 子集 / 摘要+外链 / 非模态+开关 / 暂不做双语）。
> 约束：未改动内容层 `help_content.py` 接口与 `res/help/*` 内容，未改 `commands.py` 注册接口。

---

## 1. 改动位置一览（文件:行）

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `src/help_window.py` | 新增（全文件，约 760 行） | 渲染层：入口 / 非模态 / 搜索 / 复制 / 主题 / 缩放 / 几何 / 降级 |
| [`src/dialogs.py`](src/dialogs.py:104) | 104-122 | `show_help_dialog()` 改为**薄委托**，失败回落旧实现 |
| [`src/dialogs.py`](src/dialogs.py:124) | 124-268 | 旧实现改名保留为 `_show_help_dialog_legacy()`（函数体原样未动） |
| [`src/state.py`](src/state.py:157) | 157-160 | `_config_schema` 增 `help_geometry`/`help_maximized`/`help_modal` |
| [`src/state.py`](src/state.py:230) | 230-233 | UI 状态块增同名大写别名 `HELP_GEOMETRY`/`HELP_MAXIMIZED`/`HELP_MODAL` |
| [`src/ACRPA.py`](src/ACRPA.py:2036) | 2036-2040 | 主题切换处追加 `help_window.refresh_theme(root, C)` |
| [`ACRPA.spec`](ACRPA.spec:8) | 8 | `datas` 追加 `('D:/CodingEmber/ACRPA/docs','docs')` |
| [`src/settings_window.py`](src/settings_window.py:1442) | 1442-1454 | 设置→系统：新增「帮助窗口使用模态」复选框（绑定 `state.HELP_MODAL`） |
| [`src/settings_window.py`](src/settings_window.py:1528) | 1528-1532 | `_apply_system_settings` 收口写入 `state.HELP_MODAL` |
| `tools/_smoke_help_window.py` | 新增（全文件） | UI 冒烟 A1-A9、A11 |

> `help_content.py`、`res/help/*`、`commands.py`、`utils.py`、`ACRPA.py:1828`（调用方）**未改**。

---

## 2. 渲染层结构 `src/help_window.py`

### 2.1 对外入口（稳定签名）

```python
open_help_window(root=None, colors=None, fonts=None, section=None, modal=None) -> Toplevel | None
open_help = open_help_window              # 设计文档 10.2 别名
refresh_theme(root=None, colors=None)     # 主题切换时重刷已打开窗口
get_instance() / is_open()                # 供冒烟读取实例/存活
```

- **单实例复用**：模块级 `_instance`；已打开则 `set_colors → _apply_theme → 可选跳转 → focus`，不重复建窗。

### 2.2 类 `_HelpWindow` 关键成员

- 内容装载：`_load_content()`（`help_content.get_sections()`）、`_load_commands()`（`get_command_groups()`）。
- 建窗：`_build()`（工具栏 + 左 `Listbox` 导航 + 右 `Text` 内容区 + 滚动条）。
- 渲染：`_render_section / _render_blocks / _render_block / _insert_inline / _render_commands`。
- 搜索：`search_hits(query)` 返回 `{sections:[id...], commands:[name...]}`；`_on_search` 过滤导航 + 命令条目 + 命中高亮（`hit` tag）。
- 复制：工具栏「复制本章」`_copy_current()`；命令条目逐条「[复制]」`copy_N` tag，点击即复制「命令名 + 参数」。
- 几何：`_apply_geometry()` / `_on_close()`。

### 2.3 内容渲染映射（Markdown 子集 → Text tag）

| Block.kind | 渲染 |
| --- | --- |
| heading | `h1`/`h2`/`h3`（按 level 分级） |
| para | `para` + 行内 `bold`/`inline`/`link_N` |
| ulist / olist | `• ` / `1. ` + `ulist`/`olist` |
| code | `code`（等宽 `FONT_LOG=Consolas`） |
| table | 首行 `thead`、余行 `table`（单元格以竖线分隔） |
| hr | `─`×48 + `hr` |

### 2.4 链接可点击（离线优先 + 可见提示）

- `http(s)://` → `webbrowser.open`（惰性导入）。
- 其他（如 `docs/xxx.md`）→ `help_content.resolve_doc_path()` 命中后 `os.startfile`；未命中再试 `resolve_res_path()`。
- 失败 → `show_toast(...)` 可见提示 + `log1` 记录，**不静默、不阻断、不弹模态**。

### 2.5 容错降级

- `help_content` 导入失败 / `get_sections()` 异常 → `_fallback_only=True`，仅渲染「命令速查」（`commands.list_all()` 直出，经 `_SimpleEntry` 适配）。
- `get_command_groups()` 失败 → 单组「全部命令」直出。

---

## 3. 非模态开关实现

- 建窗：`win.transient(root)`；**默认不 `grab_set()`**（非模态，可边看边操作）。
- `_resolve_modal()` 读取 `state.HELP_MODAL`；为 `True` 时执行 `win.grab_set()` 恢复模态。
- `open_help_window(..., modal=...)` 可显式覆盖；已打开窗口再次调用会同步 `modal` 取值。
- 开关 UI：设置→系统卡「帮助窗口使用模态」复选框 → `_toggle_help_modal` 即时落盘（`state.save_config` + `_flash_saved`），并在 `_apply_system_settings` 收口。

---

## 4. 主题 / 缩放 / 几何接线

### 4.1 主题实时（仿 `netlink_window.refresh_theme`）

- `refresh_theme(root, colors)`：`set_colors()` 后 `_apply_theme()`，重刷窗口/工具栏/导航/内容区/滚动条/按钮 `bg`、`fg`、`selectbackground` 等；`_configure_tags()` 重配**全部 Text tag**（含动态生成的 `link_N` / `copy_N`）。
- 接入点：[`ACRPA.py:2036`](src/ACRPA.py:2036)（`dialogs.C = C` 与 `netlink_window.refresh_theme()` 之后追加 `help_window.refresh_theme(root, C)`，已 `try/except` 包裹）。

### 4.2 缩放跟随

- 内边距/间距/间距一律走 `utils.scaled(...)`（接入 `dpi_factor × ui_scale`）。
- 字号走命名角色 `utils.FONT_*`（随 `set_ui_scale` 自动 `fontconfigure`）。
- 冒烟断言 `help_window.scaled is utils.scaled`（同源令牌）。

### 4.3 几何记忆

- 键：`state.HELP_GEOMETRY`（str，默认 ""）、`state.HELP_MAXIMIZED`（bool，默认 False）。
- 恢复：`_apply_geometry()` 用 `utils.geometry_in_screen(root, geom)` 校验；为空/越界 → `utils.center_geometry(root, scaled(900), scaled(640))`（回退居中）；`HELP_MAXIMIZED=True` → `win.state("zoomed")`。
- 写回：`_on_close()`（关窗按钮 / `WM_DELETE_WINDOW` / Esc）保存 `HELP_MAXIMIZED`（是否 zoomed）与 `HELP_GEOMETRY`，并 `state.save_config()`。

---

## 5. 整合与向后兼容

- 调用方 [`ACRPA.py:1828`](src/ACRPA.py:1828) `show_help_dialog()` 名称/位置/签名**完全不变**。
- [`dialogs.py:104`](src/dialogs.py:104) 新 `show_help_dialog()`：

```python
def show_help_dialog():
    try:
        import help_window
        win = help_window.open_help_window(root, C, (FONT_TITLE, FONT_BODY, FONT_SMALL, FONT_BUTTON))
        if win is not None:
            return win
    except Exception as e:
        log1("帮助窗口委托失败, 回落旧实现: {}".format(e), "warning")
    return _show_help_dialog_legacy()
```

- 旧实现完整保留为 `_show_help_dialog_legacy()`（异常/导入失败自动回落，保证「新窗口崩了也不会没帮助」）。
- 颜色表：委托时传 `dialogs.C`；`ACRPA` 切主题时 `dialogs.C = C` 已同步，`refresh_theme` 再以新 `C` 重刷已在窗。

---

## 6. 冒烟覆盖与结果 `tools/_smoke_help_window.py`

`[OK]/[WARN]/[FAIL]` + 退出码 0/1；输出落盘 `tools/_smoke_result_phase2/_smoke_help_window.out`。

| 编号 | 覆盖 | 结果 |
| --- | --- | --- |
| A1 | 窗口可开（Toplevel 存活） | [OK] |
| A2 | 章节 ≥13 且正文非空 | [OK] 13 章 |
| A3 | 命令集合 == `commands.list_all()` | [OK] 68 == 68 |
| A4 | 分组全覆盖 | [OK] 68 条均归属分组 |
| A5 | 搜索命中章节/命令 | [OK] '循环'→循环开始/结束/跳出；'netlink'→netlink/releases |
| A6 | 主题跟随（颜色变化且无异常） | [OK] text.bg `#fdfdfd`→`#1a2332` |
| A7 | 缩放跟随（`utils.scaled` 同源） | [OK] scaled(100) 100→150 |
| A8 | 几何记忆 + 越界回退 | [OK] 屏内恢复 / 越界居中 |
| A9 | 离线可用（外链 monkeypatch 拦截） | [OK] docs 走 startfile、http 走 webbrowser |
| A11 | `show_help_dialog()` 向后兼容 | [OK] 委托成功，legacy 保留 |

> A10「三处漂移守卫」由既有 [`tools/_test_help_consistency.py`](tools/_test_help_consistency.py) 覆盖（A3/A4/A10），本轮复跑 PASS。

---

## 7. 自验证结果（7 条，退出码均 0）

| # | 命令 | 退出码 | 结论 |
| --- | --- | --- | --- |
| 1 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_help_window.py` | 0 | PASS（FAIL=0 WARN=0，A1-A11） |
| 2 | `.venv\Scripts\python.exe -X utf8 tools\_test_help_consistency.py` | 0 | PASS（注册表 68，FAIL=0 WARN=0） |
| 3 | `.venv\Scripts\python.exe -X utf8 tools\_debug_check.py` | 0 | PASS（113 文件语法、18/18 导入） |
| 4 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_launch_app.py` | 0 | OK（GUI 存活 ≥10s，无 Traceback） |
| 5 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_introspect.py` | 0 | OK，控件溢出命中=0（无 [OVERFLOW]） |
| 6 | `.venv\Scripts\python.exe -X utf8 tools\_debug_dark_theme.py` | 0 | OK（57 项，主题往返无回归） |
| 7 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_main_geometry.py` | 0 | OK（utils + 4 分支，FAIL=0） |

---

## 8. 残留问题与说明

1. **`docs` 打包体积**：`ACRPA.spec` 追加 `('docs','docs')` 会把 24 份 md（含 netlink/marketplace/browser/releases）一并打入冻结包，用于「摘要 + 外链」离线打开。均为纯文本，体量为 KB 级（远小于 `res/` 图片）。如需收窄，可仅纳入被 `res/help/*.md` 实际外链引用的子集。
2. **设置勾选已落地**：设置→系统卡新增「帮助窗口使用模态」复选框，绑定 `state.HELP_MODAL`，即时落盘；实测 UI introspect 控件溢出=0（未引入新溢出）。默认关闭 = 非模态，与设计决策一致。
3. **`_smoke_launch_app.py` 已知噪声**：测试脚本自带子进程日志读取线程在 GBK 输出下抛 `UnicodeDecodeError`（与本改动无关，退出码仍 0、判定 OK）。
4. **A8 语义说明**：冒烟先关闭已开窗口（写回当前几何）再重开，实际验证的是「关闭写回 → 重启恢复 → 越界回退」链路，均通过。
5. **旧实现保留**：`_show_help_dialog_legacy()` 仍为模态单页文本，仅作 fallback；后续收尾阶段（阶段 4）可视需要删除，需另行批准。
