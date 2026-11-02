# 参考分析：AutomationOperation 浏览器能力对标与 ACRPA 复用落点

> 对标对象：`AutomationOperation-v4.9.2`（第三方闭源脚本插件）
> 本文只做**能力/参数语义层面的对标**与 ACRPA 的**独立实现**记录；不含任何对方源码、DLL、模板文件或其 schema 的拷贝。
> 相关既有文档：[`浏览器后端增强设计.md`](浏览器后端增强设计.md)、[`浏览器使用说明.md`](浏览器使用说明.md)、[`rpa-architecture-benchmark.md`](rpa-architecture-benchmark.md)

---

## 0. 净室与许可约束（硬约束，先读）

1. 对标对象是**闭源商业插件**：目录内有 `integrity.manifest.json` / `.sig`、`AutomationOperation.exe`、`Microsoft.Playwright.dll`、`OpenCvSharp*.dll`、`OcrWorker.exe`，无 LICENSE 文件。
2. 因此本次落地遵守 [`浏览器后端增强设计.md`](浏览器后端增强设计.md) 第 0 节同一原则：
   - **可借鉴**：用户可见的操作分类、参数语义（"打开/接管/关闭"三态、"通道/浏览器类型"、"等待模式"、"选择器+类型"二元组）、通用工程套路（会话复用、失败重试、步骤级失败策略）。这些是 Playwright/Selenium 生态的公开通行概念。
   - **不借鉴**：任何实现代码、DLL 引用、`.playwright` 内核、模板 JSON、`skills.bundle.json` 内的提示词/字段表原文。
3. 本轮新增代码全部基于 **Playwright 公开 API**（`launch_persistent_context`、`channel=`、`wait_until=`、`select_option`、`locator.count/inner_text/get_attribute`）独立编写，无新增第三方依赖。

---

## 1. 对标对象的浏览器实现形态（探测结论）

| 探测点 | 证据 | 结论 |
|---|---|---|
| 自动化内核 | `Microsoft.Playwright.dll`、`playwright.ps1`、`.playwright/`（约 87 MB node + 浏览器内核） | .NET + Playwright，内核随包分发 |
| 浏览器种类 | 模板中 `BrowserType` 取值同时出现 Chrome / Edge | 走 **channel**（系统已装浏览器）而非仅内置内核 |
| 会话模式 | 同一"浏览器管理"操作含 打开 / 接管 / 关闭 | 会话有显式三态，接管走 CDP |
| 登录态 | `UserDataDir`、`UsePersistentContext` | **持久化上下文**，profile 落盘复用 |
| 图像/验证码 | `OpenCvSharp.dll`、`opencv_world4120.dll`、`OcrWorker.exe`、ONNX Runtime | 模板匹配 + OCR 本地推理，用于滑块/点选验证码 |
| 步骤包络 | 每个操作都带 `Description`、`IsCollapsed`、`FailureHandlingMode`、`JumpTargetIndex`、`IncludeInBatchExecution` | 步骤级公共字段 + **失败策略/跳转目标**下沉到单步 |

---

## 2. 能力矩阵（对标 → ACRPA 现状）

| 能力域 | 对标语义 | ACRPA 本轮前 | 判定 |
|---|---|---|---|
| 会话打开 | 指定浏览器类型、无头、profile 目录、起始 URL、视口/最大化 | 仅内置 Chromium + 读取 `browser_headless` / `browser_slow_mo` | **补强**（本轮） |
| 会话接管 | CDP endpoint 接管已开浏览器 | 已有 `连接已开浏览器` / `接管浏览器` | 已对齐 |
| 会话关闭 | 显式关闭并复位，可再次打开 | 有 `browser_close()` 但**无命令**；关闭后 `_init_attempted` 常驻，**会话不可重建** | **补强**（本轮） |
| 导航 | URL + 等待模式（dom/load/networkidle）+ 超时 | `页面跳转` 固定 `domcontentloaded`、固定 30s | **补强**（本轮） |
| 等待元素 | 选择器类型 + 出现/消失/可见等状态 + 超时 | 有 `浏览器等待元素`（出现/消失） | 已对齐 |
| 点击/输入/上传/下载/截图/frame/tab/cookie | 全套 | 已实现（"浏览器自动化"分组 26 条，本轮前 22 条） | 已对齐 |
| 读取输出 | 元素文本/属性/值/数量，页面 URL/标题 | 只能靠 `执行JS` 变通，无专用命令 | **补强**（本轮） |
| 下拉选择 | 按标签/值/序号 | 无 | **补强**（本轮） |
| 选择器类型 | "选择器 + 类型"二元组（CSS/XPath/Text/ID/Name/Class/Tag/LinkText） | 纯前缀 DSL，无类型字段 | **补强**（兼容层，本轮） |
| 滑块验证码 | 句柄/背景/缺口三选择器 + 拖动时长/步数/偏移修正 + 匹配阈值 + 成功检测 + 重试/刷新 | 有 OCR 后端与 DD 鼠标拖拽，但无浏览器内滑块闭环 | **排期**（P2，见 §6） |
| 多浏览器实例 | `BrowserId` 引用实例 | 单会话（`_pages` + 活动页） | 排期（P3，收益中等） |
| 步骤级失败策略 | 停止/继续/跳转目标 | 仅全局"出错即停"开关 `STOP_ON_ERROR` | 排期（P2，与 UI 列承载联动） |

**不抄的部分（明确否决）**：

| 项目 | 否决理由 |
|---|---|
| 内置 `.playwright` 约 87 MB 内核随包分发 | 安装包体积换便利性；ACRPA 走 `channel` 复用系统 Chrome/Edge 即可规避 |
| 引用 `OpenCvSharp` / `OcrWorker` | 违反"不新增第三方依赖"红线；滑块缺口检测用 PIL/numpy 列差分即可 |
| 复制其模板 JSON / 操作 schema | 净室约束，且 ACRPA 用 Excel 行 + 命令注册表表达同一语义 |
| 其 `skills.bundle.json` 内的操作说明文本 | 属于其产品内的提示词资产，不复用 |

---

## 3. 本轮落地清单（已实现，可回归）

### 3.1 启动参数层（`src/browser_backend.py`）

新增 `_launch_options()` 合并「state 默认值 + 命令级覆盖」，供 `_get_page()` 使用：

| 字段 | 来源 | 语义 |
|---|---|---|
| `channel` | `BROWSER_CHANNEL` / 命令参数 | `chrome` / `edge` 走 `channel=`，复用系统浏览器；`firefox` / `webkit` 换引擎；空/未知回退内置 Chromium |
| `persistent` + `user_data_dir` | `BROWSER_PERSISTENT`、`BROWSER_USER_DATA_DIR` | 走 `launch_persistent_context()`，登录态/cookie 跨脚本保留；目录缺省为 `CONFIG_PATH/browser_profile` |
| `viewport` | `BROWSER_WINDOW_WIDTH/HEIGHT` | 两值都 >0 才生效 |
| 最大化 | 推导 | 无视口 + 有头 → 追加 `--start-maximized` |
| `slow_mo` / `args` / `headless` | state | 保持旧行为 |

配套修正：

- `_cleanup()` 现在会关闭 `_context`（持久化上下文只能经 context 关闭），并复位 `_init_attempted` 与命令级覆盖 —— 修复「关闭一次后无法再开」的隐性缺陷。
- `atexit` 兜底关闭浏览器，避免脚本异常中断后残留 `chrome.exe` / driver 进程。

### 3.2 新增命令（4 条，纯追加）

| 命令 | 参数 | 说明 |
|---|---|---|
| `浏览器管理` | 模式(打开/接管/关闭), 浏览器类型, 无头, 用户数据目录, 起始网址 | 打开时先收尾旧会话再按新参数启动；接管模式第 2 列填 endpoint；关闭等价 `关闭浏览器` |
| `关闭浏览器` | 无 | 释放进程并复位启动覆盖，下次命令按配置重新拉起 |
| `浏览器读取` | 定位(可空), 模式(文本/属性/值/HTML/数量/URL/标题/存在/可见), 写回变量, 属性名, 选择器类型 | 元素级与页面级统一入口，替代"执行JS 取文本"的变通写法 |
| `浏览器选择` | 定位, 选项, 方式(标签/值/序号), 选择器类型 | `select_option` 优先；非原生下拉自动降级为"点开容器→点选项文本" |

### 3.3 既有命令扩展

- `打开网页`：参数 2 = 等待模式（默认/快速→`domcontentloaded`，加载完成→`load`，网络空闲→`networkidle`，提交→`commit`），参数 3 = 超时秒。**不传参时代码路径与旧版一致**。

### 3.4 选择器类型兼容层

`parse_selector(spec, sel_type)`：把「选择器 + 类型」归一为现有 DSL 串（`css:` / `xpath:` / `text:` / `text=` / `#` / `[name=""]` / `.a.b` / `tag:`）。类型为空/`auto`/未知时**原样透传**，不改变既有 DSL 解析顺序。

价值：①消除"文本里含 `#`、`.` 被误判为 CSS"的歧义；②对标的模板可机械迁移成 ACRPA 的行，不需要人工改写定位串。

### 3.5 配置项（`src/state.py`，纯追加）

`browser_channel`、`browser_user_data_dir`、`browser_persistent`、`browser_window_width`、`browser_window_height`，默认值均不改变现有行为；设置窗口「高级」卡新增 类型 / 持久化登录态 / Profile 目录 三项。

生效时机：这些是**会话启动参数**，改动后需下一次拉起会话才生效（或脚本首行用 `浏览器管理, 打开, ...` 显式指定，命令级覆盖优先于设置项）。

### 3.6 命令注册与帮助

- `src/commands.py` 注册 4 条新命令；`src/engine.py` 绑定 handler（与现有浏览器命令同一 `try/except ImportError` 块）。
- `res/help/command_groups.json` 浏览器组补齐；`res/help/command_schema.json` 重新导出（92 条）。

---

## 4. 收益

| 维度 | 之前 | 现在 |
|---|---|---|
| 安装体积/首启 | 必须 `playwright install chromium`（约 150 MB） | 选 Chrome/Edge 通道即可复用系统浏览器，**零内核下载** |
| 登录态 | 每次干净 profile，需重复登录/扫码 | 持久化上下文，profile 落盘复用 |
| 会话生命周期 | 关闭后无法重建；退出残留进程 | 可关闭可重建；进程退出兜底清理 |
| 结果取值 | 只能"执行JS + 拼返回" | `浏览器读取` 一行取值并写回变量 |
| 导航稳态 | 固定 DOM 就绪，SPA 易读到空壳 | 可按"网络空闲"等待，或自定义超时 |

---

## 5. 回归与验收

```powershell
python tools\_test_browser_manage.py    # 命令归一 / 通道映射 / 等待模式 / 选择器类型 / 启动参数合并
python tools\_test_browser_locator.py    # 定位 DSL 未被类型兼容层破坏
python tools\_test_browser_waiter.py
python tools\_test_command_schema.py     # 注册表 ↔ schema ↔ 分组 三方一致
```

`_test_browser_manage.py` 同时断言两条硬约束：`import browser_backend` **不触发** `import playwright`；`atexit` 兜底已注册。前四条纯逻辑用例已加入 `tools/run_tests.py` 的 `SAFE_BROWSER_LOGIC`，纳入核心子集 CI（此前被 `_test_browser_*` 前缀规则整体排除）。

真实浏览器手工验收（需装机环境）：

1. `浏览器管理, 打开, Edge, 否` → 独立窗口出现，标题栏为 Edge。
2. 手动登录某站点后 `关闭浏览器`，再 `浏览器管理, 打开, Edge, 否, <同一 profile 目录>` → 登录态仍在。
3. `打开网页, <SPA 地址>, 网络空闲, 20` → 元素在跳转返回时已可读。
4. `浏览器读取, .price, 文本, price` + `打印变量, price` → 输出价格文本。
5. `浏览器选择, #city, 北京` → 原生 `<select>` 与自定义下拉两种页面均生效。

---

## 6. 排期项（本轮不做，附设计方案）

### 6.1 P2 滑块验证码（`浏览器验证码` + 模式=滑块）

参数语义对齐对标对象，但实现全自研：

- 输入：`句柄定位`（滑块按钮）、`背景图定位`、`缺口图定位`（可选）、`拖动时长ms`、`拖动步数`、`偏移修正px`、`成功检测定位`、`校验超时s`、`重试次数`、`刷新定位`（可选）、`选择器类型`。
- 缺口偏移：优先用 `缺口图定位` 的元素截图求左缘；不可用时对背景图做灰度列梯度差分取最大跳变（PIL/numpy，无需 OpenCV）。
- 拖动：`page.mouse.move/down` + 分段缓动（前段快、末段减速），每步 `sleep` 均分总时长；松手后轮询 `成功检测定位`，失败按 `刷新定位` 重试。
- 边界：仅处理"同页可见滑块"，不破解行为风控；文档标注"合规使用，仅限自有系统"。

### 6.2 P2 步骤级失败策略

对标 `FailureHandlingMode` + `JumpTargetIndex`（停止/继续/跳转到第 N 步）。ACRPA 现状只有全局 `STOP_ON_ERROR`。

落地要点的 UI 约束：**不新增常显列**。设计为表格行元数据 + 选中行详情面板编辑（见 [`UI承载与刷新优化方案-tk承载力补救.md`](UI承载与刷新优化方案-tk承载力补救.md) §3.2），`#0` 序号列尾随一个错误策略角标（`⊘` 停止 / `↷` 继续 / `⇥N` 跳转），信息量靠角标而非列。

### 6.3 P3 多浏览器实例

`sessions: {id: {page, browser, context}}` + 命令可选 `会话ID`，默认 `default`。收益在于并行采集与 A/B 对照；成本是活动页/帧/cookie 全部要带会话维度，暂缓。

---

## 7. 变更文件一览

| 文件 | 变更 |
|---|---|
| `src/browser_backend.py` | 启动参数层 + `parse_browser_mode/parse_wait_until/parse_selector/browser_channel_kwargs/_launch_options` + 4 条命令 + `_cleanup` 修复 + `atexit` |
| `src/state.py` | 5 个浏览器键 + `log_max_lines` |
| `src/commands.py` | 4 条 `register()` |
| `src/engine.py` | 4 条 handler 绑定 |
| `src/ui/settings/cards/advanced.py` | 类型 / 持久化 / Profile 三项 + `apply()` 写回 |
| `res/help/command_groups.json`、`command_schema.json` | 浏览器组补齐、schema 重导出 |
| `tools/_test_browser_manage.py` | 新增纯逻辑验收 |
| `tools/run_tests.py` | `SAFE_BROWSER_LOGIC` 白名单（5 个纯逻辑浏览器用例进核心子集） |
