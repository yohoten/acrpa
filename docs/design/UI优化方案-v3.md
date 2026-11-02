# UI 优化方案 v3 —— 布局 · 细节 · 加载与显示

> 状态：P0 已实施（本文数值随实施同步回填）；P1 已全部落地（见 §5.2）；P2 已全部落地（netlink 套壳 / D8 空态 / D9 忙碌态 / D12 工具提示组件化，见 §5.2）。
> 适用范围：主窗口 + 全部从窗口（设置 / 帮助 / 市场 / 互联 / 通用对话框）。
> 不变式：不改变脚本执行、调度、热键、日志语义；色板只消费既有 token。

---

## 0. 与既有文档的关系

| 文档 | 定位 | 与本文关系 |
| --- | --- | --- |
| `docs/UI美化设计方案.md` | 皮肤层权威（clam / 色板 / 字体 / `sp·radius·ctrl_h` / `create_card` / `_refresh_theme`） | **完全继承**，本文不新造视觉语言，只消费其 token |
| `docs/UI美化与布局设计方案-v2.md` | 布局层诊断 L1–L6 + 分期 | **承接**：L1（列宽错配）、L2（标题栏语义）、L3（跨窗口纪律）、L5（图例常驻）在本轮给出可执行实现；L4（响应式断点）升级为「列显示策略」；L6（文档行号失效）由本文统一重锚 |
| `docs/底部常驻日志面板重构设计方案.md` | 两 Tab + 常驻执行栏 + 底部日志 | 日志链路**零改动**；本文只改「把文本推给控件的批次」 |
| `docs/设置窗口优化设计方案.md` / `工作流界面美化设计方案.md` | 单窗设计 | 从窗口统一骨架需兼容其卡片/画布既有实现，不改其内部信息架构 |

新增分区（v2 未覆盖）：**加载与显示性能**（§4）、**细节打磨清单**（§3）、**量化验收**（§6）。

---

## 1. 现状基线（量化）

### 1.1 区域模型（已核对行号）

```text
┌─ 原生 Windows 标题栏 (root.title = "A/C RPA v<ver>") ────────────────┐
├──────────────────────────────────────────────────────────────────────┤
│ row0  自绘标题栏 :5121-5130（双击可改；默认文案硬编码于 :5125）        │
├──────────────────────────────────────────────────────────────────────┤
│ row1  执行控制栏 ui/exec_bar.build :5423（常驻，跨 Tab）              │
├──────────────────────────────────────────────────────────────────────┤
│ row2  main_paned(vertical, sash=sp(4)) :5194                          │
│       ├─ pane0 notebook :5203                                         │
│       │    ├─ Tab 脚本编辑 :5209                                      │
│       │    │    row0 toolbar :5216（canvas 横向溢出滚动）              │
│       │    │    row1 tree_frame :5255  ┄ 待机背景层 _idle_bg_label     │
│       │    │    row2 legend_frame :5308（10 组常驻）                   │
│       │    └─ Tab 工作流 :5531                                        │
│       └─ pane1 bottom_dock → ui/log_dock :5480                        │
├──────────────────────────────────────────────────────────────────────┤
│ row3  status_bar :5583（状态 · dash_info · 进度条 · ▾日志）            │
└──────────────────────────────────────────────────────────────────────┘
```

窗口尺寸全部走 `utils.TOKENS`：`win_w=1000 / win_h=680 / win_min_w=640 / win_min_h=520`，经 `_apply_main_geometry()`（:949，调用 :5048）派生。

### 1.2 表格列现状与错配

| 项 | 值（`src/ACRPA.py:5283-5287`） |
| --- | --- |
| `#0` | width 32 / minwidth 32 / stretch **False** |
| `cmd` | width 100 / minwidth 80 / stretch 未设 |
| `p1..p9` | width 65 / minwidth 50 / stretch 未设 |
| `displaycolumns` | **未设置** → 9 个参数列永远全显 |
| 固定占用 | 32 + 100 + 9×65 = **717 px** > `win_min_w=640` |

命令 schema 统计（68 条）：加权平均元数 **2.24**，≤2 参数占 **69%**，≥5 参数仅 **13%**。
→ 典型行有 7 个空列；最小窗口必然横向滚动；列头是「参数1..参数9」，与 schema 真实参数名脱节。

### 1.3 跨窗口 token 覆盖率（改造前）

| 文件 | 行数 | `sp(` | `radius(` | `ctrl_h(` | `gap(` | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| `src/dialogs.py` | 2730 | 0 | 0 | 0 | 0 | 全字面量 |
| `src/help_window.py` | 1153 | 0 | 0 | 0 | 0 | 全字面量（有 `scaled()`） |
| `src/netlink_window.py` | 3642 | 0 | 0 | 0 | 0 | 全字面量 |
| `src/market_window.py` | 1828 | 12 | 0 | 0 | 67 | 基本 token 化 |
| `src/ui/settings/window.py` | 720 | 20 | 0 | 1 | 0 | 基本 token 化 |
| `src/ACRPA.py`（主窗） | 5716 | 全面消费 | | | | 基准 |

→ 同一应用存在两套尺度语言；`utils.set_ui_scale()` 对前三个窗口基本无效。

### 1.4 三处性能热点（改造前）

| 热点 | 位置 | 现状 | 代价 |
| --- | --- | --- | --- |
| 日志逐条重绘 | `src/utils.py:713-760`，热点 `:735` | 每条消息 `insert` → `tag_add` → **`update()`** → `see(END)` | N 条消息 = N 次强制同步重绘；刷屏时整窗卡顿 |
| 表格全量重建 | `src/ACRPA.py:2489-2545` | 每行 `insert` + `tag_has` + `tree.item(...)` 读 + `tree.item(tags=...)` 写 | 每行 4 次 Tcl 往返；500 行 ≈ 2000 次 |
| 待机背景同步合成 | `_sync_idle_bg` :4820 → `ui/bg_image.py:299-386` | 尺寸变化即 `Image.open` + LANCZOS contain + alpha + 合成 + `PhotoImage`，全部在主线程 | 首次显示与拖动窗口时掉帧；缓存键含尺寸，缩放必重算 |

### 1.5 基线测量方法（可复跑）

- 启动：`crashlog.startup_mark`（`src/crashlog.py:316`，阶段表 `BOOT_STAGES :271-284`），由 `src/app.py:87-138` 埋 `config/log_ready/theme/tray/commands/scheduler/capabilities`，`:272` 埋 `ui_build`。
- 表格/日志/列策略：`tools/_bench_ui.py`（新增，见 §5.3）。
- 数字记录表见 §6.2（首轮实施后回填）。

---

## 2. 布局优化方案

### 2.1 主窗口：只调「列策略 + 显隐 + 间距」，不动骨架

四行网格与 `main_paned` 主干**保持不变**（改动面最小、可回退）。主窗口改造集中在 4 处：

#### (a) 表格列策略（承接 v2 的 L1 + L4）

不改数据模型（不为每类命令动态生成列），改用 ttk 原生能力达成 80% 效果：

```text
宽度 W = tree.winfo_width()
┌ W < 760   → 显示 #0, cmd, p1..p3      (4 个数据列)
├ W < 1024  → 显示 #0, cmd, p1..p6      (7 个)
└ W ≥ 1024  → 显示 #0, cmd, p1..p9      (10 个)
```

- 实现：`_tree_column_plan(w)`（纯函数，返回 `(visible_cols, widths)`）+ `_apply_tree_columns()`（`<Configure>` 去抖 150ms，仅在「可见列集合变化」时 `tree.configure(displaycolumns=...)`）。
- 列宽：`cmd` 与 `p*` 显式 `stretch=True`、`minwidth` 保留，可见列按剩余宽度均分；`#0` 继续 `stretch=False`（左缘色条 + 行号锚定）。
- 隐藏列的**数据不丢**：行值仍写满 10 列，只是不显示；内联编辑定位在 `_edit_cell` 内做「显示列 → 逻辑列」映射（隐藏列不可点击）。
- 收益：640×520 下不再必然横滚；典型 2 参数行不再被 7 个空列挤压。

#### (b) 图例条按需显示（承接 v2 的 L5）

- 现有：`_LEGEND_ITEMS`（:1630，10 组）在 build 期一次性建完，**无任何显隐逻辑**。
- 改为：`legend_frame` 的每个分组建成具名子 frame，`_sync_legend()` 依据当前脚本 `cmd_type` 集合判定：
  - 命中 ≥1 组 → 只显示命中组（`pack`/`pack_forget`），保持 `grid` 行位不变；
  - 0 命中（空脚本 / 全部是「等待」这类未登记色）→ `legend_frame.grid_remove()`，把垂直空间还给编辑区。
- 触发点：`_editor_sync_to_tree()` 末尾（与树重建同一次，O(n) 计数，无额外遍历）+ 主题切换。
- 开关：`_LEGEND_AUTO = True`，关闭即恢复常驻（回退路径）。

#### (c) 标题栏语义（承接 v2 的 L2，最小改动版）

- 本方案**不启用** `overrideredirect`（自绘栏 + 原生栏并存是既有交互，改造成本与回归面过大），只做语义收敛：自绘栏显示**文档标题**，默认值从应用名改为「未命名脚本」，并在保存/打开后显示文件名。
- 细节：`:5125` 的硬编码文案改引用常量（与 `TITLE_DEFAULT_TEXT :1145` 同源），避免两处漂移。

#### (d) 首帧顺序（与 §4 联动）

- 构建末尾的待机背景合成从「同步」改为「首帧后 idle 调度」（详见 §4.3），避免首帧被 LANCZOS 拖住。

### 2.2 响应式断点（主窗口）

| 断点 | 触发条件 | 变化 |
| --- | --- | --- |
| 窄 | `winfo_width() < 760` | 表格只显 4 个数据列；图例单行不换行（已 10 组时自动隐藏） |
| 中 | `760 ≤ W < 1024` | 7 个数据列 |
| 宽 | `W ≥ 1024` | 10 个数据列 |
| 高度 | `winfo_height() < 560` | 日志面板默认折叠（沿用 `log_dock` 既有 `main_paned.forget/add`，P1） |

去抖与幂等：所有断点判定走同一个 150ms `after` 句柄，且仅在结果变化时执行布局动作，避免 `<Configure>` 抖动。

### 2.3 从窗口统一「窗口骨架」

新增 `src/ui/window_shell.py`（无业务依赖，只 `tkinter + utils + ui_theme`），**已实现**：

```python
def shell_size(size) -> (w, h)        # 设计 px (不预缩放! 交给 place_dialog 缩)
def apply_window_shell(win, title=None, size="md", min_size=None, modal=False,
                       parent=None, bg_key="bgc", resizable=True,
                       pad="sp_md", center=True, colors=None,
                       icon_setter=None) -> ShellBody
def open_shell(parent, title, **kwargs) -> ShellBody     # 建窗 + 套骨架一步到位

class ShellBody:
    win / content / footer                                # 属性
    header(title, subtitle="", row=0)                     # 标准标题区
    add_button(text, command, kind="normal", tooltip=None) # 底部右对齐按钮
    close()                                               # 安全销毁
```

约定：
- 尺寸一律来自 `utils.TOKENS` 的 `dlg_*` 键（`dlg_sm/md/lg/xl` 与 `dlg_min_*`），不写字面量；
- 内边距一律 `utils.sp("sp_md")` 级别，控件间距 `utils.gap()`；
- **缩放只允许发生一次**：`place_dialog()` 内部已 `scaled()`，`shell_size()` 因此返回设计 px；
- 主题：沿用既有色板与 ThemeBus（`colors=` 可传调用方快照），不新增色键；
- 几何记忆：沿用各窗口既有 `saved_geo` 机制，shell 只做「有则用之、无则居中」。

### 2.4 从窗口迁移批次

| 批次 | 窗口 | 理由 | 阶段 |
| --- | --- | --- | --- |
| B0 | `src/dialogs.py`（通用对话框 + 帮助对话框 legacy） | 调用面最广、结构最简，先立模板 | **P0** |
| B1 | `src/help_window.py` | 已有 `scaled()`，替换为 `sp()` 即可 | P1 |
| B2 | `src/market_window.py` / `ui/settings/window.py` | 已基本 token 化，只补 geometry/minsize 走 shell | P1 |
| B3 | `src/netlink_window.py` | 3642 行、0 命中，收益最大但回归面最大 | P2 |

---

## 3. 细节打磨清单

全部为「字面量 → token / 语义角色」替换，不改结构。逐项可直接核对。

| ID | 项 | 现状 | 目标 | 验收 |
| --- | --- | --- | --- | --- |
| D1 | 字号层级 | 主窗已用 `FONT_*`；从窗口混用 `("Microsoft YaHei UI", 9)` 字面量 | 一律 `FONT_BODY/SMALL/TINY`，`set_ui_scale` 能整体生效 | 从窗口字号随缩放档变化 |
| D2 | 控件高度 | `ctrl_h()` 统一 22/24/28；部分按钮/输入框手写 `height=`/`ipady=` | 一律 `ctrl_h("ctrl_h")` 起 | 同屏按钮高度一致（视觉 1px 内） |
| D3 | 间距节奏 | 主窗 `sp(3)/sp(4)` 混用；从窗口 `padx=10/12` | 4pt 节奏：`sp_xs/sm/md/lg` 四档 | 代码内无新增裸数字 padx/pady |
| D4 | 对齐 | 工具栏右对齐文件名标签、图例左对齐、状态栏三段 | 保持；从窗口 footer 按钮统一右对齐 + `gap()` | 各窗 footer 右缘对齐一致 |
| D5 | 图标一致性 | `FONT_ICON*` 与文字混排；个别 emoji 尺寸不一 | 统一 `icon_size()` + 同一基线 | 图标簇高度差 ≤ 2px |
| D6 | 焦点态 | ttk 默认虚线框 + 自绘 `focus_w` | 键盘 Tab 必须可见焦点环；鼠标点击不显示 | Tab 遍历全程可见 |
| D7 | 悬停态 | 树有 `rowhover`，按钮仅 ttk 默认 | 统一 hover 底色 `C["hover"]`/`cardhover` | 悬停/按下/禁用三态可辨 |
| D8 | 空态 | 脚本空表格 = 纯空白 | 空态提示（「Ctrl+N 新建 / Ctrl+O 打开」）+ 待机背景层共存不冲突 | 空脚本有指引 |
| D9 | 忙碌态 | 运行中仅状态点变色 | 运行中表格行 `running` tag + 进度条 + 禁用编辑入口 | 运行中不可误编辑 |
| D10 | 滚动条 | 主窗 8px 扁平；从窗口有 16px 默认 | 统一 8px + `bd/fgb` 配色 | 各窗滚动条宽度一致 |
| D11 | 分隔线 | 1px 发丝线 `C["bd"]`；部分窗口用 `ttk.Separator` | 统一 `hairline` token | 同屏分隔线粗细/颜色一致 |
| D12 | 工具提示 | `attach_tooltip` 有，延迟不统一 | 统一延迟 400ms、同一底色/边框 token | 提示外观与延迟一致 |
| D13 | 快捷键提示 | 按钮文案无快捷键 | 高频按钮 tooltip 内附 `(Ctrl+S)` 等 | 工具提示含快捷键 |

---

## 4. 加载与显示性能方案（无侵入优先）

约束：日志语义链路（`rz` / `_tlog` / `_log_*` / `_periodic`）零改动；不改业务线程模型。

### 4.1 日志：批量渲染（P0）

**现状**（`src/utils.py:718-735`）：每条消息一次 `insert` + `tag_add` + `update()` + `see()`。

**改造**：
1. 循环内只累积：`(text, tag, start_offset)`，用局部 `buf_text` 拼接与 `tag_ranges` 合并；
2. 循环结束后一次 `self._w.insert("end", buf_text)`，然后按累计偏移逐段 `tag_add`；
3. 循环外**一次** `self._w.see(END)` + `update_idletasks()`（不是 `update()`，避免重入）；
4. 单次 flush 上限（`GUI_BATCH_MAX = 400` 条）防止一次卡死，余量留给下一个 tick（队列不清空语义不变）；
5. 增加 `_w.winfo_exists()` 守卫，关窗后日志涌入不再抛 `TclError`。

**不变**：消息顺序、级别过滤（文件侧）、徽标拼装（`_log_badge`）、时间戳格式、`_buffer` 内容、文件落盘时机与批量常量。

**收益**：N 次强制重绘 → 1 次；1000 行刷屏从 O(N) 重绘降为 O(1)。

### 4.2 表格：批量重建（P0）

**现状**（`_editor_sync_to_tree` :2507-2517）：每行 `insert` + `tag_has` + `item` 读 + `item` 写。

**改造**：
1. `tag_configure` 在启动期对 `_CMD_COLORS` **全量预配置一次**（20 条），运行期不再 `tag_has`；
2. 每行 `tags` 在 Python 侧一次算好：`(cmd_tag, "even")`，`insert(..., tags=tags)` 直接带上，删除随后的 `item` 读写；
3. 行文本/值预拼（`_row_text_for` + `to_tuple` 已在循环内，保持）；
4. 选中恢复的 `sig_to_iid` 构建与插入合并为一次遍历（避免第二次 O(n)）。

**收益**：每行 Tk 往返 4 → 1；500 行 ≈ 2000 → 500 次。

### 4.3 待机背景：首帧后合成 + 结果复用（P0）

**现状**：构建末尾同步调用 → 首帧前完成 `open + LANCZOS + 合成`。

**改造**：
1. 构建末尾改为 `root.after_idle(_sync_idle_bg)`（首帧后再做），并保留现有 `after(300)` 兜底；
2. `_sync_idle_bg` 增加「尺寸未变且已显示」的早退（现有判断 `holder["size"] != (w,h)` 已具备）；
3. `bg_image.load_bg_photo` 的缓存键已含 `(path,w,h,alpha,bg,mode)`，补充：**按 `(path, w)` 复用中间缩放结果**，避免同一宽度下高度微调反复重采样（P1）；
4. 失败路径与非待机路径行为不变。

**收益**：首帧不再承担图像重采样；拖动窗口时只在尺寸真正变化时重算。

### 4.4 首帧与启动顺序（P0）

1. `init_fonts` 两次调用收敛为一次：先把 `tk scaling` 前置（`:5037-5042` 区间内无字体度量调用，安全）；
2. `crashlog.startup_mark` 增补分段：`ui_skeleton / ui_toolbar / ui_tree / ui_exec_bar / ui_log_dock / ui_workflow / ui_first_frame / main_loop`，让后续优化有据可依；
3. `app.py` 在主循环前补 `ui_first_frame`（`after_idle`）与 `main_loop` 标记。

### 4.5 重面板延迟创建（P1 —— 工作流 Tab 已实施）

`ui_workflow_view.build`（3232 行模块）启动即全建的成本已消除，实施口径：

- **占位先行**：`build_app()` 只建空 `tkinter.Frame` 并 `notebook.add(..., text="  工作流  ")`（Tab 顺序与主题回填需要真实句柄），控件树（工具栏 / 操作库 / 步骤树 / 流程图）不再在启动期创建。
- **构建入口**：`ui_workflow_view.build(notebook, tab=tab_workflow, ...)` 新增 `tab=` 参数 —— 传入宿主占位 Tab 时只填充内容，不再自建/重复 add（`tab=None` 保留旧行为）。
- **触发点**：① `notebook` 的 `<<NotebookTabChanged>>` 命中该 Tab（懒切换）；② `ACRPA._wf_ensure()` —— 供 IPC（`run_workflow`/`stop_workflow`）、快捷键、`_wf_toggle_view`/`_wf_refresh_tree` 等**绕过 Tab 切换**的入口先构建。两者共用 `_build_workflow_tab()`。
- **守卫**：`_WF_LAZY = {"built", "ok", "ensure"}` —— `built` 保证幂等（重复事件不二次构建、失败不重试刷屏），`ok` 供 `_periodic` 判定是否刷新工作流高亮（未构建时直接跳过，避免每 100ms 抛异常）。
- **句柄回填**：控件句柄模块级先置 `None`（`ACRPA.wf_*` 直读不抛 `AttributeError`），构建成功后经 `workflow_view.get_widgets()` 回填；`_setup_scroll_bindings` 的 wf 分支改为构建后补绑（工具栏滚轮）。
- **回归**：`tools/_test_workflow_view_split.py` 新增 G 段（延迟构建 9 项）+ 实机断言（启动后 `wf_tree is None` → `_wf_ensure()` 后非空 → 重复 ensure 幂等）；`tools/_test_exec_bar_split.py` 实机段先 `_wf_ensure()` 再比对 `wf_maxmin_var is exec_maxmin_var`。

---

## 5. 分期与任务

### 5.1 P0（本轮落地，全部可回退）

| # | 任务 | 文件 | 验收 |
| --- | --- | --- | --- |
| P0-1 | 日志批量渲染 + `winfo_exists` 守卫 | `src/utils.py` | 1000 条 → `insert` 1 次 / `update` ≤1 次 |
| P0-2 | 表格批量重建 + tag 预配置 | `src/ACRPA.py` | 每行 Tk 调用 ≤1；`_test_editor_cells` 全绿 |
| P0-3 | 列策略（displaycolumns + stretch + 去抖） | `src/ACRPA.py` | 640 宽显示 4 数据列、无横滚 |
| P0-4 | 图例条按需显示 | `src/ACRPA.py` | 空脚本隐藏；含「找图」只显示对应组 |
| P0-5 | 首帧顺序：`init_fonts` 去重、背景 idle 合成、启动埋点细分 | `src/ACRPA.py`、`src/app.py` | 背景合成发生在首帧之后 |
| P0-6 | 从窗口骨架 `ui/window_shell.py` + `dialogs.py` 试点 | 新增 + `src/dialogs.py` | 试点 2 窗的外边距/尺寸/最小尺寸全部 token 化；其余窗口 P1 |
| P0-7 | 基准脚本 + 静态回归断言 | `tools/_bench_ui.py`、`tools/_test_ui_layout.py` | 可复跑、退出码 0 |

P0-1 ~ P0-7 均已落地，实测数据见 §6.2；四套回归全绿（`_test_editor_cells` / `_test_script_model` /
`_test_param_form` / `_test_ui_layout`）。

### 5.2 P1 / P2

- **P1**：~~工作流 Tab 延迟构建~~（已完成，见 §4.5）；~~`log_dock` 折叠断点~~（已完成）；~~D1–D13 清单批量替换~~（已完成）；~~`help/market/settings` 套 shell~~（已完成：三窗均走 `apply_window_chrome`）；~~背景中间尺寸缓存~~（已完成：`bg_image.py` 二级缓存 `_src_cache` + `_tile_cache`）。
- **P2**：~~`netlink_window` token 化~~（已完成：主窗 + 4 个子对话框全部走 `apply_window_chrome`，`padx/pady` 字面量换成 `utils.sp()`）；~~空态/忙碌态完善~~（已完成，见下）；~~工具提示统一组件化~~（已完成，见下）。
  - **D8 空态**：`src/ACRPA.py` 新增 `_EMPTY_HINT` / `_sync_empty_hint()`。零行时在编辑区居中浮一层「还没有任何步骤 / Ctrl+N · Ctrl+O · Ctrl+I」；提示层挂在 `tab_edit` 且晚于待机背景层创建 → 与背景图共存不冲突，`tree_frame` 被 `grid_remove` 也不影响；同步点在 `_editor_sync_to_tree` / `_cmd_clear` / `_cmd_new`。
  - **D9 忙碌态**：`running` tag + 进度条原已就绪，本次补齐运行期编辑守卫 `_edit_blocked(action)`：运行中拦截插入/删除/上移/下移/复制行/粘贴行/注释行/删除行/F2 编辑/单元格内联编辑/撤销/重做/新建/打开/清空/片段/模板/粘贴表格共 18 处入口，统一 toast + warn 日志提示「请先停止」，停止后自动恢复。
  - **D12 工具提示**：标题栏 6 个按钮（置顶/折叠/深浅色/设置/帮助/互联）的自绘 `Toplevel` tooltip 全部收敛到 `utils.attach_tooltip`，动态文案改用 getter（`_tip_pin` / `_tip_fold` / `_tip_dark`），删除 `_tip_win` 及 `src/ui/settings/window.py` 残留，全应用统一 400ms 延迟与同一底色/描边 token。

### 5.3 基准脚本设计（`tools/_bench_ui.py`）

| 用例 | 方式 | 输出 |
| --- | --- | --- |
| 启动阶段 | `--gui` 时读 `crashlog.startup_marks()` | 各阶段 ms |
| 表格重建 | `--gui` 建 500/2000 行后计时 `_editor_sync_to_tree` | ms / 每行 μs |
| 日志 flush | 假控件（计数 `insert/tag_add/see/update`）+ 1000 条 | 调用次数 + ms |
| 列策略 | 纯函数，640/800/1024/1440 | 可见列数 + 总宽 |
| 背景合成 | `--gui` 计时 `load_bg_photo` 两种尺寸 | ms |

默认（无 `--gui`）只跑纯逻辑项，GUI 项标 `SKIP`，退出码 0。

---

## 6. 验收指标

### 6.1 硬指标

| 指标 | 基线 | 目标 |
| --- | --- | --- |
| 日志 1000 条：`Text.update()` 调用次数 | 1000 | **≤1** |
| 日志 1000 条：`insert` 调用次数 | 1000 | **≤3**（400/批） |
| 表格 500 行：每行 Tk 调用 | 4 | **≤1** |
| 表格 500 行重建耗时 | 见 §6.2 | ≤ 基线 60% |
| 640×520 可见数据列 | 9（横滚） | **4（无横滚）** |
| 首帧前图像合成 | 有 | **无** |
| `init_fonts` 调用次数 | 2 | **1** |
| `dialogs.py` 试点窗口字面量 padx/pady | 多处 | **0**（版本历史 / AI 调试；全文件清零属 P1） |
| 回归测试 | 3 套全绿 | 保持全绿 + 新增 1 套（39 条） |

### 6.2 实测记录（实施后回填）

复跑方式：`python tools/_bench_ui.py`（纯逻辑）与 `python tools/_bench_ui.py --gui`（实机）。
`改造前` 列中标注「代码基线」的项来自改造前实现的调用次数（可按 git 历史复跑旧版复核），
未单独计时。

| 用例 | 改造前 | 改造后 |
| --- | --- | --- |
| 日志 1000 条 · `Text.insert` 次数 | 1000（代码基线） | **3**（`GUI_BATCH_MAX=400` → 3 批） |
| 日志 1000 条 · 强制重绘次数 | 1000（每条 `update()`） | **3**（每条批一次 `update_idletasks()`） |
| 日志 1000 条 · `flush` 耗时 | 未计时 | **1.6 ms** |
| 表格 500 行 · 每行 Tk 调用 | 4（insert + tag_has + item 读 + item 写） | **1**（insert 直接带 tags） |
| 表格 500 行重建 | 未计时（代码基线 4 次/行） | **33 ms**（66 μs/行） |
| 表格 2000 行重建 | 未计时 | **49 ms**（25 μs/行） |
| 可见数据列 @640 / 760 / 1024 / 1440 px | 9 参数列固定（717px，必横滚） | **4 / 7 / 10 / 10 列**（327 / 522 / 717 / 717px） |
| 待机背景合成 900×500 | 在首帧内同步（阻塞） | **284 ms**，移到 idle 之后 |
| 待机背景合成 1400×800 | 在首帧内同步（阻塞） | **18 ms**（图源已缓存，仅重采样） |
| 主窗口构建耗时（`build_app`，实机） | 未计时 | **686–974 ms**（多次取样区间） |
| 启动阶段埋点 | `ui_build` | 新增 `ui_build_done` / `ui_first_frame` / `ui_ready`，可用 `crashlog.startup_marks()` 读取 |
| 从窗口尺寸缩放 | 字面量像素，`set_ui_scale` 对从窗口无效 | `ui_scale=1.25` 时 `md` 由 560×480 → **700×600**（单次缩放，无双重放大） |
| 回归测试 | 3 套 | **4 套全绿**（`_test_ui_layout` 39 条：§6 共 8 条骨架断言 + §5h/5i 回退开关断言） |

**`ui/window_shell.py` 落地状态**：`apply_window_shell()` + `ShellBody`（`header/footer/add_button/close`）
已就绪，`dialogs.py` 中「版本历史」「AI 智能调试」两个窗口完成试点迁移（几何/最小尺寸/居中/模态/
外边距全部 token 化）；`help/market/netlink/settings` 已全部套用 `apply_window_chrome`（P1 收口）。

**关于尺寸口径的一个坑（已修）**：`utils.place_dialog()` 内部会对 `w/h` 再做一次 `scaled()`，
骨架因此把 `shell_size()` 定义为**返回设计 px**，缩放只在 `place_dialog` 发生一次；
若骨架里用 `sp()` 取尺寸再交给 `place_dialog`，`ui_scale≠1` 时会出现平方放大（1.25 → 1.5625）。

---

## 7. 回退与回归

- **模块级开关**：`_TREE_AUTO_COLS`、`_LEGEND_AUTO`、`_IDLE_BG_DEFER`，默认开启；置 False 即恢复旧行为，无需回滚代码。
- **必须保持全绿**：`tools/_test_editor_cells.py`（55 条）、`tools/_test_script_model.py`、`tools/_test_param_form.py`（35 条，含源码文本断言：`tree.bind("<Double-1>", lambda e: _edit_cell(e))` 等字符串**不得改写**）。
- **禁止**：改 `log_dock.py` 的 `main_paned.forget/add` 显隐机制；改四行网格与 `main_paned` 结构；改 `commands.py` 语义。
- **性能红线**：列策略不得在 `<Configure>` 内同步重建 tree；日志热路径不得写日志。

---

## 附：改造点清单（文件级）

| 改造项 | 文件 | 位置 | 风险 | 阶段 |
| --- | --- | --- | --- | --- |
| flush 批渲染 | `src/utils.py` | `:713-760` | 低 | P0 |
| `winfo_exists` 守卫 | `src/utils.py` | `:718` 前 | 低 | P0 |
| 树批量重建 | `src/ACRPA.py` | `:2489-2545` | 中 | P0 |
| tag 预配置 | `src/ACRPA.py` | `:2440-2446`、`:5327-5329` | 低 | P0 |
| 列策略 | `src/ACRPA.py` | `:5279-5287` | 中 | P0 |
| 图例显隐 | `src/ACRPA.py` | `:1630`、`:5308-5324` | 低 | P0 |
| 背景延迟合成 | `src/ACRPA.py` | `:4820-4885`、构建末调用 | 低 | P0 |
| `init_fonts` 去重 | `src/ACRPA.py` | `:5037-5042` | 低 | P0 |
| 标题默认文案 | `src/ACRPA.py` | `:5125` vs `:1145` | 低 | P0 |
| 启动埋点细分 | `src/app.py` | `:271-290` | 低 | P0 |
| 窗口骨架 | `src/ui/window_shell.py`（新） | — | 中 | P0 |
| dialogs 试点 | `src/dialogs.py` | `:261-290` 等 | 中 | P0 |
| 工作流延迟构建 | `src/ACRPA.py`、`ui/workflow_view.py` | `_build_workflow_tab` / `_WF_LAZY` | 高 | P1（已完成） |
| 从窗口 token 化 | `help/market/netlink/settings` | 见 §2.4 | 中 | P1/P2（netlink 已完成） |
| D8 空态提示 | `src/ACRPA.py` | `_EMPTY_HINT` / `_sync_empty_hint` | 低 | P2（已完成） |
| D9 运行期编辑守卫 | `src/ACRPA.py` | `_edit_blocked` + 18 处入口 | 低 | P2（已完成） |
| D12 tooltip 组件化 | `src/ACRPA.py` / `utils.py` | `utils.attach_tooltip` | 低 | P2（已完成） |
