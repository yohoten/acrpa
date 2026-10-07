# 参考分析：Playsaurus Auto Clicker 对 ACRPA 的借鉴要点

> 分析对象：`F:\Download\Google download\auto-clicker-free-v212\auto-clicker`  
> 生成日期：2026-10-05  
> 相关文档：本仓库 `docs/ACRPA-完善路线图.md`（§5 UI、§11 工作流编排、§12 可选依赖插件化）



---

## 0. 结论先行

按借鉴价值排序，共三条值得动，一条必须警惕：

| 优先级    | 借鉴项                                                             | 性质                        | 依据                                                                             |
| ------ | --------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------ |
| **P1** | **参数表单组件化**：`Generic/` 通用输入组件 + `Units/` 的「展示组件 + 编辑 Modal」成对结构 | 直接可抄，有现成落地点               | `Components/Generic/*.razor.css`、`Components/Units/*`                          |
| **P1** | **脚本可移植性三件套**：分辨率自适应 / ROI 区域限定 / 色匹配                           | **ACRPA 当前空白**，且是 RPA 的刚需 | 467 个 i18n key 中 `Resolution*` 6 个、`RegionOfInterest*` 4 个、`ColorMatchingMode` |
| **P2** | **`XxxExplanation` 作为数据**：每个参数自带一句解释文本                          | 抄的是**数据结构约定**，不是代码        | 467 key 中约 60 个以 `Explanation` 结尾                                              |
| ⚠️     | **不要照搬它的 UGC 路线**（Steam 创意工坊）                                   | 战略分歧，需自行判断                | `Workshop*` 22 个 key                                                           |

同时必须看清一件事：**两个产品的定位只在一个薄层上重叠**。Auto Clicker 是「游戏挂机连点器 + Steam UGC」，ACRPA 是「办公/业务 RPA」。功能交集只有最底层的鼠标键盘模拟。**在广度上 ACRPA 反而领先**（§6）。

---

## 1. 它是什么

### 1.1 技术栈

来自 `data/AutoClicker.deps.json`：

| 项目    | 值                                                                                                                             |
| ----- | ----------------------------------------------------------------------------------------------------------------------------- |
| 运行时   | `.NETCoreApp,Version=v8.0/win10-x64`，self-contained（`runtimepack` 8.0.28）                                                     |
| UI 框架 | **MAUI + Blazor Hybrid**（`Microsoft.AspNetCore.Components.WebView.Maui` 8.0.100 → UI 渲染在 WebView2 里，逻辑用 C#）                   |
| 组件库   | MudBlazor 8.2.0 + Blazor.Bootstrap 2.1.0                                                                                      |
| 图像处理  | **OpenCvSharp4 4.11.0**（`OpenCvSharp4` + `.Extensions` + `.runtime.win`）+ SixLabors.ImageSharp 3.1.12 + System.Drawing.Common |
| 本地化   | `My.Extensions.Localization.Json` 3.3.0 —— **JSON 文件驱动，加语言不用重编译**                                                             |
| 商业化   | **Steamworks.NET 20.1.0**（Steam 创意工坊 + 云存档）                                                                                   |
| 其它    | Newtonsoft.Json 13.0.3、CommunityToolkit.Maui.Core 9.1.1、Microsoft.VisualStudio.Threading 17.8.14、Obfuscar 2.2.40（构建期混淆）       |

**注意它与 ACRPA 的同一个技术选型**：两者都用 OpenCV 做模板匹配。区别在于分发——它 self-contained 打包进 269 MB，ACRPA 选择外置（路线图 §12）。**同一技术选型的两种分发答案，ACRPA 的选择在体积上明显更优。**

### 1.2 分发布局

```
auto-clicker/
├── AutoClicker.exe          22.5 KB   ← 启动器（launcher）
└── data/                    ~269 MB   ← 全部内容
    ├── *.dll × 340                    （.NET 运行时 + MAUI + WinUI + OpenCV…）
    ├── AutoClicker.dll      1.37 MB   （应用本体，含全部 Razor 组件）
    ├── AutoClicker.pdb      582 KB
    ├── Resources/Languages/ 30 个 JSON
    ├── Components/          8 个分组的 .razor.css
    ├── Assemblies/          System.Windows.Forms.dll
    └── appicon*.png × 30+  （多分辨率图标：scale-100/125/150/200/400 × 6 种尺寸）
```

对比 ACRPA：`dist/ACRPA.exe` = **14.7 MB**（PyInstaller onefile）。

**可借鉴的一处**：它把「启动器」与「内容目录」分离，内容是**不解包**的、直接加载。ACRPA 的 onefile 每次启动要解包到临时目录。若后续按 §12 把扩展落到本地目录，改为「小 exe + data/ 目录」的形态会比继续塞进单文件更自然——但**这只是形态选择，14.7 MB 本身是 ACRPA 的优势，不是问题**。

### 1.3 方法说明（重要）

本次分析对象**是编译产物，不含源码**。所有结论来自四处可观测证据：

1. `AutoClicker.deps.json` —— 完整依赖清单与版本
2. `data/Resources/Languages/en.json` —— **467 个 i18n key**，这是反推功能集最有效的途径
3. `data/Components/` 目录结构 —— 组件分层
4. `staticwebassets.endpoints.json` —— 静态资源清单

因此下文对功能集的推断**基于 i18n key 的语义命名**，可信度较高（命名直接对应 UI 控件），但具体实现细节（如算法参数）无法确认。凡属推断而非确证的，均已标注。

---

## 2. 架构层借鉴

### 2.1 组件分层（P1，直接可抄）

`data/Components/` 的目录结构：

```
Components/
├── Layout/       MainLayout, NavMenu
├── Pages/        AdvancedClickerPage, AppConfigurationPage, HelpPage,
│                 PermissionsPage, RecordingPage, SimpleClickerPage,
│                 SupportPage, UpgradePage, WorkshopPage, EmptyPage
├── Units/        BlockUnit, ClickUnit, CustomUnit, DetectionUnit,
│                 IntervalUnit, KeyUnit, MacroUnit, MoveUnit, RepeatUnit,
│                 RandomUnit, ScrollUnit, StopUnit
│    ├── Modals/  上述每个 Unit 对应一个编辑弹窗
│    └── Handles/ UnitHandle（拖拽手柄）
├── Generic/      NumberInput, EnumInput, HexInput, CultureInput,
│                 GradualMovement, KeyRelativePosition, MassEdit,
│                 DragPathContainer, Loading, AdvancedModeLoop
├── Recordings/   RecordingComponent
├── Workshop/     WorkshopComponent, DetailsModal, PublishModal,
│                 StatusModal, ImportWarningModal
├── MudBlazor/    HelpAdvancedTree, HelpRecordingTree, HelpStandardTree
└── SortableJS/   SortableList（拖拽排序）
```

三个可提炼的规律：

1. **一种动作类型 = 展示组件 + 编辑组件 + 数据模型**。`Units/ClickUnitComponent` 负责在序列里显示，`Units/Modals/ClickUnitModalComponent` 负责编辑。两者严格配对。
2. **`Generic/` 是参数类型的控件映射层**。`NumberInput`/`EnumInput`/`HexInput`/`CultureInput` 对应不同的参数 `kind`。
3. **CSS 与组件同源**（`.razor.css` 作用域隔离），不产生全局样式表。

对照 ACRPA：`ACRPA.py` 5,738 行单体，脚本表格里 9 个无标签的 `参数1..参数9` 单元格，样式靠全局 `_FLOW_COLORS`（`:4316`）与手工 `tip=`。

**现成的落地点**：`commands.py:26-27` 已经用 `_NUMERIC_HINTS` 解析参数的类型语义，`commands.schema()` 已能产出 `{'name': ..., 'kind': 'number', 'default': '0.96'}`。**缺的只是最后一层「kind → 控件」映射**。新建一个 `ui/units/inputs.py` 提供 `NumberInput / EnumInput / BoolInput / TextInput / ColorInput`，再把 `commands.schema()` 接上去，即可把 9 个裸参数格变成具名表单。这正是路线图 §5 所列"体验提升最大的单点改动"，而 Auto Clicker 证明了这个模式在产品上是成立的。

对应的拆分目标（与 §3.2 一致）：ACRPA 不需要换技术栈，只需把 `Units/` 的粒度抄过来——`ui/units/click_unit.py`、`ui/units/modals/click_unit_modal.py` 等。

### 2.2 拖拽排序已作为基础设施

`Components/SortableJS/SortableList` —— 序列编辑用成熟的拖拽库，而非自绘。

对照 ACRPA：§11 诊断过工作流画布只有垂直滚动、无框选/连线/undo，且脚本表格的步骤重排体验未做专门设计。**建议：若做工作流 D 期（连线交互）之前，先把"列表重排"这一步用现成方案做掉**——投入小、收益直接，且能提前验证 CFG 模型与拖拽操作的数据契约。

---

## 3. 功能层对照

下表左侧为从 i18n key 反推的 Auto Clicker 能力，右侧为 ACRPA 现状（已核对 68 条命令全表与源码 grep，避免误报缺失）。

### 3.1 ACRPA 明确空白、且应补的（P1）

| 能力                   | Auto Clicker key 证据                                                                                                                                                                                                                                             | ACRPA 现状                                           | 影响                                       |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------------------------- |
| **分辨率 / 多显示器适配**     | `Resolution`、`YourResolution`、`ItemResolution`、`AllResolutions`、`WindowResolutions`、`WindowResolutionsExplanation`、`WindowResolutionsImportRecommendation`、`ResolutionMismatchTitle`、`ResolutionMismatchExplanation`、`ImportScaled`、`ImportAsIs`、`ResizeFactor` | **0 处**（`grep 分辨率` = 0）                            | 脚本用绝对坐标，换机器或调分辨率即失效。这是 RPA 可移植性的头号杀手     |
| **ROI 区域限定（全局）**     | `RegionOfInterest`、`RegionOfInterestShort`、`RegionOfInterestExplanation`、`NoROIWarning`、`DetectionArea`、`DetectionAreaExplanation`                                                                                                                              | 有「区域找图 / 区域点图」两条命令（区域作为**命令参数**），但**无脚本级全局 ROI**   | 全屏找图慢且易误匹配；全局 ROI 可同时提升速度与准确率            |
| **色匹配**              | `ColorMatchingMode`、`ColorMatchingModeExplanation`、`CapturingColor`、`Red`、`Green`、`Blue`、`Hexadecimal`、`Tolerance`、`ToleranceExplanation`                                                                                                                       | **0 处**（`grep 色` = 0）                              | 低成本高价值：**用 Pillow（已在主包）即可实现**，不需要 OpenCV |
| **随机偏移 / 渐变移动（拟人化）** | `RandomOffset`、`RandomOffsetMS`、`RandomOffsetExplanation`、`RandomOffsetCoordinatesExplanation`、`GraduallyMoveToPosition`、`GraduallyMoveToClickPosition`、`GradualMovementDelayMS`                                                                                | **0 处**（`grep 随机` = 0，`渐变` = 0）                    | pyautogui 是瞬移，在对抗反自动化检测的场景下表达能力不足        |
| **块级重试语义**           | `RetryXTimes`、`RetryXTimesExplanation`、`RetryUntilStopped`、`RetryUntilStoppedExplanation`、`Retries`、`DetectionRetries`                                                                                                                                          | 有 `retry_max` / `retry_interval`，但**是全局设置**，单步不可覆盖 | 不同步骤的重试策略通常不同                            |
| **多坐标系移动**           | `MoveScreenExplanation`、`MoveWindowExplanation`、`MoveRelativeToCursorExplanation`、`MoveFixedLocationExplanation`、`RelativePosition`、`Offset`                                                                                                                    | 录制有 absolute/relative 模式，移动命令的坐标参考系单一              | 相对窗口坐标在窗口位置变动时仍可用                        |

### 3.2 ACRPA 已有、但可打磨的（P2）

| 能力             | Auto Clicker 的做法                                                                                                                                               | ACRPA 现状                | 建议                                    |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------- |
| **并发执行**       | `AllowMultipleSimultaneousExecutions` + `...Explanation`                                                                                                       | 无                       | 阶段三候选                                 |
| **批量编辑**       | `MassEdit`、`BlockMassEditExplanation`                                                                                                                          | 无                       | 对几十上百步脚本是刚需，配合 `ScriptTableModel` 一起做 |
| **自定义可复用块**    | `CustomBlock`、`CreateCustomBlock`、`CreateCustomBlockFromRecording`、`CustomBlocks`、`CustomBlockAlreadyExists`                                                   | 有 `snippets.py`（代码片段）   | **"从一次录制直接生成可复用块"这条链缺失**，值得补          |
| **录制项的细粒度开关**  | `RecordIntervals`、`RecordDragPath`、`RecordKeys`、`RecordScroll`、`RecordHold`、`AlwaysRecordCursorMovement`、`IntervalBetweenRecordings`、`RecordKeyCursorPosition` | 有 `recorder.py`，可配项少于对方 | 逐项对齐成本不高                              |
| **采集时最小化自身窗口** | `MinimizeWhileCapturing`                                                                                                                                       | 需确认                     | 取点/截图时若不隐藏自己，会截到自身窗口。细节但直接影响可用性       |
| **权限引导页**      | `PermissionsPage`、`Permissions`、`GrantPermission`、`PermissionRestart`、`Accessibility`、`InputMonitoring`                                                        | DD 驱动需管理员权限，但无专门引导页     | 建议加一个权限自检页                            |

### 3.3 两者共有、实现口径不同

| 能力   | Auto Clicker                                                                                                           | ACRPA                                                           |
| ---- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| 图像匹配 | `TemplateMatching` **+ `FeatureDetection`** 两种模式                                                                       | 仅模板匹配（cv2 `confidence`）                                         |
| OCR  | `OCR`、`OCRTextExplanation`、`TextToFind`                                                                                | `识别文字` / `点击文字` / `等待文字` + 四后端（`ocr_backend.py`）—— **ACRPA 更强** |
| 调度   | `Schedule`、`EnableSchedule`、`RunEvery`、`StartTime/EndTime`、`StartDate`、`NextStart/NextStop`、`ScheduleKeepAwakeWarning` | `scheduler.py` 已有；&#x4F46;**"防止系统休眠"提示缺失**                      |
| 窗口绑定 | `ProcessName`、`Window`                                                                                                 | `bound_window_title`                                            |
| 按键模式 | `KeyPressMode`、`KeyDown`、`KeyUp`、`KeyPress`                                                                            | 按键 / 按下 / 释放 + DD 的 `auto/direct/simulate`                      |
| 停止块  | `StopBlock`、`StopBlockExplanation`                                                                                     | 有停止语义，但 §2-P0-5「停止只是建议性」未收敛                                     |

---

## 4. 工程实践借鉴

### 4.1 `XxxExplanation` 作为数据（P2，抄的是约定）

467 个 key 中约 **60 个以 `Explanation` 结尾**，与设置项一一配对：

```
MouseButton            ↔ MouseButtonExplanation
Tolerance              ↔ ToleranceExplanation
RandomOffset           ↔ RandomOffsetExplanation
RegionOfInterest       ↔ RegionOfInterestExplanation
AllowMultipleSimultaneousExecutions ↔ ...Explanation
GraduallyMoveToPosition ↔ ...Explanation
```

**这不是文档，是把帮助文本变成了数据结构的一部分。** 好处是：tooltip 自动生成、帮助窗口自动组装、翻译者一次翻完、新增参数时不会忘记写解释。

对照 ACRPA：`settings_window.py` 有 `tip=` 参数机制（如 `tip="选择 PaddleOCR 模型目录"`），思路相同，但**覆盖不完整**，多数命令参数没有解释文本。

**落地建议**：`commands.register()` 增加可选 `explain=` 字段（沿用 `commands.py:36-44` 已确立的向后兼容策略），与已有的 `schema` 并列。68 条命令补齐解释文本的工作量约半天，收益是 tooltip + 帮助窗口 + 未来的 i18n 三处同时到位。

### 4.2 本地化方案（P2）

- 30 种语言，**zh-CN 与 en 均为 467 key，零缺失**（已程序化校验）
- 扁平 JSON，PascalCase 语义 key，支持 `{0}` 占位符（`"PageXofY": "第 {0} 页，共 {1} 页"`）
- 用 `My.Extensions.Localization.Json`，**运行时读 JSON，加语言无需重编译**

对照 ACRPA：i18n 排在阶段三（"先抽 40 条承重串"）。

**建议直接采用这套格式**：扁平 JSON + 语义 key + `{0}` 占位符。比起嵌套结构或 `gettext`，扁平 JSON 对翻译者最友好，且新增语言只是多一个文件。ACRPA 若要走这一步，`zh-CN.json` 可以直接作为模板，且这个项目的 467 key 组织方式（按功能而非按页面分组、Explanation 与主体并列）可作为命名规范的参考。

### 4.3 图标资源

`appicon*.png` 共 30+ 个，覆盖 `scale-100/125/150/200/400` × `Logo/LargeTile/MediumTile/SmallTile/WideTile/StoreLogo` + `targetsize-16/24/32/48/256`。

这是 MSIX/商店打包的规范要求。ACRPA 目前是 `res/automation.ico` 单文件。**只有走商店分发才需要这套**，现阶段不必做。

### 4.4 不建议借鉴的

| 项                            | 理由                                                               |
| ---------------------------- | ---------------------------------------------------------------- |
| **Obfuscar 代码混淆**            | ACRPA 是开源项目，混淆与定位冲突                                              |
| **self-contained 269 MB 分发** | ACRPA 的 14.7 MB 是优势。且 PyInstaller onefile 已有成熟更新链路（`updater.py`） |
| **MAUI / Blazor Hybrid 技术栈** | 换栈代价极高，且会失去 Python 生态（OCR / Playwright / AI）——那是 ACRPA 的核心竞争力    |
| **Steam 创意工坊 UGC 路线**        | 见 §5                                                             |

---

## 5. UGC 路线：两条路，各有代价

Auto Clicker 依托 **Steam 创意工坊**（`Workshop*` 22 个 key：浏览、搜索、排序、订阅、发布、标签、预览图、导入确认、Steam 依赖提示）。ACRPA 自建 `marketplace.py`。

| 维度 | Steam 创意工坊 | ACRPA 自建市场 |
| -- | ---------- | ---------- |


| 账号体系 | Steam 账户，白拿 | 需自建（`accounts.py`） |
| 存储 / CDN | Steam 提供 | 自建（当前依赖 GitHub Release + 本地目录） |
| 审核 / 举报 | Steam 提供 | 需自建 |
| 评分 / 订阅数 | 平台提供 | 自建数据中已含 `downloads`/`rating`/`rating_count` |
| 分发前提 | **用户必须装 Steam 且拥有该游戏** | 无前提 |
| 适用场景 | 游戏挂机脚本 | 企业内部 / 业务脚本——**多数企业环境不装 Steam** |

**判断**：ACRPA 的目标场景（办公/业务自动化）与企业环境，**不适合绑定 Steam**。自建市场是对的路线。真正该补的是路线图 §7 已列的两项：**市场包签名 + 更新强制校验**——自建市场必须自己补上平台本来会提供的信任基础设施。

---

## 6. ACRPA 的领先项（无需妄自菲薄）

Auto Clicker 的 467 个 i18n key 里**完全没有**以下能力，而 ACRPA 已经实现：

| 能力 | ACRPA | Auto Clicker |
| --- | --- | --- |
| **AI 集成** | `AI找图`、`AI识别界面`、`AI优化建议` + `ai_smart_retry` + `ai_anomaly_detect`（`ai_client.py` / `ai_enhance.py`） | 无 |
| **内联脚本** | `Python` / `代码` 命令 + `py_sandbox` 三级权限（AST 预检 + 超时 + 审计） | 无 |
| **浏览器自动化** | 20+ 条命令，Playwright + CDP + 网络监听（`browser_backend.py` 2,812 行） | 无——只能点屏幕 |
| **多机协同** | NetLink 15 个模块（配对 / TLS / 权限 / 远控 / 传文件 / Web 面板） | 无 |
| **自动更新** | `updater.py` 1,052 行，多源探测 + sha256 + 原子替换 | 仅 1 个 `Update` key，实际依赖 Steam 更新 |
| **OCR 后端** | 四后端可选（PaddleOCR / PaddleOCR.dll / WinRT / Tesseract），配置化 | 有 OCR，但后端选择未暴露 |
| **命令广度** | 68 条 | 12 类 unit |

**结论：在自动化能力的广度上 ACRPA 明显领先。** 该抄的是"如何把这些能力组织好、讲清楚、做得稳"，而不是"还缺什么功能"。

---

## 7. 建议的落地优先级

按投入产出比排序，与路线图现有分期对齐：

**阶段一（v0.1.30，随 P0 修复一起做）**

1. `commands.register()` 增加 `explain=` 字段，68 条命令补齐解释文本（约半天，同时喂 tooltip / 帮助窗口 / 未来 i18n）

**阶段二（v0.2.0）**

2. **参数表单组件化** —— 新建 `ui/units/inputs.py`（Number/Enum/Bool/Text/Color 五类输入控件），把 `commands.schema()` 接上去，替换 9 个裸参数格
3. **色匹配** —— 用 Pillow 实现（主包已有），无需引入新依赖，是本章清单里成本最低的功能项
4. **组件拆分对齐 `Units/` 粒度** —— 与 §3.2 已有的拆分计划合并执行，不额外立项

**阶段三（v0.3.0）**

5. **分辨率 / 多显示器适配** —— 工作量最大的一项：需要在脚本元数据里记录目标分辨率，加载时做坐标变换，并提供 `ImportScaled` / `ImportAsIs` 两种导入策略。建议先做"检测 + 警告"（对应 `ResolutionMismatchTitle`），再自动变换
6. **全局 ROI** —— 脚本级配置项，命令未显式指定区域时回退到全局 ROI
7. **随机偏移 / 渐变移动** —— 反检测场景
8. **i18n** —— 采用 §4.2 的扁平 JSON 方案

---

## 附：本次分析的证据来源

| 证据 | 路径 |
| --- | --- |
| 依赖清单与版本 | `data/AutoClicker.deps.json` |
| 运行时配置 | `data/AutoClicker.runtimeconfig.json`（`.NETCoreApp v8.0 / win10-x64`） |
| 功能集反推 | `data/Resources/Languages/en.json`（467 key）+ `zh-CN.json`（467 key，零缺失，已程序化校验） |
| 组件分层 | `data/Components/{Pages,Units,Generic,Layout,Recordings,Workshop,MudBlazor,SortableJS}/` |
| 静态资源 | `data/AutoClicker.staticwebassets.endpoints.json`（含每个资产的 sha256 integrity 与 fingerprint） |
| 体积 | 根目录 exe 22.5 KB，`data/` ~269 MB，340 个 DLL |

**方法局限**：对象为编译产物，无源码。功能集推断基于 i18n key 的语义命名（命名与 UI 控件直接对应，可信度较高），但算法参数、内部数据结构、性能特征无法确认。所有推断项已在正文中标注。
