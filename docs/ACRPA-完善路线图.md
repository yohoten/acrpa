# ACRPA 完善路线图 —— 功能 · 软件设计 · UI 设计

> 版本基线：`v0.1.29-beta` · HEAD `9555be6`（"拆分 ACRPA.py 第一阶段：Mini Bar 出模块"）
> 代码规模：`src/` 53 个模块 / 约 35,500 行 · 6 个超 75 KB 的巨型模块
> 分析方式：静态通读 + AST 统计 + 实机执行验证。所有 P0 结论均已在源码中复核。

---

## 0. 结论先行

ACRPA 的**功能 breadth 已经相当完整**——68 条命令、OCR/浏览器/AI/NetLink/市场/调度齐全，且 `script_package`、`paddle_dll`、`netlink/security` 三个子系统的工程严谨度明显高于同类个人项目。

真正制约它从"能用的 beta"走向"可信的生产工具"的，不是功能数量，而是**四笔结构性欠账**：

| 欠账 | 表现 | 后果 |
|---|---|---|
| **① 确定性缺陷未收敛** | 找图缓存类型不一致、DD 驱动无视开关、预发布版本比较失效 | 旗舰命令（找图/点图）在缓存命中时必崩；用户显式关闭的内核驱动仍然加载 |
| **② 结构不可测试** | `import ACRPA` 即构建 `Tk()` 并启动定时器；6 个 75–245 KB 上帝模块 | 引擎控制流测试被 CI 误判跳过 → 缺陷可长期静默存在 |
| **③ 脚本格式无 schema** | 表头 `命令类型`/`操作` 并存、空值 `""` 与字面量 `"None"` 混用、四处硬编码 `range(2, nrows)` | 无迁移路径、静默丢首行、`"None"` 泄漏为真实参数 |
| **④ UI 是过程式单体** | ACRPA.py 5,738 行 / 0 个类 / 88 个模块级控件全局量 / 40 处 `global` | 每个改动都是全局编辑；主题靠"嗅探中文文案"反推语义色 |

**建议的投入顺序**：先修 ①（1–2 天，收益立竿见影），再做 ② 的第一刀（应用入口 + 事件总线，约 1 周，解锁后续所有重构），④ 按模块逐步拆（已有 Mini Bar 的成功先例），③ 与功能扩展并行推进。

---

## 1. 代码库体检基线

| 维度 | 实测值 | 评价 |
|---|---|---|
| GUI 框架 | tkinter + ttk（`clam` 主题），**100% 一致，零 Qt** | 选型统一，优点 |
| 命令总数 | 68（62 有 handler，6 个块标记） | 覆盖充分 |
| 脚本表模型 | `ttk.Treeview` 直接命令式操作，52 处；每次编辑全量 delete+reinsert | 无 model/view |
| 巨型模块 | ACRPA.py 245 KB · netlink_window.py 158 KB · settings_window.py 137 KB · browser_backend.py 105 KB · engine.py 85 KB · market_window.py 75 KB | 6 个主要障碍 |
| 最长单函数 | `open_settings_window` **2,336 行**（settings_window.py:465–2800） | 占该文件 83% |
| 宽泛异常捕获 | `src/` 共 **1,620** 处，其中 **722** 处函数体仅 `pass` | 失败与成功不可区分 |
| 硬编码中文串 | UI 层 **1,961** 处；i18n 机制 **零** | 不可本地化 |
| 类型注解覆盖率 | ACRPA.py / settings_window.py / dialogs.py / mini_bar.py / help_window.py 均为 **0%** | 无静态契约 |
| 测试 | **无 pytest、无 tests/**；自研 runner 47 个脚本，CI 仅跑 23 个 | 引擎控制流测试被跳过 |
| `scaled()` 采用率 | ACRPA.py 仅 8 处 vs 283 处裸像素字面量 | `ui_scale` 实际只缩放字体 |

---

## 2. P0 —— 已验证的确定性缺陷（建议立即修）

以下 7 项均已逐行复核，非推测。

### P0-1 找图缓存返回 `tuple`，调用方按 `Point` 取值 → **缓存命中必崩**

```python
# src/engine.py:845   —— 缓存命中返回普通元组
                return (x, y)
# src/engine.py:916,920 —— 调用方按属性访问
                    pa.click(loc.x, loc.y, ...)
```

`_find_cached` 的 docstring（engine.py:832）明确声明"Returns pyautogui Point object (with .x/.y attributes)"，实现与声明不一致。缓存未命中时返回 `loc`（真 Point）正常，**命中时抛 `AttributeError: 'tuple' object has no attribute 'x'`**。

- 影响面：`找图 / 区域找图 / 点图 / 区域点图` 四条旗舰命令，在 5 秒 TTL 内的重复搜索必崩。
- 旁证：`acrpa_api.find_image:189-196` 同时处理了两种形态，证明该不一致是疏漏而非设计。
- 附带缺陷：缓存键仅用 `img_path`（engine.py:837），忽略 `confidence / region / grayscale` —— 区域限定搜索会被全屏命中污染；`_image_cache` / `_CACHE_ORDER` 为跨线程模块全局量且**无锁**（engine.py:212-215）。
- **修法**：返回 `pyautogui.Point(x, y)`；缓存键改为 `(img_path, confidence, region, grayscale)`；加 `threading.Lock`；补一条缓存命中的回归测试。

### P0-2 DD 内核驱动无视用户开关，仍然加载并使用

```python
# src/engine.py:1018  —— 无条件获取并使用
        dd = get_dd_backend()
        if dd and dd.enabled and mode in ["auto", "direct"]:
```

`state.INPUT_MODE` / `state.USE_DD_DRIVER` 由 `settings_window.py:590` 写入，但在 `engine.py` 中**零处读取**（已 grep 确认）。`config.json` 中 `use_dd_driver: false`、`input_mode: "sendinput"`，而命令默认 `mode="auto"`（engine.py:1008-1010）恰好落在 DD 可用集合内，`DDBackend.__init__` 还会自动搜索 DLL（dd_backend.py:68-69）。

- 后果：用户明确关闭的内核态驱动实际仍在生效——正确性缺陷 + 杀软/反作弊误报 + 合规风险。
- 附带：`_find_dd_dll`（dd_backend.py:103-108）把 `os.getcwd()` 纳入 DLL 搜索路径 → 经典 DLL 植入面；加载前无哈希/Authenticode 校验。
- **修法**：`engine.py` 中读取 `INPUT_MODE`/`USE_DD_DRIVER` 做门禁；参照 `paddle_dll.py:906` 的**子进程预检**模式隔离崩溃；DLL 搜索限制在安装目录内并校验哈希。

### P0-3 预发布版本号解析为 `0.0.0` → 市场更新检查永久失效

```python
# src/script_package.py:73-87
def version_tuple(v):
    ...  # "0.1.29-beta".split(".") → int("29-beta") 抛异常 → 列表清空 → 补零 → (0,0,0)
```

`marketplace.check_update` 使用 `version_gt`（marketplace.py:489）。当前版本正是 `0.1.29-beta`，因此**市场更新检查对当前版本恒定返回"无更新"**。

- **修法**：委托给 `updater.compare_versions`（已正确处理 prerelease），或统一版本比较器（见 §7-重复实现）。

### P0-4 引擎控制流测试被 CI 误判为"GUI 冒烟"而跳过

```python
# tools/run_tests.py:51-52
if name.startswith(GUI_PREFIXES):      # GUI_PREFIXES = ("_smoke_",)
    return "GUI 冒烟: 需要真实窗口会话 (本机全量回归时用 --all)"
```

`tools/_smoke_engine.py`（180 行，覆盖 如果/嵌套如果/条件循环/循环+如果/嵌套循环/跳出循环）**只 import `state`、`engine`、`scriptdata`，不需要窗口**，实测 9/9 通过、exit 0，却被前缀规则排除。

- 后果：§2 中 P0-1 自 commit `b19b493` 起长期存在而未被发现，正是此缺口所致。
- **修法**：改按**文件内容/依赖**判定而非文件名前缀；或显式维护一份 CI 白名单，把 `_smoke_engine.py` 纳入。

### P0-5 停止执行是"建议性"的 —— `_chk()` 返回值被全部丢弃

```python
# src/engine.py:822-827
    def _chk(self):
        if state.quit2: return False
        ...
# 调用处：engine.py:892, 953, 962, 968, 989, 1053 … 均为裸语句 self._chk()
```

`_chk()` 实际只充当**暂停闸门**；`quit2` 置位后当前命令仍会跑完。真实中断点仅在 engine.py:337（行间）、:649（循环迭代间）、:1591、:1810。

- 后果：用户点"停止"无法打断 20 秒的找图超时、30 秒的 Python 块或长 `等待`。
- **修法**：`_chk()` 返回 `False` 时向上抛出统一的 `AbortSignal`；或在 `等待`/`_image_search_loop`/`py_sandbox` 中分段检查。

### P0-6 脚本保存非原子，失败即截断且无备份

```python
# ACRPA.py:2157 / :2199 —— 直接写用户目标路径
                wb.save(fp)
# :2164-2169 —— 版本快照在保存之后写入，且失败被吞
```

无临时文件 + `os.replace`、无 `.bak`、无保存前快照。磁盘满或 Excel 占用文件时，用户脚本被截断且无前像可恢复。

- 反差：`script_package.py:552-593` **已经正确实现了**"临时目录解压 + `os.replace`"的原子安装——编辑器自身的保存路径反而更脆弱。
- **修法**：复用 `script_package` 的原子写模式；保存前先落版本快照。

### P0-7 三个"看得见但不起作用"的设置项

| 配置项 | 写入处 | 读取处 |
|---|---|---|
| `recording_stop_hotkey` | settings_window.py:1377/1388/1400 | **无** —— 停止热键硬编码在 ACRPA.py:5670-5677 |
| `ocr_preload`（启动时预热） | settings_window.py:727 | **无** |
| `input_mode` / `use_dd_driver` | settings_window.py:590 | **无**（见 P0-2） |

用户改了设置、界面显示成功、实际行为不变——这类"静默无效"对信任度的伤害远大于功能缺失。**修法**：要么接线，要么从 UI 移除。

---

## 3. 软件设计 —— 四个结构性瓶颈与拆解路线

### 3.1 导入期即构建应用 → 彻底不可测试

```python
# src/ACRPA.py:755
root = tkinter.Tk()
# :5513-5514  root.after(100, _periodic)     —— 导入即启动 100ms 轮询
# :5694        root.after(200, _hotkey_poll)
# :5716-5718  NetLink 启动
# run.py:57   from ACRPA import root; root.mainloop()
```

`src/` 下**没有任何 `if __name__ == "__main__"` 守卫**（仅 `dd_backend.py:391` 有一个无关的）。`import ACRPA` 会建窗口、起定时器、启动调度器。

**这是所有其他重构的前置阻塞项。** 必须先落地：

```
src/app.py     # 薄入口：main() 内构建 root、装配依赖、mainloop
src/ACRPA.py   # 退化为纯定义模块（可被 import 而无副作用）
```

`help_window.py` / `help_content.py` 是仓库中**结构最好的一对文件**——真实类、调色板与字体依赖注入、`_load_content → _load_commands → _build → _load_nav` 分阶段、内容与渲染彻底分离、`scaled()` 用了 46 次（全库最多）、零硬编码颜色。**这是应当复制到全库的范式。**

### 3.2 上帝模块拆解（延续你已开始的第一阶段）

你已经在 `9555be6` 完成了"Mini Bar 出模块"。按同样手法继续，建议顺序（按**边界清晰度 × 收益**排序）：

| 序 | 目标 | 体量 | 拆分依据 | 难度 |
|---|---|---|---|---|
| 1 | **工作流 Tab** → `ui/workflow_view.py` | ACRPA.py:3985–5354，**1,370 行** | 已有 `workflow.py` 作为领域层，边界最干净 | 低 |
| 2 | **日志面板** → `ui/log_dock.py` | ACRPA.py:3564–3912，349 行 | 8 个 `_log_*` 函数，自包含 | 低 |
| 3 | **执行控制栏** → `ui/exec_bar.py` | ACRPA.py:3204–3580，377 行 | 与引擎通过状态交互，接口窄 | 低 |
| 4 | **脚本表格** → `ui/script_table.py` | ACRPA.py:1595–2091，约 500 行 | 顺势引入 `ScriptTableModel`（见 3.4） | 中 |
| 5 | **主题引擎** → `ui/theme.py` | `_refresh_theme` 205 行 + `utils.apply_theme` 123 行 | 见 §5.2 | 中 |
| 6 | **设置窗口** → `ui/settings/{window,cards/*}.py` | 2,336 行单函数 → 11 个卡片构建器 | 每卡 100–300 行，机械拆分 | 中 |
| 7 | **NetLink 窗口** → 6 个子对话框模块 | 3,601 行 / 1 类 / ~150 方法 | `_shot_*` `_audit_*` `_pair_*` `_rs_*` `_web_*` 天然成组 | 中 |

### 3.3 跨模块全局注入 → 构造注入

```python
# mini_bar.py:86-91
def bind(**kw):
    globals().update(kw)      # 17 个宿主符号直接写入模块命名空间
```

模块自己的 docstring 承认代价："漏一个就是运行期 NameError"。同类模式还有 `dialogs.init_ctx`（dialogs.py:42）、`settings_window.init_ctx`（:66）、以及直接改调色板 `settings_window.C = C`（ACRPA.py:1406）、`dialogs.C = C`（:1411）。

**建议**：统一改为 `_HelpWindow.__init__(root, colors=..., fonts=...)` 式的显式注入。注意 `mini_bar.py:19` 提到过一个静态校验脚本 `tools/_test_minibar_split.py`，但该文件已不在 `tools/` 中——**护栏已丢失，建议补回**并纳入 CI。

### 3.4 引入 model/view，解耦"行索引即主键"

```python
# ACRPA.py:2072-2089  每次变更全量删除重建
    tree.delete(*tree.get_children())
    for i, sd in enumerate(state._editor_rows): ...
# ACRPA.py:1926 / 1994-1995  用 tree 的位置当主键
        sd = state._editor_rows[tree.index(item)]
```

500 行脚本下，每次编辑都是 O(n) 控件churn；且任何未来的排序/筛选/虚拟化都会立刻破坏索引耦合。

**建议**：`ScriptTableModel` 适配器 —— `state._editor_rows` 变更时发射**行级失效**而非全量重建；行身份改用稳定 ID 而非位置。

### 3.5 用事件总线替换 100ms 轮询

```python
# ACRPA.py:5397-5525  _periodic：10Hz 轮询做 10 件不相关的事
```

且 `_periodic` 在 `ACRPA.py:5402` 遇异常直接 `return` —— **一次异常就永久杀死整条轮询链**，UI 全部更新静默失效。

**建议**：`state.on_config_change(cb, keys)`（state.py:212）已存在但只有 1 个订阅者、覆盖 6 个 key。把它扩展到完整 schema，并补充"运行态"事件（行变更、状态迁移、日志到达），即可拆解 `_periodic`。

---

## 4. 数据模型 —— 最大的一笔技术债

### 4.1 现状

- 内存模型 `ScriptData`（scriptdata.py:5-44）= **1 个命令名 + 恰好 9 个位置字符串参数**，全字符串化，无类型、无 ID、无启用位、无注释列、断点不落盘。
- 磁盘格式是**遗留的位置式 Excel**，且内部不一致：

| 问题 | 证据 |
|---|---|
| 表头文字不稳定 | `命令类型`（间隔点击.xls）vs `操作`（脚本模板.xls）；两个文件第 0 行还是自由文本说明 |
| 空值两种编码 | `""` 与字面量 `"None"` 并存；**只有** `ai_enhance.py:676-756` 做了归一化，引擎所有命令都不处理 → `"None"` 会成为真实参数值 |
| 四处硬编码跳过前 2 行 | `ACRPA.py:2051`、`ACRPA.py:652`、`workflow.py:50`、`snippets.py:147` —— 无表头嗅探，只有 1 行表头的脚本会静默丢掉首条命令 |
| `templates.py` 传 8 个参数 | templates.py:79 等，与 9 参数约定不符 |
| `ScriptData.COMMANDS` 导入期冻结 | scriptdata.py:6 —— 插件命令永远不出现在其中 |

- **无 schema 版本、无魔数、无迁移路径**。对比：`.acrpapkg` 是全项目唯一有真正版本化 schema 的东西（`SUPPORTED_MANIFEST_VERSIONS`，script_package.py:39）。

### 4.2 建议：引入 `.acrpas`（JSON）作为一等格式

```json
{
  "schema": 1,
  "id": "uuid",
  "name": "间隔点击",
  "created": "2026-10-03T20:00:00+08:00",
  "meta": { "app_min": "0.2.0", "author": "", "desc": "" },
  "rows": [
    { "id": "r1", "cmd": "按键", "enabled": true, "comment": "",
      "args": { "key": "down", "times": 1, "interval": 0.1 },
      "breakpoint": null }
  ],
  "vars": {}, "images": ["btn_ok.png"]
}
```

要点：
1. **具名参数**替代 9 个位置参数 —— 命令 schema 层（`commands.py` 的 `_parse_params`/`schema`/`validate`，已存在且设计良好）可直接驱动校验与 UI 表单。
2. **稳定行 ID** —— 解决 §3.4 的索引耦合，也让断点/注释可持久化。
3. **`schema` 字段 + 迁移函数表** —— 为未来任何格式演进留门。
4. **保留 `.xls` 双向兼容**：`xls → JSON` 导入器（复用现有 4 处解析逻辑，合并为唯一实现）+ `JSON → xls` 导出。Excel 仍可作为一个编辑入口，但不再是唯一真相。
5. 顺带解决：`"None"` 归一化、表头嗅探、模板 8/9 参数不一致。

> 这一步是**后续所有功能扩展的前置条件**：断点续跑、diff/版本对比、脚本市场增量更新、AI 结构化生成，都依赖稳定的行身份。

---

## 5. UI 设计完善建议

### 5.0 先明确：已有设计文档覆盖了什么

`docs/UI美化设计方案.md`（61 KB）已非常详尽地规定了**色板 Token、字体尺度、组件规范（标题栏/命令栏/Tab/数据表/日志面板/状态栏）、焦点与悬停态、深浅色草图、B0–B3 分阶段计划**。`docs/设置窗口优化设计方案.md`（27 KB）已列出 P0-1～P2-5 问题清单与目标信息架构。

**因此下面只提出这两份文档尚未覆盖的增量部分**，避免重复劳动：

| 已有文档覆盖（不必重做） | 本节新增的增量 |
|---|---|
| 色板 / 字体 / 尺度 Token | 交互架构（状态机 + 事件总线） |
| 组件外观规范、草图 | 信息架构与可发现性 |
| 设置窗口信息架构 | 数据表 → 真 DataGrid 的编辑体验 |
| 焦点/悬停微观态 | 主题引擎的实现层重构（去文本嗅探） |
| B0–B3 视觉改造阶段 | 缩放与可达性的系统性补齐 |

### 5.1 增量 A：交互架构——把"轮询 + 全局布尔"换成显式状态机

当前运行态散落在 `state.running` / `state.pause_event` / `state.quit2` / `state.quit3` / `engine._script_failed` 多个布尔与事件里，由 10Hz 轮询反推 UI。建议定义一个显式运行态机：

```
idle → validating → running ⇄ paused → stopping → finished
                       ↓                              ↑
                    failed ───────────────────────────┘
```

- 每个状态迁移触发事件，UI 订阅后更新；删除 `_periodic` 中的四态按钮手搓机（ACRPA.py:5415-5425）。
- 收益：暂停/继续/单步/停止的语义清晰，"单步"目前是 200ms 定时器（ACRPA.py:3514-3516），换成真状态机后可做真正的单步。

### 5.2 增量 B：主题引擎——删除"靠中文文案反推语义色"

这是当前 UI 层最脆弱的一处设计：

```python
# src/ACRPA.py:1282-1285
                    txt = w.cget("text")
                    if txt and ("●" in txt or "运行" in txt or "就绪" in txt):
                        fg_color = C["ac"] if "就绪" in txt else (C["sc"] if "运行" in txt else C["fgm"])
```

以及靠**旧颜色值反推语义角色**（`_semantic_bg_for`，ACRPA.py:1229-1242）。后果：改一个文案会改颜色；翻译成英文则整套配色失效；新增控件必须在 `_refresh_theme` 手工登记否则不换肤；每次换肤 O(widgets) 全树递归 + `root.update_idletasks()`。

**修法（三合一）**：
1. 把语义按钮从经典 `tkinter.Button` 迁移到 ttk 的 `Action / Success / Danger / Warning.TButton`（`utils.apply_theme` 里**已经定义好了**）。
2. 用 widget 的**语义标签**而非文本承载角色（例如统一维护 `_SEMANTIC_ROLE[widget] = "danger"`）。
3. 引入 `ThemeBus.subscribe(fn)`，取代 ACRPA.py:1405-1425 里对 5 个外部模块的手工 `C` 注入扇出。

这一步可删除约 120 行代码和 2 个潜在 bug，并让"新增窗口自动换肤"成为默认而非例外。

### 5.3 增量 C：信息架构与可发现性

68 条命令目前的入口是**工具栏平铺 + 下划线前缀命名**（`_cmd_*` / `_wf_*` / `_log_*`）。随命令继续增长，建议：

- **命令库（Command Palette）**：`Ctrl+K` 唤起，模糊搜索全部 68 条命令 + 参数提示，回车插入当前行。替代不断变长的工具栏。
- **命令分组**：按领域分栏（图像/键鼠/窗口/数据/OCR/浏览器/AI/流程/扩展），并在表格中按分组着色（现有 `_CMD_COLORS`，ACRPA.py:2063 可复用）。
- **参数表单化**：命令 schema 层已存在（`commands.py` 的 `schema`/`hints`），但目前**只写日志、零校验**（engine.py:702-725）。把它接到 UI：选中行时按 schema 渲染具名参数表单，替代 9 个无标签的 `参数1..参数9` 单元格 —— 这是体验提升最大的单点改动。
- **空态引导**：新建脚本时给出模板卡片视图（`templates.py` 已有内容），而非空白表格。

### 5.4 增量 C2：数据表 → 真 DataGrid

现行 `ttk.Treeview` 直接命令式操作（52 处）、全量重建、用位置当主键。建议：

| 能力 | 现状 | 建议 |
|---|---|---|
| 行编辑 | 内联 Entry（ACRPA.py:2637-2723），校验逻辑写在 Tk 回调里 | 按 schema 渲染类型化编辑器（数值/枚举/路径/表达式） |
| 排序 / 筛选 | 无（且索引耦合使其不可能） | 行 ID 解耦后即可支持 |
| 多选批量操作 | 部分 | 批量启停/注释/删除/移动 |
| 撤销重做 | 仅编辑器键盘级 | 命令级 undo stack（配合 §4 的行 ID） |
| 断点 | 标记 `●` 拼接在行号文本里 | 独立列 + 持久化的条件表达式 |
| 大数据量 | 全量重建 | 虚拟化（Treeview 可懒加载） |

### 5.5 增量 D：缩放与可达性

- **`ui_scale` 名不副实**：ACRPA.py 中 `scaled()` 仅 8 处 vs 283 处裸像素；settings_window.py / dialogs.py / mini_bar.py **0 处**。同一函数内尚不一致：`_apply_main_geometry` 中 :782 缩放、:794 `root.minsize(640, 520)` 不缩放、:799 `"960x680"` 不缩放。设置界面自己承认"建议重启程序"（settings_window.py:1620）。
  **建议**：把 `_FONT_SPECS` 的成功做法平移到尺寸——建立统一的 spacing/size token（`utils.py:902` 的 `TOKENS` 已有雏形），全量替换裸像素。
- **31 处硬编码 `"WxH+x+y"`**：固定屏幕偏移在小屏/多显示器下会溢出。`utils.center_geometry`（:989）/ `geometry_in_screen`（:970）已存在，但**只用于主窗口** —— 对话框应统一接入。
- **可达性**：标题栏的 `△ ⊟ ◑ ⚙ ? ⊕` 是 `Label` + `<Button-1>`，**不可 Tab 聚焦、无 accessible name**；全库仅 1 处 `takefocus`。建议改用真 Button 或补 `takefocus` + 语义名；`ACRPA_TINY` 7pt / `ACRPA_SMALL` 8pt 低于密集表格的可读下限，建议提到 9pt。

### 5.6 增量 E：i18n（同时解决"文案即状态"）

当前 UI 文本同时被当作**状态载体**和**主题输入**：

```python
# ACRPA.py:5426-5447  状态回环经过 label 文本
        status_text.config(text=" 就绪{}{}  |  {} 行  |  ...".format(...))
```

任何需要读状态的逻辑都得从控件文本里解析回来。建议：
1. 抽取字符串目录 `i18n.py`（`t(key)`），**优先抽取约 40 条承重串**（状态文案、被主题嗅探的标签）。
2. 状态与显示彻底分离：状态进 state machine（§5.1），控件只做渲染。
3. `index.html`（136 KB）/ `index.en.html`（142 KB）目前是整份手工复制，必然漂移 —— 抽取后可由模板生成。

---

## 6. 功能完善路线图（按用户价值排序）

### 6.1 让执行"可信"

| 项 | 现状 | 建议 |
|---|---|---|
| **运行前校验（dry-run）** | `commands.validate()` **零调用点**；`hints()` 只写日志 | 运行前全量校验：命令是否存在、参数个数/类型、引用的图片是否存在、变量是否已定义。结果以可跳转的问题列表呈现（而非日志） |
| **断点续跑 / 崩溃恢复** | 无：`state.filename` 与进度不持久化 | 结合 §4 的行 ID，落盘 checkpoint，支持从失败行恢复 |
| **失败现场留存** | 失败自动截图存在但失败被吞（engine.py:784） | 失败即保留截图 + 上下文（变量快照、前后 5 行），可在 UI 回放 |
| **幂等保护** | `Python`/`代码` 返回 `False` 会被 retry 循环重跑最多 4 次 | 副作用型命令标记 `idempotent=False`，禁止重试（与其自身 docstring 的意图一致，engine.py:1150） |
| **全局超时** | `max_execution_minutes` 仅在 autorun 外层检查 | 下沉到命令级 |
| **行号/进度正确性** | `如果` 递归用切片（:597-608）导致行号变成切片相对；`循环` 内联执行**完全不更新行状态** | 改为携带绝对行号，修进度条/ETA/当前行高亮 |

### 6.2 让录制"回放得住"

录制目前只产出绝对像素 `坐标`，无图像/OCR 锚点 —— 换分辨率、移窗口、换机器即失效。建议：

1. **多锚点录制**：一次点击同时记录「图像模板裁剪 + 窗口相对坐标 + OCR 文本 + 绝对坐标」，回放时按可靠性顺序回退（图像 → OCR → 相对 → 绝对）。
2. **文本合并**：连续按键合并为 `输入`/`写入` 命令（当前逐字符回放，固定 0.1s 间隔）。
3. **接上已实现但未接线的采集器**：`_record_scroll`、`_record_window_activate`、`_record_screenshot` 三个 helper 存在但**事件路径从不调用**（滚轮钩子根本没装）。
4. **录制时截图**：每个动作附一张裁剪图，直接作为后续找图的模板，形成"录制即产出资产"的闭环。

### 6.3 调试器

已有断点/条件断点/单步/变量监视。建议补：调用栈可视化（已有 `call_stack` 但 `iteration` 从不推进）、变量变更追踪（watch + diff）、**执行时间火焰图**（哪条命令慢）、单步回退（需 §4 的行 ID + 快照）。

### 6.4 命令生态与插件

- **插件子系统当前实际是死代码**：`plugins/__init__.py:38` 跳过 `_` 前缀文件，而示例插件就叫 `_example_plugin.py` —— 实机验证 `list_plugins() == []`，文档中承诺的 3 条示例命令从未加载；加载器本身还被 `engine.py:2014` 的静默 except 包裹。
  **修法**：改名启用 + 补启动测试断言"示例插件必须加载成功"。
- **插件是任意代码、启动即执行、零门槛**（plugins/__init__.py:37-49）。建议引入显式启用清单 + `PLUGIN_API_VERSION` + 清单文件。
- **handler 契约不统一**：必须同时处理 `row.args` 和 `row[i].value` 两种形态（ai_enhance.py:669-678），无版本协商；`list_plugins` 靠**函数身份**反查（plugins/__init__.py:79-85），装饰器包装后即失效。
- `commands.get_handler` 是 O(n) 线性扫描（commands.py:116-119），且插件可静默遮蔽内建命令 —— 建议改为 dict + 命名空间 + 重复名拒绝。

### 6.5 无头 / CLI（打开新场景）

`run.py` 只做依赖检查后 `mainloop`。核心层其实已近乎无头（`utils.log1` 在无 `_tlog` 时优雅降级，`py_sandbox` 刻意不引入 GUI），只是没人接线。建议：

```
acrpa.exe --script foo.acrpas [--headless] [--var k=v] [--json-report out.json] [--exit-code]
```

价值：CI 集成、服务器定时批处理、NetLink 远端执行、以及——**让端到端自动化测试成为可能**。

### 6.6 AI 能力的可信化

- `AIAnomalyDetector` 声称后台执行（ai_enhance.py:404-405），实际在**调用线程同步运行**，timeout=90s，每 10 条命令或失败率 >0.2 触发一次 → 脚本卡顿最多 90 秒。
- AI 输出**无校验、无边界检查**：`ai_find_element` 直接 `int(result["x"])` 后 `pa.click(x, y)`（:197-198、:692），幻觉坐标会点击屏幕任意位置。建议 JSON schema 校验 + 坐标钳制到屏幕范围 + 解析失败重试一次。
- 无重试/退路/熔断：`APIRateLimitError` 从未被捕获重试，失败即静默 no-op（"点这个按钮"这一步悄悄没发生）。
- 两套模型注册表不一致（UI 下拉用 `MODEL_REGISTRY`，provider 层用 `PROVIDER_PRESETS`，两者几乎不重叠）；Anthropic 是死路（`openai_compatible: False` 但 `create_client_active` 仍会发 OpenAI 格式请求）。
- **数据出境无脱敏**：全屏截图 base64 上传，变量与日志原文序列化 —— 脚本若输入过密码，该值会随变量一起发给第三方。建议：上传前脱敏 + 显式确认 + 允许"仅本地"模式。

### 6.7 其他已实现但未接线/未生效的能力

| 能力 | 状态 |
|---|---|
| `${var}` 通用插值 | **只对** `如果`/`循环开始`/`数学运算` 生效；`输入,${x}` 会字面输入 `${x}` |
| `跳出循环` | 只在直接循环体内有效；在 `如果` 块内静默 no-op（engine.py:744-745） |
| 固定次数循环 | 硬编码上限 1000（engine.py:648），忽略解析出的次数 |
| 空 `循环开始` 参数 | 静默跳过整个循环体 |
| `LEGACY_CODE_COMMAND` | 保留的裸 `exec` 逃生通道，无 AST 预检/超时/审计 |
| `ocr_threads` | 仅对 Paddle DLL 后端生效 |

---

## 7. 安全与供应链（Top 6）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| 1 | **更新校验是条件性的，可完全缺失**：`_verify_file` 仅 `if expect_sha256` 才校验；Release API 无 `digest` 且远端 manifest 不可达时，**只校验大小与魔数就安装**。无签名、无 Authenticode、无回滚 | updater.py:645、:687 | 校验强制化（缺失即拒绝）；加 `Get-AuthenticodeSignature`；下载前备份旧 exe + 支持回滚 |
| 2 | **市场：有完整性、无真实性**：`resolve_download_url` 优先采用索引里的自由文本 `info.url`，同一条记录还可提供匹配的 sha256 → 全部校验通过。`manifest["signature"]` 是永不产生、永不校验的占位符 | marketplace.py:272-282；script_package.py:145 | 用 minisign/ed25519 签名索引与包；`info.url` 限制在仓库白名单内或移除 |
| 3 | **NetLink 配对暴破限制是每连接的**：`_fails` 按 conn 对象计，断开即清零；服务端不按 IP 计数 → 换一个 socket 即可无限次猜 6 位 PIN（10⁶ 空间）。且 salt/nonce/ts/proof 全明文，离线可暴破 | security.py:674、:794-821；server.py:103-118 | 全局 + IP 维度限流；配对密钥加长/加熵 |
| 4 | **设备指纹完全由客户端自报**：`make_fingerprint` 从不传入 `machine_id`；指纹取自对端自己的 HELLO 载荷 → 可伪冒、可碰撞（`MachineGuid` 读了却没用） | security.py:95-98；node.py:210、:88-104 | 把 `MachineGuid` 或 TLS 证书指纹纳入身份 |
| 5 | **Web 面板 token 放在 URL query**，且默认 `0.0.0.0` + 明文 HTTP | webui.py:1764、:1247-1249；config.json:100 | 改为一次性交换 + Cookie；默认绑定 `127.0.0.1`；补 CSP/X-Frame-Options 等头 |
| 6 | **浏览器下载文件名未净化**：`download.suggested_filename`（远端可控）直接 `os.path.join` → 跨越目录写入 | browser_backend.py:980-993、:2557 | `os.path.basename` + 拒绝 `..`/绝对路径 |

值得保留的优势：PIN 从不上线、`hmac.compare_digest` 一致使用、密钥一律存 Windows 凭据管理器（含 AI key）、三处 TLS 均无静默降级、`script_package.unpack` 的 zip-slip/符号链接/前缀白名单/原子安装堪称范本、`paddle_dll` 的子进程预检是正确的原生 DLL 隔离模式、零遥测。

---

## 8. 工程基建

1. **把引擎测试接进 CI**（P0-4）：改判定方式，让 `_smoke_engine.py` 真正跑起来。
2. **引入 pytest**：现有 47 个脚本靠"子进程 exit code"判定，无断言库、无 fixture、无覆盖率。建议逐步迁移，`tools/` 下的领域脚本（`_test_python_sandbox.py` 515 行等）是良好素材。
3. **`.github/workflows/ci.yml` 补**：`_smoke_engine`、`_test_command_schema`、插件加载断言、P0-1/P0-3 的回归测试。
4. **异常策略收敛**：1,620 处宽泛捕获 / 722 处纯 `pass`。优先治理最危险的几个：`state.py:416`（`save_config` 吞掉一切 → 磁盘满时**静默丢失全部配置**）、`state.py:380`（配置损坏**静默重置全部设置**）、`engine.py:2014`（插件加载失败完全不可见）。
5. **消除重复实现**：sha256 计算 3 份、版本比较 2 份且互不兼容、流式下载 2 份且校验规则不同（一个只认 `PK\x03\x04`，一个还认 `MZ`）、HTTP GET 3 份、int 钳制 4+ 份。
6. **类型注解**：从 `commands.py` / `scriptdata.py` / `state.py` 这类契约层开始补，收益最高。
7. **统一日志**：`logs/netlink.log` 无轮转无大小上限；且日志会原文镜像给 NetLink 对端并渲染在网页面板 —— 脚本中输入的凭据可能外泄，建议加脱敏。

---

## 9. 分阶段路线图

### 阶段一 · 稳定化（建议 v0.1.30，约 1–2 周）

- P0-1 找图缓存类型 + 缓存键 + 加锁
- P0-2 DD 驱动门禁接线
- P0-3 版本比较统一
- P0-4 引擎测试接入 CI
- P0-5 停止语义强化
- P0-6 脚本保存原子化
- P0-7 三个失效设置项：接线或从 UI 移除
- 全局限流（安全 #3）、下载文件名净化（安全 #6）

**验收**：四条找图命令在缓存命中场景 100% 通过；关闭 DD 后内核驱动不加载；CI 中引擎控制流测试常绿。

### 阶段二 · 结构化（v0.2.0，约 1–2 个月）

- `app.py` 入口拆分 + `main()` 守卫（**解锁测试**）
- `ui/theme.py` + `ThemeBus`，删除文本嗅探测色
- 拆分工作流 Tab / 日志面板 / 执行控制栏（约 1,900 行出模块）
- 设置窗口 11 卡片拆分
- `.acrpas` JSON 格式 + 迁移 + xls 双向兼容
- `ScriptTableModel` + 行 ID
- 运行前校验（dry-run）+ 失败现场留存
- 命令 schema 驱动的参数表单 UI

**验收**：`import ACRPA` 无副作用；单测可覆盖 engine/commands/scriptdata；新增窗口自动换肤无需改 `_refresh_theme`。

### 阶段三 · 能力跃迁（v0.3.0+）

- 多锚点录制 + 录制时截图
- 断点续跑 / 崩溃恢复
- 命令库（Ctrl+K）+ 命令分组
- CLI / 无头模式 + JSON 报告
- i18n 字符串目录（先抽 40 条承重串）
- 市场包签名 + 更新回滚 + 强制校验
- NetLink 设备身份加固、token 移出 URL
- 调试器增强（时间火焰图、变量 diff、回退）
- 缩放 token 全量替换 + 可达性补齐

---

## 10. 建议的度量指标

| 指标 | 当前 | 目标（v0.2.0） |
|---|---|---|
| CI 中引擎控制流测试用例数 | 0 | ≥ 9（`_smoke_engine` 全量） |
| 最大单文件行数 | 5,738（ACRPA.py） | ≤ 3,000 |
| 最大单函数行数 | 2,336 | ≤ 300 |
| 纯 `pass` 的宽泛异常 | 722 | ≤ 200（且核心路径为 0） |
| `scaled()`/token 覆盖率 | ~3% | ≥ 90% |
| 失效配置项数量 | 3 | 0 |
| 核心模块类型注解覆盖率 | 0% | ≥ 60%（契约层 100%） |

---

### 附：分析覆盖范围

UI 层 7 个文件全量通读（`ACRPA.py`、`settings_window.py`、`dialogs.py`、`mini_bar.py`、`tray.py`、`help_window.py`、`help_content.py`）+ `run.py`；核心层 15 个文件；外围子系统（NetLink 16 个模块、市场 5 个、AI 2 个、后端 4 个、更新 3 个）全量；`config.json`、`VERSION`、`template/*.xls`、`.github/workflows`、`tools/` 抽样。所有 P0 结论已在源码中逐行复核，其中 P0-1 经实机执行复现、P0-2/P0-3/P0-7 经 grep 确认无读取点。

---

## 11. 增量：2026-10-03 夜间批次（已实施 + 新需求）

> 本节由实现方在 `9555be6` 基线之上追加，记录**已落地**的修复与新增需求，供后续阶段排期。

### 11.1 已修复：AI 脚本生成「AI返回的内容为空或格式无效」

**现场**：点「AI 生成」→ 弹窗「生成失败 / 发生未知错误：AI返回的内容为空或格式无效」。

**根因（本机实测复现，非推测）**：当前端点背后是**推理模型**，它"想"的时候会把整个
`max_tokens` 预算烧在 `reasoning_content` 上；此时 HTTP 仍是 200，但 `message.content`
为空 → `normalize_ai_output("")` 返回 ""（该函数对任何非空输入都必然返回非空）→ 抛错。
同一提示词实测：

| max_tokens | finish_reason | content | reasoning |
|---|---|---|---|
| 500 | `length` | **0 字符** | 943 |
| **2000（应用原值）** | **`length`** | **0 字符** | 5379 |
| 1000 | `stop` | 593 字符 | 0 |
| 8000 | `stop` | 640 字符 | 0 |

**修法**（`src/ai_client.py` + `src/dialogs.py`）：

1. 新增 `generate_script_content()`：默认预算 8000；正文为空且 `finish_reason=length`
   或存在 `reasoning_content` 时自动放大 2 倍重试一次；仍为空则抛出**带成因**的错误
   （finish_reason / 推理长度 / reasoning_tokens / completion_tokens），并在状态行显示
   「模型思考占满输出预算，正以 N tokens 重试…」。
2. `chat_completions` 不再用 `raise_for_status()` 丢掉正文：400/401 等会把服务端
   `error.message` 带进异常（例如 `Model Not Exist` 一眼可见）。
3. 响应对象补充 `finish_reason` / `reasoning_content` / `usage` / `model`。
4. 回归：`tools/_test_ai_generation_flow.py`（21 条离线断言，含「非截断空正文不浪费
   第二次请求」与「HTTP 错误带出服务端原文」）。

**仍未解决（留作后续）**：模型可用性未校验 —— 本机 `GET /models` 实际返回
`deepseek-flash` / `deepseek-v4-pro`，而 `MODEL_REGISTRY` 里是 `deepseek-v4-flash` /
`deepseek-chat`（配置里正是前者）。该端点接受它，但换到官方端点会 400。
建议设置页加「测试连接」：拉 `/models` 并高亮不可用项。

### 11.2 新增需求 R1：脚本编辑区缩放（已实现）

- **需求**：Excel 编辑区支持 `Ctrl+滚轮` 与 `Ctrl+加减号` 缩放。
- **实现**：为脚本表格派生独立样式 `ScriptEditor.Treeview`（不牵连变量监视 / 时序 /
  工作流列表）；新增配置项 `editor_zoom`（int，默认 0，与 `ui_scale` 解耦）；
  绑定 `Ctrl+滚轮`（指针须落在编辑区内）/ `Ctrl++` / `Ctrl+=` / `Ctrl+-` / `Ctrl+0` 复位；
  倍率钳制 -4…+12、字号钳制 6…40pt；落盘持久化；`ui_scale` 变更后自动重算。
- **回归**：`tools/_test_ui_zoom_shortcuts.py`（28 条，含**实机进程**验证字号真的变化）。
- **备注**：该测试第一版就抓出实现里的**静默失效** —— `utils.font()` 返回的是命名字体
  **字符串**而非 Font 对象，`.cget()` 抛异常后被 `except` 吞掉，缩放完全不生效。
  这正好印证 §3.1：GUI 相关改动必须同时有静态断言与实机断言。

### 11.3 新增需求 R2：通用快捷键（已实现基础集）

| 快捷键 | 行为 | 状态 |
|---|---|---|
| `Ctrl+S` | 保存当前脚本 | ✅ 新增（窗口级） |
| `Ctrl+Shift+S` | 另存为 | ✅ 新增 |
| `Ctrl+N` | 新建脚本 | ✅ 新增 |
| `Ctrl+O` | 打开脚本 | ✅ 新增 |
| `Ctrl+滚轮` / `±` / `0` | 编辑区缩放 | ✅ 新增（见 R1） |
| `Ctrl+C/V/Z/Y/D` / `Ctrl+/` | 复制 / 粘贴 / 撤销 / 重做 / 复制行 / 注释 | ⚠️ 已有，但**仅表格聚焦时**生效 |

- **设计取舍**：快捷键走 `root.bind_all`，但加了「主窗口聚焦」护栏 —— 否则在设置窗口
  或 AI 生成窗口里按 `Ctrl+S` 会误存脚本。
- **可发现性**：工具栏「新建/打开/保存/另存」的 tooltip 已写明快捷键。
- **后续建议**：① 把表格级的 `Ctrl+C/V/Z/Y` 也提到窗口级并统一护栏；② 增加
  `Ctrl+F`（搜索定位）、`Ctrl+P`（命令库，见 §5.3）、`F5`（运行）、`Shift+F5`（停止）；
  ③ 在「帮助 → 快捷键」集中列出。

### 11.4 勘误（对前文的修正）

1. **§2 P0-3 的后果描述不成立**：`version_tuple("0.1.29-beta") → (0,0,0)` 属实，但
   `marketplace.check_update(script_id, local_version)` 比的是**脚本版本**（测试传
   `"1.0.0"`），应用版本不会传到这里，因此不会「市场更新检查永久失效」。真实影响是
   潜伏的：任何带预发布后缀的版本都会被算成 `0.0.0`。
   顺带发现**同类新问题**：`MARKET_AUTO_CHECK_UPDATE` 有 UI 写入、src 下无消费者
   （`check_update` 仅测试在调），应并入 §2 P0-7 的「看得见但不起作用」清单。
2. **§3.3 关于护栏的判断有误**：`tools/_test_minibar_split.py` **存在**，就在本文基线
   `9555be6` 中，且已是 CI 安全集成员。护栏没有丢失。

### 11.5 本批次提交

| 提交 | 内容 |
|---|---|
| `d205d81` | 更新链路 sha256/exe 修复、测试 runner + CI、6 条陈旧断言修正 |
| `9555be6` | 命令参数 schema、ACRPA.py 拆分第一阶段（Mini Bar 出模块） |
| 本次 | AI 生成失败修复、编辑区缩放、通用快捷键、本文档增量与勘误 |
