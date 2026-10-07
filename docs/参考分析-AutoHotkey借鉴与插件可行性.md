# 参考分析：AutoHotkey 的借鉴点与纳入可行性

> 分析日期：2026-10-05
> 相关文档：`docs/ACRPA-完善路线图.md`（§12 可选依赖插件化）、`docs/参考分析-AutoClicker借鉴要点.md`

---

## 0. 结论先行

三个问题，三个不同的答案：

| 问题 | 结论 | 核心理由 |
| --- | --- | --- |
| **有何借鉴之处？** | **有，且优先级高于另两项** | 4 项能力是 ACRPA 当前的真实空白，且与上一轮 Auto Clicker 分析的结论**独立地互相印证** |
| **适合纳入 ACRPA 吗？** | **不适合** | ACRPA 已有等价物（Python 命令 + `acrpa_api`）且更强。纳入等于引入第二套语法、第二个脚本引擎、第二个沙箱边界 |
| **适合作为插件吗？** | **技术上可行，但优先级应排最后** | 唯一理由是复用存量 `.ahk` 脚本。价值前提是"目标用户已有大量 AHK 脚本"，需先确认这个前提 |

一个必须先讲清楚的判断：**AHK 与 ACRPA 不是同类物**。AHK 是一门脚本语言 + 解释器 + Windows 自动化原语库；ACRPA 是一个应用 + 命令表 + Python 扩展。它们的关系是"语言 vs 应用"，不是"功能 vs 功能"。把这个关系搞错，就会得出"把 AHK 抄进来"的错误结论。

---

## 1. 先纠正两个认知

### 1.1 ACRPA 的「热键」命令不是 AHK 的热键

这是最容易混淆的一点，务必分清：

```python
# src/commands.py:306
register("热键", "组合键操作", "按键1, 按键2")

# src/engine.py:1408
def _hotkey(self, row, z):
    self._chk()
    pa = get_pyautogui()
    pa.hotkey(row[1].value, row[2].value)      # ← 发送 Ctrl+C 这样的组合键
```

ACRPA 的「热键」= **发送**组合键（`pyautogui.hotkey`）。
AHK 的 `^j::` = **注册**一个热键，按下时**执行**一段脚本。

**两者语义完全不同——一个是"按下"，一个是"绑定"。**

ACRPA 侧真正的"绑定热键"机制存在于另一处（`ACRPA.py:3655-3789`），但只服务于 3 个内建动作：

```python
# src/ACRPA.py:3655
_HOTKEY_ACTIONS = {}          # {action: (mods_set, vk)} — run / pause / stop
_RECORD_STOP_SPEC = ...       # 录制停止热键
# src/ACRPA.py:3747 _hotkey_poll()
#   通过 ctypes 轮询键盘状态（非 RegisterHotKey），带边沿检测避免按住重复触发
```

即：**ACRPA 有热键机制，但只能触发固定的 3–4 个应用动作，用户无法自定义"按下某键 → 执行某个脚本/命令"。**

### 1.2 ACRPA 已经有 AHK 的等价物，且更强

AHK 的两大杀手锏是 `DllCall`（直接调 Win32 API）与 `ComObjCreate`（调用 Excel/Outlook 等 COM 组件）。ACRPA 的对应物：

| AHK 能力 | ACRPA 对应 | 对比 |
| --- | --- | --- |
| `DllCall` | `Python` 命令 + `pywin32`（已在 `requirements.txt`） | 等价，且 Python 侧可 `ctypes` |
| `ComObjCreate` | `Python` 命令 + `pywin32` COM | 等价 |
| 脚本语言 | `Python` / `代码` 命令（`commands.py:320-321`） | **Python 生态远大于 AHK** |
| 执行隔离 | `py_sandbox` 三级权限（AST 预检 + 超时 + 审计） | AHK 无沙箱概念 |
| 脚本内可用 API | `acrpa_api.py` 的 `AcrpaAPI`（15 个方法） | 对应 AHK 的内建命令集 |

`AcrpaAPI` 当前方法：`log` / `click` / `type_text` / `key_press` / `find_image` / `sleep` / `wait` / `move_to` / `screenshot` / `get_var` / `set_var` / `get_row` / `run_command` / `read_excel_cell` / `available`。

**结论：脚本化扩展这条路 ACRPA 已经走通了，走的是 Python 而不是自研语言。** 这个选择是对的——下面 §2.5 会谈 AHK v1→v2 的社区分裂代价。

---

## 2. 值得借鉴的四项能力

### 2.1 热字串 Hotstring（ACRPA 完全空白，办公场景刚需）

```
::ftw::Free the whales
::btw::by the way
```

输入触发文本自动替换为目标文本（或执行一段脚本）。

- ACRPA 侧：`grep 热字串|hotstring|自动替换` = **0 处**
- 价值：快捷短语、话术模板、表单填充、自动纠错——**办公自动化的高频场景**
- 实现：监听键盘输入流 + 缓冲匹配 + 回删替换
- 已知难点：与中文输入法候选窗口的交互（IME 组合状态下的按键拦截）。建议先做 ASCII 触发词，中文展开内容

### 2.2 上下文敏感热键 `#IfWinActive` / `#HotIf`

```
#IfWinActive Untitled - Notepad
#Space:: MsgBox, You pressed WIN+SPACE in Notepad.
#IfWinActive
#Space:: MsgBox, You pressed WIN+SPACE in any window.
```

同一个按键在不同窗口触发不同动作。

对照 ACRPA：`_HOTKEY_ACTIONS` 是全局且固定的，无上下文维度。若要支持"用户自定义热键 → 触发工作流/脚本"，**上下文条件应该从一开始就设计进去**，否则后期补要改数据结构。

### 2.3 窗口匹配语法 `ahk_class` / `ahk_exe` / `ahk_pid` / `ahk_id`

AHK 的 `WinTitle` 参数支持多种稳定标识：

| 标识 | 含义 | 稳定性 |
| --- | --- | --- |
| `ahk_exe` | 进程名 | 高 |
| `ahk_class` | 窗口类名 | 高 |
| `ahk_pid` | 进程 ID | 中（会变） |
| `ahk_id` | 窗口句柄 | 中（会变） |
| 标题文本 | 默认 | **低**（会变、会重复、会被本地化） |

还有 `SetTitleMatchMode`（开头匹配 / 包含 / 精确 / RegEx）。

对照 ACRPA：`bound_window_title` 只用标题。**标题是窗口标识里最不稳定的一种**——软件版本变化、文档标题变化、语言切换都会导致匹配失败。建议增加 `ahk_exe` 等价的进程名匹配作为首选、`ahk_class` 作为次选，标题匹配降为兜底。

### 2.4 坐标参考系 `CoordMode`（与上一轮结论呼应）

AHK 的 `CoordMode` 支持 `Screen` / `Window` / `Client` / `Relative` 四种，运行时可切换。

对照 ACRPA（`state.py:100`）：只有 `absolute`（屏幕）与 `relative`（相对窗口）两种，且**在录制时决定**，运行期不能切换。

### 2.5 一个反面借鉴：不要自研脚本语言

AHK v1 → v2 是不兼容迁移，v2 直到 2022 年才正式发布，**期间社区长期分裂**，至今仍有大量脚本停留在 v1。这是"自研语法"的长期代价：语言演进、兼容性、文档、工具链全部要自己扛。

**ACRPA 选择 Python 而非自研 DSL 是正确的决策，不要因为 AHK 语法糖简洁（`^j::` 一行定义热键）就动摇。**

---

## 3. 与上一轮 Auto Clicker 分析的交叉印证

这一节是本次分析最有价值的发现——**两个完全独立的产品在几项能力上给出了相同答案**：

| 能力 | Auto Clicker（.NET/Blazor） | AutoHotkey（脚本语言） | ACRPA |
| --- | --- | --- | --- |
| 色匹配 | `ColorMatchingMode`、`Tolerance` | `PixelGetColor`、`PixelSearch` | **0 处** |
| 多坐标参考系 | `MoveScreen/Window/RelativeToCursor/FixedLocation` Explanation | `CoordMode` 四种 | 2 种，录制期固定 |
| 区域限定 | `RegionOfInterest` | `ImageSearch` 的 X1/Y1/X2/Y2 参数 | 命令级「区域找图」，无全局 ROI |
| 窗口/进程绑定 | `ProcessName`、`Window` | `ahk_exe` / `ahk_class` / `ahk_pid` | 仅标题 |
| 输入发送模式 | — | `SendInput` / `SendPlay` / `SendEvent` / `ControlSend` | DD 的 auto/direct/simulate |
| 定时器 | `Schedule` / `RunEvery` | `SetTimer` / `#Persistent` | `scheduler.py` ✅ |
| 托盘常驻 | `MinimizeToSystemTray` | 托盘菜单 | `tray.py` ✅ |

**论证价值**：色匹配、多坐标系、区域限定这三项被两个技术栈迥异、目标用户也不同的产品**各自独立地实现**，说明它们是桌面自动化的**基础设施**而非可选特性。上一轮我把它们列为 P1，本轮得到了独立佐证——**建议提升为阶段二的必做项**。

---

## 4. 不建议借鉴的

| 项 | 理由 |
| --- | --- |
| **AHK 语法本身** | 引入第二套语法必然导致用户分裂与文档双倍维护 |
| **`GoTo` / `Gosub` / Label** | AHK v1 的控制流原语，v2 已弱化。ACRPA 的 §11 CFG 方案比它严谨得多 |
| **基于标题的默认窗口匹配** | 见 §2.3，应反向改进 |
| **无沙箱的执行模型** | AHK 脚本拥有完整用户权限且无隔离。ACRPA 的 `py_sandbox` 三级权限做得更好 |

---

## 5. 作为插件的可行性评估

### 5.1 技术可行性：可行，且有意外优势

AHK 不是 Python 库，是**独立解释器**（`AutoHotkey64.exe`，约 1–2 MB）。集成方式只能是 subprocess 调用：

```
ACRPA --subprocess--> AutoHotkey64.exe script.ahk --param
```

这恰好契合路线图 §12 设计的扩展体系：

```
%LOCALAPPDATA%/ACRPA/extensions/autohotkey/<ver>/
├── extension.json
├── native/AutoHotkey64.exe     ← 约 1.5 MB
└── ahklib/                     ← 可选：随包分发的常用 AHK 库
```

**意外优势**：子进程天然是**进程级隔离**，安全性显著优于 `py_sandbox` 的进程内 AST 沙箱。`full` 权限的 Python 命令已经走 subprocess（`py_sandbox` 文档 §1 已确认），AHK 可以直接复用这条成熟路径，**不需要为它单独设计沙箱**。

代价（必须写清楚）：

1. **变量交换困难**。AHK 与 ACRPA 不共享内存，只能通过命令行参数 / 环境变量 / 临时文件 / stdout 传递。复杂交互会很别扭
2. **错误反馈弱**。只能拿到退出码与 stderr，无法像 Python 命令那样把结果写回 `result` 变量
3. **无法共享 AcrpaAPI**。`.ahk` 脚本内不能调用 `acrpa.click()`，只能调 AHK 自己的原语 → **AHK 脚本与 ACRPA 脚本是两套世界，无法混编**
4. **需处理版本分歧**：v1 与 v2 语法不兼容，插件必须声明支持哪个版本，或两者都带

### 5.2 法律合规：一条硬约束（必读）

**AutoHotkey 以 GPLv2 发布。** 若 ACRPA 随扩展分发 `AutoHotkey64.exe`，构成 GPL 程序的再分发，需要遵守 GPLv2 义务（提供源码或书面索取途径、附带许可证与版权声明）。

这与路线图 §12.9 阶段三计划外置的 **DD 驱动**是同一类问题——两个可选依赖都带第三方授权约束。**建议在扩展清单的 `extension.json` 里增加 `license` 字段，并在安装向导中对非宽松许可证显式告知用户。**

替代路径（规避 GPL）：**插件不自带 `AutoHotkey64.exe`，改为检测用户本机已安装的 AutoHotkey**，缺失时给出官方下载指引。这是最省事且零合规风险的做法。

### 5.3 价值评估：前提是"存量脚本"，需先验证

纳入 AHK 的唯一实质价值是**复用 AHK 社区 20 年积累的存量脚本**。但这个价值成立的前提是：

> ACRPA 的目标用户（办公自动化场景）手里**已经有一批在用的 `.ahk` 脚本**。

如果这个前提不成立，插件的价值就接近于零——因为新写脚本的话，Python 明显更好。

**建议在立项前做一次用户调研确认这个前提。** 若确认存在，插件化成本不高（约 1–2 天 + 合规处理）；若不存在，应直接放弃。

### 5.4 关于参考链接本身

用户给出的 `https://auto-hotkey.net/zh/tutorials.html` **不是 AutoHotkey 官方站**。其下载链接指向 `download-direct.opik.net`，而官方域名是 `www.autohotkey.com`（下载在 `www.autohotkey.com/download/`）。

第三方站提供的安装包来源不明。**建议只从官方域名获取 AutoHotkey 二进制**，尤其是插件要分发 exe 的场景——从非官方源获取的可执行文件不应进入分发链路。教程内容本身是准确的（热键 / 热字串 / 修饰符 / 上下文热键四节与官方文档一致）。

---

## 6. 若要做：最小设计

若 §5.3 的前提确认成立，建议的最小实现（沿用 §12 扩展体系，不新建机制）：

1. **新增命令**：`运行AHK脚本`（参数：文件路径, 参数串, 超时秒）
2. **依赖声明**（复用 §12 层 2）：`requires=("script.autohotkey",)`，未安装时命令表显示角标并引导安装
3. **扩展 `script.autohotkey`**：
   - 优先模式：检测本机 `AutoHotkey64.exe`（注册表 + PATH + 常见安装路径），不自带二进制 → **零 GPL 风险**
   - 可选模式：随扩展分发（需处理 §5.2 合规）
   - `Capability.degraded = "hard"`：缺失即命令失败，不做静默降级
4. **变量交换约定**：AHK 脚本把输出写入 `%ACRPA_OUT%` 指向的临时文件（JSON），ACRPA 读取后删除。简单、可测、跨进程
5. **超时与终止**：`subprocess.run(timeout=)` + 超时 kill。与 `py_sandbox` 的 `full` 权限模式保持一致

明确不做：不实现 AHK 语法高亮编辑器、不做 v1/v2 自动转换、不做 AHK 脚本调用 `acrpa.*` API。

---

## 7. 落地优先级

按投入产出比排序，**建议把借鉴与插件分开排期**：

**阶段二（v0.2.0）——借鉴，优先级高**

| # | 事项 | 成本 | 说明 |
| --- | --- | --- | --- |
| 1 | 窗口匹配增加 `ahk_exe`（进程名）与 `ahk_class`，标题降为兜底 | 低 | 直接提升绑定窗口的鲁棒性 |
| 2 | 色匹配命令（用 Pillow，主包已有） | 低 | 两个外部产品均内置，本轮二次印证 |
| 3 | 坐标参考系扩展为 4 种，运行期可切换 | 中 | 与 §2.4 对齐 |

**阶段三（v0.3.0）——借鉴，优先级中**

| # | 事项 | 成本 | 说明 |
| --- | --- | --- | --- |
| 4 | 用户自定义热键（触发工作流/脚本），带上下文条件 | 中 | 数据结构要一次设计到位，含上下文维度 |
| 5 | 热字串 | 中 | 注意 IME 交互；先做 ASCII 触发词 |

**待定——AHK 插件**

6. 先做用户调研确认"存量 .ahk 脚本"前提是否成立；成立则按 §6 实现，不成立则放弃

---

## 附：本次核查的证据

| 结论 | 证据 |
| --- | --- |
| ACRPA「热键」是发送而非绑定 | `commands.py:306`、`engine.py:1408-1412` |
| ACRPA 全局热键仅 3–4 个内建动作 | `ACRPA.py:3655`（`_HOTKEY_ACTIONS`）、`:3707`（`_RECORD_STOP_SPEC`）、`:3747`（`_hotkey_poll`，ctypes 轮询） |
| ACRPA 热字串为 0 | `grep 热字串\|hotstring` 全库 0 命中 |
| 坐标模式仅 2 种且录制期固定 | `state.py:100` |
| ACRPA 已有脚本扩展能力 | `commands.py:320-321`（`代码` / `Python`）、`acrpa_api.py`（`AcrpaAPI` 15 个方法）、`py_sandbox` 三级权限 |
| 参考站非官方 | 教程页下载链接域名 `download-direct.opik.net`；官方为 `www.autohotkey.com` |
