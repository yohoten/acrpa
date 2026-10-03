# UI 美化设计方案 —— 经典桌面软件 × Windows 设计语言

> 文档类型：视觉 / 交互设计（**不含完整实现代码**，Python 片段为「结构 / 签名示意」）
> 目标：让主界面呈现**经典桌面软件**的秩序感 + **经典 Windows** 的血统感，同时**更实用**（密度、对齐、层级、键盘可达）
> 关联代码：[`src/utils.py`](../src/utils.py)、[`src/ACRPA.py`](../src/ACRPA.py)
> 关联布局：[`docs/底部常驻日志面板重构设计方案.md`](./底部常驻日志面板重构设计方案.md)

---

## 0. 现状基线与诊断

| 事实 | 位置 |
| --- | --- |
| ttk 主题 = **clam**（跨平台、可完全自绘，但**无原生圆角/抗锯齿/阴影**） | [`ACRPA.py:1506`](../src/ACRPA.py#L1506) |
| 色板 = 单一 `dict`（light/dark 两套），键：`bg/bgc/fgt/fgb/fgm/ac/ach/acl/sc/dg/wn/bd/logbg/logfg/ebg/hlbg/focus/...` | [`utils.py:_colors`](../src/utils.py#L108) |
| 样式集中器 `apply_theme()`：Notebook / Treeview / Cards / Labels / Combobox / Button×4 / Progressbar | [`utils.py:372`](../src/utils.py#L372) |
| 设计 token：`sp_xs/sm/md/lg/xl`、`radius`、`ctrl_h=26/22/30`、`gap`、`icon_*`、`card_pad` | [`utils.py:731`](../src/utils.py#L731) |
| 字体 = 命名字体角色（Microsoft YaHei UI / Consolas / Segoe UI Symbol） | [`utils.py:164`](../src/utils.py#L164) |
| 缩放 = `scaled()`(含 dpi×ui_scale) 与 `fit_pt()` 双通道 | [`utils.py:228`](../src/utils.py#L228) |
| 卡片工厂 `create_card` / 工具条按钮 `_btn`·`_tbtn` | [`utils.py:311`](../src/utils.py#L311) / [`ACRPA.py:3600`](../src/ACRPA.py#L3600) |
| 主题切换 `_refresh_theme`（重绑 C + re-apply ttk + 遍历刷新 widget 前后景） | [`ACRPA.py:2018`](../src/ACRPA.py#L2018) |

### 0.1 诊断（为什么现在「不够经典、不够实用」）

| # | 问题 | 现象 | 影响 |
| --- | --- | --- | --- |
| V1 | **配色「网页感」而非「系统感」** | 主色 `#2563eb`、边框 `#e2e8f0` 是典型的 Tailwind 网页蓝/灰 | 缺少 Windows 血统感 |
| V2 | **层级靠色块而非分层** | 大片白卡 + 蓝底表头，缺少「chrome 底 → 卡片面 → 内嵌域」的三层结构 | 视觉噪声大、不「经典」 |
| V3 | **强调色滥用** | 表头整条蓝底白字、Tab 选中变蓝字、按钮高饱和 | 重点不突出、久看疲劳 |
| V4 | **控件尺度不统一** | `ctrl_h` 26 / `card` 自定 / 部分按钮 `bd=3` 浮雕 | 排面参差、不整齐 |
| V5 | **网格与表格不够「数据软件」** | Treeview 22–24px、无竖分隔线、无 zebra、表头无边框 | 密集数据可读性弱 |
| V6 | **分隔/聚焦/悬停缺规范** | sash 仅颜色、focus 无可见环、hover 只有变暗 | 键盘可达性差、操作反馈弱 |
| V7 | **图标字体混杂** | `Segoe UI Symbol` + Emoji（⚙️🤖📹🌐💰🧩✅❌⚠️ 等，见 [§3.1](#31-emoji--segoe-ui-symbol-映射表v7-裁决)） | 不同机器渲染不一致、不「经典」 |

> 结论：**优先改「色板 + 样式集中器 + 令牌」这三处，能以最小改动获得最大观感提升**；布局已在 v2 重构中定型，本次只做「皮肤 + 微观质感」。

---

## 1. 设计原则（经典桌面软件 × Windows）

1. **分层而非涂色（Layering over Painting）**：用 `chrome 底 / 表面 / 内嵌域` 三层灰度 + 1px 发丝线表达层次；强调色只给「主操作 / 选中 / 焦点」。
2. **密度优先（Density）**：4px 基准网格；控件高 24（紧凑）~30（大）；表格行高 22。信息密度是桌面软件的核心优势。
3. **克制的强调色（Accent Restraint）**：一条蓝只出现在三处——主按钮、当前选中、焦点环。其余用中性灰阶。
4. **一致的控件基线**：所有按钮同高、同内边距、同 hover/pressed/disabled 三态；所有输入同边框、同焦点环。
5. **发丝线（Hairline）**：分隔线统一 1px `#E1E1E1`（暗色 `#3A3A3A`），禁止 2px 粗色条（除非状态强调）。
6. **经典 Windows 语汇**：Segoe UI / Microsoft YaHei UI 字体；`#0078D4` 系统强调色；经典分段状态栏、命令栏分组、经典浅黄 tooltip、经典细滚动条、经典 sash 抓手点。
7. **键盘可达（Keyboard-first）**：可见焦点环、Tab 顺序、F6 切换区域、快捷键提示（沿用 `_tip_with_hotkey`）。
8. **零回归**：新增色键与样式，**不删除**现有 `C[...]` 键；默认观感 = 现状更精致，不改变控件语义与布局。

---

## 2. 色板 Token（对齐 Windows 10/11）

### 2.1 语义色（浅色）

| 语义 | Token | 现值 | **建议值** | 依据 |
| --- | --- | --- | --- | --- |
| 应用底（chrome） | `bg` | `#f5f6f8` | **`#F3F3F3`** | Win10 标题/应用灰 |
| 表面（卡片面） | `bgc` | `#ffffff` | **`#FFFFFF`** | 内容面 |
| 表面-次 | `surface_alt`* | — | `#FAFAFA` | 表头、斑马纹 |
| 文字-强调 | `fgt` | `#1a202c` | **`#1F1F1F`** | Win 文本 |
| 文字-正文 | `fgb` | `#2d3748` | **`#333333`** | |
| 文字-次要 | `fgm` | `#718096` | **`#5F5F5F`** | |
| 文字-禁用 | `fgd`* | — | `#9A9A9A` | |
| 边框-发丝 | `bd` | `#e2e8f0` | **`#E1E1E1`** | |
| 边框-强 | `border_strong`* | — | `#C8C8C8` | 输入/按钮描边 |
| 强调 | `ac` | `#2563eb` | **`#0078D4`** | **Windows 系统蓝** |
| 强调-hover | `ach` | `#1d4ed8` | **`#106EBE`** | |
| 强调-pressed* | — | — | `#005A9E` | |
| 强调-淡/选中底 | `acl` | `#eff6ff` | **`#CCE4F7`** | Win 选中行 |
| 成功 | `sc` | `#10b981` | **`#107C10`** | Win 绿 |
| 危险 | `dg` | `#ef4444` | **`#C42B1C`** | Win 红 |
| 警告 | `wn` | `#f59e0b` | **`#9D5D00`**(文字) / `#F7630C`(面) | 对比度达标 |
| 焦点环 | `focus` | `#2563eb` | **`#0078D4`** | |
| 网格竖线* | `gridline` | — | `#EEEEEE` | 表格（**不可行/不适用·token 保留备用**，见 [§4.5](#45--数据表经典-datagrid--listview--b2)） |
| 行号沟* | `gutter_bg` | — | `#F6F6F6` | **不可行/不适用·token 保留备用**（见 [§4.5](#45--数据表经典-datagrid--listview--b2)） |
| 高亮（当前行） | `hlbg` | `#FEF3C7` | **`#FFF4CE`** | Win 高亮黄 |
| 日志底/字 | `logbg/logfg` | | `#FFFFFF` / `#1F1F1F` | 控制台 |

> 带 `*` 为**新增键**（向后兼容，只加不改）。

### 2.2 语义色（深色）

| 语义 | Token | 现值 | **建议值** |
| --- | --- | --- | --- |
| 应用底 | `bg` | `#0b1120` | **`#202020`** |
| 表面 | `bgc` | `#1a2332` | **`#2B2B2B`** |
| 表面-次 | `surface_alt` | — | `#262626` |
| 文字-强调/正文/次要 | `fgt/fgb/fgm` | | **`#FFFFFF` / `#E0E0E0` / `#A0A0A0`** |
| 边框/强 | `bd`/`border_strong` | | **`#3A3A3A` / `#4A4A4A`** |
| 强调 / hover / 淡底 | `ac/ach/acl` | `#60a5fa` | **`#4CC2FF` / `#3793D4` / `#094771`** |
| 焦点环 | `focus` | `#60a5fa` | **`#4CC2FF`**（= 深色 `ac`；显式取值见 [§4.9.1](#491-focus-visible-可视环)） |
| 成功/危险/警告 | `sc/dg/wn` | | **`#6CCB5F` / `#FF99A4` / `#FCE100`** |
| 高亮（当前行） | `hlbg` | `#4a3a12` | **`#4D3B00`** |

### 2.3 Windows 血统感的三条「锚点」

1. **强调色 `#0078D4`**：一眼 Windows。
2. **经典浅黄 tooltip**（`tooltip_bg`=`#FFFFE1` + 1px `tooltip_border`=`#646464`）：Windows 桌面软件集体记忆。**已落地为统一 token**（全窗 tooltip 一律消费该对 token，见 [§4.8](#48-控件微观)）。
3. **分段状态栏 + 1px 下沉发丝线**：资源管理器同款。

### 2.4 强调色（`ac`）使用白名单（V3，强制裁决）

**裁决**：强调色是「**稀缺资源**」，仅允许出现在下列 5 处；其余一律中性灰阶。

| # | 允许使用 | 说明 |
| --- | --- | --- |
| ① | 主操作按钮 | 全窗**唯一**实心 `ac` 底按钮（如 `▶ 运行`、各卡「主按钮」） |
| ② | 选中左缘指示 | 表格/日志**选中行左缘 2px** `ac` 竖条（非整条填充） |
| ③ | 焦点环 | `focus-visible` 2px `ac` 环（见 [§4.9](#49-焦点与悬停状态规范v6强制)） |
| ④ | 关键徽标 / 当前项 | 当前运行行标记、断点点、`default` 按钮 1px 描边 |
| ⑤ | 进度 / 图例语义色 | 进度条填充、命令列分类色（沿用 `_LEGEND_ITEMS`） |

**撤销（V3 直接违规项，实现阶段须逐一消除）**：

- ❌ **表头整条蓝底白字** —— 现 [`utils.py:432-434`](../src/utils.py#L432)（`background=C["ac"], foreground="white"`）→ 改为**灰底 `surface_alt` + 深灰字**（见 §4.5）；
- ❌ **Tab 选中蓝字** —— 现 [`utils.py:385`](../src/utils.py#L385)（`foreground=[("selected", C["ac"])]`）→ 改为**选中加粗 + 顶部 2px `ac` 条**，字色保持 `fgt`（见 §4.3）；
- ❌ **高饱和整块按钮** —— [`utils.py:414-426`](../src/utils.py#L414) 的 Action/Success/Danger/Warning 实心按钮：仅**主操作**保留实心 `ac`，普通按钮改扁平次级（见 §3.2/§4.8）。

**层级三档表达（用「字重 / 顶条 / 左缘」而非大面积高饱和填充）**：

| 层级 | 表达方式 |
| --- | --- |
| primary（唯一焦点） | 实心 `ac` 底 + 白字 + `ach` hover |
| secondary（常规） | `surface_alt` 底 + 1px `border_strong` 描边 + `fgb` 字 + `caption_hover` hover |
| tertiary（弱 / 图标） | 无边框透明底，hover 才显 `caption_hover` 底 + 1px 描边 |
| 选中（列表 / 表格） | 整行 `acl` 底 + 左缘 2px `ac` 条 + 字重不变 |
| 选中（Tab） | 加粗 + 顶部 2px `ac` 条 + 内容面白底（**不改字色**） |

### 2.5 日志级别语义（四级·权威，C1/C2 收敛）

> 本表为**全项目日志级别口径的唯一权威**：[`docs/底部常驻日志面板重构设计方案.md`](./底部常驻日志面板重构设计方案.md) §6 与 [`docs/设置窗口优化设计方案.md`](./设置窗口优化设计方案.md) 一律引用本表，**不得再出现 `SUGGEST` 或其它第五级**。

**事实核对（读码结论，非推测）**：

- 级别枚举（int）在 [`utils.py:11-22`](../src/utils.py#L11)：`LOG_DEBUG=0 / LOG_INFO=1 / LOG_WARNING=2 / LOG_ERROR=3`，`_LOG_LEVEL_NAMES` = `DEBUG/INFO/WARNING/ERROR`（**文件落盘/结构化格式**用）。
- GUI **tag 集合**在 [`ACRPA.py:4131-4134`](../src/ACRPA.py#L4131)（`info/success/warning/error`）与过滤集合 [`ACRPA.py:4209`](../src/ACRPA.py#L4209)（`("info","success","warning","error")`）——**四级，无 `suggest`、亦无 `debug`**。
- 级别过滤下拉在 [`ACRPA.py:4096`](../src/ACRPA.py#L4096)：`("全部","INFO","SUCCESS","WARNING","ERROR")`。
- [`log1(msg, tag, level)`](../src/utils.py#L477) 由 **tag 反推 level**，但自动映射只认 `error/warning/info/debug`；`tag="success"` 落 else → `level=LOG_INFO`（即 **SUCCESS 在落盘侧记为 INFO**）。故「磁盘级别集合」= `DEBUG/INFO/WARNING/ERROR`，「面板显示级别集合」= `INFO/SUCCESS/WARNING/ERROR` —— **二者不是同一集合**，这正是不一致的历史根源。

**裁决**：**日志面板显示级别 = `INFO / SUCCESS / WARNING / ERROR` 四级**（与 tag、与过滤下拉、与 `_LOG_LEVEL_NAMES` 的可见部分一致）；`SUGGEST` **全篇删除**（代码中不存在、从未使用）。**不新增 int 枚举**：`SUCCESS` 为「显示级」，映射 `LOG_INFO`，故 [`utils.py:11-22`](../src/utils.py#L11) 与 [`ACRPA.py:4209`](../src/ACRPA.py#L4209) **均无需改动**；唯一代码落点为「行生成按 tag 判定徽标」+「tag_configure 补 4 个 `cbar_*`」。

**徽标文案 ↔ tag 名 ↔ 颜色 token 映射表（唯一权威）**：

| 徽标文案 | tag 名 | 级别色条 token（左缘 2px） | 色条值（浅 / 深） | 徽标/正文 token | 过滤下拉项 | 文件级别 int |
| --- | --- | --- | --- | --- | --- | --- |
| `[INFO]` | `info` | `log_info` | `#8A8886` / `#9A9A9A` | `fgm` | `INFO` | `LOG_INFO(1)` |
| `[SUCCESS]` | `success` | `log_success` | `#107C10`(`sc`) / `#6CCB5F` | `sc` | `SUCCESS` | 复用 `LOG_INFO(1)` |
| `[WARNING]` | `warning` | `log_warn` | `#F7630C` / `#FCE100` | `wn`(`#9D5D00` 文本 / `#F7630C` 面) | `WARNING` | `LOG_WARNING(2)` |
| `[ERROR]` | `error` | `log_error` | `#C42B1C`(`dg`) / `#FF99A4` | `dg` | `ERROR` | `LOG_ERROR(3)` |

> **「四者一致」实现口径**：① 行生成**必须由 tag（而非 level int）决定徽标文案**（`log1(msg,"success")` 的 int 级别是 INFO，但徽标须为 `[SUCCESS]`）；② 过滤下拉项文案 = 徽标去括号；③ 左缘 2px 色条 tag 名 = `cbar_<tag>`（`cbar_info / cbar_success / cbar_warning / cbar_error`），配色条值；④ 徽标字色取「徽标/正文 token」。
> **代码改动落点**：tag 重刷 [`ACRPA.py:4131-4134`](../src/ACRPA.py#L4131)（补 `cbar_*`）；行生成 [`utils.py:579-587`](../src/utils.py#L579)（改为 tag→徽标映射）；下拉 [`ACRPA.py:4096`](../src/ACRPA.py#L4096) 维持不变。

---

## 3. 字体与尺度

| 角色 | 现状（[`utils.py:164`](../src/utils.py#L164)） | 建议 |
| --- | --- | --- |
| Title | Microsoft YaHei UI 10 bold | 保留；标题栏可 `Segoe UI Semibold` 优先、YaHei 兜底（CJK 需合并字形） |
| Body/Small/Btn | YaHei UI 9/8/9bold | 保留（CJK 场景 YaHei 最稳） |
| Log | Consolas 9 | 保留；可选 `Cascadia Mono` 优先 |
| Icon | [`ACRPA_ICON/ICON_MD/ICON_LG`](../src/utils.py#L172) = `Segoe UI Symbol` 9/11/12 | **裁决：统一使用 `Segoe UI Symbol` 单一图标字体**（★废弃 Fluent/MDL2 三级回退）。Emoji→单色字形映射见 [§3.1](#31-emoji--segoe-ui-symbol-映射表v7-裁决) |

**图标口径裁决（V7，权威、覆盖旧表述）**：图标字体**统一为 `Segoe UI Symbol` 一种** —— [`_FONT_SPECS`](../src/utils.py#L164) 的 `ACRPA_ICON / ACRPA_ICON_MD / ACRPA_ICON_LG` 已符合（现 [`utils.py:172-174`](../src/utils.py#L172)）。

- **废弃**原「`Segoe Fluent Icons` → `Segoe MDL2 Assets` → `Segoe UI Symbol` 三级回退」表述：回退链在旧系统会渲染成空白方块，且三族字形宽度不一致、字号基线漂移，反而破坏「统一」。
- **规范（强制）**：① UI 文案与控件**禁止使用彩色 Emoji**（⚙️🤖⏰📹📋💻🌐💰🧩✅❌⚠️ 等一律移除，见 [§3.1](#31-emoji--segoe-ui-symbol-映射表v7-裁决)）；② 一律改用 `Segoe UI Symbol` 覆盖的**单色码位**；③ 图标颜色一律实时取 `C[...]` 前景色，**禁止硬编码颜色**；④ 图标字号仅用 `ACRPA_ICON / ACRPA_ICON_MD / ACRPA_ICON_LG` 三个角色（**反例**：[`ACRPA.py:1845`](../src/ACRPA.py#L1845) 的 🌐 硬编码 `font=("Segoe UI Symbol",11)`，须改 `FONT_ICON_MD`）。

**尺度基线（统一后，以此为准）** —— 覆盖 [`TOKENS`](../src/utils.py#L731) 现值（代码现为 `ctrl_h=26 / ctrl_h_lg=30`，须按本表修订）：

```text
基准网格 = 4px
控件高:  ctrl_h_sm=22   紧凑(卡片内小控件 / 图标按钮)
         ctrl_h=24      标准(所有行内输入 / 下拉 / 按钮)        ← 修订(原 26)
         ctrl_h_lg=28   大(命令栏控件 / 标题栏命中区 / 主操作)   ← 修订(原 30)
表格行高: row_h=22      唯一定值(消除 22/24 冲突)
命令栏高: 30
进度条高: 10（进度条 thickness 字面量）
分类色条宽: bar_h=4    ← 保留 4（市场窗口分类色条消费该 token；布局约束，**不按 10 判定**，见 §6.1）
卡片内边距: card_pad=10
发丝线: hairline=1px
焦点环: focus_w=2px 强调色 (focus-visible, 内缩 2px)
sash: 4px + 中心抓手点
```

### 3.1 Emoji → Segoe UI Symbol 映射表（V7 裁决）

所有 UI 图标一律使用 `Segoe UI Symbol` 覆盖的**单色码位**。下表为全项目 Emoji 的**唯一替换结论**（「来源」列给出现有硬编码位置，供实现阶段逐点清除）。

| Emoji | 替换符号 | Unicode | 语义 / 用途 | 来源（file:line） |
| --- | --- | --- | --- | --- |
| ⚙️ | ⚙ | U+2699 | 设置 / 配置（卡标题、标题栏） | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 🤖 | ✦ | U+2726 | AI 增强 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| ⏰ | ⏱ | U+23F1 | 定时调度 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 📹 | ◉ | U+25C9 | 录制 / 摄像头 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 📋 | ▤ | U+25A4 | 日志 / 清单 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 💻 | ▢ | U+25A2 | 系统 / 设备 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| ⚡ | ↯ | U+21AF | 快速操作 / 流程节点 | [`settings_window.py:405-416`](../src/settings_window.py#L405) / [`ACRPA.py:4609-4611`](../src/ACRPA.py#L4609) |
| 🔧 | ⚒ | U+2692 | 调试 / 工具 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 🌐 | ⊕ | U+2295 | 网络互联 | [`settings_window.py:405-416`](../src/settings_window.py#L405) / [`ACRPA.py:1845`](../src/ACRPA.py#L1845)（**该处硬编码 `font=("Segoe UI Symbol",11)`，违反规则④，须改 `FONT_ICON_MD`**）/ [`netlink_window.py:698`](../src/netlink_window.py#L698)（`[🌐 网页面板]`） |
| 🐍 | § | U+00A7 | Python 扩展 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 🛒 | ⛁ | U+26C1 | 脚本市场 | [`settings_window.py:405-416`](../src/settings_window.py#L405) |
| 📚 | ⊞ | U+229E | 帮助 / 文档 | [`ACRPA.py:4527`](../src/ACRPA.py#L4527) |
| 📄 | ☰ | U+2630 | 文档 / 文件 | [`market_window.py:60`](../src/market_window.py#L60) |
| ⏳ | ⧗ | U+29D7 | 等待 / 排队 | [`ACRPA.py:4624-4625`](../src/ACRPA.py#L4624) |
| ⟳ | ↻ | U+21BB | 重跑 / 刷新 | [`ACRPA.py:4624-4625`](../src/ACRPA.py#L4624) |
| 📌 | ✚ | U+271A | 标记 / 图钉 | [`ACRPA.py:4609-4611`](../src/ACRPA.py#L4609) |
| 💬 | ✉ | U+2709 | 注释 / 消息 | [`ACRPA.py:4609-4611`](../src/ACRPA.py#L4609) |
| ℹ | ⓘ | U+24D8 | toast info | [`utils.py:63`](../src/utils.py#L63) |
| ✓ | ✔ | U+2714 | 成功 | [`utils.py:63`](../src/utils.py#L63) |
| ⚠ | ⚠ | U+26A0 | 警告 | [`utils.py:63`](../src/utils.py#L63) |
| ✗ | ✘ | U+2718 | 错误 | [`utils.py:63`](../src/utils.py#L63) |
| 💰 | ¥ | U+00A5 | 财务（分类图标） | [`market_window.py:60`](../src/market_window.py#L60) |
| 🧩 | ▩ | U+25A9 | 其他 / 分类缺省（分类图标） | [`market_window.py:60`](../src/market_window.py#L60) / [`88`](../src/market_window.py#L88) |
| ✅ | ✔ | U+2714 | 成功（日志正文） | [`engine.py:998`](../src/engine.py#L998) / [`1206`](../src/engine.py#L1206) / [`dd_backend.py:87`](../src/dd_backend.py#L87) / [`templates.py:239`](../src/templates.py#L239) |
| ❌ | ✘ | U+2718 | 失败（日志正文） | [`engine.py:1164`](../src/engine.py#L1164) / [`1208`](../src/engine.py#L1208) / [`templates.py:240`](../src/templates.py#L240) |
| ⚠️（含 VS16 `U+FE0F`） | ⚠ | U+26A0 | 警告（**须去 VS16**，与上面 ⚠ 行合并） | [`dd_backend.py:72`](../src/dd_backend.py#L72) / [`dialogs.py:723`](../src/dialogs.py#L723) / [`settings_window.py:800`](../src/settings_window.py#L800) |
| ⌨️ | ⌨ | U+2328 | 键盘 / 输入 | [`engine.py:1005`](../src/engine.py#L1005) |
| 🔗 | ⇄ | U+21C4 | 配对 / 连接 | [`netlink_window.py:571`](../src/netlink_window.py#L571) |
| 📂 | ▭ | U+25AD | 目录 / 远端脚本 | [`netlink_window.py:583`](../src/netlink_window.py#L583) |
| 📷 | ◎ | U+25CE | 截图 / 摄像头 | [`netlink_window.py:676`](../src/netlink_window.py#L676) |
| 📜 | ≡ | U+2261 | 审计日志 | [`netlink_window.py:686`](../src/netlink_window.py#L686) |
| 📁 | ▫ | U+25AB | 分类目录（操作库） | [`ACRPA.py:4730`](../src/ACRPA.py#L4730) |
| ⚪ | ○ | U+25CB | 禁用态步骤 | [`ACRPA.py:4886`](../src/ACRPA.py#L4886) / [`5050`](../src/ACRPA.py#L5050) |
| 🚀 | ↻ | U+21BB | 重启更新 | [`dialogs.py:1258`](../src/dialogs.py#L1258) |
| ⬇ | ↓ | U+2193 | 下载 | [`dialogs.py:1253`](../src/dialogs.py#L1253) / [`1315`](../src/dialogs.py#L1315) / [`1337`](../src/dialogs.py#L1337) |
| ✔ | ✔ | U+2714 | 已完成 | [`dialogs.py:1344`](../src/dialogs.py#L1344) |
| ✨ | ✦ | U+2726 | AI 生成 | [`dialogs.py:597`](../src/dialogs.py#L597) |

> **幻影行（代码中不存在，**无需处理**，V7 复核后从本表删除）**：`🔵` / `🟢`（**全库无命中**；命令表图例色点实为**单色** `●`(U+25CF)，[`ACRPA.py:2199`](../src/ACRPA.py#L2199)）、`🔍`（**全库无命中**；日志工具条为**文字**「搜索」，[`ACRPA.py:4105`](../src/ACRPA.py#L4105)）。**不得**再把它们列为待替换项。

> 说明：`●`(U+25CF) 的语义色由 `C[...]` 决定（找图 = `ac` 蓝 / 点图 = `sc` 绿），字号用 `ACRPA_ICON`（9pt）。若个别机器缺字形，**回退 ASCII**（`[*] / [+] / [!]`），**不再回退到 Emoji**。

### 3.2 控件尺度规范（V4，强制）

1. **唯一尺寸源**：所有窗口（**含 [`settings_window.py`](../src/settings_window.py)**）必须消费 [`TOKENS`](../src/utils.py#L731) / [`ctrl_h()`](../src/utils.py#L761)；**禁止**字面量高度（`height=26`、`bd=3`、魔法 `padx/pady`）。设置窗口当前 0 处使用 `ctrl_h/TOKENS`（[`settings_window.py`](../src/settings_window.py) 全文）属违规，须在用 P1 控件工厂重构时统一改走 `ctrl_h("ctrl_h_sm"/"ctrl_h"/"ctrl_h_lg")`。
2. **取值语义**（见 §3 基线）：`ctrl_h_sm=22` 仅卡片内紧凑行；`ctrl_h=24` 为行内控件默认；`ctrl_h_lg=28` 用于命令栏控件、标题栏图标按钮命中区、主操作按钮。
3. **按钮统一扁平化**：**取消 `bd≥2` 浮雕**（[`_btn`](../src/utils.py#L316) 、[`_tbtn`](../src/ACRPA.py#L3600) ，以及 [`dialogs.py`](../src/dialogs.py) / [`help_window.py`](../src/help_window.py) / [`netlink_window.py`](../src/netlink_window.py) 各处 `bd=2/3`）；一律 `relief="flat"` + 1px `border_strong` 描边 + 高度取 `ctrl_h()`。primary 用 `ac` 实心 + `ach` hover；次级用 `surface_alt` 底 + `border_strong` 描边。
4. **行高定值**：表格 / 列表行高**唯一取 `row_h=22`**，消除 [`utils.py:389`](../src/utils.py#L389)（22）与 [`utils.py:429`](../src/utils.py#L429)（24）自相矛盾 —— 以 `TOKENS["row_h"]=22` 为准。
5. **边框形态收敛为 3 种（`bd=1` 边界裁决）**：`bd=1` **合规、保留**（1px 即 §1.5「发丝线」的物理实现），但须语义化：① `bd=1` = 发丝描边（既有大量按钮/输入即此形，如 [`market_window.py:248`](../src/market_window.py#L248)、[`settings_window.py:1020`](../src/settings_window.py#L1020)）；② `bd=0` = 无边框（tertiary / 图标按钮）；③ **`bd≥2` = 一律禁止**（浮雕/厚边，须改 `flat`）。**唯一合法边框集合 = {`bd=0`, `bd=1`, `1px highlightthickness`}**；全项目**不再出现 `bd=2/3`**。`bd=1` 的原生 `tk.Button` 可保留 `relief="solid"` 以示可点击，但**不得**与 `bd≥2` 叠加。

---

## 4. 组件规范（逐项）

### 4.1 ① 标题栏
- 高 28–32；左：16px 应用图标 + 标题（Semibold 10）。右：状态胶囊 + 图标按钮组。
- 图标按钮 **28×28 命中区**，hover 填充 `caption_hover`（浅 `#E9E9E9` / 深 `#333`）；`⊟ 折叠` 与「关闭」类可 hover 变红。
- 底部**1px 发丝线**（`bd` 色）替代现有 2px 强调条（更「经典 Windows」）；**已落地**：实际实现即 1px `bd` 发丝线（**非** 2px `ac` 强调条）。如需品牌感，仅在标题左侧加 3px 强调竖条。

### 4.2 ② 执行控制工具栏（命令栏）
- 高度 30，背景 `#FAFAFA`（`surface_alt`），底部 1px 发丝线。
- **分组 + 1px 竖分隔**：`脚本区 │ 参数区 │ 运行四键 │ 调试区`。组内控件 24 高。
- 主操作 `▶ 运行` = 强调实心；`⏸/⏭/■` = 次级扁平，hover 浅填充；禁用 = `fgd` + 无边框。
- 字段用「内嵌 + 标签前置」：`脚本:` 灰标签 + 白底 1px 边框控件。

### 4.3 ③ Tab 选项卡 —— V3

> 落点：选中蓝字 [`utils.py:385`](../src/utils.py#L385)（`foreground=[("selected", C["ac"])]`，**须删**）；页签 padding [`utils.py:380`](../src/utils.py#L380)（现 `(20,6)` → 改 `(16,6)`）。

- 经典矩形页签：未选 `surface_alt`(`#FAFAFA`)（深 `#2A2A2A`）、选中 = 内容面白 + 上/左/右 1px 边框 + 底部融入内容。
- **去掉「选中改蓝字」**，实现为**选中加粗（`FONT_BUTTON`）+ 字色 `fgt`（去蓝字）+ 强调色描边**（`bordercolor/lightcolor/darkcolor` 映射 `selected→ac`）（现代 Explorer 语汇，但仍是经典结构）。
- **⚠ 降级说明（实现对齐）**：原「选中**顶部 2px `ac` 强调条**」为**降级项**——Tk `clam` 的 `Notebook.tab` **无原生「顶部条」元素**，无法绘制顶部 2px 条。实际实现改以**选中加粗 + 去蓝字 + 强调色描边**承载选中态。**验收措辞须改为「选中加粗 + 强调描边（去蓝字）」，不得按「顶部 2px 条」判定**（见 [§10](#10-问题--规范--代码落点--验收v3v7-对照表) V3）。
- Tab 内边距 `(16,6)`，两 Tab 等宽或内容自适应。

### 4.4 ④ Tab 工具栏（命令栏，同 4.2 规范）
- 分组：`文件▾ │ ＋−↑↓清空 │ 模板 AI▾ │ ●录制 ⊕取点 │ 调试▾ ⋯`。
- 「▾ 下拉组」= 工具条内的 Menubutton（经典拆分按钮）；`⋯` = 溢出菜单。

### 4.5 ⑤ 数据表（经典 DataGrid / ListView）—— B2

> 落点：表头 [`utils.py:432-434`](../src/utils.py#L432)（现 `ac`/`white`，**须改灰底深灰字**；注意 [`utils.py:393-395`](../src/utils.py#L393) **先设的是白底 `bgc`（非灰底）**，其后被 432-434 覆写为蓝底白字）；行高 [`utils.py:389`](../src/utils.py#L389)（22）与 [`utils.py:429`](../src/utils.py#L429)（24）**冲突，统一 `row_h=22`**；zebra 现错误使用 `acl` [`ACRPA.py:2004`](../src/ACRPA.py#L2004)/[`2205`](../src/ACRPA.py#L2205)/[`2588-2590`](../src/ACRPA.py#L2588)；选中无左缘 [`utils.py:390-392`](../src/utils.py#L390)；表头/列定义 [`ACRPA.py:2169-2173`](../src/ACRPA.py#L2169)（`show="tree headings"`，即**存在 `#0` 列**）。

- **表头（去蓝底白字）**：`surface_alt`(`#FAFAFA`) 底 + 1px `bd` 下边框 + `FONT_SMALL_BOLD` `#333` 字；hover → `heading_hover`(`#F0F0F0`)；可排序显 ▲▼（Segoe UI Symbol）。
- **行高**：统一 `row_h=22`（唯一定值，见 §3.2）。
- **行号沟（gutter）**：`gutter_bg`(`#F6F6F6`) 独立列底色 —— **不可行/不适用**（见下方「不可行说明」）；行号区仍以文本列承载断点 `●`(U+25CF) / 当前行 `◀`(U+25C0)，`gutter_bg` token **保留备用**。
- **竖向网格线**：`gridline`(`#EEEEEE`) —— **不可行/不适用**（见下方「不可行说明」）；`gridline` token **保留备用**。

> **不可行说明（实测结论，Tk 8.6）**：`ttk.Treeview` 的 `layout('Treeview')` = `field → padding → treearea`，整行仅**一个 `Treearea` 元素**，且 `style.element_options('Treeitem')` 为**空元组** → **无 per-cell / per-column 边框能力**，无法实现「竖网格线 `gridline`」与「按列着色的行号沟 `gutter_bg`」。**替代手段**：灰底表头 + 1px `bd` 下边框 + 行高 `row_h=22` + zebra(`#FAFAFA`) 表达列/行分隔。**验收时「竖网格线」「行号沟独立底色」两项判为「不可行/不适用」**（见 [§10](#10-问题--规范--代码落点--验收v3v7-对照表) V5）。
- **斑马纹**：`zebra`(`#FAFAFA`) —— **不得再用 `acl`**（`acl` 专属于选中态）。
- **选中**：整行 `acl`(`#CCE4F7`) 底 + **左缘 2px `ac` 竖条**（经典「当前项」提示）；字色 `fgt`；单元格编辑态 = 内嵌 1px `ac` 边框。
- **叠加优先级（ttk `style.map` 行为，强制）**：ttk `Treeview` 的 **`selected` 状态优先于 item tag 背景**，且 ttk **无内建行 hover 状态**。故明确定义优先级（高 → 低）：**`selected`(`acl`) > `hover`(`row_hover`) > `zebra`(`even`→`zebra`) > 默认(`bgc`)**。实现：`zebra` 用 item tag（[`ACRPA.py:2205`](../src/ACRPA.py#L2205)）；`hover` 用自绑 `<Motion>/<Leave>` 的 `row_hover` tag，并在 `<<TreeviewSelect>>` 后对选中行**移除** `row_hover`（确保 `selected` 不被 hover 冲淡）；左缘 2px `ac` 条**独立于底色**，仅随 `selected` 出现。
- **选中左缘 2px `ac` 竖条 — 可落地主方案（`#0` 树列 per-item `image`）**：ttk `Treeview` **无 per-row 边框能力**，故用 `#0` 列：每项可挂 `image`（渲染在行号文本**左侧**，即最左缘）。机制：构造 2px 宽 × `row_h` 高的 `tkinter.PhotoImage`（填充 `ac`），选中变更时 `tree.item(item, image=bar)`、取消时 `image=""`。因 `#0` `minwidth=32`（[`ACRPA.py:2169`](../src/ACRPA.py#L2169)），2px 图 + 行号文本可共存，**且不干扰 `#0` 的断点点击**（[`ACRPA.py:2209`](../src/ACRPA.py#L2209)）。代码骨架：

```python
_bar_ac = None                       # 2px 竖条 PhotoImage（主题/DPI 变更时重建）
def _sel_bar(tree, item, on):
    tree.item(item, image=(_bar_ac if on else ""))
def _bind_sel_bar(tree):
    def _on(_e):
        for it in tree.get_children(""):
            _sel_bar(tree, it, False)
        for it in tree.selection():
            _sel_bar(tree, it, True)
    tree.bind("<<TreeviewSelect>>", _on, add=True)
```

- **前提与降级规则（不得留空条款）**：主方案**仅当 `show` 含 `"tree"`（存在 `#0` 列）时可用**。适用：主脚本树 [`ACRPA.py:2167`](../src/ACRPA.py#L2167)（`show="tree headings"`）、工作流步骤树 [`ACRPA.py:4566`](../src/ACRPA.py#L4566)（`show="tree headings"`）、操作库分类树 [`ACRPA.py:4538`](../src/ACRPA.py#L4538)（`show="tree"`）。**`show="headings"`（无 `#0` 列）无法挂贴图 → 明确降级为「选中行整行 `acl` 底 + 加粗（`FONT_SMALL_BOLD`）」**（ttk `style.map` 只能整行着色）。降级落点：片段树 [`ACRPA.py:2896`](../src/ACRPA.py#L2896)、变量树 [`3300`](../src/ACRPA.py#L3300)/[`5673`](../src/ACRPA.py#L5673)、耗时树 [`3463`](../src/ACRPA.py#L3463)、版本历史 [`dialogs.py:334`](../src/dialogs.py#L334)、调度管理/调度日志 [`dialogs.py:1029`](../src/dialogs.py#L1029)/[`1104`](../src/dialogs.py#L1104)、netlink 5 树（[`netlink_window.py:541`](../src/netlink_window.py#L541) 等）。**对验收的影响**：验收项「选中左缘 2px」在 `show="headings"` 树上**不适用**，改验收「选中行 `acl` 底 + 加粗」；主脚本编辑树保留「左缘 2px」**强验收**。
- **命令列色点**：代码中命令表图例色点已是**单色 `●`(U+25CF)**（[`ACRPA.py:2199`](../src/ACRPA.py#L2199)）—— **无需替换**；原稿所述「Emoji 色点（🔵🟢）」为**幻影**（全库无命中，见 [§3.1](#31-emoji--segoe-ui-symbol-映射表v7-裁决)），该条指令作废。

### 4.6 ⑥ 日志面板（经典控制台）—— B2

> 落点：级别前景 tag [`ACRPA.py:4131-4134`](../src/ACRPA.py#L4131)；**级别过滤下拉** [`ACRPA.py:4096`](../src/ACRPA.py#L4096)；**过滤 tag 集合** [`ACRPA.py:4209`](../src/ACRPA.py#L4209)；硬编码命中色 [`ACRPA.py:4136`](../src/ACRPA.py#L4136)（`#FFEB3B`，**须改 token**）；行生成 [`utils.py:579-587`](../src/utils.py#L579)（现仅 `WARNING+` 加级别前缀，**须四级都加徽标且由 tag 判定**）；面板容器/头栏 [`ACRPA.py:4041-4089`](../src/ACRPA.py#L4041)；工具条 [`ACRPA.py:4092-4116`](../src/ACRPA.py#L4092)；`rz` Text [`ACRPA.py:4122-4124`](../src/ACRPA.py#L4122)；级联过滤 [`ACRPA.py:4209-4254`](../src/ACRPA.py#L4209)。

- **头栏** = 面板标题条：`surface_alt` 底 + 1px `bd` 下边框 + 小标题 + 右对齐工具（级别/搜索/清空/导出/收起）。
- **每一行** = **级别左缘 2px 色条** + **灰阶徽标**，正文保持中性 `fgb`（不做整行染色，长日志更耐看）：

| 徽标文案 | tag 名 | 左缘色条 token | 徽标/正文 token |
| --- | --- | --- | --- |
| `[INFO]` | `info` | `log_info` = `#8A8886`（中性灰） | `fgm`(`#5F5F5F`) |
| `[SUCCESS]` | `success` | `log_success` = `#107C10`(`sc`) | `sc` |
| `[WARNING]` | `warning` | `log_warn` = `#F7630C` | `wn`(`#9D5D00` 文本 / `#F7630C` 面) |
| `[ERROR]` | `error` | `log_error` = `#C42B1C` | `dg`(`#C42B1C`) |

> **四级集合、徽标文案与 tag 映射的唯一权威见 [§2.5](#25-日志级别语义四级权威c1c2-收敛)**：徽标 = `INFO/SUCCESS/WARNING/ERROR`（**删除 `SUGGEST`**），与过滤下拉项、tag 名一一对应；行生成**按 tag 判定**（`tag="success"` 的 int 级别实为 `LOG_INFO`，但徽标须显示 `[SUCCESS]`）。左缘色条 tag 名 = `cbar_info / cbar_success / cbar_warning / cbar_error`。

- **行生成**：`▌[HH:MM:SS] [LEVEL] msg` —— 四级**均**带 `[LEVEL]` 徽标（现 [`utils.py:583-584`](../src/utils.py#L583) 仅 `WARNING+`，须补 `INFO/SUCCESS`；**级别由 tag 判定**，见 [§2.5](#25-日志级别语义四级权威c1c2-收敛)）。**左缘色条实现方式（已落地）**：`Text` **无像素边框能力**，故以**行首字符 `▌`(U+258C) + `cbar_<tag>` tag 的**前景色**承载（**非像素边框**）；`tag_add("cbar_<tag>")` 给该窄形字符上色。
- **行 zebra** 极淡（`zebra`）。
- **搜索命中**：token `search_hit_bg` = `#FFF4CE`（即 `hlbg`，**替换硬编码 `#FFEB3B`**）。**实现对齐：仅底色高亮，无 `ac` 描边** —— 原「`search_hit_border` = `ac` 1px 描边」**未落地**，`search_hit_border` token **保留备用**。

### 4.7 ⑦ 状态栏（经典分段）
- 顶 1px 下沉发丝线；底 `#F3F3F3`。
- **分段字段 + 1px 竖分隔**：`● 状态·行数 │ 循环/行/耗时 │ 进度条 │ ⏱调度 │ ⊕互联`；数值右对齐、等宽数字（图标均 `Segoe UI Symbol`，见 §3.1）。
- 进度条内嵌（10px，`sc` 完成色）。

### 4.8 控件微观
- **按钮**：高取 `ctrl_h()`（24/28）；**取消 `bd=3` 浮雕**，改 `relief="flat"` + 1px `border_strong` 描边 [§3.2]；hover 底色 `caption_hover`(`#E9E9E9`)+描边 `border_strong`（[§4.9.2](#492-hover-状态不得仅变暗)）；pressed 更深；primary 强调实心；default（回车默认键）加 1px `ac` 强调描边。
- **输入/下拉/微调**：白底 + 1px `#ACACAC` 边框 + 内边距 6；**focus = 2px `ac` 环，内缩 2px**（替换 3px 浮雕，见 [§4.9.1](#491-focus-visible-可视环)）。
- **滚动条**：经典 12px，带两端箭头，滑块 `#CDCDCD` hover 加深（clam 可自绘）。
- **sash（PanedWindow 分隔）**：4px，`#E1E1E1`，中心 3 个抓手点；hover 强调色。
- **tooltip**：经典浅黄 `#FFFFE1` + 1px `#646464` + 无边距文字（**强 Windows 记忆点**）。**已落地为统一 token**：`tooltip_bg`(`#FFFFE1`) / `tooltip_border`(`#646464`)，全窗 tooltip 一律消费该对 token（[§2.3](#23-windows-血统感的三条锚点)）。
- **菜单/下拉**：白面 + 1px 边框 + 24px 项高 + hover `acl` + 分隔线。
- **焦点环**：见 [§4.9](#49-焦点与悬停状态规范v6强制) —— `focus-visible` **2px `ac` 环，内缩 2px**（鼠标态隐藏，键盘态显示）。

### 4.9 焦点与悬停状态规范（V6，强制）

#### 4.9.1 focus-visible 可视环

- **键盘态显示、鼠标态隐藏**：全局标志位区分（`Tab`/方向键/`Alt` 置 `kb_mode=True`，鼠标 `<Button-1>` 置 `False`），避免点击控件时到处亮环（现状全项目 `focus` 无可见环）。
- 颜色 `C["focus"]`；宽度 `focus_w=2px`；**内缩 2px 贴合控件内缘**（不撑大布局）。
- **token 显式取值（不再靠 `ac` 推导）**：浅色 `focus = #0078D4`（[§2.1](#21-语义色浅色)）；**深色 `focus = #4CC2FF`**（[§2.2](#22-语义色深色)，即深色 `ac`）。深色下**不得**沿用浅色 `#0078D4`（在 `#202020` 底上对比不足）。
- 覆盖范围与实现：

| 控件 | 实现落点 |
| --- | --- |
| ttk Button / Action / Success / Danger / Warning | 取消 `focuscolor="none"`（现 [`utils.py:415/419/422/425`](../src/utils.py#L415)）→ `style.map(..., focuscolor=[("focus", C["focus"])], bordercolor=[("focus", C["focus"])])` + `borderwidth=1` |
| ttk Entry / Combobox / Spinbox | `style.map("TEntry"/"TCombobox", bordercolor=[("focus", C["focus"])], lightcolor=[("focus", C["focus"])], borderwidth=1)` |
| ttk Treeview | `style.map("Treeview", bordercolor=[("focus", C["focus"])])`；行选中仍用 `acl` + 左缘条 |
| 原生 `tkinter.Button`/`Checkbutton`/`Radiobutton` | `highlightthickness=2, highlightcolor=C["focus"], highlightbackground=C["bd"], takefocus=True` |
| 设置窗口全部控件 | 同上（[`settings_window.py`](../src/settings_window.py) 现无统一焦点态） |

#### 4.9.2 hover 状态（不得仅变暗）

> 现实现口径**不统一**：`_btn` / `_tbtn` 仅 `_darken()` 变暗（[`utils.py:323-326`](../src/utils.py#L323)、[`ACRPA.py:3608`](../src/ACRPA.py#L3608)）；而设置窗口导航项在 `<Enter>` 时**直接置底 `bg=C["acl"]` 高亮**（[`settings_window.py:523-526`](../src/settings_window.py#L523)，**并非 `_darken()` 变暗、也无描边**）→ 三处不一致，**违规**，须按下表统一为「底色 + 描边」双变化。

| 控件 | hover 底色 | hover 描边 | 备注 |
| --- | --- | --- | --- |
| 中性底按钮（secondary） | `caption_hover`(`#E9E9E9`) | `border_strong`(`#C8C8C8`) | 替换「变暗」 |
| 强调色底按钮（primary） | `ach`(`#106EBE`) | 无 | 保留实心 |
| 语义实心按钮（Success/Danger/Warning） | 自加深（`_darken` 本体底色） | 无 | **已落地**：按语义色自加深，**不套 `border_strong`** |
| tertiary / 图标按钮 | `caption_hover` | 无 | 28×28 命中区 |
| Tab 未选 | `caption_hover` | — | |
| Treeview 行 | `row_hover`（浅 `#F3F3F3` / 深 `#2A2A2A`） | — | 与 zebra 区分 |
| sash / 滚动条滑块 | 滑块 `#CDCDCD` → 加深 | sash → `ac` | |

新增 token（`_colors()` 内，浅/深各一）：`caption_hover`（浅 `#E9E9E9` / 深 `#333333`）、`row_hover`（浅 `#F3F3F3` / 深 `#2A2A2A`）。

> **口径校准（避免「一律 `border_strong`」的歧义）**：**中性底**按钮（secondary / tertiary / 图标 / Tab 未选）hover = 底色 `caption_hover` + 描边 `border_strong`；**强调色底**按钮（primary）= 底色 `ach`、**无描边**；**语义实心**按钮（Success/Danger/Warning）= 底色**自加深**（`_darken` 本体底色）、**无描边**。三者互不相同，验收时分别判定。

---

## 5. 全局草图

### 5.1 浅色（经典 Windows）

> 注：§5.1 / §5.2 为**结构示意**；图中徽标 `[INFO ]` / `[WARN ]` 系旧稿简写，**日志级别徽标一律以 [§2.5](#25-日志级别语义四级权威c1c2-收敛) 的 `[INFO]/[SUCCESS]/[WARNING]/[ERROR]` 为准**。

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ ▣ A/C RPA Automation Workflow                          ●就绪   ⚙  ⊕  △ ◐ ⊟ │ ← 标题栏(28) + 发丝线
├────────────────────────────────────────────────────────────────────────────┤
│ 脚本:[recent.xls ▾][选择]  │ 次数:[∞▾] 最长:[0]分 ☐出错即停 │ ▶运行 ⏸ ⏭ ■ │ ← 命令栏(30, 分组)
│                            │                              │ ⚑调试 ☰变量    │
├────────────────────────────────────────────────────────────────────────────┤
│ ┏ 脚本编辑 ┓  工作流                                                        │ ← 经典页签
│ ┌──────────────────────────────────────────────────────────────────────────┐│
│ │ 文件▾ │ ＋ − ↑ ↓ 清空 │ 模板 AI▾ │ ●录制 ⊕取点 │ 调试▾ ⋯                ││
│ ├────┬───────────┬──────────┬────────┬──────────┬────────────┬────────────┤│
│ │ ␣# │ 命令 ⌄     │ 参数1     │ 参数2  │ 参数3     │ 参数4       │ 参数5      ││ ← 表头(灰底+下边框)
│ ╞════╪═══════════╪══════════╪════════╪══════════╪════════════╪════════════╡│
│ │ ●1 │ ▸ 找图     │ btn.png  │ 0.96   │           │            │            ││
│ │  2 │ ▸ 点图     │ sub.png  │ 0.90   │           │            │            ││ ← 斑马纹
│ ┃◀3 ┃ ▸ 找图     │ login.png│ 0.98   │           │            │            ││ ← 整行选中+左缘2px
│ │ #4 │ 注释       │ 检测登录 │        │           │            │            ││ ← 注释灰显
│ └────┴───────────┴──────────┴────────┴──────────┴────────────┴────────────┘│
│ ╞════════════════════ sash（4px + 抓手点）══════════════════════════════════╡│
│ ┌ 运行日志 ────────────────────────────────────────── 自动滚动☑   ▾收起 ───┐│
│ │ [全部▾] [信息][警告][错误]  ⊙______  清空  导出                          ││
│ │▌14:32:01 [INFO ] 找图成功 btn.png (0.98)  行1                             ││ ← 左缘色条
│ │▌14:32:03 [WARN ] 图像未找到 login.png (0.98)  行3                         ││
│ └──────────────────────────────────────────────────────────────────────────┘│
├────────────────────────────────────────────────────────────────────────────┤
│ ●就绪·12行 │ 循环 1/∞ · 行 5/12 · 42s │ ▓▓▓▓▓▓░░░░ 32% │ ⏱18:00 │ ⊕2台   │ ← 分段状态栏
└────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 深色（同结构，`#202020` chrome）

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ ▣ A/C RPA Automation Workflow                          ●就绪   ⚙  ⊕  △ ◐ ⊟ │  底 #202020 / 面 #2B2B2B
│ 脚本:[recent.xls ▾][选择]  │ 次数:[∞▾] 最长:[0]分 ☐出错即停 │ ▶运行 ⏸ ⏭ ■ │  强调 #4CC2FF
│ ┌ 运行日志 ────────────────────────────────────────── 自动滚动☑   ▾收起 ───┐│
│ │▌亮色徽标 [INFO]/[WARN]/[ERROR]，正文 #E0E0E0                            ││
│ └──────────────────────────────────────────────────────────────────────────┘│
│ ●就绪 │ 循环 1/∞ · 行 5/12 · 42s │ ▓▓▓▓░░ 32% │ ⏱18:00 │ ⊕2台            │  选中底 #094771
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 实现方案（最小侵入、向后兼容）

### 6.1 令牌层（改动 80%，风险最低）

1. 扩展 [`_colors()`](../src/utils.py#L108)：按 §2 换值 + **新增键**（`surface_alt / border_strong / fgd / gridline / gutter_bg / tooltip_bg / tooltip_border / caption_hover / row_hover / zebra / heading_hover / acp / log_info / log_success / log_warn / log_error / search_hit_bg / search_hit_border`），`C` 仍为 `dict`，**旧键全部保留**。（`log_success` 取代原稿的 `log_suggest`；见 [§2.5](#25-日志级别语义四级权威c1c2-收敛)）
2. 扩展 [`TOKENS`](../src/utils.py#L731)：`ctrl_h=24`（原 26）、`ctrl_h_sm=22`、`ctrl_h_lg=28`（原 30）、`row_h=22`、**`bar_h=4`（保留原值，布局约束——被市场窗口分类色条消费，见 [§3](#3-字体与尺度)）**、`hairline=1`、`focus_w=2`、`sash=4`。**进度条高 10 以字面量 `thickness` 表达，不占用 `bar_h` token。**
3. 新增小工具：`hairline(parent, orient)`、`cbar(parent, lvl)`（日志左缘色条 tag）、`severity_glyph(lvl)`（**Segoe UI Symbol** 字形映射，§3.1）、`zebra_tags(tree)`、`focus_ring(widget)`（统一 2px `ac` 环）。

### 6.2 样式层（重写 `apply_theme()`）——签名示意

```python
def apply_theme(root_widget, style):
    global C; C = _colors()
    root_widget.configure(bg=C["bg"])

    # 页签: 经典矩形 + 选中面 + 顶部强调条
    style.configure("TNotebook", background=C["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", font=FONT_BODY, padding=(16, 6),
                    background=C["surface_alt"], foreground=C["fgm"],
                    borderwidth=1)
    style.map("TNotebook.Tab",
              font=[("selected", FONT_BUTTON)],
              background=[("selected", C["bgc"]), ("active", C["caption_hover"])],
              foreground=[("selected", C["fgt"])])

    # 数据表: 灰底表头 + 下边框(去蓝底) + 行高 22 + 选中'整行+左缘'
    style.configure("Treeview", font=FONT_BODY, rowheight=TOKENS["row_h"],
                    background=C["bgc"], fieldbackground=C["bgc"],
                    foreground=C["fgb"], borderwidth=0)
    style.configure("Treeview.Heading", font=FONT_SMALL_BOLD,
                    background=C["surface_alt"], foreground=C["fgb"],
                    relief="flat", borderwidth=0, padding=(8, 4))
    style.map("Treeview", background=[("selected", C["acl"])],
              foreground=[("selected", C["fgt"])])

    # 按钮: 统一 1px 描边 + 三态; primary 强调实心
    style.configure("TButton", font=FONT_BUTTON, padding=(10, 4),
                    background=C["surface_alt"], foreground=C["fgb"],
                    borderwidth=1, relief="flat", focuscolor=C["focus"])
    style.map("TButton",
              background=[("pressed", C["acl"]), ("active", C["caption_hover"]),
                          ("disabled", C["bgc"])],
              foreground=[("disabled", C["fgd"])])
    # Action/Success/Danger/Warning 变体: 实心 + hover 用 ach/sc/...

    # 输入类: 1px #ACACAC + focus 强调环
    style.configure("TEntry", fieldbackground=C["ebg"], foreground=C["fgb"],
                    borderwidth=1, relief="solid")
    style.configure("TCombobox", fieldbackground=C["ebg"], borderwidth=1)
    style.map("TCombobox", bordercolor=[("focus", C["focus"])])

    # 进度条加高到 10
    style.configure("Exec.Horizontal.TProgressbar", thickness=10,
                    background=C["sc"], troughcolor=C["acl"], borderwidth=0)
```

> `clam` 支持 `bordercolor`/`lightcolor`/`darkcolor`/`arrowcolor` 等元素选项，可把输入框/滚动条/下拉做成经典外观；圆角在 clam 下需自绘（**建议放弃圆角，走经典直角**，正好符合「经典 Windows」）。

### 6.3 组件层（局部改写，不动布局）

| 目标（问题号） | 落点（file:line） |
| --- | --- |
| 表头去蓝底白字（V3/V5） | [`utils.py:432-434`](../src/utils.py#L432)（删 `ac`/`white`；保留 [`393-395`](../src/utils.py#L393) 先设的**白底 `bgc` + `flat`**） |
| Tab 选中去蓝字（V3） | [`utils.py:385`](../src/utils.py#L385)；padding [`utils.py:380`](../src/utils.py#L380) |
| 按钮扁平化 / 去 `bd≥2`（V4） | [`utils.py:327-330`](../src/utils.py#L327)（`_btn`）、[`ACRPA.py:3609-3611`](../src/ACRPA.py#L3609)（`_tbtn`）、[`ACRPA.py:3909`](../src/ACRPA.py#L3909)/[`3968-3999`](../src/ACRPA.py#L3968)/[`4017-4023`](../src/ACRPA.py#L4017)、**[`ACRPA.py:2759`](../src/ACRPA.py#L2759)/[`2858-2865`](../src/ACRPA.py#L2858)/[`3007-3013`](../src/ACRPA.py#L3007)**、**Mini Bar [`686`](../src/ACRPA.py#L686)/[`702`](../src/ACRPA.py#L702)（`_MB_BTN_BD`）**、**日志工具条 [`4083-4085`](../src/ACRPA.py#L4083)/[`4105-4116`](../src/ACRPA.py#L4105)**、[`dialogs.py:212/230/489/529/541/549/813/909/914`](../src/dialogs.py#L212)、[`help_window.py:272/278`](../src/help_window.py#L272)、[`netlink_window.py:478/489/525/530/574/586/634/641/666/689/701/1928/2373/2996/3286`](../src/netlink_window.py#L478)（**全篇 `bd=2`**） |
| 设置窗口尺度收口（V4） | [`settings_window.py`](../src/settings_window.py) 全文（现 0 处用 `ctrl_h/TOKENS`） |
| focus 可视环（V6） | 按钮 [`utils.py:415/419/422/425`](../src/utils.py#L415)（去 `focuscolor="none"`）；输入/下拉 [`utils.py:398-401`](../src/utils.py#L398) |
| hover 非仅变暗（V6） | [`utils.py:323-326`](../src/utils.py#L323)、[`ACRPA.py:3608`](../src/ACRPA.py#L3608)（`_darken()` 变暗）；[`settings_window.py:523-526`](../src/settings_window.py#L523)（**`<Enter>` 置 `bg=C["acl"]` 高亮，非变暗**） |
| 表格行高 / zebra / 选中左缘（V5） | 行高 [`utils.py:389`](../src/utils.py#L389) 与 [`429`](../src/utils.py#L429)（统一 22）；zebra [`ACRPA.py:2004`](../src/ACRPA.py#L2004)/[`2205`](../src/ACRPA.py#L2205)/[`2588-2590`](../src/ACRPA.py#L2588)；选中左缘 [`utils.py:390-392`](../src/utils.py#L390) + **主方案/降级见 [§4.5](#45--数据表经典-datagrid--listview--b2)**；表头/列定义 [`ACRPA.py:2169-2173`](../src/ACRPA.py#L2169) |
| 日志色条 / 徽标 / 命中色（V5） | 级别 tag [`ACRPA.py:4131-4134`](../src/ACRPA.py#L4131)；级别下拉 [`4096`](../src/ACRPA.py#L4096)；过滤 tag 集合 [`4209`](../src/ACRPA.py#L4209)；命中色 [`4136`](../src/ACRPA.py#L4136)；行生成 [`utils.py:579-587`](../src/utils.py#L579)；级联过滤 [`ACRPA.py:4209-4254`](../src/ACRPA.py#L4209) |
| 图标字体统一 / Emoji 清除（V7） | 字体 [`utils.py:172-174`](../src/utils.py#L172)；Emoji 清单 [`settings_window.py:405-416`](../src/settings_window.py#L405)、卡标题 [`731/855/1196/1289/1371/1419/1744/1803/2086/2471/2579`](../src/settings_window.py#L731)；[`ACRPA.py:4527`](../src/ACRPA.py#L4527)/[`4609-4611`](../src/ACRPA.py#L4609)/[`4624-4625`](../src/ACRPA.py#L4624)/[`4730`](../src/ACRPA.py#L4730)/[`4886`](../src/ACRPA.py#L4886)/[`5050`](../src/ACRPA.py#L5050)/[`1845`](../src/ACRPA.py#L1845)（🌐，且**硬编码字体须改 `FONT_ICON_MD`**，违反规则④）；`engine.py` [`998/1005/1164/1206`](../src/engine.py#L998)；`dd_backend.py` [`72/87/89/92`](../src/dd_backend.py#L72)（含 **VS16**）；`dialogs.py` [`597/723/1253/1258/1344`](../src/dialogs.py#L597)；`netlink_window.py` [`571/583/676/686/698`](../src/netlink_window.py#L571)；`templates.py` [`239/240`](../src/templates.py#L239)；toast [`utils.py:63`](../src/utils.py#L63)；[`market_window.py:60`](../src/market_window.py#L60)（💰🧩） |
| 标题栏发丝线 + 图标按钮 hover | [`ACRPA.py:1792`](../src/ACRPA.py#L1792)（现 2px 强调条，须改 1px 发丝线）、[`1787`](../src/ACRPA.py#L1787) 右簇 |
| 命令栏分组/分隔/hover | [`_tbtn`](../src/ACRPA.py#L3600) 与 [`_sep`](../src/ACRPA.py#L3223) 统一为「组 + 1px 竖线」 |
| 状态栏分段 | [`status_bar`](../src/ACRPA.py#L5781) 加 1px 竖分隔 Frame；进度 [`5798`](../src/ACRPA.py#L5798) 加高 |
| tooltip 经典浅黄 | [`utils` tooltip](../src/utils.py#L359) + [`ACRPA._show_*_tip`](../src/ACRPA.py#L1757) 统一样式 |

### 6.4 兼容与回归

- **不删键、不改控件语义、不动布局**；`_refresh_theme` 的遍历刷新机制保持不变（[`ACRPA.py:2018`](../src/ACRPA.py#L2018)）。
- 主题切换后 tag 颜色需按新键重刷（已在 `_refresh_theme` 内有 `rz/tree` tag 刷新，扩展新 tag 即可）。
- DPI/缩放全部走 `scaled()`/`fit_pt()`，禁止硬编码像素。

---

## 7. 分阶段实施计划

### B0 —— 令牌 + 样式重构（视觉 ROI 最高，零布局改动）—— V1/V2/V3
1. `_colors()` 换值 + 新增键（§2.1/§2.2/§2.4）。
2. `TOKENS` 收紧（§3）：`ctrl_h=24`、`ctrl_h_lg=28`、`row_h=22`、`focus_w=2`、**`bar_h=4`（保留，布局约束，不改为 10）**。
3. `apply_theme()` 重写：Tab（去蓝字 + 顶条）、表头（去蓝底白字）、按钮（扁平 + 三态）、输入（focus 环）、进度（§6.2）。
4. tooltip 经典浅黄统一。
**验收**：深浅两主题下主窗明显更「系统」；无控件错位；缩放档位不破。

### B1 —— 外观骨架（chrome）—— V3/V4
5. 标题栏发丝线 + 图标按钮 hover（28×28）+ 去 `bd=3`。
6. 命令栏分组/竖分隔/hover（非变暗）；主操作实心 `ac`，次级扁平。
7. 状态栏分段 + 1px 竖分隔；进度加高 10。
8. Tab 页签经典化（选中加粗 + 顶部 2px `ac` 条，去蓝字）。
9. 全窗控件高度统一消费 `TOKENS`/`ctrl_h()`（**含设置窗口**）。

### B2 —— 数据视图 —— V5
10. 表头去蓝底、灰底深灰字 + 下边框 + 排序箭头。
11. 行高统一 22；行号沟 + 断点/当前行标记；**zebra=`zebra`(`#FAFAFA`)，去 `acl`**；竖网格线（可开关）；选中整行 + 左缘 2px `ac`。
12. 日志控制台：**四级**左缘色条 + 灰阶徽标 `[INFO]/[SUCCESS]/[WARNING]/[ERROR]`（[§2.5](#25-日志级别语义四级权威c1c2-收敛)，**删除 `SUGGEST`**）；命中高亮改 token（去 `#FFEB3B`）。

### B3 —— 微观与可达性 —— V6/V7
13. 滚动条/输入框经典外观；sash 抓手点。
14. `focus-visible` 2px `ac` 环（ttk + 原生控件全覆盖，含设置窗口）。
15. hover 全量改为「底色 + 描边」双变化。
16. **图标字体统一 `Segoe UI Symbol`**；Emoji → 单色字形全量替换（[§3.1](#31-emoji--segoe-ui-symbol-映射表v7-裁决) 映射表）。

---

## 8. 风险与规避

| 风险 | 规避 |
| --- | --- |
| clam 无原生圆角/阴影 | **顺势走经典直角 + 1px 层次**（本方案目标即经典，不追 Material/Fluent 2 阴影） |
| 换主色影响既有硬编码色 | 全项目只用 `C[...]` 与 `_darken`；禁止新增硬编码（现状已守约） |
| 主题切换后部分 tag 未刷新 | 复用 `_refresh_theme` 的 tag 刷新块，补全新 tag |
| 淡色高对比不足（警告黄） | 文字走 `#9D5D00`（浅）/`#FCE100`（深），面用小面积 |
| 图标字形在个别机器缺失 | 统一 `Segoe UI Symbol`（Win7+ 恒在）；缺字形回退 ASCII（`[*]/[+]/[!]`），**不回退 Emoji**（见 §3.1） |
| 缩放档位错位 | 一律 `scaled()/fit_pt()`，禁止 pt/px 硬编码 |

---

## 9. 建议与优先级（一句话）

1. **先做 B0**：只改 `_colors()` + `apply_theme()` + `TOKENS` 三处，就能把「网页感」拉回「Windows 系统感」——投入最小、观感提升最大、回归风险最低。
2. **V1/V3 强调色收敛**：一条蓝只给「主操作 / 选中左缘 / 焦点环 / 关键徽标」；**撤销**表头蓝底白字（[`utils.py:432-434`](../src/utils.py#L432)）、Tab 选中蓝字（[`utils.py:385`](../src/utils.py#L385)）。
3. **V7 图标统一（最高优先，用户明确要求）**：一律 `Segoe UI Symbol` 单色字形，**禁彩色 Emoji**（映射见 §3.1）。
4. **V4 尺度收口**：全窗（含设置窗口）消费 `TOKENS`/`ctrl_h()`；去 `bd=3` 浮雕；行高 22 唯一。
5. **V6 可达性**：`focus-visible` 2px `ac` 环 + hover「底色 + 描边」双变化。
6. **锚点三件套**：强调色 `#0078D4`、经典浅黄 tooltip、分段状态栏——最快建立「经典 Windows」辨识度。
7. **不追圆角阴影**：`clam` 下直角 + 层次就是最「经典桌面」的选择，也最省实现成本。

---

## 10. 问题 → 规范 → 代码落点 → 验收（V3–V7 对照表）

> 本表为**实现阶段的唯一检查清单**：每一行「验收」均应逐条勾选通过。

| # | 问题 | 规范条款 | 代码落点（file:line） | 验收（可勾选） |
| --- | --- | --- | --- | --- |
| V3 | 强调色滥用 | [§2.4](#24-强调色ac使用白名单v3强制裁决) + [§4.3](#43--tab-选项卡--v3) + [§4.5](#45--数据表经典-datagrid--listview--b2) | 表头 [`utils.py:432-434`](../src/utils.py#L432)；Tab [`utils.py:385`](../src/utils.py#L385)；按钮 [`utils.py:414-426`](../src/utils.py#L414) | ☐ 表头为灰底深灰字，无蓝底白字（**已实现**） ☐ Tab 选中**无蓝字**，为**加粗 + 强调描边**（**降级**：Tk clam `Notebook.tab` 无原生顶部条，原「顶部 2px `ac` 条」**不可行**，见 [§4.3](#43--tab-选项卡--v3)） ☐ 全窗仅主操作实心 `ac`（**已实现**） |
| V4 | 控件尺度不统一 | [§3](#3-字体与尺度) 基线 + [§3.2](#32-控件尺度规范v4强制) | [`TOKENS`](../src/utils.py#L731)；[`_btn`](../src/utils.py#L327)；[`_tbtn`](../src/ACRPA.py#L3609)；[`settings_window.py`](../src/settings_window.py) 全文 | ☐ 全窗高度取 `ctrl_h()` ☐ 无 `bd≥2` 浮雕按钮 ☐ 表格行高均 22 |
| V5 | 表格/日志不够数据软件 | [§4.5](#45--数据表经典-datagrid--listview--b2) + [§4.6](#46--日志面板经典控制台--b2) | 见 §4.5 / §4.6 落点行 | ☐ 灰底表头 + 1px `bd` 下边框（**已实现**） ☐ zebra `#FAFAFA`（非 `acl`）（**已实现**） ☐ **竖网格线 → 不可行/不适用**（ttk.Treeview 无 per-cell/per-column 边框，见 [§4.5](#45--数据表经典-datagrid--listview--b2)） ☐ **行号沟独立底色 → 不可行/不适用**（同上，`gutter_bg` 保留备用） ☐ 选中左缘 2px（主脚本树**已实现**；`show="headings"` 树**降级**为整行 `acl` + 加粗，见 [§4.5](#45--数据表经典-datagrid--listview--b2)） ☐ 日志四级徽标 + 左缘色条（**已实现**，行首 `▌` + `cbar_*` 前景色） ☐ 无 `#FFEB3B`（**已实现**，改 `hlbg`/`search_hit_bg`） |
| V6 | 缺焦点/悬停规范 | [§4.9](#49-焦点与悬停状态规范v6强制) | 按钮 [`utils.py:415/419/422/425`](../src/utils.py#L415)；hover [`utils.py:323-326`](../src/utils.py#L323) / [`ACRPA.py:3608`](../src/ACRPA.py#L3608) / [`settings_window.py:523-526`](../src/settings_window.py#L523) | ☐ 键盘 Tab 可见 2px `ac` 环（ttk + 原生 + 设置窗口） ☐ 鼠标点击不亮环 ☐ hover 有底色 + 描边变化 |
| V7 | 图标字体混杂 | [§3](#3-字体与尺度) 裁决 + [§3.1](#31-emoji--segoe-ui-symbol-映射表v7-裁决) | 字体 [`utils.py:172-174`](../src/utils.py#L172)；Emoji 清单 [`settings_window.py:405-416`](../src/settings_window.py#L405) 等 | ☐ 仅 `Segoe UI Symbol` ☐ 无彩色 Emoji ☐ 缺字形回退 ASCII |

> 关联文档同步：[`docs/设置窗口优化设计方案.md`](./设置窗口优化设计方案.md)（§5 视觉规范）、[`docs/底部常驻日志面板重构设计方案.md`](./底部常驻日志面板重构设计方案.md)（§6 日志面板）。三份文档 token 口径以本文件 §2/§3 为准。

> **实现状态图例（本表专用）**：`已实现` = 与代码一致；`降级` = 因 Tk/ttk 能力限制改用等效手段，验收按降级后措辞判定；`不可行/不适用` = 平台无对应能力，**该项不参与验收**（token 可保留备用）。当前：**降级** = V3 Tab 选中顶部条（改「加粗 + 强调描边」）、V5 选中左缘（`show="headings"` 树改「整行 `acl` + 加粗」）；**不可行/不适用** = V5 竖网格线 `gridline`、V5 行号沟 `gutter_bg`；**token 保留备用** = `gridline / gutter_bg / search_hit_border`；**bar_h 保留 4**（布局约束，非 10）。
