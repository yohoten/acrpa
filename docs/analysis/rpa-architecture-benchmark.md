# ACRPA 架构借鉴与演进方案（RPA 三方对标）

> **对标对象**
> - **A** = `F:\（8）Desktop\rpastudiobook`：开源 RPAStudio 配套书（121 篇 md，CC BY-SA 3.0）+ C#/WPF 示例（BSD-3）
> - **B** = `F:\（8）Desktop\Files\script_plugin\AutomationOperation\AutomationOperation-v4.9.2\AutomationOperation`：已编译商用自动化工具（.NET + OpenCvSharp + onnxruntime + RapidOCR/Paddle + Playwright + ADB/HID，94 MB exe，无开源许可）
> - **本项目**：ACRPA v0.1.30-beta（Python 3.12 + Tkinter，PyInstaller onefile ≈ 14.3 MiB）
>
> 日期：2026-11-01　详细原始分析：`docs/参考分析-RPAStudio与AutomationOperation借鉴方案.md`
> 关联：`docs/ACRPA-完善路线图.md`（§③ 脚本格式无 schema）、`docs/落地总清单-ACRPA能力补强与工作流拓展.md`、`docs/开发计划-逐级实施.md`

**体积红线口径（v0.1.30 起生效）**：打包上限由 15 MB 放宽为 **300 MB**（`build.py::MAX_EXE_MB = 300.0`，`tools/_check_volume.py::DEFAULT_MAX_MB`）。
影响本文全部取舍：早期以「塞不进 15 MB」为由否决的项（ONNX 模型、独立 OCR worker、内置依赖）**不再因体积被否**，判据改为**许可 + 冷启动 + 安装包体积 + 维护面**四项。体积优化仍是默认取向（`excludes` 与体积报告继续保留），只是不再卡死构建。

---

## 一、三方定位速览

| 维度 | ACRPA | RPAStudio（A） | AutomationOperation（B） |
|---|---|---|---|
| 语言/栈 | Python + Tkinter，单进程 | C#/.NET + WPF + Windows Workflow Foundation，多进程 | C#/.NET，编译后二进制（闭源） |
| 脚本模型 | `.xls` 表格即脚本（88 条扁平命令） + 自研工作流视图 | WF 活动（Activity）+ 序列/流程图/状态机 | 流程图节点（JSON 模板，`OperationType` + 命名字段） |
| 架构 | 平铺模块函数库（`src/*.py`）+ `ACRPA.py` 主文件 | 接口-实现分层（`RPA.Interfaces` / `RPA.Services`）+ Autofac IoC + AppDomain 隔离 | 主程序 + 独立 `OcrWorker` 进程 |
| 输入 | SendInput + DD 驱动（`src/dd_backend.py`） | 标准键鼠 | SendInput / 外接 HID(esp32) / ADB / 罗技 GHUB 四通道 |
| OCR | 4 种后端（`src/ocr_backend.py`），PaddleOCR DLL 可选（`src/paddle_dll.py`） | — | PaddleOCR(CPU) + RapidOCR(onnx) + 云端 API 三源 |
| 多机 | NetLink（自研，内网） | 控制器（Web，书中未实现） | — |
| 开源程度 | 开源 | 书 CC BY-SA 3.0 / 代码 BSD-3 | 闭源，带 `integrity.manifest.json` + RSA 签名 |

**核心判断**：两者价值都**不在代码可移植性**，而在**数据契约**。
- A 的技术栈（WF Activity / WPF / AppDomain）与 Python + Tkinter 无交集，可借鉴的是分层与进程边界这类语言无关的设计；
- B 的真正资产是 `Templates/Reference/*.json` 与 `config/appsettings.json` 里那套**「步骤 = OperationType + 命名字段 + 变量表 + 执行设置」**的结构——正好补上 ACRPA 的最大欠账。

ACRPA 现状证据（读代码核对）：
- 脚本只有位置槽：`COLUMNS = ["命令类型"] + ["参数1".."参数9"]`（`src/scriptdata.py:9`）；
- 命令元数据只有中文名：`res/help/command_schema.json` `stats = {commands: 88, with_schema: 79, with_choices: 29, no_params: 9, variadic: 1}`，每条仅 `name/description/params_text/signature/params/has_handler`，无稳定英文键、无分类、无单位/范围；
- 模板只有裸文件：`template/` 下 12 个 `.xls` + 1 个 `.bak` + `workflow001.json`，无索引无 preview；内置片段 `TEMPLATE_METADATA`（`src/templates.py:9`）只有 `rows/category`；
- 变量只是运行时 dict：`Interpolator(variables=...)`（`src/interp.py:182`），无声明层；
- 执行设置散在全局 `state` 与 `src/scheduler.py`，脚本自身不携带循环/超时/失败策略。

---

## 二、优先级总表

| 优先级 | 借鉴项 | 依据 | 增量体积 |
| --- | --- | --- | --- |
| **P0** | 命令字段字典（88 命令 → 稳定英文键 + 分类/别名/单位/枚举/范围） | 现有 79/88 schema 只有中文名，改名即读不回 | < 60 KB |
| **P0** | 结构化脚本 `.acrpa.json`（与 `.xls` 双向无损） | B `Templates/Reference/微信发送信息.json` | < 40 KB |
| **P0** | 模板索引 `template/index.json` | B `template-index.json`（id/name/category/description/preview） | < 20 KB |
| **P1** | 声明式变量表（名/类型/默认值/必填/启用） | B `ConfigVariables`；ACRPA 只有运行时 dict | < 25 KB |
| **P1** | 脚本级执行设置（循环/超时/窗口四元组/失败策略） | B `LoopSettings` / `ExecutionSettings` / `BoundWindow*` | < 20 KB |
| **P1** | 「中文描述 → 流程」生成器（目录载荷 + 强制 JSON + 三道校验 + 预览插入） | B `Assets/skills.bundle.json` 的 operation-catalog | < 15 KB |
| **P2** | 扩展包元数据升级（`id/version/minAppVersion/dependencies`） | A `RPA.Interfaces/Nupkg/*.cs` | < 30 KB |
| **P2 缓做** | 设计器/执行器/机器人进程分离 + 命名管道 | A 三进程；ACRPA 已有 `src/cli.py` + `src/single_instance.py`(WM_COPYDATA) | 回归面过大 |
| **P3（新，受红线放宽解锁）** | 独立 OCR worker 进程 / ONNX 轻量引擎 / YOLO 找物 | B `OcrWorker.exe`、`rapidocr_models/`、`yolo11n.onnx` | 见 §七 |

全部 P0–P2 为纯 Python 数据/文本改造，**总体积增量 < 200 KB**。

---

## 三、能力差距矩阵

| 维度 | ACRPA 现状 | B | A | 结论 |
| --- | --- | --- | --- | --- |
| 步骤模型 | `.xls`：`命令类型` + `参数1..9` 位置槽（`src/scriptdata.py:9`） | `OperationType` + 命名字段 | WF Activity 属性 | **最该补**（P0） |
| 动作规模 | 88 注册 / 79 有 schema / 29 含 choices | 70+ `OperationType` 逐条字段释义 | 活动库 | 数量不输，缺元数据 |
| 模板 | 裸 `.xls`，无索引无 preview | `index.json` + category + preview | `.xaml` 模板 | 直接可抄字段（P0） |
| 变量 | 运行时 dict + `--var`（`src/interp.py:182`） | 声明式 + 类型 + 默认值 | Activity 变量 | 补声明层（P1） |
| 执行设置 | 全局 `state` + `scheduler.py` | 脚本级 Loop/Timeout/窗口绑定 | 无 | 补脚本级（P1） |
| 输入通道 | 键鼠（SendInput/DD）/ 浏览器 / OCR | 前台 / 后台 / ADB / HID 四通道 | 无 | 参考，后台点击缓做 |
| 视觉 | 模板匹配 + 4 种 OCR 后端 | + ONNX OCR + YOLO | 无 | **体积已解禁**，见 §七 |
| 扩展包 | `.acrpapkg` zip + sha256 + 白名单 | 无 | Nupkg + 仓库服务 | 只借字段 |
| 调试 | 断点 + 单步（`src/ACRPA.py:1700`） | 未公开 | 完整调试服务 | 已够用 |
| 进程模型 | 单进程（子线程执行 + `_edit_blocked` 守卫） | 主程序 + OcrWorker | 设计器/执行器/机器人三进程 | 参考，缓做 |

---

## 四、P0 设计

### 4.1 命令字段字典 `src/command_fields.py`

痛点：schema 用中文名当键，改名即读不回；无分组/单位/范围，参数面板与 AI 生成只能靠标签对齐。

```python
COMMAND_FIELDS = {
    "找图": {
        "key": "image.find", "category": "图像识别",
        "aliases": ["找图片", "find image"], "summary": "查找目标图片并定位",
        "fields": [
            {"name": "图片名", "key": "image", "kind": "string", "required": True},
            {"name": "精度", "key": "threshold", "kind": "number",
             "default": "0.96", "range": [0.5, 1.0]},
        ],
    },  # …88 条
}
```

落地路径：
1. `tools/gen_command_fields.py` 从 `res/help/command_schema.json` 自动生成骨架（`name/kind/default/optional/choices` 已可取），仅「分类/别名/单位/范围」需人工补齐；
2. `commands.schema()` 改为**优先查字典、查不到回落文本解析**，20+ 存量消费方（参数面板、帮助、AI 目录）零改动。

验收：覆盖 88/88；`key` 全局唯一；参数顺序与旧实现逐条一致；生成器幂等（二次运行 `git diff` 为空）。成本 S。

### 4.2 结构化脚本 `.acrpa.json`（`.xls` 仍是真源，不迁移）

```jsonc
{
  "format": "acrpa.script/1",
  "name": "微信发送信息",
  "variables": [{"name": "wx_contact", "type": "string", "value": "群名", "enabled": true}],
  "settings": {"loop": {"mode": "count", "count": 1},
               "timeout": {"minutes": 30},
               "target": {"title": "微信", "process": "WeChat", "class": "WeChatMainWndForPC", "hwnd": ""},
               "onError": "stop"},
  "steps": [
    {"op": "window.activate", "title": "微信", "process": "WeChat"},
    {"op": "keyboard.input", "text": "{wx_contact}", "method": "clipboard"},
    {"op": "key.press", "main": "ENTER", "delayAfter": 500}
  ]
}
```

映射（新增 `src/script_json.py`）：`op` ↔ 第 1 列（经 `COMMAND_FIELDS[*]["key"]` 反查中文命令名）；字段按声明顺序落第 2..10 列；未登记命令直接以中文名作 `op`（前向兼容）。

约束：
- 往返无损，夹具用 `template/*.xls` 12 个；
- 无新依赖（`json` + 既有 xlrd/xlwt）；
- 写盘复用 `src/script_package.py` 的临时文件 + 原子替换；
- GUI 先只加文件类型过滤，不引入双内存模型。

验收：`tools/_test_script_json.py` 四例——往返一致 / 未知命令不丢且可回写 / `"0.96"` 与 `0.96` 归一 / 缺必填能报行号。成本 M。

### 4.3 模板索引 `template/index.json`

```json
{"version": 1, "templates": [
  {"id": "im.send_message", "name": "IM 消息发送", "category": "办公",
   "description": "激活窗口 → 搜索联系人 → 粘贴发送",
   "fileName": "im_message_sender.xls", "preview": "1 激活\n2 Ctrl+F\n3 粘贴\n4 回车"}]}
```

`src/template_index.py`：加载 + 校验（`fileName` 与磁盘一一对应、`category` 白名单）+ 坏文件隔离（不影响启动）。插入片段对话框按 category 分组、按 name/description/aliases 搜索。

验收：索引与 `template/` 磁盘文件一一对应（缺/多均报错）；插入对话框可分组与搜索。成本 S，**投入产出最高**——现有 12 个 xls 只能靠文件名猜用途。

---

## 五、P1 设计

### 5.1 声明式变量（对齐 B `ConfigVariables`）

`src/script_vars.py`：`{name, type(string|number|bool|list), value, enabled, required, desc}`。

价值：运行前变量体检、市场脚本参数化、AI 先生成变量再生成步骤。规则：声明值与 `--var` 冲突时 **CLI 优先**；`Interpolator` 的单遍展开/转义/脱敏语义（`src/interp.py:132-165`）保持不变。成本 M。

### 5.2 脚本级执行设置

`settings` 四段落到 `src/script_settings.py`，与 `src/scheduler.py` 解耦：

| 字段 | 语义 | 实现要点 |
| --- | --- | --- |
| `loop.mode` | `none / count / infinite` | `infinite` 需二次确认 |
| `timeout.minutes` | 脚本级超时 | 复用引擎既有超时检查点，**不开线程** |
| `target` | 窗口四元组 `title/process/class/hwnd`（借 B `BoundWindow*`） | 配合 `激活窗口` 做前置校验；缺失给「窗口未找到」 |
| `onError` | `stop / continue / retry:N` | 接引擎已有的 `failed_row`（`cli._record_history`） |

验收：默认值 == 当前行为（向后兼容）；超时落 `failed_row`。成本 M。

### 5.3 「中文描述 → 流程」生成器

新增 `src/ai_steps.py`，复用 `src/ai_client.py` / `src/ai_enhance.py`：

1. `build_catalog_prompt()` 由 `COMMAND_FIELDS` 生成按 category 分组的目录载荷（约 6–8 KB，可本地缓存）；
2. 强制模型只输出 `{"steps": [...]}`，失败重试 1 次后放弃（不插入）；
3. 三道校验：`op` 存在 → 必填齐全 → 枚举合法；不过则只在预览窗标红；
4. 通过后复用 `_insert_snippet_rows` 的「追加 / 插到选中行前 / 替换全部」。

验收：10 条离线夹具（中文描述 → 期望 op 序列），不依赖真实 API。成本 M（依赖 §4.1）。

---

## 六、P2

**扩展包元数据**：沿用 zip + sha256 + 白名单（`src/script_package.py` + `src/marketplace.py`），仅补 `id/version/minAppVersion/dependencies`（安装前版本闸门与依赖提示）。不引 NuGet 格式、不引远程仓库协议（口径见 `docs/marketplace-v2-design.md`）。成本 S。

**编辑/执行分离：建议缓做**。A 的三进程解决「崩溃拖垮 IDE」「无人值守」，而 ACRPA 已有 `src/cli.py`（历史/失败行）与 `src/single_instance.py`（WM_COPYDATA），执行在子线程 + `_edit_blocked` 守卫。改造要动 `engine` 全局与 `state` 单例，回归成本远超收益。
**触发条件**：① 多租户调度需互不影响；② headless 服务器部署；③ 调试暂停时仍需改脚本。

---

## 七、B 的功能地图（体积解禁后的取舍）

原先「体积不允许」的判断已随 300 MB 红线失效，以下按新判据（许可 / 冷启动 / 安装包体积 / 维护面）重排。

| # | B 的能力 | 证据 | 新判据下的结论 |
| --- | --- | --- | --- |
| 1 | OCR 三源：Paddle(CPU) + RapidOCR(onnx) + 云端 API，`OcrWorker.exe` 独立进程，`EnableOcrWarmupOnStartup` / `OcrCpuThreads` / `OcrTextRecognitionBatchSize` 可配，验证码强制本地（`OcrCaptchaForceLocal`） | `config/appsettings.json`、`OCR_CPU/` | **半采纳**：ACRPA 已有 4 种后端，「独立 worker 进程」值得做（崩溃隔离，纯 Python 用 `multiprocessing` 即可，不增体积）；模型随包分发仍不做（见 §九） |
| 2 | 设备抽象：`AutomationMode ∈ {外部HID, ADB, 默认}`、`ExternalHidDevices/esp32-uart*.json`、`AdbDevices/default-adb.json`、`AutomationGHUB.driver.dll` | 同名目录/配置 | **借接口不借实现**：把 `dd_backend.py` 与 `写入` 命令的 `direct/simulate` 收敛为统一 `InputBackend` 抽象；HID/ADB 依赖强、合规边界不清，暂不进主包 |
| 3 | 窗口绑定：`BoundWindowTitle/ProcessName/ClassName/Hwnd` + `WindowBindingResolveMode=AutoRelocate` + `WindowMatchMode=TitleContains` + 绑定组 | `Templates/*.json`、`appsettings.json` | **采纳字段**（=§5.2 的 `target`），窗口移动后仍稳定命中 |
| 4 | 随机延迟：`UseRandomOperationDelay` + `Min/MaxOperationDelayMilliseconds` | `appsettings.json` | **采纳**：`src/engine.py` 固定 delay 加抖动区间，防检测 |
| 5 | YOLO 找物：`YOLO检测_示例.json`（`yolo11n.onnx`）、`滑动验证码测试.json` | `Templates/Reference/` | **暂缓**：体积可行（≈5 MB），但 ultralytics 系模型多为 AGPL-3.0，分发需法务确认；先做「用户自备模型目录 + 能力探测」 |
| 6 | 模板/变量 schema：`template-index.json`（6 字段）、`runtime-variables.json`（`Kind/Scope/Lifetime/SourceProcessId`） | `Templates/Reference/` | **采纳结构**（P0-3 / P1-1） |
| 7 | 录屏 / 压缩 / 调度：`ffmpeg.exe`（96 MB）、`7z.dll`、`TimeSlots` 调度、`MaxBackgroundWorkerCount=5` | 程序目录 | **不做随包**：ffmpeg 为 GPL 构建、node 87 MB 亦属分发义务；改用「检测系统已装 ffmpeg / 用户自备」 |
| 8 | AI 凭据：DeepSeek（`AiApiUrl/AiApiModel`），Token 用 DPAPI（`dpapi:v1:ai:...`） | `appsettings.json` | ACRPA 已用 Windows 凭据库，做法等价，**互证不移植** |

---

## 八、A 的架构思想与借鉴程度

| # | 设计 | A 的做法 | ACRPA 借鉴结论 |
| --- | --- | --- | --- |
| 1 | 接口-实现分层 | `RPA.Interfaces` 按域分目录（`Activities/App/AppDomains/ExpressionEditor/Nupkg/Project/Service/Share/Workflow`），`RPA.Services` 一一对应，Autofac 注册 | **长期目标**：`ACRPA.py` 巨文件与平铺 `src/` 可按域拆包；本期不动（回归面大） |
| 2 | AppDomain 隔离 | 活动在独立 AppDomain 加载，可热卸载 | Python 无等价物；等价物是 `subprocess` + `importlib`。已有 `src/py_sandbox.py` 覆盖大半 |
| 3 | 三进程（Designer/Executor/Robot） | 经 IPC 通信，`WorkflowRunManager` 管运行、`VisualTrackingParticipant` 管断点高亮 | **缓做**（§六） |
| 4 | 活动组件化 + Nupkg | `Activity` 抽象基类，组件库按需安装 | 只借元数据字段（§六），不借包格式 |
| 5 | 多视图设计器 | 流程 / 可视化 / 源码 / 属性 / 大纲 / 调试 / 输出 | ACRPA 已有表格 + 流程图 + Mini Bar，**可补「源码/文本视图」对照** |
| 6 | 跟踪调试 | `TrackingParticipant` 把调试状态建模成事件流 | `src/workflow.py` 已有执行高亮与标红，可把状态抽成事件流，供「调试视图」 |
| 7 | 日志接管 | `LogToOutputWindowTextWriter`（TextWriter 适配器） | 自查项：确认底部日志面板已统一接管第三方输出 |

---

## 九、可复用资源

### 9.1 可直接复用（注意署名）

| 资源 | 路径 | 用法 | 许可 |
| --- | --- | --- | --- |
| NSIS 安装脚本骨架 | `rpastudiobook/samples/RPAStudioSetup/make-setup/nsis-scripts/script.nsi` | 改造为 ACRPA 安装器（当前只有 onefile，无安装包） | BSD-3（保留声明） |
| 模板索引字段契约 | B `Templates/Reference/template-index.json` | 照搬字段名，内容自写 | 通用结构 |
| 流程 JSON 四段式 | B `Templates/Reference/微信发送信息.json` | `LoopSettings / ExecutionSettings / ConfigVariables / Operations` | 结构非表达 |

### 9.2 可借鉴思路（自己实现，勿拷文本）

| 主题 | 出处 | 借什么 | 落点 |
| --- | --- | --- | --- |
| 动作目录当提示词载荷 | B `Assets/skills.bundle.json` | 「op + 字段语义 + 约束 + 反例」喂模型 | §5.3 |
| 字段语义密度 | 同文件 operation-catalog | 一句一字段的写法 | `fields[].help` |
| 窗口四元组 | B `Templates/*.json` | Title/Process/Class/Hwnd | §5.2 |
| 输入通道枚举 | B `appsettings.json` `AutomationMode`、`AdbDevices/`、`ExternalHidDevices/` | 前台/后台/ADB/HID | §七-2 |
| Python 活动入参 | A `RPA.Learn.Activities/Python/InvokePythonFileActivity.cs`（`PythonFilePath/WorkingDirectory/SysPathAppendList/Arguments/ContinueOnError` + stdout 重定向） | 自查 `src/py_sandbox.py` 缺项（尤其 sys.path 追加、错误继续） | 自查清单 |
| 工作流服务职责 | A `RPA.Interfaces/Workflow/IWorkflow{Designer,Debug,Breakpoints}Service.cs` | 接口边界（非实现） | 断点已有，作自查 |
| 扩展包服务切分 | A `RPA.Interfaces/Nupkg/IPackage{Export,Import,Repository}Service.cs` | 导入/导出/仓库三分 | `script_package.py` + `marketplace.py` |
| IDE 面板清单 | A `books/zh/13.5.02~13.5.14` | 8 面板信息架构 | 工作流视图评审 |
| 三方库日志接管 | A `RPA.Interfaces/Share/LogToOutputWindowTextWriter.cs` | TextWriter 适配器 | 确认日志面板已接管 |

### 9.3 不可用（明确不做）

| 资源 | 风险 |
| --- | --- |
| `rpastudiobook/tools/actipro-wpf-controls-*.zip`（57.6 MB） | 商业控件试用包，禁止再分发 |
| A `samples/**` 的 338 个 C#/WPF 文件 | 栈不匹配，移植成本 ≫ 收益 |
| A `books/zh/*.md` 正文/插图 | CC BY-SA 3.0 传染性，闭源文档不可抄 |
| A `tools/*.exe`（CPython 3.7、.NET 4.6.1 离线包） | EULA 限制 + 版本过时 |
| B `OCR_CPU/`（paddle_inference 99 MB + mkl 142 MB）、`inference/*.pdiparams` | 总体虽已进 300 MB 内，但装上即 ≈255 MB 安装包、冷启动数秒，且与 onnxruntime 不兼容 → **不随包** |
| B `.playwright/node/node.exe`（87 MB）、`ffmpeg/`（96 MB）、`7z.dll` | LGPL/GPL 分发义务；Python 端应 `pip install` 或检测系统已有 |
| B `rapidocr_models/*.onnx`（≈21 MB） | 尺寸不再是障碍，但仅为「再换一个 OCR 后端」要拖进 onnxruntime（≈50–80 MB），而 ACRPA 已有 4 种后端 → 走「用户自备模型 + 能力探测」，与 `paddle_dll` 模式一致 |
| B `config/appsettings.json` | 含 DPAPI `AiApiToken` 等隐私数据 |
| B `integrity.manifest.json` 绕过 | 签名校验，破解涉法律风险 |
| B `Assets/skills.bundle.json` 提示词原文 | 受版权保护的表达，须自写 |
| B `yolo11n.onnx` | 模型许可（AGPL-3.0 系）需法务确认后方可分发 |

---

## 十、批次与验收

| 批次 | 内容 | 判据 |
| --- | --- | --- |
| **B0（≈1 天）** | 字段字典 + 模板索引 | 88/88 覆盖、生成器幂等；模板 index 与磁盘一一对应、插入对话框可分组搜索 |
| **B1（≈2 天）** | `.acrpa.json` 读写 + 往返测试 | 12 个模板 xls 往返无损；未知命令不丢 |
| **B2（≈2 天）** | 变量表 + 执行设置 | 变量体检拦截缺必填；循环/超时/窗口绑定按语义生效且默认行为不变 |
| **B3（≈1 天）** | AI 生成器 | 10 条离线夹具通过；非法 op/字段被拦 |
| **B4（0.5 天）** | 扩展包元数据 | 旧客户端装新包给版本提示而非崩溃 |
| **B5（评估后定）** | OCR 独立 worker 进程（不随包模型） | 主程序杀 worker 进程后 OCR 能力自动降级，不崩溃 |

每批结束跑一次体积门禁（`python tools/_check_volume.py`，上限 300 MB）与 `python tools/_check_spec_excludes.py`。

---

## 十一、不做什么（防返工）

1. 不引入 `.xls` 之外的**唯一**真源——JSON 是平行格式，`.xls` 保持可读可写；
2. 不移植 WF/Activity 模型（`Sequence/If/While` 语义 ACRPA 已有 `if/while/loop` 命令与工作流视图）；
3. 不随包分发 GPL/AGPL 二进制（ffmpeg、node、YOLO 权重）与商业控件（Actipro）；
4. 不照抄 B 的提示词与模板内容（许可 + 表达权）；
5. 不在本轮做「后台点击 / HID / ADB」，因 ACRPA 无驱动依赖，收益边界不清（列入 `落地总清单` A 系列）；
6. 不因红线放宽就去「填满 300 MB」——体积优化仍是默认取向，新增依赖必须能说清「许可、冷启动、维护面」三项收益。

---

## 十二、风险与取舍

1. **语言鸿沟**：A 的 C# 代码不可直接复用，只能借设计；WF/AppDomain 在 Python 无等价物（对应 `subprocess`/`multiprocessing` + `importlib`）。
2. **闭源限制**：B 只能借功能面与配置结构，不可抄实现；其引用的开源模型需逐个核对协议（YOLO 的 AGPL 尤其要谨慎）。
3. **进程隔离 ROI**：`py_sandbox` + `crashlog` 已覆盖大部分崩溃场景；进程分离只在「执行器独立进程」这一最小切入点评估后再投入。
4. **红线放宽后的纪律风险**：15 MB 时代形成的「先问体积」习惯可能退化为「反正装得下」；本文 §十一-6 即为对冲条款。
5. **硬件输入合规**：外接 HID / DD 驱动类输入可能触及特定场景合规边界，落地前需评估。

---

*文档更新：2026-11-01（体积红线 15 MB → 300 MB 后全面复核取舍）*
