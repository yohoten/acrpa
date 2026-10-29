# 落地总清单：ACRPA 能力补强与工作流拓展（合并去重版）

> **性质**：本文件是 [`docs/参考分析-zTasker-DLL复用判定与能力补强方案.md`](参考分析-zTasker-DLL复用判定与能力补强方案.md)（下称「DLL 篇」）与 [`docs/方案-Quicker截图驱动的功能与工作流拓展.md`](方案-Quicker截图驱动的功能与工作流拓展.md)（下称「Quicker 篇」）的**校验 + 合并去重 + 单一执行清单**。
> **校验方式**：两文全部 `path:line` 引用逐条打开源码核对（只读），所有「实际内容」均来自真实文件；无法确认的一律标注原因。
> **写作约束**：不复述方案细节（细节回查原文对应章节）；本文件只回答「做什么 / 改哪个文件 / 依赖谁 / 怎么验收 / 排在哪一批」。
> **代码基线**：ACRPA v0.1.29-beta，Python 3.12 x64 + Tkinter，体积硬目标 < 15 MB（`build.py:2`、`build.py:21`）。

---

## 0. 一页摘要

| 项 | 结论 |
| --- | --- |
| 引用核查 | 抽取并逐条打开 **74** 处 `path:line`：**✔ 60 / ⚠️ 6 / ✘ 8**；另发现 **5** 处文档内部口径不一致。共修正 **14** 处（详见 §6） |
| 最高优先级核查项 | `workflow_view.py` **存在**，真实路径 `src/ui/workflow_view.py`（2650 行）。Quicker 篇全篇使用的是 `src/ui/workflow_view.py`，**没有**把它写成 `src/workflow_view.py` → 无需连锁修正 §6 与 W2 |
| 命令数实测 | `command_groups.json`：**9 组 / 68 条**；`command_schema.json`：`stats.commands = 68`、commands 数组 68 项；`src/commands.py` 顶层 `register(` 调用 **88** 处（68 + 20 条 L2）。**88 这个数字成立** |
| 能力基线口径 | 两文**一致**：热键任务级绑定已支持、调度器 8 触发器 + 错过补偿 + 生效区间 + 次数上限已达标、`cv2` 冻结版被排除、`paddle_dll` / `input_dd` 探测是假阳性 |
| 体积基线 | **静态推算，未实测**（工作区无 `dist/ACRPA.exe` 可读，且本轮不允许执行构建）。旧文档记载 onefile ≈ 14.3 MiB；剩余预算 ≈ 0.7 MB；两文增量合并后 ≈ 570 KB → 余量 ≈ 130 KB（详见 §5） |
| 条目总数 | **59** 条（A-01 ~ A-59）。B0 = 21 / B1 = 12 / B2 = 9 / B3 = 17 |
| 最紧要的一条结论 | 先做 B0 的「数据层 + 假阳性修复」，**再动执行层，最后才动 UI**；顺序颠倒会产生两次返工（Quicker 篇 §7.0 的论证经代码核对成立） |

---

## 1. 总览表（A-01 ~ A-59，合并去重后）

> **类型**：数据层 / 执行层 / UI层 / 原生能力 / 打包
> **工作量**：`S` 单文件可独立验收 / `M` 跨 2~3 模块需回归 / `L` 引新依赖或改打包形态（沿用两文口径，不使用人日估算）
> **来源**：`DLL §x` = DLL 篇章节号；`Q §x` = Quicker 篇章节号；`两文合并` = 两文都提到、本清单已合并为一条

### 1.1 B0 —— 零依赖 / 数据层 / 假阳性修复（21 条）

| 编号 | 条目名（统一命名） | 来源 | 目标文件 | 类型 | 前置 | 量 | 验收判据（可测断言） | 批 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-01 | `native_check.py`（`pe_machine` / `is_x64` / `dll_loadable` / `dir_ascii_only`） | DLL §5.8 步1、P0-1 | `src/native_check.py`（新建） | 原生能力 | — | S | x86 样本 `pe_machine` 返回 `0x14C`、x64 返回 `0x8664`；`is_x64(32位DLL)` 返回 `(False, 含"32 位")` | B0 |
| A-02 | 四个 probe 改写（消除假阳性 READY） | DLL §5.8 步2、P0-2 | `src/capabilities.py`（改） | 原生能力 | A-01 | S | ① `lib/paddle_ocr` 只放裸 `PaddleOCR.dll` → `probe("ocr.paddle_dll")=BROKEN` 且 reason 列出 4 个缺文件名；② 32 位 `dd63330.dll` → `input.dd=BROKEN`；③ 冻结环境 `cv.match` 的 reason 含「冻结版不含 cv2」；④ playwright 包装了但浏览器未装 → `browser.playwright=BROKEN` | B0 |
| A-03 | `ocr_consts.py`（`DEP_DLLS` / `MODEL_FILES` 单一定义） | DLL §5.2(b)、P0-3 | `src/ocr_consts.py`（新建）+ `src/paddle_dll.py`（改 import） | 数据层 | — | S | `paddle_dll.DEP_DLLS is capabilities.PADDLE_DEP_DLLS` 为真（同一对象）；全库 grep 无第二份字面量清单 | B0 |
| A-04 | `paddle_dll.self_check()` | DLL §5.2(a)、P0-3 | `src/paddle_dll.py`（改） | 原生能力 | A-01, A-03 | S | 缺依赖返回 `("broken", 原因, [4 个文件名])`；`self_check` 全程不调 `ctypes.WinDLL`（调用计数 0） | B0 |
| A-05 | OCR 自检清单与降级提示 | DLL §5.2(d)、P0-3 | `src/ui/settings/cards/advanced.py`（改）+ `src/ocr_backend.py`（改） | UI层 | A-04 | M | 设置→高级 OCR 行显示当前后端 + 失败原因；「DLL 自检」列出逐项缺失/就绪；缺包时 toast 含最短修复路径 | B0 |
| A-06 | 能力体检卡片 | DLL §5.8 步4、P0-10 | `src/ui/settings/cards/extensions.py`（改） | UI层 | A-02 | S | 卡片列出全部已注册能力的 `label/state/reason`，与 `capabilities.probe_all()` 输出逐条一致 | B0 |
| A-07 | 启动期 BROKEN 汇总日志 | DLL §5.8 步4、P0-10 | `src/app.py` 或 `src/crashlog.py`（改） | 原生能力 | A-02 | S | 启动日志中 BROKEN 项有且仅有 1 条汇总记录；MISSING 项不产生日志 | B0 |
| A-08 | `hotkey_win.py`（`RegisterHotKey` + `WM_HOTKEY` 消息窗口 + 三步迁移） | DLL §5.1、P0-4 | `src/hotkey_win.py`（新建）+ `src/state.py`（改，加 `hotkey_backend`）+ `src/ACRPA.py`（改） | 原生能力 | — | L | ① 绑 `Ctrl+Shift+F9` 触发脚本且前台记事本收不到该键；② 非主线程 `register` 仍能触发；③ `state.HOTKEY_BACKEND="poll"` 时行为与当前版本等价 | B0 |
| A-09 | 热字串 `HotstringEngine` | DLL §5.1、P0-5 | `src/hotkey_win.py`（改） | 原生能力 | A-08 | M | 输入 `;;acr` 自动展开为配置文本；管理员权限窗口内不触发（符合预期，UI 有说明）；默认关闭 | B0 |
| A-10 | `archive_ops.py`：压缩文件 / 解压文件 / 压缩包列表 | DLL §5.5、P0-6 | `src/archive_ops.py`（新建）+ `src/commands.py`（改） | 执行层 | — | M | 压缩→解压后逐文件哈希一致；含 `../../evil.txt` 的恶意 zip 被拒且目标目录外零文件；1KB→4GB 炸弹被拦 | B0 |
| A-11 | `priv_ops.py`：以管理员运行 + `is_admin()` | DLL §5.7、P0-7 | `src/priv_ops.py`（新建）+ `src/commands.py`（改） | 原生能力 | — | S | 提权启动 notepad：UAC 出现、确认后为提权进程；取消时 5s 内返回 `(False,"用户取消了提权")` | B0 |
| A-12 | `audio_ops.py`：播放声音 | DLL §5.6、P0-8 | `src/audio_ops.py`（新建）+ `src/commands.py`（改） | 原生能力 | — | S | 2s WAV 播放成功且 `async` 时命令立即返回；传 `.mp3` 返回 `(False,"仅支持 WAV…")` 不抛异常 | B0 |
| A-13 | `screen_ops.py`：连续截图 + 屏幕取色 + 颜色转换 | DLL §5.6 P0-8 + Q §4.11 | `src/screen_ops.py`（新建）+ `src/commands.py`（改） | 原生能力 | — | M | 间隔 1s×5 张，相邻帧时间差 1.0±0.15s；中途「停止」1 个间隔内终止且保留已产出文件；取色 1000 次无 GDI 句柄泄漏 | B0 |
| A-14 | `sys_ops` 追加 3 条命令（蓝牙设备 / 校时检查 / 连通性检测） | DLL §5.7、P0-9 | `src/sys_ops.py`（改）+ `src/commands.py`（改） | 原生能力 | A-01 | M | 无蓝牙机器返回 `(False,"未检测到蓝牙无线电")` 且 <3s；校时前后系统时间未被修改；TCP 开放端口 ok / 未开放端口 False | B0 |
| A-15 | `command_groups.json` 补齐 20 条 L2 + 2 个新分组 | Q §3.1.2、§7.1 W0-1 | `res/help/command_groups.json`（改） | 数据层 | — | S | 组内命令总数 == `len(commands.list_names())` == **88**，且 `_fallback_title` 组为空；共 **11** 组 | B0 |
| A-16 | `gen_command_schema.py`（schema 单向生成 + CI 一致性校验） | Q §7.1 W0-2 | `tools/gen_command_schema.py`（新建）+ `res/help/command_schema.json`（生成） | 数据层 | A-15 | S | 改注册表却不重跑生成器 → CI 失败；生成结果与 `commands.list_all()` 逐条一致 | B0 |
| A-17 | 分组元数据扩展（`icon` / `tier` / `order`） | Q §7.1 W0-3 | `res/help/command_groups.json`（改） | 数据层 | A-15 | S | 旧读取方（`src/help_content.py`）无异常；新字段能被 `module_catalog` 读到 | B0 |
| A-18 | `flow_graph` 节点类型常量 + `CONTAINER_TYPES` 扩项 + `K_BRANCHES` | Q §7.1 W0-4 | `src/flow_graph.py`（改） | 数据层 | — | S | 旧样例 `demote(promote(x))==x` 全绿；新增 `group`/`if` 样例往返等价 | B0 |
| A-19 | `NO_INTERP` 覆盖率门禁单测 | Q §7.1 W0-6 | `tools/`（新增单测） | 执行层 | A-16 | S | 故意漏登记一条命令 → 单测失败（现状 88 条已全登记，`src/commands.py:900-972`） | B0 |
| A-20 | `analyze()` 静态检查扩 4 个 code | Q §7.1 W0-5 | `src/flow_graph.py`（改） | 数据层 | A-18 | M | `CTRL_OUT_OF_SCOPE` / `PARALLEL_GUI` / `BAD_CONDITION` / `SUBPROGRAM_MISSING` 各一条构造用例能报出对应 code | B0 |
| A-21 | 打包配置同步（新模块进 `_PROJECT_MODULES` 与 `ACRPA.spec` hiddenimports） | DLL §6.4 | `build.py`（改）+ `ACRPA.spec`（改） | 打包 | A-01…A-14 | S | `python build.py` 通过且 `check_exe_volume()` 返回 0；冻结版中 A-10/11/12/13/14 的 9 条命令可用 | B0 |

### 1.2 B1 —— 执行层：控制流与作用域（12 条）

| 编号 | 条目名（统一命名） | 来源 | 目标文件 | 类型 | 前置 | 量 | 验收判据 | 批 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-22 | `_execute_step` 返回控制流信号契约 | Q §4.5、§5.3.2、W1-1 | `src/workflow.py`（改） | 执行层 | A-18 | L | 加断言 `sig in (None,"break","continue","return")`；双层循环 + group 穿透 + 子程序 return 三组用例通过 | B1 |
| A-23 | `group` 节点（步骤组） | Q §4.1、W1-2 | `src/workflow.py`（改） | 执行层 | A-22 | M | `enabled=False` 的组内 3 步全跳过且日志出现 1 条「跳过已禁用步骤组」；组级变量组外不可见；组内 `break` 穿透到外层 `loop` | B1 |
| A-24 | `if` 节点（多分支 if/elif/else） | Q §4.2、W1-2 | `src/workflow.py`（改） | 执行层 | A-22 | M | 三分支对 3 组输入各命中一次且每分支 5 步全执行；非法表达式走 else + warning 不抛；旧 `condition` 文件行为逐条不变 | B1 |
| A-25 | `break` / `continue` / `return` 三节点 | Q §4.5、W1-2 | `src/workflow.py`（改） | 执行层 | A-22 | M | 内层 `break` 只退内层；子程序内 `return 42` → 调用方 `out` 拿到 42；顶层孤立 `break` 不抛异常且检查面板列出 `CTRL_OUT_OF_SCOPE` | B1 |
| A-26 | `loop` 三形态（`count` / `while` / `foreach`） | Q §4.3、W1-3 | `src/workflow.py`（改） | 执行层 | A-22 | M | `foreach` 遍历 5 行执行 5 次且 `{var:index}` 为 0..4；空序列 0 次 + info 日志；`count`/`while` 与现状逐条一致 | B1 |
| A-27 | `subprogram` 定义 + `call` 节点 | Q §4.4、W1-4 | `src/workflow.py`（改） | 执行层 | A-22 | L | 传参互不污染；子程序内 `return` 后调用方继续；递归 17 层报「子程序递归过深」；子程序内设变量调用方不可见 | B1 |
| A-28 | 五层变量作用域 + `ScopedContext` | Q §5.3.3、W1-5 | `src/interp.py`（改）+ `src/engine.py`（改 `make_interp_ctx`） | 执行层 | A-23…A-27 | L | 5 层查找链单测通过；旧脚本变量行为零变化（回归基线一致） | B1 |
| A-29 | `try` 节点（重试 + `on_error` 三档） | Q §5.3.4、W1-6 | `src/workflow.py`（改） | 执行层 | A-24 | M | 重试 2 次后成功/失败两态正确；`on_error=跳到` 执行 `fallback_steps` 并跳过剩余步骤 | B1 |
| A-30 | `schema:3` 读写与迁移 | Q §5.6.2、W1-7 | `src/workflow.py`（改）+ `src/flow_graph.py`（改） | 数据层 | A-23…A-29 | M | 旧样例迁移前后字段等价；`schema:2` 文件日志与步骤顺序逐条一致；`schema:3` 经 `_normalize_step` 不出现「未知步骤类型」 | B1 |
| A-31 | `input_router.py` 双后端（DD + SendInput） | DLL §5.3、P1-1 | `src/input_router.py`（新建）+ `src/engine.py`（改） | 执行层 | — | L | 未装 pyautogui 时「坐标/按键/写入/滚轮/悬停/拖拽/按下/释放/热键」9 条全可用；「写入」中文不依赖剪贴板；悬停 500px/0.5s 实测 0.5±0.1s 且为渐变 | B1 |
| A-32 | JSONPath 子集增强「JSON 取值」 | DLL §5.5(e) P1-3 + Q §4.21 | `src/net_ops.py`（改） | 数据层 | — | S | 5 种语法各一条单测；旧 `a.b.c`（无 `$` 前缀）行为不变；10MB JSON 解析 < 300ms | B1 |
| A-33 | 薄封装命令第一批（`str_ops.py` / `list_ops.py`） | Q §4.25、W1-8 | `src/str_ops.py`、`src/list_ops.py`（新建）+ `src/commands.py`（改） | 数据层 | A-16 | S×7 | `生成GUID` / `随机数` / `计算时间` / `字符信息` / `正则提取` / `文本模板` / `文本转列表`+`列表转文本` 各一条单测并出现在分组里 | B1 |

### 1.3 B2 —— 编辑器 UI：三栏 + 卡片视图（9 条）

| 编号 | 条目名（统一命名） | 来源 | 目标文件 | 类型 | 前置 | 量 | 验收判据 | 批 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-34 | `module_catalog.py` + 模块库改数据源（schema 驱动） | Q §6.1.3、W2-1 | `src/ui/module_catalog.py`（新建）+ `src/ui/workflow_view.py`（改） | UI层 | A-15, A-17 | M | 模块库条目数 = 注册表条目数（B0 后 88，B1 后 ≈110）；JSON 损坏时回退硬编码 `_WF_LIB_CATEGORIES` 兜底不崩 | B2 |
| A-35 | 模块库交互增强（`Ctrl+F` / 悬停提示 / 拖拽 / 折叠持久化 / 搜索去抖） | Q §6.1.4、§6.1.5、W2-2 | `src/ui/workflow_view.py`（改） | UI层 | A-34 | M | 5 项各有可测行为；折叠状态重启后保持；搜索态强制全展开且**不写回**折叠状态 | B2 |
| A-36 | `inspector_panel.py` 右栏（复用 `build_arg_form`） | Q §6.3、W2-3 | `src/ui/inspector_panel.py`（新建）+ `src/ui/workflow_view.py`（改） | UI层 | A-34 | L | 选中 `group`/`if`/`call`/`command` 四类各渲染正确；参数控件与 `commands.schema()` 一致；切换选中时 `frame.destroy()` 重建无残留 | B2 |
| A-37 | `ui_bridge.py`（GUI 主线程回投基础设施） | Q §4.7/§4.8/§4.12 共同前置 | `src/ui/ui_bridge.py`（新建） | UI层 | — | M | 非主线程调用 GUI 命令不卡死；`root` 不可用时统一返回 `NO_GUI` | B2 |
| A-38 | 拾取器族命令：表单 / 弹出菜单 / 屏幕拾取 / 等待按键 | Q §4.7、§4.9、§4.10、§4.12、W2-7 | `src/ui/form_dialog.py`（新建）+ `src/commands.py`（改）+ `src/recorder.py`（只复用钩子） | UI层 | A-37 | L | 并行步骤中调用不卡死（各一条用例）；`Esc` 取消后全屏遮罩销毁且无残留置顶窗；`等待按键` 连续 100 次无句柄泄漏 | B2 |
| A-39 | 进度条 + 操作窗 | Q §4.8、W2-7 | `src/ui/progress_window.py`、`src/ui/pane_window.py`（新建） | UI层 | A-37 | M | 确定模式按 update 推进到 100% 自动关闭；用户手动关窗后 `更新` 返回 `STALE_HANDLE` 不崩；点取消 3s 内中断流程 | B2 |
| A-40 | `step_card_view.py` 卡片视图（第三视图） | Q §6.2、W2-4 | `src/ui/step_card_view.py`（新建）+ `src/ui/workflow_view.py`（改） | UI层 | A-36, A-30 | L | 300 节点滚动重绘 < 16ms；大纲/图/卡片三视图切换数据不丢（共享同一 `Graph`）；卡片类型色与 `_FLOW_COLORS`(`src/ui/workflow_view.py:218`) 同源 | B2 |
| A-41 | 录制 → 步骤树（`rows_to_steps` / `compress_steps` / `suggest_groups`） | Q §5.5、W2-5 | `src/recorder_convert.py`（新建） | 数据层 | A-30 | M | 录制 50 步 → 转树 → 运行，`_step_results` 序列与直接跑行式脚本一致；`suggest_groups` 只产建议不自动改 | B2 |
| A-42 | 脏标记 + 快捷键 | Q §6.4、W2-6 | `src/ui/workflow_view.py`（改）+ `res/help/10-快捷键.md`（**只追加 5 行**） | UI层 | A-40 | S | 关窗弹「保存/放弃/取消」生效；`Ctrl+F`/`Ctrl+S`/`Esc`/`Ctrl+Shift+G`/`Ctrl+Shift+R` 可用且与既有键不冲突 | B2 |

### 1.4 B3 —— 打磨与扩展包（17 条）

| 编号 | 条目名（统一命名） | 来源 | 目标文件 | 类型 | 前置 | 量 | 验收判据 | 批 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-43 | 「检查依赖」命令（`dep_ops.py`） | Q §4.6 | `src/dep_ops.py`（新建）+ `src/commands.py`（改） | 数据层 | A-16, A-02 | M | 缺 1 文件 + 策略=报错 → 命令失败且后续步骤不执行；策略=跳过 → 成功且 `{var:dep_result}` 含 `ok:false`；全齐时 < 50ms | B3 |
| A-44 | 系统类命令族（文件监控 / 查找窗口 / 服务控制 / 进程信息 / 输入法状态 / 系统路径） | Q §4.14/§4.15/§4.22/§4.23/§4.24、W3-3 | `src/fs_watch.py`（新建）+ `src/commands.py`（改）+ `src/engine.py`（复用 `_find_window_by_pattern`） | 原生能力 | A-16 | M×6 | 各条按原文验收断言；`文件监控` / `等待按键` / 屏幕拾取 / `HTTP服务` 四条各含「连续调用 N 次无句柄增长」 | B3 |
| A-45 | 网络类命令族（HTTP服务 / AI对话 / HTML取值） | Q §4.19/§4.20、W3-4 | `src/http_serve.py`（新建）+ `src/commands.py`（改） | 原生能力 | A-16 | M×3 | HTTP 服务：`curl` 返回文件、URL 编码的 `../../` 返回 403、端口占用返回 `PORT_BUSY`、默认仅绑 127.0.0.1；AI对话：无 key 时成功且空串 + warning，`失败即停=是` 时失败 | B3 |
| A-46 | 真并行（变量快照 + join 策略 + 冲突告警） | Q §5.3.6、W3-1 | `src/workflow.py`（改） | 执行层 | A-28 | L | 两分支写同名变量按 `steps` 顺序后者覆盖并记 warning 列出冲突键；日志时间戳证明并发度 > 1（现状 `lock` 串行） | B3 |
| A-47 | 断点续跑（checkpoint + `node_path`） | Q §5.3.5、W3-2 | `src/wf_checkpoint.py`（新建）+ `src/flow_graph.py`（改） | 执行层 | A-30 | L | 每完成一个顶层节点落 checkpoint；重启可从失败节点继续；`parallel` 内节点不可续跑（UI 明示） | B3 |
| A-48 | 专家层收纳与分层隐藏 | Q §9.1、W3-5 | `src/ui/module_catalog.py`（改）+ `src/ui/workflow_view.py`（改） | UI层 | A-34 | S | `core` 默认展开 / `advanced` 折叠 / `expert` 默认隐藏；搜索时忽略 tier | B3 |
| A-49 | 帮助文档同步 | Q §7.4 W3-6 | `res/help/01-命令速查.md`（自动）、`04-工作流.md`（手写）、`10-快捷键.md`（追加） | 数据层 | 各条完成 | M | 命令速查随注册表自动更新（`sections.json:3` `dynamic:"commands"`）；`04-工作流.md` 按节点类型分节 | B3 |
| A-50 | `mail_ops.py`（发送邮件）+ 解禁 `smtplib`/`imaplib`/`poplib` | DLL §5.5、P1-2 | `src/mail_ops.py`（新建）+ `build.py`（改第 219 行）+ `ACRPA.spec`（改第 13 行） | 打包 | A-02 | M | StartTLS 发纯文本 + 1 附件成功；日志密码显示 `***`；`check_exe_volume()` 返回 0 | B3 |
| A-51 | `uia` 扩展包（`comtypes` + 宿主侧 4 条命令壳） | DLL §5.4、P1-4 | `extensions_src/uia/`（新建）+ `src/uia_backend.py`（新建）+ `src/capabilities.py`（改） | 打包 | A-01, A-02 | L | 记事本「控件赋值」成功；`calc.exe` 按 AutomationId 控件点击结果正确；未装 comtypes 时 4 条命令带 ⚠ 且给可读提示而非 `ModuleNotFoundError` | B3 |
| A-52 | 气泡通知 + 注册表读/写命令 | DLL §5.7、P1-5 | `src/commands.py`（改） | 原生能力 | A-11 | S | 托盘气泡弹出；标题/内容过长时截断不报错（`NOTIFYICONDATAW.szInfo` 上限） | B3 |
| A-53 | `rapidocr` 第 5 后端（代码支持、默认不打包）+ `ocr_rapid` 扩展包 | DLL §5.2、P1-6 | `src/ocr_backend.py`（改）+ `extensions_src/ocr_rapid/`（新建） | 打包 | A-04 | M | 未装时 `_backend_order` 结果与改造前**逐元素相同**；装后并把偏好设为 `rapidocr` 时「识别文字」返回文本 | B3 |
| A-54 | 找色 + 找图 ROI/多分辨率自适应 | DLL §7.3 P2-5 + Q §4.16 | `src/screen_ops.py`（改）+ `src/engine.py`（改） | 原生能力 | A-13 | M | 全屏找 `#FF0000` 1920×1080 步长 2 < 150ms；容差 0 不误命中邻近色；冻结版（无 cv2）可用 | B3 |
| A-55 | 真录屏扩展（`mss` + `imageio-ffmpeg`） | DLL §5.6、P2-1 | `extensions_src/screen_record/`（新建） | 打包 | A-51（复用扩展机制） | L | 未装时 `probe("screen.record")=MISSING` 且主包无 import 报错；扩展说明明示 ffmpeg 为 GPL 构建、不得随发行包分发 | B3 |
| A-56 | 录音扩展（`sounddevice`） | DLL §7.3 P2-2 | `extensions_src/audio_io/`（新建） | 打包 | A-51 | M | 同上（未装 `MISSING`，主包零 import 报错） | B3 |
| A-57 | `.7z` 支持（`subprocess` 调本机 `7z.exe`） | DLL §5.5(d)、P2-3 | `src/archive_ops.py`（改）+ `src/commands.py`（改） | 原生能力 | A-10 | S | 打包 `.7z` 与解包往返一致；无 `7z.exe` 时给可读提示（复用 `build.py:51-55` 的路径探测常量） | B3 |
| A-58 | 上下文敏感热键（等价 `#HotIf`） | DLL §7.3 P2-4 | `src/hotkey_win.py`（改） | 原生能力 | A-08 | M | 记事本前台时热键生效、浏览器前台时不生效 | B3 |
| A-59 | 扩展包族（Excel / Websocket / 二维码 / 语音） | Q §3.2 F1/F6、J7、N4/N10、K17、§9.2 | `extensions_src/excel/`、`ws/`、`qrcode/`、`voice/`（新建） | 打包 | A-51 | L | 各扩展未装时对应命令 `MISSING` 且主包无 import 报错；`openpyxl`/`websocket-client`/`qrcode`/`pyzbar`/`sounddevice` **一律不进主包** | B3 |

---

## 2. 批次划分

> 三个批次的划分原则来自 Quicker 篇 §7.0「数据层 → 执行层 → UI」，该顺序经代码核对成立：`_WF_LIB_ACTIONS`(`src/ui/workflow_view.py:596-641`) 是手写 lambda、`CONTAINER_TYPES`(`src/flow_graph.py:55`) 只有 2 个容器、`command_groups.json` 缺 20 条 —— 先做 UI 必然造出第二套数据结构。

### 2.1 B0 —— 零依赖 / 数据层 / 假阳性修复（21 条：A-01 ~ A-21）

| 项 | 内容 |
| --- | --- |
| **进入条件** | 无。B0 全部为零新增 pip 依赖、纯 `stdlib` / `ctypes` 条目，且 A-15 / A-18 与 DLL 侧条目**无共享文件**，两条轨道可并行 |
| **退出判据** | ① `requirements.txt` 零改动；② `python build.py` 通过且 `check_exe_volume()` 返回 0；③ 88 条命令全部回归通过；④ `capabilities` 中不再存在「READY 但调用必失败」项（四类构造环境验证）；⑤ 冻结版中 A-10/11/12/13/14 的 9 条新命令可用；⑥ 模块库/命令速查能读到 88 条命令，`_fallback_title` 组为空 |
| **包含条目** | A-01 A-02 A-03 A-04 A-05 A-06 A-07 A-08 A-09 A-10 A-11 A-12 A-13 A-14 A-15 A-16 A-17 A-18 A-19 A-20 A-21 |
| **可并行轨道** | 轨道 A（DLL 侧，只碰 `src/*.py` 新模块 + `capabilities.py` + `ACRPA.py`）：A-01→A-02→{A-03→A-04→A-05, A-06, A-07}；A-08→A-09；A-10；A-11；A-12；A-13；A-14。轨道 B（数据层，只碰 `res/help/*.json` + `src/flow_graph.py`）：A-15→A-16→{A-17, A-19}；A-18→A-20 |

### 2.2 B1 —— 执行层：控制流与作用域（12 条：A-22 ~ A-33）

| 项 | 内容 |
| --- | --- |
| **进入条件** | B0 退出；A-18（`flow_graph` 常量）与 A-15（分组补齐）已验收；A-22 落地前**必须先 grep 全部 `_execute_step` 调用点**并记录当前返回值使用情况（见 §4 第 6 项） |
| **退出判据** | ① `group`/`if`/`loop`(三形态)/`call`/`break`/`continue`/`return`/`try` 八类节点各有一条单测 + 一条组合用例；② `schema:2` 旧文件日志与步骤顺序逐条不变；③ 五层查找链单测通过且旧脚本变量行为零变化；④ `input_router` 在无 pyautogui 环境下 9 条输入命令全可用 |
| **包含条目** | A-22 A-23 A-24 A-25 A-26 A-27 A-28 A-29 A-30 A-31 A-32 A-33 |
| **关键顺序** | A-22 是唯一硬前置（信号契约）。A-23/A-24/A-25/A-26 可在 A-22 后并行；A-27 依赖 A-22 的 `return` 语义；A-28 必须在 A-23~A-27 之后（作用域是容器实现的下游）；A-30 依赖 A-23~A-29 全套；A-31 / A-32 / A-33 与主链**无依赖**，可全程并行 |

### 2.3 B2 —— 编辑器 UI（9 条：A-34 ~ A-42）

| 项 | 内容 |
| --- | --- |
| **进入条件** | A-15 / A-17 已验收（数据源可读）；A-30 已验收（`schema:3` 与 `branches` 结构可读写）；A-37 必须先于 A-38 / A-39 |
| **退出判据** | ① 模块库条目数 = 注册表条目数；② 右栏四类节点渲染正确且参数控件与 schema 一致；③ 卡片视图 300 节点滚动重绘 < 16ms；④ 三视图切换数据不丢；⑤ 并行步骤中调用 4 条 GUI 命令不卡死；⑥ 关窗脏数据拦截生效 |
| **包含条目** | A-34 A-35 A-36 A-37 A-38 A-39 A-40 A-41 A-42 |
| **关键顺序** | A-34 → A-35；A-34 → A-36 → A-40 → A-42；A-37 → {A-38, A-39}；A-41 依赖 A-30，可提前并行 |

### 2.4 B3 —— 打磨与扩展包（17 条：A-43 ~ A-59）

| 项 | 内容 |
| --- | --- |
| **进入条件** | B1 退出（`try` / 作用域 / `schema:3` 可用）；扩展类条目（A-51/A-55/A-56/A-59）必须先完成 A-51 的扩展机制对接（宿主侧 `capabilities` + `extensions_src/` 目录骨架） |
| **退出判据** | ① 所有扩展在未安装时主包零 import 报错、`probe` 返回 `MISSING`；② 真并行并发度 > 1 且冲突有 warning；③ `check_exe_volume()` 仍返回 0，且主包体积增量 < 700 KB（见 §5）；④ 帮助文档三处同步完成 |
| **包含条目** | A-43 A-44 A-45 A-46 A-47 A-48 A-49 A-50 A-51 A-52 A-53 A-54 A-55 A-56 A-57 A-58 A-59 |
| **关键顺序** | A-51（扩展机制）→ {A-50, A-53, A-55, A-56, A-59}；A-13 → A-54；A-10 → A-57；A-08 → A-58；A-28 → A-46；A-30 → A-47；A-34 → A-48；A-49 收口 |

---

## 3. 依赖图

### 3.1 缩进列表（权威版，与 §3.2 图一致）

```
B0（无前置，两条轨道并行）
├─ 轨道 A：能力与原生补强
│  ├─ A-01 native_check
│  │  ├─ A-02 四个 probe 改写
│  │  │  ├─ A-06 能力体检卡片
│  │  │  ├─ A-07 启动期 BROKEN 汇总日志
│  │  │  ├─ A-43 检查依赖命令（B3）
│  │  │  └─ A-50/A-51（B3 扩展类，还需 A-51 的机制落地）
│  │  └─ A-14 sys_ops 3 条命令
│  ├─ A-03 ocr_consts ──> A-04 paddle_dll.self_check ──> A-05 OCR 自检 UI
│  │                                                └─> A-53 rapidocr 第 5 后端（B3）
│  ├─ A-08 hotkey_win ──> A-09 热字串
│  │                  └─> A-58 上下文敏感热键（B3）
│  ├─ A-10 archive_ops ──> A-57 7z 支持（B3）
│  ├─ A-11 priv_ops ──> A-52 气泡通知 + 注册表（B3）
│  ├─ A-12 audio_ops
│  ├─ A-13 screen_ops ──> A-54 找色 + ROI（B3）
│  └─ A-21 打包配置同步（依赖 A-01..A-14 全部落地）
└─ 轨道 B：数据层
   ├─ A-15 分组补齐 ──┬─> A-16 schema 生成器 ──┬─> A-19 NO_INTERP 门禁
   │                 │                      └─> A-33 薄封装命令第一批（B1）
   │                 ├─> A-17 分组元数据 ──> A-34 module_catalog（B2）
   │                 └─> A-43 / A-44 / A-45（B3，需先有分组才可达）
   └─ A-18 flow_graph 常量 ──┬─> A-20 analyze 扩 4 code
                            └─> A-22 _execute_step 信号契约（B1，硬前置）

B1 执行层
├─ A-22 ──┬─> A-23 group ──┐
│         ├─> A-24 if ─────┤
│         ├─> A-25 break/continue/return
│         ├─> A-26 loop 三形态
│         └─> A-27 call/subprogram ──> A-28 五层作用域 ──> A-46 真并行（B3）
│                                    A-23..A-27 ──> A-29 try ──> A-30 schema:3
│                                                                  ├─> A-41 录制转树（B2）
│                                                                  └─> A-47 断点续跑（B3）
├─ A-31 input_router（无前置，可全程并行）
├─ A-32 JSONPath（无前置）
└─ A-33 薄封装命令（需 A-16）

B2 编辑器 UI
├─ A-34 ──┬─> A-35 模块库交互
│         ├─> A-36 inspector 右栏 ──> A-40 卡片视图 ──> A-42 脏标记/快捷键
│         └─> A-48 专家层收纳（B3）
├─ A-37 ui_bridge ──┬─> A-38 拾取器族
│                  └─> A-39 进度条/操作窗
└─ A-41 录制转树（需 A-30）

B3 打磨与扩展包
├─ A-51 扩展机制 ──┬─> A-50 邮件
│                 ├─> A-53 rapidocr
│                 ├─> A-55 录屏扩展
│                 ├─> A-56 录音扩展
│                 └─> A-59 扩展包族
└─ A-49 帮助文档同步（收口，需全部条目完成）
```

### 3.2 关键约束（必须先于后）

| 约束 | 内容 | 若不遵守的后果 |
| --- | --- | --- |
| 数据层先于 UI | A-15 / A-17 必须先于 A-34 | 模块库只能读到 45 条硬编码动作，新命令「加了也看不见」；`_WF_LIB_ACTIONS` 会被再复制一份到卡片视图 |
| 信号契约先于 `break`/`continue` | A-22 必须先于 A-23~A-27 | 容器各自发明返回值约定，`_run_group`/`_run_loop`/`_run_if` 三套语义互不兼容 |
| 作用域后于容器 | A-28 必须在 A-23~A-27 之后 | 先改 `Context.get_var` 会与容器帧的字段定义反复推翻 |
| 扩展机制先于扩展包 | A-51 的宿主侧骨架必须先于 A-50/A-53/A-55/A-56/A-59 | 每个扩展各写一套加载/探测，`capabilities` 出现 5 种 `ext_id` 口径 |
| `ui_bridge` 先于 GUI 命令 | A-37 必须先于 A-38 / A-39 | 「三处重复实现会演化出三种死法」（Quicker 篇 §4.8 原话） |

---

## 4. 风险与阻断项（假设 → 验证结果 → 影响）

| # | 原假设 | 验证结果（证据） | 影响与已做处理 |
| --- | --- | --- | --- |
| 1 | `src/workflow_view.py` 存在 | **成立，但路径是 `src/ui/workflow_view.py`**（2650 行）。Quicker 篇全篇写的就是 `src/ui/workflow_view.py`，引用的 `:291-300`（`_WF_LIB_CATEGORIES`）、`:544`（`wf_tree`）、`:567`（`wf_flow_canvas`）、`:596-641`（`_WF_LIB_ACTIONS`）**全部命中** | **无阻断**。Quicker 篇 §6 与 W2 全部有效，无需连锁修正。旧文档称「工作流构建段在 `ACRPA.py`」，实际已抽到 `src/ui/workflow_view.py`（`src/ACRPA.py:3500-3530` 只留转发别名）——两文都按新位置写，正确 |
| 2 | `build_arg_form` 存在 | **成立**：`src/ui/param_form.py:187` `def build_arg_form(parent, cmd_type, initial_args=None, *, colors, fonts, theme=None)` | A-36 无需新写渲染逻辑，只需从模态 `Toplevel`（`src/ui/workflow_view.py:1443` `_wf_edit_step`）搬到常驻 Frame |
| 3 | `_fallback_title` 兜底组存在 | **成立**：`res/help/command_groups.json:3` `"_fallback_title": "其他命令"` | A-15 判据「`_fallback_title` 组为空」成立 |
| 4 | `flow_graph` 有 `K_BRANCHES` | **不成立（确属新增）**：`src/flow_graph.py:49-53` 只有 `K_CHILDREN` / `K_THEN` / `K_ELSE` / `REF_KEY`，`_RESERVED`（`:53`）也只含前三者 | A-18 必须**同时**把 `K_BRANCHES` 加进 `_RESERVED`，否则 `demote` 剥离保留键时会误删；这是 Quicker 篇未点明的实现细节 |
| 5 | `analyze()` 现有 code 与新增 4 个不冲突 | **成立**：`src/flow_graph.py:616` `def analyze(g)`，返回 `[{code, level, nodes, message}]` | A-20 可纯增量添加 4 个 code |
| 6 | `_execute_step` 返回值被别处依赖，改造高风险 | **部分验证**：`_run_parallel`（`src/workflow.py:560`）显式**丢弃**返回值；`_run_loop`/`_run_condition`/`run_workflow` 的调用点本轮未逐行展开 | **风险下调但未清零**。A-22 的第一步动作固定为「grep `_execute_step(` 全部调用点并列表记录」；Quicker 篇 §9.3 建议「先在行式引擎实现等价语义再加测试」的缓解措施保留 |
| 7 | `ai_client.chat()` 存在 | **不成立**：`src/ai_client.py` 无模块级 `chat()`。可用入口为 `AIClient.chat_completions(model, messages, temperature, max_tokens, timeout)`(`:46`)、`create_client_active()`(`:564`)、`resolve_model()`(`:467`)、`has_ai_key()`(`:517`) | **已修正** Quicker 篇 §4.19 的实现路径与伪代码（见 §6）。A-45 的 `AI对话` 按修正后签名实现 |
| 8 | 剩余体积预算 ≈ 0.7 MB | **静态推算，未实测**。见 §5 | A-50（邮件 +250 KB）与 A-33/A-43/A-44/A-45 的源码增量需合并计算；余量只剩 ≈130 KB → **A-50 与 A-59 是唯一可能破门禁的两项**，必须逐项过 `check_exe_volume()` |
| 9 | `state.PADDLE_DLL_ENABLED` / `INPUT_MODE` / `OCR_PRELOAD` 是真实配置键 | **成立**：`src/state.py:74`（`paddle_dll_enabled`）、`:106`（`ocr_preload`）、`:109`（`input_mode`），经 `:198-199` `globals()[_key.upper()] = _default` 导出为运行时全局 | DLL 篇 §5.2/§5.3 的 `state.py:70-80` / `104-109` / `106` / `109` 引用**全部准确**，A-08 的 `state.HOTKEY_BACKEND` 照同一 schema 追加即可 |
| 10 | `_cred_delete` 在 `state.py:656` 附近 | **不成立**：实际在 `src/state.py:514` | **已修正** DLL 篇 §5.5(b)（见 §6）。A-50 的凭据要求改为指向 `:514` + `:616-634` 的 API Key 迁移逻辑 |
| 11 | DLL 篇 P0-9「4 条命令」 | **不成立（文内不一致）**：§5.7 的命令注册块只有 5 条 register（蓝牙设备/校时检查/连通性检测/以管理员运行/气泡通知），**没有「文件版本号」**；§7.1 判据 3 列的 9 条 P0 命令也不含它 | **已修正**两文为「3 条命令 + 文件版本号工具函数」，并明确其随 P1-5 命令组交付。A-14 与 A-52 的边界据此重划 |

---

## 5. 体积基线（静态推算，未实测）

> **为什么未实测**：① 工作区无 `dist/ACRPA.exe`（`build.py:24-29` 的 `exe_candidates()` 只查 `dist/`），因此 `check_exe_volume()` 现在必然返回 `(2, "未找到打包产物…")`；② 本轮任务禁止改动 `build.py` / `ACRPA.spec` / `requirements.txt`，不执行构建。以下为按 `build.py` 既有算法（`os.path.getsize(path)/(1024*1024)`，`build.py:42`）与两文声明的增量做的静态推算。

| 项 | 数值 / 依据 | 性质 |
| --- | --- | --- |
| 体积硬目标 | **< 15 MB**（`build.py:2` 文档串；`build.py:21` `MAX_EXE_MB = 15.0`） | 实测（读代码） |
| 门禁逻辑 | `check_exe_volume()`(`build.py:32-48`) 超限返回 1，`build()` 见 1 即 `return 1`(`build.py:374-382`) | 实测（读代码） |
| 门禁实际比较对象 | `dist/ACRPA.exe` 或 `dist/ACRPA/ACRPA.exe` 的 **exe 文件大小**（不是解压后总体积） | 实测（读代码） |
| 当前基线体积 | 旧文档记载 onefile ≈ **14.3 MiB**（`docs/参考分析-zTasker借鉴与ACRPA优化方案.md:4`、`:57`） | **未实测**（引用旧文档） |
| 剩余预算 | 15.0 − 14.3 = **≈ 0.7 MB** | 静态推算 |
| DLL 篇声明的增量 | ≈ 370 KB（§6.3：纯源码 105 KB + 探测/OCR 14 KB + 邮件 250 KB） | 静态推算（源自方案自述） |
| Quicker 篇声明的增量 | 纯 Python 源码 ≈ 250 KB → 打包后增量 **< 200 KB**（§9.1） | 静态推算（源自方案自述） |
| **两文合并增量** | ≈ **570 KB** | 静态推算 |
| **余量** | ≈ 130 KB | 静态推算 |

**由此得出的硬结论（与两文原文口径的差异，需并入执行约束）**

1. DLL 篇 §6.3 的「落地后 ≈ 14.67 MB，余量 0.33 MB」与 Quicker 篇 §9.1 的「< 200 KB」**各自成立但不可叠加**：叠加后余量只剩 ≈130 KB，而不是 0.33 MB。
2. 因此 **A-50（邮件 +250 KB）必须与 A-59 的扩展包严格隔离**，且落地时按顺序单项验证：每完成一条就 `python build.py` 一次，`check_exe_volume()` 必须仍为 0。
3. 所有新增 pip 依赖（`comtypes` / `rapidocr_onnxruntime` / `mss` / `imageio-ffmpeg` / `sounddevice` / `openpyxl` / `websocket-client` / `qrcode` / `pyzbar`）**一律只走 `extensions_src/`**，主包 import 面零增长。
4. `ACRPA.spec:13` 与 `build.py:175-273` 的 `EXCLUDE_MODULES` 必须逐项对齐（现状已对齐：两边都排除 `cv2` / `smtplib` / `imaplib` / `poplib` / `cryptography` / `cffi` / `bcrypt` / `paramiko`）。A-50 解禁邮件时**两份清单要同时改**。

---

## 6. 校验结论：引用核查与两文冲突

### 6.1 引用核查表（仅列有问题与被修正的条目）

> 抽取并逐条打开核对 **74** 处 `path:line`（DLL 篇 45 处、Quicker 篇 29 处）：**✔ 60 / ⚠️ 6 / ✘ 8**。下表是全部 ⚠️ 与 ✘，以及 5 处非行号类的口径不一致。其余 60 处命中项按文件汇总见 §6.2。

| 文档 | 引用（path:line） | 声称内容 | 实际内容 | 判定 | 修正 |
| --- | --- | --- | --- | --- | --- |
| DLL 篇 | `../src/state.py:656` | 附近有 `_cred_delete` 凭据存储逻辑 | `_cred_delete` 在 `src/state.py:514`；`:616-634` 是 `load_config()` 的 API Key 迁移 | ✘ 错误（偏移 −142） | 改为 `../src/state.py:514` 的 `_cred_delete`，并补 `:616-634` |
| DLL 篇 | `../src/ACRPA.py:3918-3970`（§5.1 正文） | `_hotkey_poll` 范围 | 函数从 `3918` 延伸到 `3977`（`except Exception: pass`） | ⚠️ 尾部偏移 +7 | 改为 `3918-3977` |
| DLL 篇 | `../src/ACRPA.py:3918-3970`（§10 索引） | 同上 | 同上 | ⚠️ 尾部偏移 +7 | 改为 `3918-3977` |
| DLL 篇 | `../src/ui/settings/cards/advanced.py:160-165` | `_refresh_ocr_status` 所在 | `def _refresh_ocr_status()` 在 `:125` | ✘ 错误（偏移 −35） | 改为 `:125` |
| DLL 篇 | `../src/ui/settings/cards/extensions.py:205-214` | 扩展卡片的 capabilities 刷新 | `capabilities.refresh()` 在 `:217`（try 块 `:216-217`） | ⚠️ 偏移 | 改为 `216-217` |
| DLL 篇 | DLL §5.2(e) 结尾「形态见 §6.2」 | 扩展包形态章节 | 扩展包形态在 **§6.5**；§6.2 是体积账 | ⚠️ 文内自引用错 | 改为「形态见 §6.5」 |
| DLL 篇 | DLL §5.6(C/D)「`extensions_src/recorder_video/`」 | 真录屏扩展目录名 | 同节代码块与 §6.5 表都用 `extensions_src/screen_record/` | ✘ 命名不一致 | 统一为 `extensions_src/screen_record/` |
| DLL 篇 | DLL §7.1 P0-9「蓝牙/校时/连通性/文件版本号，4 条命令」 | 4 条新命令 | §5.7 的命令注册块只有 5 条 register，**无「文件版本号」**；§7.1 判据 3 的 9 条 P0 命令也不含它 | ✘ 文内不一致 | 改为「3 条命令 + 文件版本号工具函数（随 P1-5 交付）」 |
| DLL 篇 | DLL §7.1 判据 5「既有 68+ 条命令」 | 回归范围 | 注册表实际 **88 条**（9 组 68 + 20 条 L2） | ⚠️ 口径模糊、会漏回归 20 条 | 改为「88 条（9 组 68 + 20 条 L2）」 |
| DLL 篇 | DLL §10「命令清单（68+ 条）」 | 引用 `commands.py:301-386`、`803-825` | 20 条 L2 注册在 `:786-830`；`NO_INTERP` 在 `:900-972` | ⚠️ 行号不准 | 改为 `301-386`、`786-830`、`900-972` |
| Quicker 篇 | `src/commands.py:393` 附近 `[:9]` | 证明参数槽位固定 9 个 | `commands.py` 全库**无** `[:9]`；运行期截断在 `src/workflow.py:394` 与 `src/scheduler.py:641` | ✘ 符号/内容不存在 | 改为两处真实 `[:9]` + `param_form._norm9` |
| Quicker 篇 | `ai_client.chat(prompt=…, system=…)`（§4.19 伪代码） | 复用 `src/ai_client.py` 的对话入口 | **无此函数**。真实入口：`AIClient.chat_completions()`(`:46`)、`create_client_active()`(`:564`)、`resolve_model()`(`:467`)、`has_ai_key()`(`:517`) | ✘ 符号不存在 + 签名错误 | 重写 §4.19 实现路径与伪代码 |
| Quicker 篇 | `src/ui/workflow_view.py:21-22`（§6.5.2） | `_FLOW_COLORS` / `_FLOW_ICONS` | `:21-22` 是模块 docstring 里的**清单索引**；定义在 `:218`（`_FLOW_COLORS`）与 `:228`（`_FLOW_ICONS`） | ⚠️ 指向索引而非定义 | 改为 `:218` / `:228`，并注明 `:21-22` 是索引 |
| Quicker 篇 | K15「`src/audio_ops.py` 已含音量能力」 | 音量能力归属 | 音量是 `src/sys_ops.py`（`IAudioEndpointVolume` 手写 COM，`:7-8`；`:636` `_keyboard_volume` 回退）；`audio_ops.py` 在 DLL 篇是新建的 WAV 播放模块 | ✘ 事实错误 | 改为「音量已在 `src/sys_ops.py`，设备枚举建议追加到同模块」 |
| Quicker 篇 | Q §8.2 序 9「`sys_ops` 追加 4 条（蓝牙/校时/连通性/文件版本号）」 | 4 条命令 | 同 DLL 篇 P0-9，实际 3 条命令 | ✘ 继承上游不一致 | 改为「3 条命令 + 文件版本号工具函数」 |

### 6.2 命中项汇总（✔ 60 处的分布，供复查）

| 文件 | 命中数 | 代表性引用（全部逐条打开） |
| --- | --- | --- |
| `build.py` | 18 | `:2` `:21` `:32-48` `:50-55` `:116` `:135` `:136-138` `:150-153` `:155-170` `:206-207` `:219` `:232-234` `:264` `:265` `:334-341` `:346-347` `:374-382` |
| `src/engine.py` | 14 | `:126` `:148` `:220` `:245` `:260` `:308-313` `:498` `:569` `:753` `:800` `:822` `:826` `:882` `:971` `:1036` `:1151` `:1378` `:1583` `:1676` `:1906` `:2501` |
| `src/workflow.py` | 11 | `:63` `:115` `:227-233` `:256` `:266` `:312` `:334-373`（`_execute_step`）`:375`（`_run_command`）`:442` `:488` `:534-591`（`:556` lock、`:569` max_workers、`:582-584` 合并）`:593` `:615` `:640` |
| `src/flow_graph.py` | 12 | `:46` `GRAPH_SCHEMA=2` / `:49-53` 保留键 / `:55` `CONTAINER_TYPES` / `:57` `_COND_BRANCHES` / `:70` `:129` `:161` `:309` `:346` `:616` `:712` |
| `src/commands.py` | 12 | `:60` `:106` `:110` `:175` `:180` `:227` `:288` `:453` `:537` `:786-830` `:838-865` `:900-972` `:978` |
| `src/ui/workflow_view.py` | 16 | `:218` `:228` `:291-300` `:311` `:400-401` `:498` `:510` `:517` `:534-559` `:562-593` `:596-641` `:752` `:774` `:785` `:821` `:852` `:891` `:1194` `:1386` `:1443` `:1906` `:1976` `:2073` `:2100` |
| `src/ui/param_form.py` | 7 | `:62` `:79` `:100` `:127` `:150` `:162` `:187` |
| `src/ACRPA.py` | 11 | `:38-40` `:2036` `:3549`（`_periodic`）`:3745` `:3748` `:3752` `:3756` `:3786` `:3827-3874`（`:3864-3871` 冲突检测）`:3918-3977` `:3956` |
| `src/capabilities.py` | 6 | `:187-222` `:210-212` `:236-252`（`:245-248`）`:255-270` `:341-381` `:349-352` |
| `src/triggers.py` | 4 | `:153-192`（注册表函数）`:195` `Trigger` `:246-542`（8 种）`:492-505` `:517-542` |
| `src/scheduler.py` | 6 | `:8-10` `:61-66` `:289-303` `:336-351` `:786-792` `:893` |
| `src/ocr_backend.py` | 10 | `:148-151` `:155` `:186` `:210` `:228` `:249` `:267` `:315-340` `:329-337` `:347` |
| `src/paddle_dll.py` | 5 | `:30-31` `:54-90` `:57` `:60` `:63` |
| `src/dd_backend.py` | 5 | `:40` `:58-113` `:71-73` `:81` `:115-120` |
| `src/state.py` | 4 | `:70-80` `:104-109` `:106` `:109` |
| `src/file_ops.py` / `sys_ops.py` / `utils.py` / `tray.py` / `net_ops.py` / `interp.py` / `recorder.py` | 19 | `file_ops.py:445-461`；`sys_ops.py:7-8` `:636`；`utils.py:71` `:433`；`tray.py:144-160` `:291-307` `:445-458`；`net_ops.py:1-27` `:10` `:30`；`interp.py:54-67` `:144` `:171` `:192` `:442`；`recorder.py:39` `:57` `:77` `:95` `:105` `:132` `:246` `:321` `:349` `:359` `:370` `:388` `:463` `:508` `:527` |
| `ACRPA.spec` | 3 | `:8`（`datas` 含 `dd63330.dll`）`:9`（hiddenimports 含 `ui.workflow_view`）`:13`（excludes 含 `cv2`/`smtplib`/`cryptography`） |
| `res/help/*` | 6 | `command_groups.json:3`（`_fallback_title`）`:4-48`（9 组）`command_schema.json:5`（`commands: 68`）`sections.json:3`（`dynamic:"commands"`）`10-快捷键.md:5/17/18/22/29-31/32` |
| 旧文档 | 2 | `参考分析-zTasker借鉴与ACRPA优化方案.md:4`、`:57`（≈14.3 MiB 基线） |

### 6.3 两文冲突与统一命名

| # | 同一件事的两种叫法 / 两文都做 | 统一处理 |
| --- | --- | --- |
| 1 | **找色**：DLL 篇 P2-5「找色命令（纯 Pillow）」↔ Quicker 篇 §4.16「`找色`（`color_find`）」 | 统一命令名 **`找色`**；条目合并为 **A-54**（算法与 ROI 归 A-54，命令形态按 Quicker 篇 §4.16 参数表） |
| 2 | **找色落点冲突**：Quicker 篇 §4.11 建议并入 `screen_ops.py`，§4.16 却新建 `src/find_color.py` | 统一落点 **`src/screen_ops.py`**（A-13 建立、A-54 扩展）；**废弃 `src/find_color.py`** |
| 3 | **播放声音**：DLL 篇 P0-8 `audio_ops.py` ↔ Quicker 篇 K16「见 P0-8」 | 合并为 **A-12**（唯一落点 `src/audio_ops.py`） |
| 4 | **连续截图 / 屏幕截图**：DLL 篇 `screen_ops.grab_sequence` ↔ Quicker 篇 N5「`截屏` 已有，连续截图见 P0-8」 | 合并为 **A-13**；`截屏`（已有，`src/commands.py:320`）不重做 |
| 5 | **气泡通知 / 提示消息**：DLL 篇 §5.7「气泡通知」+ P1-5 ↔ Quicker 篇 G4「提示消息」 | 统一命令名 **`气泡通知`** → **A-52** |
| 6 | **能力体检 vs 检查依赖**：DLL 篇 P0-10（系统自动探测）↔ Quicker 篇 §4.6 `检查依赖`（脚本显式声明） | **保留两条，明确分层**：`能力体检`（UI，A-06/A-07）与 `检查依赖`（命令，A-43） |
| 7 | **JSONPath**：DLL 篇 P1-3 ↔ Quicker 篇 §4.21（增强「JSON 取值」不新增命令） | 合并为 **A-32**（实现归 `net_ops.py`，参数契约按 Quicker 篇 §4.21） |
| 8 | **邮件**：DLL 篇 P1-2 `mail_ops.py` ↔ Quicker 篇 J11 | 合并为 **A-50** |
| 9 | **UIA**：DLL 篇 P1-4 扩展包 ↔ Quicker 篇 F8 / K3 | 合并为 **A-51**（Quicker 篇不另设方案） |
| 10 | **音量能力归属**：Quicker 篇 K15 原写「`audio_ops.py` 已含音量」 | 已改为 `src/sys_ops.py`（实测 `:7-8` / `:636`）；A-12 只负责播放 |
| 11 | **命令数量口径**：DLL 篇「68+ 条」↔ Quicker 篇「88 条」 | 统一 **88 条 = 9 组 68 条 + 20 条 L2**（已改 DLL 篇 2 处） |
| 12 | **扩展包目录名**：DLL 篇文内 `recorder_video` ↔ `screen_record` | 统一 **`extensions_src/screen_record/`**（已改） |
| 13 | **P0-9 命令条数**：DLL 篇「4 条」↔ §5.7 实际 3 条 register | 统一 **3 条命令 + 文件版本号工具函数**（两文已改） |
| 14 | **`OCR 自检` 与 `DLL 自检`** | 统一为设置界面里的同一个按钮 **「DLL 自检」**（A-05） |

### 6.4 可行性抽查（10 项，逐条给证据）

| # | 抽查项 | 结论 | 证据（path:line） |
| --- | --- | --- | --- |
| 1 | `src/ui/workflow_view.py` 是否存在 | **存在**（2650 行） | `src/ui/workflow_view.py:291`（`_WF_LIB_CATEGORIES`，8 分类 45 项到 `:300`）、`:544`（`wf_tree`）、`:567`（`wf_flow_canvas`）、`:596-641`（`_WF_LIB_ACTIONS`，44 个键） |
| 2 | `build_arg_form` 是否存在 | **存在** | `src/ui/param_form.py:187` |
| 3 | `_fallback_title` 与「其他命令」兜底组 | **存在** | `res/help/command_groups.json:3` |
| 4 | `_execute_step` 返回值契约 | 现状无显式返回值（隐式 `None`）；`_run_parallel` 显式丢弃 | `src/workflow.py:334`（定义）、`:560`（调用处不接收） |
| 5 | `flow_graph` 节点常量 / `analyze()` | `K_CHILDREN/K_THEN/K_ELSE/REF_KEY` 存在、`K_BRANCHES` **不存在**；`CONTAINER_TYPES=("parallel","loop")` | `src/flow_graph.py:49-53`、`:55`、`:57`、`:616`、`:712` |
| 6 | `capabilities` 两个 probe 只查文件存在 | **成立** | `src/capabilities.py:236-252`（`_probe_paddle_dll` 只 `os.path.exists`）、`:255-270`（`_probe_input_dd` 同） |
| 7 | `scheduler` 错过补偿 / 生效区间 | **成立** | `src/scheduler.py:893`（`_run_missed_compensation`）、`:336-351`（`_task_window_state` 含 start/end_date + max_runs）、`:786-792`（次数上限自动停用） |
| 8 | `triggers` 8 种触发器 + `HotkeyTrigger` | **成立** | `src/triggers.py:246`（once）`:279`（daily）`:301`（weekly）`:334`（interval）`:351`（countdown）`:402`（hourly）`:444`（on_boot）`:518`（hotkey）；`:492-505` 事件队列 |
| 9 | `ACRPA` 的 `_TASK_HOTKEYS` / `_rebuild_task_hotkeys` | **成立，且带双类冲突检测** | `src/ACRPA.py:3748`、`:3827-3874`（冲突检测 `:3864-3871`）、`:3918-3977`（200ms 轮询） |
| 10 | 命令数实测 | `command_groups.json` **9 组 68 条**；`command_schema.json` `stats.commands=68`；`src/commands.py` 顶层 `register(` **88** 处 | `res/help/command_groups.json:4-48`、`res/help/command_schema.json:5`、`src/commands.py:302-385`（68）+ `:786-830`（20） |

---

## 7. 一页速览：如果只能做 10 件事

> 排序依据 = 投入产出比（用户可感知程度 ÷ 依赖深度）。前 5 件全是 S/M 级且互不阻塞，可立即开工。

| 序 | 条目 | 一句话 | 判据 |
| --- | --- | --- | --- |
| 1 | **A-02** 四个 probe 改写 | 把「界面显示可用、一点就报错」的假 READY 全部消掉 —— 这是当前最伤信任的缺陷 | 只放裸 `PaddleOCR.dll` 时 `probe("ocr.paddle_dll")=BROKEN` 且 reason 列出 4 个缺文件名 |
| 2 | **A-04** `paddle_dll.self_check()` | 让「缺什么、怎么补」变成一句可读中文，而不是 `FileNotFoundError` | 自检返回 `(state, reason, missing[])` 且全程不 `WinDLL` |
| 3 | **A-15** `command_groups.json` 补齐 20 条 | 命令速查/模块库立刻不再丢 20 条命令，且是后续所有 UI 改造的数据前提 | 组内总数 == `list_names()` == **88**，`_fallback_title` 组为空 |
| 4 | **A-01** `native_check.py` | 一个 120 行的纯 stdlib 模块，同时喂给 A-02 / A-14 / 未来的扩展安装校验 | x86 DLL → `is_x64` 返回 False；非 PE → `pe_machine` 返回 None |
| 5 | **A-06** 能力体检卡片 | 一屏看清每项能力的四态与原因，把「隐性坏」变「显性可见」 | 卡片内容与 `probe_all()` 逐条一致 |
| 6 | **A-10** `archive_ops.py` 3 条命令 | 零依赖、最高频缺失能力（压缩/解压/列表），且自带 Zip-Slip 与炸弹防护 | 压缩→解压后逐文件哈希一致；`../../evil.txt` 被拒 |
| 7 | **A-13** `screen_ops.py`（连续截图 + 取色 + 颜色转换） | 巡检/取证刚需，零依赖，且是 A-54「找色」的地基 | 5 帧间隔 1.0±0.15s；取色 1000 次无 GDI 泄漏 |
| 8 | **A-12** `audio_ops.py`（播放声音） | 成本最低、用户当场可感知的一条新命令（80 行 + 4 KB） | 播 2s WAV 成功；传 `.mp3` 返回可读失败不抛异常 |
| 9 | **A-08** `hotkey_win.py` `RegisterHotKey` | 从「200ms 轮询、吞不掉按键」升级为「系统消息投递、O(1) 唤醒」，并保留 `poll` 回退 | 绑 `Ctrl+Shift+F9` 后前台记事本收不到该键；`HOTKEY_BACKEND="poll"` 时行为等价 |
| 10 | **A-18 + A-22** `flow_graph` 常量 + `_execute_step` 信号契约 | B1/B2 全部编排能力的唯一地基；先做这两条，后面 12 条执行层条目才有共同语言 | 旧样例 `demote(promote(x))==x` 全绿；`sig ∈ (None,"break","continue","return")` 断言生效 |

**明确不要先做的三件事**（会返工）

1. 不要先写 `step_card_view.py`（A-40）——它要渲染的 `group`/`if`/`try` 此时还不存在。
2. 不要先改 `src/ui/workflow_view.py:596-641` 的 `_WF_LIB_ACTIONS` ——数据源（`command_groups.json`）没补齐前改了也读不到新命令。
3. 不要先在 `src/interp.py` 加作用域链 ——容器帧的字段定义还没定，改了必返工。

---

## 8. 文件变更清单（本批次校验任务）

| 文件 | 变更性质 | 说明 |
| --- | --- | --- |
| `docs/参考分析-zTasker-DLL复用判定与能力补强方案.md` | 定点修改（9 处） | ① `state.py:656`→`:514`（`_cred_delete`）；②③ `ACRPA.py:3918-3970`→`3918-3977`（§5.1 与 §10 两处）；④ `advanced.py:160-165`→`:125`（`_refresh_ocr_status`）；⑤ `extensions.py:205-214`→`216-217`；⑥ §5.2(e)「形态见 §6.2」→「§6.5」；⑦ `extensions_src/recorder_video/`→`screen_record/`；⑧ P0-9 由「4 条命令」改为「3 条命令 + 文件版本号工具函数」；⑨「68+ 条命令」→「88 条」（§7.1 判据 5）与 §10 命令清单行号改为 `301-386`/`786-830`/`900-972` |
| `docs/方案-Quicker截图驱动的功能与工作流拓展.md` | 定点修改（6 处） | ① §3.1.3 不存在的 `src/commands.py:393` `[:9]` → 真实的 `src/workflow.py:394` / `src/scheduler.py:641`；② §4.19 实现路径补 `ai_client` 真实入口清单；③ §4.19 伪代码由不存在的 `ai_client.chat()` 改写为 `create_client_active()` + `chat_completions()`；④ §6.5.2 类型色引用由 `:21-22`（索引）改为 `:218`/`:228`（定义）；⑤ §3.2.6 K15 音量能力归属由 `audio_ops.py` 改为 `sys_ops.py`；⑥ §8.2 序 9 由「4 条」改为「3 条命令 + 文件版本号工具函数」 |
| `docs/落地总清单-ACRPA能力补强与工作流拓展.md` | **新建** | 本文件 |

> 未改动任何源码（`src/**`）、`res/help/*.json`、`ACRPA.spec`、`build.py`、`requirements.txt`、`docs/截图分析-OCR原始文本-ac.md`。
