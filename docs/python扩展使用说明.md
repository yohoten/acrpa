# ACRPA Python 扩展使用说明

> 适用版本：ACRPA 第 5 项优化（自定义 Python 代码脚本扩展 · 保守版）
> 相关模块：`src/py_sandbox.py`、`src/acrpa_api.py`、`src/engine.py`（`Python` 命令）

`Python` 命令允许在脚本（Excel）中直接书写内联 Python 代码，由沙箱
（`py_sandbox`）统一执行：**执行前 AST 预检 + 执行期超时 + 单行 JSON 审计**。

命令行格式（前 9 列约定）：

```
Python | <代码> | <权限: sandbox/trusted/full，可空>
```

* `代码` 支持多行（Excel 单元格内换行）。
* `权限` 留空时取设置里的「默认权限」（`state.PYTHON_DEFAULT_PERM`，默认 `sandbox`）。
* 脚本里可把结果写入变量 `result`，执行完成后会打印到日志（例如 `result = 1 + 1`）。

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
| `forbidden call: <name>` | 调用了危险内建（`open/input/getattr/...`） | 仅 `sandbox`（`trusted/full` 仅拦 `eval/exec/compile/__import__`） |
| `forbidden attribute: <name>` | 访问了 dunder 属性（如 `__class__/__globals__/__subclasses__`） | 仅 `sandbox` |
| `forbidden import` | 出现 `import` / `from ... import` | 仅 `sandbox` |
| `forbidden statement: <kind>` | 出现 `With/AsyncWith/Global/Nonlocal/Lambda/ClassDef` | 仅 `sandbox` |

**逐权限拒绝矩阵（最终实现）**

* `sandbox`：语法错误、体积超限、`FORBIDDEN_CALLS` 全部、dunder 属性、`import`、
  六种受限语法（`With/AsyncWith/Global/Nonlocal/Lambda/ClassDef`）。
* `trusted`：语法错误、体积超限、`eval|exec|compile|__import__` 直接调用；其余放行。
* `full`：同 `trusted`（因为 `full` 的隔离靠**独立进程**，而非语言级限制）。

---

## 3. `sandbox` 可用内建白名单（`SANDBOX_BUILTINS`）

```
abs, all, any, bool, dict, divmod, enumerate, filter, float, format, frozenset,
int, isinstance, issubclass, iter, len, list, map, max, min, next, pow, print,
range, repr, reversed, round, set, slice, sorted, str, sum, tuple, type, zip,
True, False, None, NotImplemented,
Exception, ValueError, TypeError, KeyError, IndexError,
ZeroDivisionError, ArithmeticError, RuntimeError, StopIteration
```

**不包含**（安全关键）：`__import__ / open / eval / exec / compile / globals /
locals / getattr / setattr / delattr / input / help / breakpoint`。

---

## 4. 超时机制

| 权限 | 机制 | 说明 |
|---|---|---|
| `sandbox` / `trusted` | `sys.settrace` **行级时间检查** | 每执行约 200 个行事件比较一次 `time.time()`，超过 deadline 抛 `TimeoutError`；**trace 一定在 `finally` 中清除**，不会拖慢后续执行。 |
| `full` | `subprocess` 的 `timeout=` | `subprocess.TimeoutExpired` → `proc.kill()`，`timed_out=True`。 |

* 超时秒数取 `state.PYTHON_TIMEOUT`（设置卡「超时(秒)」，默认 30，范围 1–600）。
* 模块内绝对兜底默认 30 秒。
* **不使用 `multiprocessing`**（打包时已被 `EXCLUDE_MODULES` 排除）。

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
| `source` | 来源：`excel` / `plugin` / `hook` / `console` |
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
| `forbidden import` | `sandbox` 不允许 `import`。改用 `trusted`（可 `import math` 等）或 `full`。 |
| `forbidden attribute: __class__` | `sandbox` 禁止 dunder 属性访问（防沙箱逃逸）。 |
| `code too large` | 代码超过 20000 字符或 AST 节点超 20000，请拆分。 |
| `full disabled` | 请求了 `full` 但未在设置中开启「允许 full 权限」。 |
| 超时被中断 | 达到 `state.PYTHON_TIMEOUT`（默认 30s）。`sandbox/trusted` 由行级 trace 中断，`full` 由子进程 kill。日志会显示 `超时`。 |
| 界面卡死 | 不应发生：`sandbox/trusted` 有行级超时；`full` 为独立进程。若卡死请检查是否使用了 `full` 且系统杀死子进程受阻。 |
| 审计日志没有生成 | 检查 `<程序目录>/logs/` 是否可写；审计失败是**静默**的（不影响脚本执行）。 |
| 脚本无输出 | 把结果写入 `result` 变量（如 `result = 1 + 1`），执行后会打印到日志。 |
