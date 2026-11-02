# UI 承载与刷新优化方案（Tk 承载力补救）

> 适用对象：ACRPA 桌面端（Python 3.12 + tkinter/ttk，Windows 为主）
> 本文分两部分：**§2 是本轮已落地的机制**（加性改动，均可回退），**§4 是尚未落地的分期路线**（含触发条件）。
> 文中所有耗时均为实机实测（i7 笔记本 / 2560×1600 @125% DPI / Python 3.12 / Tk 8.6），非估算。

---

## 0. 一句话结论

Tk 本身能承载当前界面，**问题不在控件类型（不用 ttk 也能流畅），而在三件事**：

1. **刷新模式**：把"整表重建"当默认刷新手段 → 每次编辑都 O(n) 重建；
2. **刷新频率**：固定 100ms 心跳 + 高频 `<Configure>/<MouseWheel>` 无去抖；
3. **主线程耗时操作**：磁盘解析、网络请求、浏览器拉起直接跑在 UI 线程。

本轮把这三件事都动了（增量刷新 / 自适应心跳 / 事件去抖 / 后台任务执行器），并顺手治了 DPI 查询与日志 tag 的放大调用。真正的"上万行表格"仍需 §4 的窗口化虚拟表格。

---

## 1. 症状、机制与代码证据

| 用户感受 | 机理 | 代码证据（改造前） |
|---|---|---|
| 「控件太多，一开就慢」 | 每个 Tk 控件 = 一条 Tcl 命令 + 一次几何计算，嵌套 Frame 还会叠加布局传播 | `src/` 内约 **918 处**控件构造；`ACRPA.py` 单文件 6300 行 / 237 个函数 / 145 处控件构造；设置窗口 11 张卡片各自建控件 |
| 「改一行就卡一下」 | 每次编辑都走全量重建：`tree.delete(*children)` + 逐行 `insert` | `_editor_sync_to_tree()` 原先无增量分支 |
| 「拖窗口一顿一顿」 | `<Configure>` 成串触发，每次 2 次 `bbox()` + 2 次 grid 重排 | 工具栏 `toolbar_inner.bind("<Configure>", ...)` 内先 `bbox` 设 scrollregion，再调 `_tb_update_overflow()` 又 `bbox` 一次 |
| 「日志越滚越卡」 | `Text` 无行数上限；每行 1 次 `get` + 1 次 `tag_names` + 2~3 次 `tag_add` | `ui/log_dock.py` 的 `_log_apply_cbar()` / `_log_apply_filter()` 逐行查询 Tcl |
| 「空闲时也像在忙」 | 主循环固定 100ms 重入，即使没有任何变化 | `root.after(100, _periodic)` |
| 「打开大脚本白屏」 | `script_io.load_script()`（磁盘 + xlrd 解析）跑在 UI 线程 | `_editor_load_xls()` 同步读取后逐行 `insert` |
| 「高 DPI 屏拖动窗口更慢」 | `dpi_factor()` 每次都 `winfo_fpixels("1i")`（Tcl 往返），被 `scaled()/sp()` 高频间接调用 | `utils.dpi_factor()` 无缓存 |

### 1.1 实测基线

| 场景 | 耗时 |
|---|---|
| `Treeview.insert` 1500 行（10 列 values） | ≈ 37 ms |
| `_editor_sync_to_tree()` 全量重建 1500 行（含序号列/配色/缓存） | ≈ 49 ms（0.033 ms/行） |
| 同上 5000 行 | ≈ 150 ms |
| `_editor_load_xls()` 异步返回（不含解析） | ≈ 0.5 ms |
| 1500 行建树后连续 20 帧 `update()`（真实绘制） | ≈ 800 ms，即**绘制**而非同步是主项 |

结论：**同步逻辑已不是瓶颈，绘制（行 × 可见列）才是**。这直接决定了 §4 的优先级：先砍「可见列 × 行数」，再谈虚拟化。

---

## 2. 本轮已落地机制

| # | 机制 | 位置 | 回退方式 |
|---|---|---|---|
| 1 | 主表尾部增量刷新（渲染缓存 + 前缀 diff） | `src/ACRPA.py` `_tree_tail_sync()` / `_editor_sync_to_tree()` | 令 `_tree_tail_sync()` 恒返回 False 即回到全量重建 |
| 2 | 日志行数上限 + 滞回裁剪 | `src/ui/log_dock.py` `_log_trim()`；配置项 `state.LOG_MAX_LINES`（默认 6000，0=不限），设置 → 日志卡 → 面板行数上限 可直接改 | 把上限设为 0 |
| 3 | 自适应心跳（忙 100ms / 闲 250ms） | `src/ACRPA.py` `_periodic()` 末尾 `root.after(100 if _busy else 250, _periodic)` | 改回常量 100 |
| 4 | 通用后台任务执行器（threading + queue + after 轮询） | `src/ui/bg_task.py`（新文件） | 不调用 `start()` 时 `submit()` 自动同步执行 |
| 5 | 大脚本加载移出主线程（带加载令牌防串档） | `src/ACRPA.py` `_editor_load_xls()` / `_invalidate_load_token()` | 令牌恒等 + 直接同步调用 |
| 6 | DPI 查询缓存 + `tk scaling` 统一入口（含像素制开关） | `src/utils.py` `refresh_dpi_cache()` / `dpi_factor()` / `apply_tk_scaling()`；配置项 `state.TK_SCALING_FIXED` | 默认即为原行为（auto） |
| 7 | 事件绑定精简：滚轮绑定幂等、`<Configure>` 去抖合并 | `src/ACRPA.py` `_bind_scroll_recursive()` / `_debounce_cb()` / `_tb_sync_layout()` | 直接 `bind` 原回调 |
| 8 | 日志 tag 批量化（O(行) Tcl 调用降到 O(级别)） | `src/ui/log_dock.py` `_log_level_runs()` + 两个批量填充函数 | 保留旧逐行实现即可对照 |
| 9 | 提示条单例复用 | `src/utils.py` `show_toast()` + `_toast` | 每调用新建 Toplevel（旧行为） |
| 10 | 大脚本自动折叠参数列 | `src/ACRPA.py` `_tree_column_plan(width, rows)`，阈值 `_TREE_WIDE_SCRIPT_ROWS = 1200` | 传 `rows=None` 即回到纯宽度判定 |

### 2.1 后台任务执行器（`ui/bg_task.py`）

```
工作线程:  fn()  ──put(result)──▶  queue.Queue
主线程:    root.after(60ms) 轮询 ──pop──▶  on_ok/on_err 回调（只在主线程执行）
```

要点：

- **Tk 非线程安全**：工作线程只做纯计算/IO，绝不碰控件；回调统一在主线程执行。
- **无 GUI 自动退化**：未 `start()` 时 `submit()` 当场执行并同步回调 —— 既有"调用返回即完成"的测试断言与 CLI 路径保持不变（`tools/_test_bg_task.py` 覆盖）。
- **关闭安全**：`_do_close()` 先 `shutdown()`，避免窗口销毁后 `after` 回调打到失效对象。
- 目前接入：脚本加载（`load-script`）。后续接入点见 §4 P2。

### 2.2 DPI 开销治理 —— 关于 `tk scaling=1.0`

用户诉求里的 `tk.call('tk', 'scaling', 1.0)` **不能裸用**：它把「1pt = 1px」写死，而命名字体是按 pt 声明的，结果在 150% DPI 屏上字会突然缩小 1/1.5（这是最常见的"改了 scaling 界面全变小"事故）。

正确做法（已实现为开关）：

| 模式 | `tk scaling` | 字体写法 | 视觉效果 | 何时用 |
|---|---|---|---|---|
| `auto`（默认） | `fpixels("1i")/72`（125% 屏 = 1.666） | 正值 pt | 随系统 DPI 正确放大 | 绝大多数情况 |
| `fixed`（`state.TK_SCALING_FIXED = True`） | 固定 `1.0` | **负值像素**（`-round(pt × 96/72 × dpi)`） | 与 auto 完全一致（实测行高同为 17px） | 需要完全绕开 Tk 的 pt→px 换算、或排查 DPI 相关布局异常 |

另外真正的高频开销不在这里，而在 `dpi_factor()`：它被 `scaled()/sp()` 间接调用，改造前每次都是一次 Tcl 往返。现在只在 `init_fonts()`（root 就绪）时算一次并缓存，`scaled()` 之后全走内存；跨屏移动需要刷新时显式调 `refresh_dpi_cache()`。

> 注意：Tk 8.6 **没有硬件加速**（无 GPU 合成），"启用硬件加速"在本项目只能落到「减少绘制量」，不能靠某个开关。任何声称"打开 Tk 硬件加速"的 API 都不存在。

### 2.3 事件绑定精简

- `_bind_scroll_recursive()` 幂等：同一 handler 已绑过的控件跳过。改造前工具栏/运行栏/工作流栏在初始化与重建路径里被重复绑定，Tk 事件表只增不减。
- 删掉了一处无效重复绑定（工作流栏在主构建前绑到的还是占位控件）。
- `<Configure>` 去抖 + 合并：工具栏布局同步（scrollregion + 溢出提示）合并为 `_tb_sync_layout()`，由 `_debounce_cb(..., 120ms)` 驱动 —— 每次尺寸变化从「2 次 `bbox` + 2 次 grid 重排」降到「一次 `bbox` + 一次判定」，且拖动过程中只处理最终状态。

### 2.4 组件复用

- **提示条单例**：`show_toast()` 复用同一个隐藏 `Toplevel`（`withdraw`/`deiconify`），不再每次提示新建 3 个控件 + 销毁窗口。提示是高频反馈（保存/撤销/复制/校验），Windows 上建窗口成本达数十毫秒且有映射闪烁。
- **命名字体复用**：`utils.init_fonts()` 建一次 `tkinter.font.Font`，全应用按名引用；缩放档位切换时只 `configure(exists=True)`，不重建控件。
- **主题不遍历控件**：`theme.apply_theme()` 只配置 `ttk.Style` 与主题键，面板通过 `ThemeBus` 自行重配（避免"换肤 = 全树 walk"）。

### 2.5 日志与 Treeview 批量化

`ui/log_dock.py`：

- 改造前每行：`get` + `tag_names` + `insert` + `tag_add`(×1~2) ≈ 4~5 次 Tcl 往返；6000 行 = 3 万次。
- 改造后：全文 `get` **一次** + 每个级别 `tag_ranges` **一次**（共 5 次），在 Python 里算「行号→级别」映射；过滤时把连续同判定行合并成区间，一段一次 `tag_add/tag_remove`。
- 幂等仍以「行首是否已含 ▌」判定，中途异常不会重复插入色条。

### 2.6 大脚本自动折叠参数列

`_tree_column_plan(width, rows)`：宽度断点（<760 → 4 列显示，<1024 → 7 列，否则 10 列）之外新增行数断点 —— **行数 > 1200 时最多显示 4 列**（命令 + 前 3 参数）。

- 只改 `displaycolumns`，**不动列结构、不动单元格编辑逻辑**，被隐藏的参数不丢（双击行仍可编辑全部参数），并在首次折叠时写一条日志提示。
- 收益直接对应用户体感：绘制成本 ≈ 行 × 可见列，10 列 → 4 列即降 60%。

---

## 3. Tk 的硬约束（不要试图"优化"绕过）

| 约束 | 现实影响 | 对策 |
|---|---|---|
| 无 GPU 合成 / 无虚拟化 | 行数 × 可见列决定绘制成本，上万行必卡 | 窗口化（§4 P2-a）或换承载（P4） |
| Tcl 往返成本 ≈ 10~20 µs/次 | 循环里逐行 `tree.item()` / `text.tag_names()` 必炸 | 批量读、批量写、缓存（已落地） |
| 回调里阻塞即冻结 UI | 任何 >50ms 的同步调用都会掉帧 | `ui/bg_task`（已落地） |
| `Treeview` 无 per-row 边框/色条 | 视觉需求只能靠 `#0` 文本符号或图片列 | `#0` 序号列 + 行首 `▌`（已落地） |
| ttk 主题不保证可预测行高 | 字体变化后行高漂移 | 显式 `rowheight` + `font` 一次性配置 |

---

## 4. 分期路线（未落地部分）

### P2-a 窗口化虚拟表格（触发条件：5000 行同步 > 300ms，或用户脚本常态 > 5000 行）

设计：只渲染视口附近 ±50 行的占位 item，滚动时按需重填。

- 必须**接管滚动条**（`yscrollcommand`/`yview` 自绘映射），否则 detached 行不占空间，滚不到底部；
- 必须保持 `iid = 行 id` 的映射，否则选中/断点/定位全乱；
- 必须先补齐回归：选中保持、双击编辑、上下移、撤销/重做、断点定位、查找定位、导出。
- 评估：约 300~500 行改动 + 7 项回归，属"两周内可做但不该顺手做"的规模。

### P2-b 参数列 → Inspector 分流（与 P2-a 互补）

主表默认只显示「序号 + 命令 + 前 2 参数」，其余参数在右侧/底部 Inspector 编辑（当前已有参数区，缺的是"主表不再为参数列留宽度"）。可把列数从 10 稳定压到 4，与 §2.6 的自动折叠合并成一套策略。

### P3 交互层

- 命令面板（Ctrl+K）：替代在 68 条命令里翻工具栏；
- 面板浮出（`place` + `lift`）代替 `Toplevel`：少一次窗口映射；
- 渐进披露：参数面板默认 3 个常用参数 + "更多"；设置卡片同理；
- 密度档位（紧凑/标准/宽松）：统一改 `rowheight/padding/字体`，一次生效。

### P4 承载替换（仅在实测仍不足时评估）

WebView2 / Qt 迁移的代价：打包体积、Win7 兼容、现有 6300 行 UI 代码重写、3 处原生对话框行为差异。**红线**：单文件脚本 < 5000 行且优化后交互 < 100ms 时，不迁移。

---

## 5. 验收指标（可用于回归对比）

| 指标 | 目标 | 测法 |
|---|---|---|
| 5000 行脚本打开 → 可交互 | < 800 ms | 掐表 + 日志时间戳 |
| 追加 1 行（尾部）刷新 | < 30 ms（实测 2.7 ms） | cProfile `_editor_sync_to_tree` |
| 1500 行全量重建 | < 60 ms（实测 49 ms） | 同上 |
| 拖动窗口 3 秒内 `<Configure>` 回调次数 | < 30 次（去抖后） | 回调里累加计数 |
| 空闲 10 秒主循环唤醒次数 | ≈ 40 次（250ms 档） | `_periodic` 计数 |
| 日志 6000 行滚动 | 无可感卡顿 | 手工 |
| 125%/150%/200% DPI 三档 | 无字体错位、无溢出 | `tools/_smoke_ui_scale.py` |

手工验收清单（需装机环境）：打开大脚本滚动、断点定位、撤销/重做 10 次、日志满屏后过滤切换、双屏不同 DPI 拖窗口、设置窗口反复开关 20 次。

---

## 6. 风险与回退

| 改动 | 风险 | 回退 |
|---|---|---|
| 尾部增量刷新 | 中部插入/删除若 diff 判断有误会漏更新 → 已用"前缀一致才走增量，否则全量"兜底 | 见 §2 表 #1 |
| 后台加载 | 加载期间又打开别的脚本 → 令牌机制丢弃旧结果 | 令牌恒定即退化为同步 |
| 日志裁剪 | 裁剪与游标（`_cbar_state`）不一致 → 已同步回退游标 | `LOG_MAX_LINES=0` |
| 列自动折叠 | 用户找不到参数列 | 选中行后参数区仍可编辑；日志有提示；传 `rows=None` 关闭 |
| `TK_SCALING_FIXED` | 误开导致字体异常 | 默认 False；改回 auto 即恢复 |

---

## 附：相关文件

- `src/ui/bg_task.py`（后台任务执行器）+ `tools/_test_bg_task.py`
- `src/utils.py`：DPI 缓存 / `apply_tk_scaling` / 像素制字体 / 提示条单例
- `src/ACRPA.py`：增量刷新、事件去抖、列折叠、异步加载
- `src/ui/log_dock.py`：行数上限、批量 tag
- `src/state.py`：`LOG_MAX_LINES`、`TK_SCALING_FIXED`
- `src/ui/settings/cards/log.py`：日志卡新增「面板行数上限」
