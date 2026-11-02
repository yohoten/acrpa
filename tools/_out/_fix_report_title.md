# 修复报告：顶部标题「A/C RPA Automation Workflow」双击可自定义编辑

## 1. 改动位置（文件:行）

| 文件 | 行 | 内容 |
|------|----|------|
| `src/ACRPA.py` | 1701-1705 | 新增模块级会话状态：`TITLE_DEFAULT_TEXT` / `_title_text_session` / `_title_edit_entry` |
| `src/ACRPA.py` | 1707-1735 | 新增 `_commit_title_edit(save=True)`：结束编辑、保存/放弃、销毁 Entry、恢复 Label |
| `src/ACRPA.py` | 1737-1765 | 新增 `_start_title_edit(event=None)`：双击时在原位叠加同字体/同色无边框 Entry |
| `src/ACRPA.py` | 1767 | 新增 `title_lbl.bind("<Double-Button-1>", _start_title_edit)` |

> 原标题 Label 定义（`src/ACRPA.py:1697-1699`）**未做任何改动**：仍是
> `tkinter.Label(title_bar, text="A/C RPA Automation Workflow", font=FONT_TITLE, fg=C["fgt"], bg=C["bg"])`
> 并以 `grid(row=0,column=0,sticky="w")` 布局。仅在其后新增编辑逻辑，属最小改动、内聚实现。

## 2. 实现方式

- **控件定位**：界面顶部 `title_bar` 容器内、`grid` 于 (0,0) 的 `title_lbl`（[`src/ACRPA.py`](src/ACRPA.py:1697)）。与操作系统窗口标题 `root.title("A/C RPA")` 无关。
- **默认文案**：常量 `TITLE_DEFAULT_TEXT = "A/C RPA Automation Workflow"`，与现状逐字符一致。
- **双击进入编辑**：`<Double-Button-1>` 触发 `_start_title_edit()`，用 `tkinter.Entry(title_bar, ...)` + `ent.place(x=title_lbl.winfo_x(), y=title_lbl.winfo_y(), width=..., height=...)` **覆盖在 Label 原位**；Label 始终是父容器布局的一部分 → 不产生位移。
- **外观一致（像素级）**：Entry 的 `font`/`fg`/`bg` 均由 `title_lbl.cget(...)` **运行时读取当前实际值**（随暗色/亮色主题变化，不硬编码），并设 `borderwidth=0`、`highlightthickness=0`、`relief="flat"` → 无任何可见边框/下划线/图标/光标变化。
- **无任何提示**：未添加 tooltip、占位提示或 `cursor` 改变；Label 保持默认光标。
- **编辑结束自动保存**：`_commit_title_edit()` 集中处理保存；绑定 `<Return>`、`<KP_Enter>`（确认并保存）、`<FocusOut>`（鼠标松开/焦点移出自动保存）、`<Escape>`（放弃本次修改、恢复编辑前文本）。
- **空/纯空白回退**：保存前 `strip()`，为空则回退 `TITLE_DEFAULT_TEXT`。
- **幂等去重**：`_commit_title_edit()` 开头即将 `_title_edit_entry = None`，重复触发（Return 后 FocusOut 等）直接返回，避免重复保存。
- **仅会话内存**：保存只写模块级变量 `_title_text_session` 与 Label 文本，**不触碰 `state.save_config()` / `config.json` / 任何持久化**；重启（新进程重新 import）后模块级变量复位为默认 → 自动回默认文案。

## 3. 约束逐条满足情况

| # | 约束 | 满足 | 证据 |
|---|------|------|------|
| 1 | 目标是界面顶部标题 Label（非窗口标题栏） | ✅ | 改的是 `title_lbl`（[`src/ACRPA.py`](src/ACRPA.py:1697)）；`root.title("A/C RPA")` 未动 |
| 2 | 默认文案 `A/C RPA Automation Workflow` | ✅ | `TITLE_DEFAULT_TEXT`；冒烟断言 1) 通过 |
| 3 | 默认外观像素级一致，双击才进入编辑，无位移 | ✅ | Entry 复用 Label 同 font/fg/bg + `place()` 覆盖；断言 2b/2c/2d 通过；introspect 溢出=0 |
| 4 | 无任何提示（tooltip/光标等） | ✅ | 未添加任何提示或 cursor 改变 |
| 5 | 编辑结束自动保存 + Return/FocusOut/Escape + 空回退 | ✅ | 断言 3a/3b/3c/5a/5b/5c/6 通过 |
| 6 | 仅会话内存，不写 config.json，重启回默认 | ✅ | 断言 4（config md5/mtime 不变）+ 断言 7（子进程重新 import 回默认）通过 |
| 7 | 不破坏现有行为（几何/主题/缩放/紧凑模式） | ✅ | introspect 溢出=0；dark_theme 57 项 OK；ui_scale OK；launch 存活 OK；编辑态色取 Label 现值随主题 |

## 4. 验证命令与结果

| 命令 | 结果 |
|------|------|
| `.venv\Scripts\python.exe -X utf8 tools\_smoke_editable_title.py` | **OK 16/16，退出码 0** |
| `.venv\Scripts\python.exe -X utf8 tools\_debug_check.py` | **syntax_failed=0 import_failed=0，退出码 0** |
| `.venv\Scripts\python.exe -X utf8 tools\_smoke_launch_app.py` | **启动成功（GUI 已建窗），退出码 0** |
| `.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_introspect.py` | **控件溢出命中=0，退出码 0** |
| `.venv\Scripts\python.exe -X utf8 tools\_debug_dark_theme.py` | **OK（57 项），退出码 0** |
| `.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_scale.py` | **OK，退出码 0** |

### config.json 未被写入的证据（断言 4 摘录）

```
[CFG] 编辑前: (True, '7fc17b223c782dcd778e25d4de894fe8', 3323, 1790952276.4616666)
[CFG] 编辑后: (True, '7fc17b223c782dcd778e25d4de894fe8', 3323, 1790952276.4616666)
[OK] 4). config.json 内容/md5/mtime 未被编辑改动
```

→ 内容 md5、字节数、mtime 三者编辑前后完全一致。

### 冒烟断言清单（16/16 通过）

```
[OK] 1). 启动标题为默认文案
[OK] 2a-binding). 标题已注册 <Double-Button-1> 双击绑定
[OK] 2a). 双击后产生编辑控件 (Entry)
[OK] 2b). 编辑控件字体与标题一致 (ACRPA_TITLE)
[OK] 2c). 编辑控件前景/背景与标题一致 (#1a202c / #f5f6f8)
[OK] 2d). 编辑控件无可见边框 (borderwidth=0 highlightthickness=0)
[OK] 2e). 编辑控件预填当前文本
[OK] 3a). <Return> 后 Label 更新为新值
[OK] 3b). <Return> 后编辑控件消失
[OK] 3c). 会话变量已更新
[OK] 4). config.json 内容/md5/mtime 未被编辑改动
[OK] 5a). 空/纯空白回退为默认文案
[OK] 5b). FocusOut 后编辑控件消失
[OK] 5c). 会话变量回退为默认
[OK] 6). <Escape> 放弃修改, 保持编辑前文本
[OK] 7). 干净子进程重新 import → 标题为默认 (重启回默认)
=== 结果: OK  通过 16/16, 失败 0 ===
```

## 5. 残留问题

- **无**（与本次改动相关）。编辑超长文本时标题 Label 的请求宽度会随文本增长，理论可能增大窗口内容需求宽度——但这是「自定义标题」的固有语义，且默认文案下 introspect 溢出仍为 0，未引入回归。
