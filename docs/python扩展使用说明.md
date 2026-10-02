# ACRPA Python 扩展使用说明

> 适用版本：ACRPA 第 5 项优化（自定义 Python 代码脚本扩展 · 保守版）＋ **P0 安全修复批次**
> 相关模块：`src/py_sandbox.py`、`src/acrpa_api.py`、`src/engine.py`（`Python` 命令与 `代码` 命令）、`src/state.py`（`legacy_code_command`）

`Python` 命令允许在脚本（Excel）中直接书写内联 Python 代码，由沙箱
（`py_sandbox`）统一执行：**执行前 AST 预检 + 执行期超时 + 单行 JSON 审计**。

命令行格式（前 9 列约定）：

```
Python | <代码> | <权限: sandbox/trusted/full，可空>
```

* `代码` 支持多行（Excel 单元格内换行）。
* `权限` 留空时取设置里的「默认权限」（`state.PYTHON_DEFAULT_PERM`，默认 `sandbox`）。
* 脚本里可把结果写入变量 `result`，执行完成后会打印到日志（例如 `result = 1 + 1`）。
* **末行表达式**：若代码最后一条语句是表达式（如直接写 `1 + 2`、或 `acrpa.click(500, 300)`），
  会被当作结果写入 `result`，且**只执行一次**（§1.4 修复——不再出现"打印两次 / 点击两次"）。

> 相关的 `代码` 命令（读取 `.txt` 执行）在本批次也已统一走同一沙箱，详见 §10。

---

## 0. 威胁模型与安全边界（务必先读）

`sandbox` / `trusted` 均为**进程内 AST 沙箱**：只对 Python 源码做语法级（AST）过滤，
**不是完备的安全边界**。

* **定位**：防**误操作**与**低级滥用**——阻止误删文件、误调 `open/eval/exec`、
  误用危险语法等；**不用于对抗蓄意恶意代码**。字符串技巧与解释器实现细节属于
  "军备竞赛"，进程内方案无法穷尽（例如 `trusted` 可 `import`，可绕开超时）。
* **已知边界局限**：`trusted` 超时依赖 `sys.settrace`，可被 `sys.settrace(None)`
  或 C 层阻塞绕过（见 §4 / §13）。`sandbox` 的白名单与"拒绝含 `__` 字符串"规则
  只覆盖**已知**向量，不构成形式化保证。
* **跨信任边界**：对**真正不可信**的脚本（脚本市场下载、NetLink 远程分发等），
  **不得仅依赖本沙箱**。应以最小权限（`sandbox`）运行，并叠加 **OS 级隔离**
  （独立账户 / 受限令牌 / 容器）或安装期静态扫描。

> 一句话：进程内沙箱是"围栏"而非"金库"。跨信任边界执行，请用操作系统级隔离。

---

## 1. 三级权限对比

| 能力 | `sandbox`（沙箱，默认） | `trusted`（受信） | `full`（完全权限） |
|---|---|---|---|
| 内建函数 | **仅白名单内建**（见 §3） | 完整内建（`__builtins__`） | 完整内建 |
| `import` 语句 | ❌ 拒绝 | ✅ 允许 | ✅ 允许 |
| 文件读写（`open`） | ❌ 拒绝 | ❌ 拒绝（`open` 仍被拦） | ✅ 允许 |
| `eval/exec/compile/__import__` 直接调用 | ❌ 拒绝 | ❌ 拒绝 | ❌ 拒绝（核心危险调用） |
| dunder 属性（`x.__class__` 等） | ❌ 拒绝 | ✅ 允许 | ✅ 允许 |
| `with` / `lambda` / `class` / `global` / `nonlocal` | ❌ 拒绝 | ✅ 允许 | ✅ 允许 |
| 其它危险调用（`input/getattr/vars/...`） | ❌ 拒绝 | ✅ 允许 | ✅ 允许 |
| 执行方式 | 当前进程 · 单命名空间 | 当前进程 · 单命名空间 | **独立子进程**（subprocess） |
| 超时机制 | `sys.settrace` 行级时间检查 | `sys.settrace` 行级时间检查 | 子进程 `timeout=` + `kill` |
| `acrpa`（AcrpaAPI）可用 | ✅ | ✅ | ✅ |
| 是否默认可用 | ✅ 默认 | ✅（需在设置里选默认权限） | ⚠ **默认关闭**，需显式允许 |

> `full` 为**独立进程**执行：代码在子进程中运行，超时会被 `kill`，
> 不会卡死主界面。但 full 权限可执行任意代码/命令，风险极高。

---

## 2. AST 预检拒绝清单（`py_sandbox.precheck`）

预检在**执行之前**完成，失败原因短语稳定（便于自动化断言）：

| 失败原因 | 含义 | 适用权限 |
|---|---|---|
| `syntax error` | `ast.parse` 抛出 `SyntaxError` | 全部 |
| `code too large` | 源码 > 20000 字符，或 AST 节点数 > 20000 | 全部 |
| `forbidden call: <name>` | 调用了危险内建（`open/input/getattr/format/format_map/...`） | 仅 `sandbox`（`trusted/full` 仅拦 `eval/exec/compile/__import__`） |
| `forbidden attribute: <name>` | 访问了 dunder 属性（如 `__class__/__globals__/__subclasses__`） | 仅 `sandbox` |
| `forbidden dunder in string` | 字符串常量内含 `__`（含 f-string 字面量片段，堵 `str.format` 穿透） | 仅 `sandbox` |
| `forbidden import` | 出现 `import` / `from ... import` | 仅 `sandbox` |
| `forbidden statement: <kind>` | 出现 `With/AsyncWith/Global/Nonlocal/Lambda/ClassDef` | 仅 `sandbox` |

**逐权限拒绝矩阵（最终实现）**

* `sandbox`：语法错误、体积超限、`FORBIDDEN_CALLS` 全部（含 `format`/`format_map`）、
  `str.format` / `str.format_map` 属性调用、**含 `__` 的字符串常量**、dunder 属性、
  `import`、六种受限语法（`With/AsyncWith/Global/Nonlocal/Lambda/ClassDef`）。
* `trusted`：语法错误、体积超限、`eval|exec|compile|__import__` 直接调用；其余放行。
* `full`：同 `trusted`（因为 `full` 的隔离靠**独立进程**，而非语言级限制）。

---

## 3. `sandbox` 可用内建白名单（`SANDBOX_BUILTINS`）

```
abs, all, any, bool, dict, divmod, enumerate, filter, float, frozenset,
int, isinstance, issubclass, iter, len, list, map, max, min, next, pow, print,
range, repr, reversed, round, set, slice, sorted, str, sum, tuple, type, zip,
True, False, None, NotImplemented,
Exception, ValueError, TypeError, KeyError, IndexError,
ZeroDivisionError, ArithmeticError, RuntimeError, StopIteration
```

**不包含**（安全关键）：`__import__ / open / eval / exec / compile / globals /
locals / getattr / setattr / delattr / input / help / breakpoint`。

**`format` 已被移除**：`"…{0.__class__…}".format(x)` 这类字符串里的 dunder 对 AST
不可见，可被用于穿透读取对象属性，故 sandbox 下 `format` / `format_map` 一律拒绝
（`forbidden call: format`），并额外拒绝任何含 `__` 的字符串常量。需要格式化时请用
**f-string**（内部表达式是真实 AST 节点，dunder 会被正常拦截）、`%` 或字符串拼接
（拼接出的 `__` 同样被拦）。`trusted` / `full` 不受影响。

---

## 4. 超时机制

| 权限 | 机制 | 说明 |
|---|---|---|
| `sandbox` / `trusted` | `sys.settrace` **行级时间检查** | 每执行约 200 个行事件比较一次 `time.time()`，超过 deadline 抛 `TimeoutError`；**trace 一定在 `finally` 中清除**，不会拖慢后续执行。 |
| `full` | `subprocess` 的 `timeout=` | `subprocess.TimeoutExpired` → `proc.kill()`，`timed_out=True`。 |

* 超时秒数取 `state.PYTHON_TIMEOUT`（设置卡「超时(秒)」，默认 30，范围 1–600）。
* 模块内绝对兜底默认 30 秒。
* **不使用 `multiprocessing`**（打包时已被 `EXCLUDE_MODULES` 排除）。
* ⚠ **已知局限（§1.3，本期未修复）**：`sandbox` / `trusted` 的 deadline 仅在
  **行事件**触发时检查，因此 `import sys; sys.settrace(None); while True: pass`
  （一行摘除 tracer）或 `time.sleep(大值)` 这类 **C 层阻塞**会**绕过超时**，
  脚本可永久挂起（主界面不卡，但该行永不结束）。可靠的兜底方案（常驻 worker
  子进程 + 超时 `kill` + Windows Job Object 内存限制）属**后续批次**，见 §13。

---

## 5. 审计日志

* 路径：`<程序目录>/logs/acrpa_py_YYYYMMDD.log`（每日一个文件，UTF-8 无 BOM）。
* 格式：**单行 JSON**（`ensure_ascii=False`），字段如下：

| 字段 | 含义 |
|---|---|
| `ts` | 时间戳 `YYYY-MM-DD HH:MM:SS` |
| `perm` | 实际使用的权限 |
| `row` | 执行行号（1-based） |
| `code_sha1` | 代码 SHA1 前 **12** 位 |
| `code_len` | 代码字符长度 |
| `elapsed` | 执行耗时（秒） |
| `ok` | 是否成功 |
| `error` | 失败原因（成功为空串） |
| `timed_out` | 是否超时 |
| `source` | 来源：`excel` / `plugin` / `hook` / `console` / `file`（`代码` 命令读文件执行时） |
| `code_preview` | 首行前 **80** 字符（**仅预览，不写全文**） |

> **隐私保护**：审计**绝不写入代码全文**，仅以 sha1 + 长度 + 首行预览记录。
> 清理：`py_sandbox.cleanup_audit(days)` 只删除 `acrpa_py_*.log`，**绝不触碰**
> 既有的 `acrpa_*.log`（默认保留天数取 `state.LOG_RETENTION_DAYS`）。

---

## 6. `AcrpaAPI`（脚本内变量名 `acrpa`）速查

脚本中通过 `acrpa` 调用 ACRPA 能力（`acrpa_api.AcrpaAPI`）：

| 方法 | 说明 |
|---|---|
| `acrpa.log(msg, level="info")` | 写日志 |
| `acrpa.click(x, y)` | 点击坐标（同「坐标」命令） |
| `acrpa.type_text(text)` | 逐字输入（同「写入」，支持 DD 回退） |
| `acrpa.key_press(key)` | 按键；含 `+` 视为组合键（同「按键」/「热键」） |
| `acrpa.find_image(path, timeout=None)` | 找图，返回 `(x, y)` 或 `None` |
| `acrpa.sleep(sec)` / `acrpa.wait(sec)` | 等待（`wait` 感知暂停/停止） |
| `acrpa.move_to(x, y)` | 鼠标移动到坐标 |
| `acrpa.screenshot(path=None)` | 截图保存，返回路径或 `None` |
| `acrpa.get_var(name)` / `acrpa.set_var(name, value)` | 读写运行变量 |
| `acrpa.get_row()` | 当前行上下文 dict |
| `acrpa.run_command(cmd_type, args)` | 复用 engine 执行任意内置命令，返回 bool |
| `acrpa.read_excel_cell(path, sheet, row, col)` | 读取 Excel 单元格 |
| `acrpa.available` | 当前可用能力名列表 |

`acrpa_api.ACRPA_API_CAPS` 提供 `{"levels": [...], "methods": [...]}` 供插件做能力声明。

> **有意耦合声明**：`AcrpaAPI` 直调 engine 的既有私有/公有方法（`_coord/_write/
> _key/_wait/_hover/_find_cached/_hotkey/execute` 等）以保持与内置命令行为 100% 一致。
> 若 engine 私有方法签名变更，需同步 `src/acrpa_api.py`。

---

## 7. 关于脚本级钩子（重要）

第 5 项实现了 Tier3 **脚本级钩子**（`engine.register_script_hook("before"/"after", fn)`），
在 `ExecutionEngine.execute_script()` 的非嵌套分支触发。

> ⚠ **工作流场景不触发脚本级钩子**：工作流（`workflow.py`）通过 `eng.execute()`
> **直接调用命令 handler**，**不经过** `execute_script()`，因此脚本开始/结束钩子
> 在工作流下不会被调用。**本期设计如此**（未修改 `workflow.py`）。

---

## 8. `full` 权限的风险与开关

* `full` **默认关闭**：`state.PYTHON_FULL_ENABLED = False`。
* 在「设置 → Python 扩展」卡中勾选「允许 full 权限」后才会生效；
  勾选时会显示红色风险提示：
  `⚠ 完全权限可绕过沙箱执行任意代码与命令，仅在完全信任的脚本上启用`。
* 未开启时，脚本即使写了 `full` 权限也会被**拒绝执行**，原因 `full disabled`
  （同时写入审计）。
* 打包后（PyInstaller 冻结）`full` 依赖 `sys.executable` 启动子进程；若在冻结环境
  中 `-c` 无法运行，会返回错误而非崩溃。

---

## 9. 故障排查

| 现象 / 报错 | 原因与处理 |
|---|---|
| `forbidden call: open` | 在 `sandbox` 下调用了 `open`。若确需读写文件，改用 `trusted`？——`open` 在 `trusted` 下同样被拦（仅 `eval/exec/compile/__import__` 放行例外）。真正需要文件操作请启用 `full`。 |
| `forbidden call: format` / `forbidden call: format_map` | `sandbox` 禁止 `str.format` / `str.format_map`（防 dunder 穿透读取）。改用 f-string、`%` 或字符串拼接。 |
| `forbidden dunder in string` | `sandbox` 下字符串常量里出现了 `__`（防 `"{0.__class__}"` 之类穿透）。这是**有意**的严格拦截，请改用不含 `__` 的写法。 |
| `forbidden import` | `sandbox` 不允许 `import`。改用 `trusted`（可 `import math` 等）或 `full`。 |
| `forbidden attribute: __class__` | `sandbox` 禁止 dunder 属性访问（防沙箱逃逸）。 |
| `code too large` | 代码超过 20000 字符或 AST 节点超 20000，请拆分。 |
| `full disabled` | 请求了 `full` 但未在设置中开启「允许 full 权限」。 |
| 超时被中断 | 达到 `state.PYTHON_TIMEOUT`（默认 30s）。`sandbox/trusted` 由行级 trace 中断，`full` 由子进程 kill。日志会显示 `超时`。 |
| 界面卡死 | 不应发生：`sandbox/trusted` 有行级超时；`full` 为独立进程。若卡死请检查是否使用了 `full` 且系统杀死子进程受阻。 |
| 审计日志没有生成 | 检查 `<程序目录>/logs/` 是否可写；审计失败是**静默**的（不影响脚本执行）。 |
| 脚本无输出 | `print` 的输出会转发到 ACRPA 日志（前缀 `[py]`，打包版 windowed 模式亦不再静默丢失；无 GUI 环境退回标准错误）。也可把结果写入 `result` 变量（如 `result = 1 + 1`），执行后会打印到日志。 |
| `代码` 命令：`安全错误: 禁止访问脚本目录外的文件` | 脚本名越出脚本目录（或命中 `scripts` 与 `scriptsX` 之类前缀同名目录）。请把 `.txt` 放在脚本目录内。 |
| `代码` 命令：`脚本文件不存在` | 脚本目录下缺少 `<文件名>.txt`。 |
| `代码` 命令返回 `False` | 默认（`legacy_code_command=False`）下失败即返回 `False`、不再中断 / 重试；请查看日志中的具体原因（`forbidden ...` / 运行期异常 / 路径越权）。 |

---

## 10. `代码` 命令（文件形态，与 `Python` 命令同构）

`代码` 命令从脚本目录读取 `.txt` 文件执行 Python 代码：

```
代码 | <文件名，不含 .txt 后缀>
```

**本批次（P0 安全修复）已把 `代码` 命令统一改为走 `py_sandbox` 执行内核**，
与 `Python` 命令共享同一套安全护栏：

| 维度 | 旧实现（≤ v0.1.28-beta） | 现实现（本批次起） |
|---|---|---|
| 预检 | ❌ 无（dunder / 调用自由，可用 `().__class__.__base__.__subclasses__()` 逃逸） | ✅ AST 预检（与 `Python` 同规则，按 `state.PYTHON_DEFAULT_PERM`，默认 `sandbox`） |
| 超时 | ❌ 无（`while True` 永久挂起） | ✅ `sys.settrace` 行级 / 子进程 kill（随权限） |
| 审计 | ❌ 无 | ✅ 写 `logs/acrpa_py_*.log`（`source="file"`） |
| `acrpa` API | ❌ 无 | ✅ 注入 `AcrpaAPI` |
| 路径校验 | ⚠ `abs_path.startswith(base_dir)`（`scripts` 与 `scriptsX` 前缀同名目录会被误放行） | ✅ `os.path.commonpath([base_dir, abs_path]) == base_dir` |
| 失败语义 | ⚠ `raise`（被 retry 机制捕获 → **整段代码重复执行最多 `retry_max+1` 次**） | ✅ `return False`（不再中断 / 重试，避免副作用重复） |
| 权限 | 无（等同半受限 `exec`） | 取 `state.PYTHON_DEFAULT_PERM`（默认 `sandbox`） |

* 权限来源与 `Python` 命令一致；`full` 仍受 `state.PYTHON_FULL_ENABLED` 门控
  （未开启时 `full disabled`）。
* 成功时日志形如 `✅ 执行了脚本: <path> result=...`，handler 返回 `None`（继续执行）；
  失败（预检拒绝 / 运行期报错 / 路径越权 / 文件不存在）返回 `False` 并记日志。

---

## 11. `print` 输出与 `full` 结果通道分离（§1.5）

* **进程内（`sandbox` / `trusted`）**：`print` 被替换为包装函数，输出转发到 ACRPA
  日志（`utils.log1`，前缀 `[py]`）。**打包版（PyInstaller windowed，`sys.stdout is None`）
  不再静默丢失**；无 GUI 环境退回 `sys.stderr`。保留 `sep` / `end` 语义；显式传
  `file=` 时仍写入该流。
* **`full`（子进程）**：日志通道与结果通道**分离**——
  * 用户 `print` 经 `sys.stderr` 回传，由父进程转发进 ACRPA 日志；
  * 脚本结果单独走带标记的 stdout（`ACRPA-RESULT:`），**不再被用户输出污染**。
    例如 `print("hi")` + `result = 5`，`result` 稳定为 `5` 而非 `"hi\n5"`。

> 调试时可直接 `print(...)` 看日志；需要把值带回流程请写 `result`。

---

## 12. `legacy_code_command` 过渡开关（临时，建议尽快移除）

* 键：`state.LEGACY_CODE_COMMAND`（config 键 `legacy_code_command`），**默认 `False`**。
* 默认关闭时：`代码` 命令走沙箱（见 §10），具备预检 / 超时 / 审计 / API / 路径校验。
* 开启后：`代码` 命令**回退到旧的 `exec` 行为**——**失去上述全部安全护栏**
  （无预检、无超时、无审计、无 API、弱路径校验、失败 `raise`）。
* 用途：仅为兼容"依赖旧无限制行为"的存量脚本而保留的**临时过渡**，
  建议公告一个版本周期后**移除**；日常与市场分发的脚本请保持关闭。

---

## 13. 本期未实现 / 路线图（如实标注，勿当作已具备）

以下为外部建议文档中**已识别但本期未落地**的项，**当前版本不具备**，勿据此承诺：

| 编号 | 项目 | 状态 |
|---|---|---|
| §1.3 | `trusted` / 子进程 **常驻 worker + Windows Job Object**（超时兜底、内存限制、崩溃隔离） | ❌ 未实现。`trusted` 超时仍依赖 `sys.settrace`，可被 `sys.settrace(None)` 或 C 层阻塞（如 `time.sleep`）绕过；属后续批次 |
| §2.1 | 执行结果**写回引擎变量**（`result` → `${var}` 闭环） | ❌ 未实现（`run()` 已预留加性 `raw_result` 字段，但 engine 尚未消费） |
| §2.2 | **AcrpaAPI v2**（`ocr` / 窗口管理 / 剪贴板 / `find_image` 置信度 / `fail` / `interpolate` 等） | ❌ 未实现 |
| §2.3 | `sandbox` **受控标准库**（只读注入 `math/random/json/re/datetime` 等） | ❌ 未实现（`sandbox` 仍无这些模块） |
| §2.4 | **代码编辑对话框**（F5 试运行 / 行号映射 / 单元格 ↔ `.py`） | ❌ 未实现 |
| §3.1 | 插件 **CommandContext** SDK | ❌ 未实现 |
| §3.2 | 插件**元数据与管理 UI** | ❌ 未实现 |
| §3.3 | 市场**供应链加固**（安装期 precheck 扫描 / 签名验签） | ❌ 未实现（`.acrpapkg` 目前仅 sha256 完整性校验） |

**本期已实现（P0）**：§1.1（`format` 逃逸）→ 见 §2 / §3 / §9；§1.2（`代码` 命令统一走沙箱
+ 路径校验 + `return False`）→ 见 §10；§1.4（末尾表达式**单次执行**）→ 见开头说明；
§1.5（`print` 转发 + `full` 通道分离）→ 见 §11。
