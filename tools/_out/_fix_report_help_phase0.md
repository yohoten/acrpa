# 帮助系统重构 · 阶段 0（内容层）实施报告

> 范围：仅**内容层**（不做 UI 渲染层）。
> 设计依据：[`docs/帮助窗口重构设计.md`](../docs/帮助窗口重构设计.md) 第 3/4/6 节。
> 环境：Windows + cmd.exe；解释器 `.venv\Scripts\python.exe`；命令逐条执行，中文输出加 `-X utf8`。

---

## 1. 交付物清单

| 类型 | 文件 | 说明 |
| --- | --- | --- |
| 新增 | [`src/help_content.py`](../src/help_content.py) | 内容层（**无 tkinter**，可 headless 测试） |
| 新增 | `res/help/sections.json` | 章节清单（显式顺序） |
| 新增 | `res/help/*.md`（13 个） | 各章节 Markdown 子集正文 |
| 新增 | `res/help/command_groups.json` | 命令→分组映射（有序分组 + 兜底标题） |
| 新增 | [`tools/_test_help_consistency.py`](../tools/_test_help_consistency.py) | 一致性守卫（纯数据，退出码 0/1） |
| 修改 | [`ACRPA.spec`](../ACRPA.spec:8) | `datas` 增加 `('.../res/help', 'res/help')`（仅此一处） |

**未改动**（遵守边界）：`src/dialogs.py`、`src/help_window.py`（下阶段创建）、`src/ACRPA.py`、`src/commands.py`（注册接口不变）、`tools/_debug_check.py`。

---

## 2. 内容层接口（渲染层唯一事实来源）

模块：`src/help_content.py`

### 2.1 数据模型

```python
@dataclass
class Section:      id: str; title: str; order: int; source: str = ""; body: str = ""

@dataclass
class Span:         kind: str; text: str; target: str = ""          # kind ∈ text/bold/code/link

@dataclass
class Block:        kind: str; text: str = ""; level: int = 0
                    items: list = []; rows: list = []; target: str = ""
                    lang: str = ""; spans: list = []
                    # kind ∈ heading / para / ulist / olist / code / table / hr

@dataclass
class CommandEntry: name: str; desc: str; params: str
```

### 2.2 函数

| 函数 | 签名 | 行为 |
| --- | --- | --- |
| `get_sections` | `() -> list[Section]` | 按 `order` 升序返回全部章节 |
| `get_section` | `(section_id: str) -> Section \| None` | 取单章，缺失返回 `None` |
| `parse_markdown_subset` | `(text: str) -> list[Block]` | Markdown 子集 → 结构化块 |
| `parse_inline` | `(text: str) -> list[Span]` | 行内样式（粗体/行内代码/链接） |
| `get_command_groups` | `() -> list[tuple[str, list[CommandEntry]]]` | 有序分组，含兜底组 |
| `get_about_info` | `() -> dict` | `{author, email, version, qr_path}` |
| `resolve_doc_path` | `(rel: str) -> str \| None` | 解析 `docs/*.md` 等外链为绝对路径 |
| `resolve_res_path` | `(rel: str) -> str \| None` | 解析 `res/` 下资源为绝对路径 |
| `get_version_notes_text` | `() -> str` | 汇总 `docs/releases/*.md` 为 Markdown 文本 |
| `read_command_groups_raw` | `() -> dict \| None` | 原始 `command_groups.json`（校验用） |

### 2.3 资源路径解析（开发态 + 冻结态）

`_res_roots()` 依次尝试（去重）：

1. 冻结态：`sys._MEIPASS/res`
2. 冻结态：`EXE 同级/res`
3. 开发态：`项目根/res`
4. 开发态：`src/res`

`resolve_doc_path()` 同理覆盖 `_MEIPASS` / EXE 同级 / 项目根，找不到返回 `None`（沿 [`dialogs.py:127`](../src/dialogs.py:127)-132 的多路径回退范式）。

---

## 3. 章节清单（13 章）与来源

| # | id | 标题 | 正文文件 | 来源（`source`） |
| --- | --- | --- | --- | --- |
| 1 | `quickstart` | 快速开始 | `00-快速开始.md` | 内嵌（迁移自 使用说明.txt 头部 + 新增） |
| 2 | `commands` | 命令速查 | `01-命令速查.md` | **动态**：`commands.list_all()` + `command_groups.json` |
| 3 | `script-editing` | 脚本编辑 | `02-脚本编辑.md` | 内嵌 + `docs/python扩展使用说明.md` |
| 4 | `execution-control` | 执行控制 | `03-执行控制.md` | 摘要 + 外链 `docs/执行控制tab设计方案.md` |
| 5 | `workflow` | 工作流 | `04-工作流.md` | 内嵌（workflow 能力摘要） |
| 6 | `browser` | 浏览器自动化 | `05-浏览器自动化.md` | 摘要 + 外链 `docs/浏览器使用说明.md`、`docs/浏览器后端增强设计.md` |
| 7 | `netlink` | NetLink 设备互联 | `06-NetLink设备互联.md` | 摘要 + 外链 `docs/netlink-*.md`（8 份） |
| 8 | `marketplace` | 脚本市场 | `07-脚本市场.md` | 摘要 + 外链 `docs/marketplace-v2-使用说明.md` |
| 9 | `settings` | 设置 | `08-设置.md` | 静态镜像 `settings_window._NAV_ITEMS`（11 项，见下） |
| 10 | `faq` | 常见问题 FAQ | `09-常见问题FAQ.md` | 内嵌（新增） |
| 11 | `hotkeys` | 快捷键 | `10-快捷键.md` | `settings_window.py` 快捷键注册表 + `state` 配置项 |
| 12 | `releases` | 版本与更新 | `11-版本与更新.md` | **动态**：汇总 `docs/releases/*.md`（md 为回退文本） |
| 13 | `contact` | 联系与支持 | `12-联系与支持.md` | 内嵌（迁移自 `dialogs.show_help_dialog` 联系卡片） |

- 「执行控制 / 浏览器 / NetLink / 脚本市场」采用**摘要 + 外链**：Markdown 内用 `[文字](docs/xxx.md)` 给出相对项目根的本地路径，渲染层用 `resolve_doc_path()` 实现「打开文件」。
- 章节 `dynamic` 字段：`commands` → 命令速查（渲染层填充命令列表）；`releases` → 由 `get_version_notes_text()` 运行时生成正文。

### 3.1 设置章节导航镜像（与 `settings_window.py:370` `_NAV_ITEMS` 一致）

基础执行 / AI 增强 / 定时调度 / 录制设置 / 日志 / 系统 / 快速操作 / 高级设置 / 网络互联 / Python 扩展 / 脚本市场

> 内容层不得引入 tkinter，故此处为**静态镜像**（非运行时派生），文件内已加注释提醒同步。

---

## 4. 命令分组映射要点

映射文件：`res/help/command_groups.json`（结构：`{_fallback_title, groups:[{title, commands:[...]}]}`）。

有序分组（9 组，共覆盖注册表 **68** 条命令）：

| 顺序 | 分组标题 | 命令数 | 覆盖命令 |
| --- | --- | --- | --- |
| 1 | 基础操作 | 19 | 找图/区域找图/点图/区域点图/按键/热键/输入/写入/等待/坐标/悬停/拖拽/滚轮/相移/按下/释放/复制/粘贴/截屏 |
| 2 | 流程控制 | 6 | 如果/否则/结束如果/循环开始/循环结束/跳出循环 |
| 3 | 窗口管理 | 7 | 激活窗口/关闭窗口/最小化窗口/最大化窗口/获取窗口位置/等待窗口/窗口坐标 |
| 4 | 变量与数据处理 | 4 | 设置变量/读取剪贴板/字符串处理/数学运算 |
| 5 | 代码执行 | 2 | 代码/Python |
| 6 | OCR 文字识别 | 3 | 识别文字/等待文字/点击文字 |
| 7 | AI 增强 | 3 | AI找图/AI识别界面/AI优化建议 |
| 8 | 工作流 | 2 | 运行工作流/工作流变量 |
| 9 | 浏览器自动化 | 22 | 打开网页/浏览器点击/浏览器输入/等待元素/浏览器截图/浏览器执行JS/执行JS/浏览器读取Cookie/浏览器设置Cookie/切换框架/返回主框架/新建标签页/切换标签页/关闭标签页/等待下载/浏览器上传/连接已开浏览器/接管浏览器/开始监听/等待数据包/停止监听/启动浏览器录制 |

### fallback / 降级行为

- 未列入映射的命令（含**插件运行时追加**的命令）→ 追加到兜底分组 `_fallback_title`（默认「其他命令」），**永不丢失**。
- 同名命令跨组出现时以**首次命中**为准（`mapped` 去重）。
- 映射文件缺失 / 解析失败 → 整体降级为单组「全部命令」，UI 不崩、命令不少。
- `commands.register()` 签名**不变**；分组只做外置映射。

---

## 5. Markdown 子集支持

`parse_markdown_subset` 白名单：标题（`#`/`##`/`###`）、无序列表（`- `/`* `）、有序列表（`1. `）、代码块（```）、表格（`| a | b |` + 分隔行）、水平线（`---`）、段落。
`parse_inline` 解析行内：**粗体** `**x**`、行内代码 `` `x` ``、链接 `[text](target)`（`Span.kind ∈ text/bold/code/link`）；段落块同时挂载 `spans`。

移除的行内标记语义：段落 `Block.text` 保留原始标记文本，供渲染层按 `spans` 上色/加粗/绑定链接。

---

## 6. 自验证结果

### 6.1 `_test_help_consistency.py`

命令：`.venv\Scripts\python.exe -X utf8 tools\_test_help_consistency.py`
**退出码 = 0，结论 PASS，FAIL=0，WARN=0**

关键结论：

- 命令双向一致：注册表 68 条 == 帮助渲染 68 条（含兜底组）。
- 章节数 13、id 唯一、body 全非空、标题与顺序符合预期、`sections.json` 的 `order` 生效。
- `command_groups.json` 合法、9 组有序、所列命令均在注册表内。
- 解析样例块序列 `['heading','para','ulist','olist','table','code','hr']` 符合预期；行内解析出 `bold/code/link`。
- `get_about_info()` 字段完整（author=yohoten，version=0.1.28-beta），`qr_path` 文件存在。
- 兜底行为：模拟插件命令落入兜底分组，未丢失。
- `help_content` 可导入且**未引入 tkinter 依赖**。

### 6.2 `_debug_check.py`

命令：`.venv\Scripts\python.exe -X utf8 tools\_debug_check.py`
**退出码 = 0**：`[SYNTAX] checked 111 files, 0 failed`（含 `src/help_content.py`）、`[IMPORT] 18/18 modules imported`。

> 说明：`_debug_check.py` 的导入清单为固定列表且不在本次允许修改范围内，故其**语法检查**已覆盖 `src/help_content.py`；`help_content` 的**可导入性 + 无 tkinter 依赖**由 `_test_help_consistency.py` 断言覆盖（见 6.1）。

---

## 7. 残留问题 / 后续（交阶段 1-2 渲染层）

1. **渲染层**需消费：`get_sections()` / `get_section()` / `get_command_groups()` / `parse_markdown_subset()` / `parse_inline()` / `get_about_info()` / `resolve_doc_path()`。
2. **命令速查**：正文由渲染层用 `get_command_groups()` 动态填充（`01-命令速查.md` 仅占位说明）。
3. **版本与更新**：正文由 `get_version_notes_text()` 运行时汇总（`11-版本与更新.md` 为回退文本）。
4. **设置章节**：为静态镜像；若 `settings_window._NAV_ITEMS` 变更，需同步 `08-设置.md` 与 `sections.json`。
5. **多语言**：暂不做，目录结构预留由后续阶段决定（当前为 `res/help/*`）。
6. **`docs/` 打包**：当前 `docs/` 未纳入 `datas`；「执行控制/浏览器/NetLink/脚本市场/版本」的外链与发布日期依赖 `docs/` 存在。若需冻结态可用，阶段 1-2 需评估是否将 `docs/`（或 `docs/releases/`）一并打包（本次按边界未改动，除 `res/help` 外的打包项）。
