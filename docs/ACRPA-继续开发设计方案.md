# ACRPA 继续开发设计方案

> 版本基线：v0.1.25（VERSION 首行）
> 审查对象：`D:\CodingEmber\ACRPA`
> 审查方式：源码通读 + 核心回归脚本实跑 + 构建链核查（只读，未修改任何项目文件）
> 结论日期：2026-09-13

---

## 0. 结论摘要

**ACRPA 的问题不是"功能不够"，而是"结果不可信、产物不可复现"。**

现状核查数据：

| 项目 | 实测值 |
| --- | --- |
| `src/` Python 文件 / 行数 | 25 个 / 15,760 行 |
| 最大模块 | `ACRPA.py` 4,694 行、`engine.py` 1,672 行、`settings_window.py` 1,453 行 |
| 已注册命令 | 50 条（`commands.py`） |
| 配置项 | 45 项（`state._config_schema`） |
| 回归脚本实跑 | `_smoke_engine.py` → 0；`_debug_workflow.py` → 0（19 项通过）；`_debug_functional.py` → 0 |
| 源码编译 | `compileall src` 通过 |
| 自动化测试框架 / CI | 无 |
| 打包产物 | `dist/ACRPA.exe`（13.6 MB）+ `dist/ACRPA.zip` |

三处结构性债决定继续开发的优先级：

1. **执行语义不闭合**——命令失败不产生失败信号，重试、`stop_on_error`、AI 异常检测、调度日志全部建立在"成功"这个假前提上。
2. **状态所有权不清**——45 项配置 + 执行运行时状态集中在模块级全局，被 GUI / 执行 / 工作流并行 / 调度四类线程共同读写；`parallel` 节点为规避数据竞争被迫串行化，"并行"名不符实。
3. **发布链不可复现**——`ACRPA.spec` 本身从未被跟踪（`.gitignore` 含 `*.spec`），其引用的 `res/` 目录又已被删除并加入忽略；README 链接的 `docs/`、`templates/` 全部不存在；自动更新地址指向同样被忽略的 `dist/`。

**建议：先做"可失败、可复现、可观测"三件事，再谈扩展。** 阶段 0 + 阶段 1 合计约 2–3 周即可把项目从"能跑"推到"可信"。

---

## 1. 现状盘点

### 1.1 技术栈与运行模型

- 技术栈：Python 3.7+ / tkinter / pyautogui（Pillow 后端，无 OpenCV 时自动降级）/ pywin32 / xlrd+xlwt / requests。
- 入口：`run.py` → 依赖检查 → `ACRPA.root.mainloop()`（`run.py:44-68`）。
- 执行模型：GUI 主线程 + 1 个 daemon 执行线程（`ACRPA.py:738`）+ 1 个调度线程（`scheduler.py:120` / `456`）+ 工作流并行时的 `ThreadPoolExecutor`（`workflow.py:562`）。
- 数据格式：`.xls`（Excel 97-2003），第 1 行为标题、第 3 行起为命令；A 列命令名、B~J 列参数（`scriptdata.py:7`、`ACRPA.py:768`）。
- 打包：PyInstaller 单文件、`console=False`（`ACRPA.spec:32`），排除清单手工维护约 150 个模块（`ACRPA.spec:13`）。
- 版本治理：`VERSION` 为唯一事实来源 → `version_info.py` → 各模块，`tools/bump_version.py --verify` 校验残留。机制本身是好的。

### 1.2 已具备能力（继续开发时不应重做）

| 能力域 | 实现位置 | 说明 |
| --- | --- | --- |
| 命令体系 | `commands.py`（50 条）+ `engine.py` handler | 注册表驱动，插件可扩展 |
| 图像定位 | `engine._find_cached` / `_image_search_loop` | TTL 5s + LRU 50 条缓存；无 OpenCV 时降级为 Pillow 精确匹配 |
| 窗口管理 | `engine.py:1039-1290` | pywin32 枚举 + 模糊匹配 + 窗口相对坐标 |
| 变量与表达式 | `safe_eval.py` | AST 白名单求值，无裸 `eval`，这是项目里质量最高的一块 |
| 工作流编排 | `workflow.py` | 8 种节点（script/parallel/condition/loop/command/variable/log/wait）、外层循环、最长执行时间 |
| 调试器 | `engine.py:259-363` + `ACRPA.py:1383-1500` | 断点/条件断点/单步/变量监视/调用栈/行耗时 |
| OCR | `ocr_backend.py` | winrt / PaddleOCR / tesseract 多后端 |
| 浏览器 | `browser_backend.py` | Playwright 可选后端 |
| AI 增强 | `ai_enhance.py`、`ai_client.py` | 视觉定位、智能重试、异常检测、自然语言调试 |
| 录制 | `recorder.py` + `ACRPA.py:352-530` | 含拖拽/滚轮/窗口激活录制 |
| 调度 | `scheduler.py` | 单任务 + 多任务列表 + 时间段过滤 + 任务日志 |
| 系统集成 | `tray.py`、`ACRPA.py:206-670` | 托盘、Mini Bar、主题、快捷键 |
| 安全存储 | `state.py:164-243` | API Key 走 Windows 凭据库，含明文自动迁移 |
| 分发 | `updater.py`、`marketplace.py` | 版本检查 + 脚本市场 |

### 1.3 已确认的运行时缺陷（逐条附证据）

| 编号 | 位置 | 现象 | 后果 |
| --- | --- | --- | --- |
| D1 | `engine.py:645` | `execute()` 内部算了 `success` 却 `return True` | 调用方永远拿不到失败 |
| D2 | `engine.py:751-755` | 找图超时只 `log1(..., "warning")` 并 `return None`，不抛异常 | 重试分支（只在 `except` 中触发）永不命中 |
| D3 | `engine.py:346` | `_exec_timings` 硬编码 `"success": True` | 调试器耗时面板的成功率失真 |
| D4 | `workflow.py:499-525` | `_run_script` 捕获异常后仅打日志并 `return` | `run_workflow` 把该步骤标为 `"ok"`（`workflow.py:306-308`），错误被吞 |
| D5 | `workflow.py:549-555` | `_run_parallel` 用 `with lock:` 包住整个 `_execute_step` | `parallel` 节点实际串行执行 |
| D6 | `scheduler.py:359-370` | 调度线程直接写 `state.filename` / `has_script` / `script_dir` | 与手动运行竞争，可能执行到错误脚本 |
| D7 | `plugins/__init__.py:37-49` | `importlib.import_module` 任意 `.py`，模块顶层以完整内建权限执行 | 插件即任意代码执行 |
| D8 | `marketplace.py:253-260` | 下载后原样 `f.write(resp.content)`，全仓无 `hashlib`/`hmac`/签名校验 | 供应链投毒无法发现 |
| D9 | `engine.py:998-1030` | `代码` 命令 `exec` 仅限制 `__builtins__` | 属性链逃逸（如 `().__class__.__bases__`）未被阻断，非真实沙箱 |
| D10 | `offline` | 见 §1.4 | 构建与文档全面失效 |

### 1.4 发布链核查结果（关键）

```
res/                        → 不存在；.gitignore 已新增 res/，且 git ls-files res 为空
res/automation.ico          → 已于提交 562c089 删除并提交，完全脱离版本控制
res/wechat_qrcode.png       → 同上
ACRPA.spec:8  datas         → ('D:/CodingEmber/ACRPA/res', 'res')  ← 构建输入不存在
ACRPA.spec:38 icon          → ['D:/CodingEmber/ACRPA/res/automation.ico'] ← 不存在
ACRPA.spec 自身              → git ls-files ACRPA.spec 为空 ← 从未被跟踪（.gitignore 含 *.spec）
                             即新克隆的仓库里没有 spec 文件     ← 单靠 res/ 补齐也无法构建
VERSION 第 2 行              → https://gitee.com/yohoten/acrpa/raw/master/dist/ACRPA.zip
                             而 .gitignore:12 忽略 dist/        ← 更新通道与忽略规则矛盾
.gitignore:52               → 新增忽略项写作 dosc/（docs 拼写错误，意图落空、当前无实际影响）
docs/                       → 本次已新建并提交方案文档；README 仍链接 docs/窗口管理功能指南.md、docs/DD_DRIVER_GUIDE.md（均不存在）
templates/                  → 不存在（实际目录为 template/，仅 5 个 .xls + 1 个 .json，README 称"11 个模板"）
tools/_test_ui.py           → 0 字节
tools/ 中的一次性脚本        → batch_decompile*.py、extract_*.py、step1_extract*.py、rebuild_all.py 等约 15 个
```

**直接后果**：干净克隆后 `python build.py` 必然失败——**且是双重失败**：既没有 `ACRPA.spec`（未跟踪），也没有 `res/` 资源（已删除 + 已忽略）。README 中 4 个链接 404；自动更新指向可能 404 的地址。这不是文档问题，是发布可用性问题。

---

## 2. 目标架构

设计原则（按约束强度排序）：

1. **不破坏用户契约**：`.xls` 中的中文命令名与 9 个参数位是公开接口，重构必须逐字保持。
2. **失败优先**：默认让错误显式化，而不是默认继续。
3. **单一所有权**：任何可变运行时状态只有一个 writer。
4. **无 UI 依赖的内核**：`core` 必须能在无 tkinter 环境下被测试。

目标分层：

- `core/`：`Task` / `Node`（IR）、`Executor`、`Result` / `ExecError`、`RetryPolicy`、`TaskContext`（变量与运行时状态）。零 tkinter、零 pyautogui import。
- `backends/`：`InputBackend`（pyautogui / dd / sendinput）、`VisionBackend`（Pillow / OpenCV / AI）、`OcrBackend`、`BrowserBackend`、`WindowBackend`。统一接口 + 可替换实现。
- `services/`：`Scheduler`（唯一，任务队列驱动）、`PluginHost`（清单 + 权限 + 校验）、`Marketplace`、`Updater`。
- `ui/`：tkinter，只订阅 `TaskContext` 快照 + 通过队列投递指令，不再直接写 `state.*`。

三条关键机制变更：

1. **统一执行结果**：handler 契约由"无返回值"改为返回 `Result{ok, code, message, attempts, artifacts}`；`Executor` 依据 `Result.ok` 决定重试与停止，而非依赖异常。
2. **单执行器 + 任务队列**：任何时刻只有一个 Task 在跑。调度器把任务投递到队列，不再抢线程、不再改全局文件名（消除 D6）。
3. **并行语义落地或改名**：`parallel` 节点给每个分支独立 `TaskContext`，真正并发；若短期无法完成，则在 UI 中显式标注为"顺序组"，不允许存在名不符实的功能（消除 D5）。

---

## 3. 分阶段路线

### 阶段 0：止血与可复现（约 0.5–1 周）

目标：**仓库从零可构建；失败可见且可停止。**

| 项 | 内容 | 触及文件 |
| --- | --- | --- |
| 0.1 | `execute()` 返回 `Result`；`_image_search_loop` 超时抛 `ImageNotFound`；`_exec_timings` 记录真实成功标志 | `engine.py:346`、`573-645`、`751-755` |
| 0.2 | `_run_script` 失败向上抛，`run_workflow` 标记 `error` 并遵守 `stop_on_error` | `workflow.py:499-525`、`306-313` |
| 0.3 | 把 `ACRPA.spec` 纳入版本控制（`.gitignore` 的 `*.spec` 改为白名单例外）；spec 路径改为相对项目根；`res/` 最小资源纳入版本控制或改由 `build.py` 生成 | `ACRPA.spec:8,38`、`.gitignore:47`、`build.py` |
| 0.4 | 解除 `dist/` 与更新通道的矛盾（二选一：把发布产物托管到独立 release 仓库，或改 `updater.UPDATE_URL`） | `VERSION`、`updater.py:9`、`.gitignore:12` |
| 0.5 | 文档对齐：README 链接指向真实路径；`config.json` 示例与 `state._config_schema` 默认值一致；`template/` → `templates/` 或反向改名 | `README.md`、`使用说明.txt` |

**阶段 0 验收（硬标准）**

1. 全新克隆 → `pip install -r requirements.txt` → `python run.py` 可启动。
2. `python build.py` 能产出 EXE，且 `--verify` 无旧版本号残留。
3. 故意构造一条找不到图的 `点图`：日志出现 `error`，且 `stop_on_error=true` 时脚本立即停止。
4. 故意让工作流中某脚本抛错：该步骤在可视化中标红，日志含 `error`。
5. README 中所有相对链接可解析到真实文件。

### 阶段 1：质量门禁（约 1–2 周）

目标：**改动有回归保护。**

1. 迁移到 pytest：`tests/test_engine_flow.py`、`test_workflow.py`、`test_safe_eval.py`、`test_config.py`、`test_commands_contract.py`。
2. 引入伪后端 `FakeInput` / `FakeVision`，使命令分支可在无桌面环境下测试——这是当前 `_debug_*` 脚本无法覆盖命令实现层的根因。
3. **契约测试**：断言 50 条命令名与参数位不发生变化（保护 §2 原则 1）。
4. CI：Windows runner，Python 3.9 + 3.12 双版本；覆盖率门槛先设 40%、目标 60%。
5. `tools/` 整理：逆向遗留脚本移入 `tools/legacy/`（或删除），删除 0 字节文件。

**验收**：CI 绿；`engine`/`workflow`/`safe_eval`/`state` 覆盖率 ≥60%；命令契约测试覆盖 50/50；至少 25 条命令级用例。

### 阶段 2：执行内核重构（约 2–3 周）

目标：**状态有主，并行真实。**

1. 引入 `core/`，把 `engine.py`（1,672 行）拆为 `core/executor.py` + `core/commands/*.py` + `backends/*.py`。
2. `state.py` 收敛为兼容 shim，真实状态迁入 `TaskContext`；UI 改为快照订阅。
3. 删除 `scheduler_loop`，只保留队列驱动的单一调度器；取消/暂停/停止改为对 Task 的事件。
4. `parallel` 节点实现真并行（每分支独立 Context），或按 §2 机制 3 改名。

**验收**：同时手动运行 + 调度触发不会执行到错误脚本；`parallel` 节点实测墙钟时间较串行下降 ≥40%；全仓 grep 无模块级全局被执行路径写入。

### 阶段 3：扩展与产品化

1. **插件沙箱**：清单文件（`name`/`version`/`permissions`/`sha256`）+ 白名单目录 + 受限执行（移除 `__builtins__` + `sys.addaudithook` 拦截 `open`/`subprocess`/`socket`）——消除 D7、D9。
2. **市场完整性**：索引含 `sha256`，下载后校验，支持撤回——消除 D8。
3. **观测**：结构化事件流（JSONL）+ 单次运行报告（成功/失败/耗时/失败截图），供调度日志与 AI 异常检测消费。
4. **体验**：崩溃一键打包诊断、错误码字典、i18n 骨架。

---

## 4. 验收指标总表

| 维度 | 现状 | 阶段 0 | 阶段 1 | 阶段 2 |
| --- | --- | --- | --- | --- |
| 命令失败可感知 | 0（仅日志 warning） | 全部显式 | 契约测试保护 | 统一 `Result` |
| `stop_on_error` 有效性 | 对找图类失败无效 | 有效 | 有效 | 有效 |
| 工作流错误传播 | 吞掉（标 ok） | 标 error | 用例覆盖 | 事件流上报 |
| 干净克隆可构建 | 否 | 是 | 是 | 是 |
| 自动化测试 | 4 个脚本 | 4 个脚本 | ≥25 用例 + CI | 覆盖率 ≥60% |
| 调度与手动运行互斥 | 竞争（D6） | 未解决 | 用例覆盖 | 队列互斥 |
| `parallel` 真实并行 | 否（串行） | 否 | 否 | 是或改名 |
| 插件/市场信任边界 | 无 | 无 | 无 | 清单 + 签名 |

---

## 5. 风险与取舍

1. **功能冻结窗口**：阶段 2 期间建议只接受 P0 修复，否则重构与新增互相干扰。需用户明确同意。
2. **Python 基线**：代码中多处注释强调 Python 3.7 兼容（`engine.py:280`，`safe_eval.py:55-64`）。若目标用户仍含 Win7 + 3.7，则不能使用 `dataclass` 之外的现代语法糖，且 CI 需补 3.7；建议先确认基线，再决定是否统一到 3.9+。
3. **命令名即公开契约**：任何重构必须保持 50 条命令名与参数位不变，`.xls` 存量脚本不能要求用户重写。此项应写入阶段 1 的契约测试。
4. **体积敏感**：项目明确为"小体积"做了大量取舍（Pillow 替代 OpenCV、spec 排除 numpy/cv2、`ACRPA.py:10-12`）。引入 OpenCV/PaddleOCR 会显著增大 EXE，阶段 0/1 不应触碰。
5. **手工 excludes 不可持续**：`ACRPA.spec:13` 的排除清单手工维护，且排除了 `xml`、`xmlrpc`、`bz2`、`lzma`、`multiprocessing`、`concurrent.futures.process`——其中 `xml` 与多进程相关模块被第三方库隐式依赖时会产生只在打包后才出现的 `ImportError`。建议改为"显式 include + `collect_submodules`"，让缺失在构建期暴露而非运行期。

---

## 6. 建议的第一批改动（按执行顺序）

1. `engine.execute()` → 返回 `Result`（重写 `engine.py:573-645`）。
2. `_image_search_loop` 超时抛 `ImageNotFound`（`engine.py:751-755`）。
3. `workflow._run_script` 错误向上传播（`workflow.py:499-525`）。
4. `ACRPA.spec` 纳入版本控制 + 路径相对化 + `res/` 资源补齐（`ACRPA.spec:8,38`、`.gitignore`）。
   注意：`res/automation.ico` 已于 `562c089` 删除，需从 `dist/` 或历史版本取回并重新纳管。
5. README 链接与 `config.json` 示例对齐（`README.md`）。
6. `tools/` 整理 + 0 字节文件清理。

以上 6 项完成后即可解锁阶段 1 的测试迁移；否则测试会不断被"失败不可见"掩盖。

---

## 附录 A：本次核查使用的命令与结果

```
# 回归脚本（均为退出码 0）
.venv/Scripts/python.exe tools/_smoke_engine.py       → 全部通过 ✓
.venv/Scripts/python.exe tools/_debug_workflow.py     → 通过 19 项，失败 0 项
.venv/Scripts/python.exe tools/_debug_functional.py   → 全部通过 ✓

# 编译检查
python -m compileall -q src                           → PASS

# 规模统计
src:   25 文件 / 15,760 行（ACRPA.py 4,694 / engine.py 1,672 / settings_window.py 1,453 / dialogs.py 1,104）
tools: 25 文件 / 3,650 行
commands.list_all() = 50 条；state._config_schema = 45 项

# 构建链
ls res        → No such file or directory
ls docs       → No such file or directory
ls templates  → No such file or directory
git status    → D res/automation.ico, D res/wechat_qrcode.png, D README.en.md
```

## 附录 B：未做的事（避免误读）

- 未修改 `D:\CodingEmber\ACRPA` 下任何现有文件，未执行构建，未运行 GUI。
- 未验证运行时行为（`dist/ACRPA.exe` 未启动），所有结论均来自源码与静态/脚本级验证。
- 未评估 AI 增强、浏览器、OCR 三个可选后端的外部服务质量，仅评估其在本仓库的接入方式。
- 未做性能基准测试（`tools/benchmark.py` 未纳入本次核查）。
