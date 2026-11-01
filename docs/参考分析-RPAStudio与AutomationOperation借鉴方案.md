# 参考分析：RPAStudio 与 AutomationOperation 对 ACRPA 的借鉴方案

> **汇总版见 [`rpa-architecture-benchmark.md`](rpa-architecture-benchmark.md)**（含三方定位、能力地图、批次验收与 300 MB 红线后的取舍复核）。本文保留为按 P0/P1/P2 展开的详细设计底稿。

> 对象
> - **A** = `F:\（8）Desktop\rpastudiobook`：开源 RPAStudio 配套书 + C# 示例（代码 BSD-3 / 书稿 CC BY-SA 3.0）
> - **B** = `F:\（8）Desktop\Files\script_plugin\AutomationOperation\AutomationOperation-v4.9.2\AutomationOperation`：已编译商用自动化工具（.NET + OpenCV + ONNX + Playwright + ADB）
> - **本项目**：ACRPA v0.1.30-beta（Python 3.12 + Tkinter，onefile ≈ 14.3 MiB / 红线 300 MB（原 15 MB，v0.1.30 起放宽））
> 日期：2026-11-01　关联：`docs/ACRPA-完善路线图.md`（§③ 脚本格式无 schema）、`docs/落地总清单-ACRPA能力补强与工作流拓展.md`

## 0. 结论

两者的价值不在代码可移植性，而在**数据契约**：把「自动化步骤」从位置参数升级为「命名字段」；工程范式（编辑/执行分离、活动包）对本项目收益低。

| 优先级 | 借鉴项 | 依据 | 体积 |
| --- | --- | --- | --- |
| **P0** | 命令字段字典（88 命令 → 稳定英文键 + 分类/别名/单位/枚举） | ACRPA `res/help/command_schema.json` 已有 79/88 schema，但只有中文名、无稳定键 | <60 KB |
| **P0** | 结构化脚本 `.acrpa.json`（与 `.xls` 双向无损） | B `Templates/Reference/微信发送信息.json`：`Operations[].OperationType` + 命名字段 | <40 KB |
| **P0** | 模板索引 `template/index.json` | B `template-index.json`（id/name/category/description/preview）；ACRPA `template/` 12 个 `.xls` 无元数据 | <20 KB |
| **P1** | 声明式变量表（名/类型/默认值/必填/启用） | B `ConfigVariables`；ACRPA 变量仅运行时 dict（`src/interp.py:184`） | <25 KB |
| **P1** | 脚本级执行设置（循环/超时/窗口四元组/失败策略） | B `LoopSettings`/`ExecutionSettings`/`BoundWindow*` | <20 KB |
| **P1** | 「中文描述 → 流程」生成器（目录注入 + 三道校验 + 预览插入） | B `Assets/skills.bundle.json` 的 operation-catalog + 强约束 JSON 输出 | <15 KB |
| **P2** | 扩展包元数据升级（id/version/minAppVersion/deps） | A `RPA.Interfaces/Nupkg/*.cs` | <30 KB |
| **P2 缓做** | 编辑器/执行器分离 + 命名管道 | A `RPAExecutor/RPARobot/RPACommandLine`；ACRPA 已有 `cli.py` + WM_COPYDATA IPC | 回归面过大 |

## 1. 差距矩阵

| 维度 | ACRPA 现状 | B | A | 结论 |
| --- | --- | --- | --- | --- |
| 步骤模型 | `.xls`：`命令类型` + `参数1..9` 位置槽（`src/scriptdata.py`） | 命名字段 + OperationType | WF Activity 属性 | **最该补** |
| 动作规模 | 88 条注册 / 79 有 schema / 29 含 choices | 70+ OperationType 逐条字段释义 | 活动库 | ACRPA 数量不输，缺元数据 |
| 模板 | 裸 `.xls`，无索引无 preview | index.json + category + preview | `.xaml` 模板 | 直接可抄 |
| 变量 | 运行时 dict + `--var` | 声明式 + 类型 + 默认值 | Activity 变量 | 补声明层 |
| 执行设置 | 全局 `state` + `scheduler.py` | 脚本级 Loop/Timeout/窗口绑定 | 无 | 补脚本级 |
| 输入通道 | 键鼠 / 浏览器 / OCR | 前台 / 后台 / ADB / HID 四通道 | 无 | 参考，暂不做后台点击 |
| 视觉 | 模板匹配 + 4 种 OCR 后端 | + ONNX OCR + YOLO | 无 | 体积不允许 |
| 扩展包 | `.acrpapkg` zip + sha256 + 白名单（严格） | 无 | Nupkg + 仓库服务 | 只借字段 |
| 调试 | 断点 + 单步（`ACRPA.py:1700`） | 未公开 | 完整调试服务 | 已够用 |

## 2. P0 设计

### 2.1 命令字段字典 `src/command_fields.py`

现状痛点：schema 用中文名当键，改名即读不回；无分组/单位/范围，参数面板与 AI 生成只能靠标签对齐。

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

落地：① `tools/gen_command_fields.py` 从 `res/help/command_schema.json` 自动生成骨架（`name/kind/default/optional/choices` 已可取），仅「分类/别名/单位/范围」需人工；② `commands.schema()` 改为**优先查字典、查不到回落文本解析**，20+ 存量消费方零改动。

验收：覆盖 88/88；`key` 全局唯一；参数顺序与旧实现一致；生成器幂等（二次跑 diff 为空）。成本 S。

### 2.2 结构化脚本 `.acrpa.json`（`.xls` 仍是真源，不迁移）

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

映射（`src/script_json.py`）：`op` ↔ 第 1 列（经 `COMMAND_FIELDS[*]["key"]` 反查中文命令名）；字段按声明顺序落第 2..10 列；未登记命令直接以中文名作 `op`（前向兼容）。

约束：往返无损（用 `template/*.xls` 12 个做夹具）；无新依赖（`json` + 既有 xlrd/xlwt）；写盘复用 `script_package.py` 的临时文件 + 原子替换；GUI 先只加文件类型，不做双内存模型。

验收：`tools/_test_script_json.py` 四例——往返一致 / 未知命令不丢 / `"0.96"` 与 `0.96` 归一 / 缺必填报行号。成本 M。

### 2.3 模板索引 `template/index.json`

```json
{"version": 1, "templates": [
  {"id": "im.send_message", "name": "IM 消息发送", "category": "办公",
   "description": "激活窗口 → 搜索联系人 → 粘贴发送",
   "fileName": "im_message_sender.xls", "preview": "1 激活\n2 Ctrl+F\n3 粘贴\n4 回车"}]}
```

`src/template_index.py`：加载 + 校验（`fileName` 与磁盘一一对应、`category` 白名单）+ 坏文件隔离（不影响启动）。插入片段对话框按 category 分组、按 name/description/aliases 搜索。验收即上面三条。成本 S，**投入产出最高**。

## 3. P1 设计

### 3.1 声明式变量（对齐 `ConfigVariables`）

`src/script_vars.py`：`{name, type(string|number|bool|list), value, enabled, required, desc}`。价值：运行前变量体检、市场脚本参数化、AI 先生成变量再生成步骤。规则：声明值与 `--var` 冲突时 CLI 优先；`interp` 的单遍展开/转义语义不变。成本 M。

### 3.2 脚本级执行设置

`settings` 四段落到 `src/script_settings.py`，与 `scheduler.py` 解耦：`loop.mode ∈ {none,count,infinite}`（infinite 需二次确认）；`timeout.minutes` 复用引擎既有超时检查点（不开线程）；`target` 借 B 的窗口四元组，配合 `激活窗口` 做前置校验；`onError ∈ {stop,continue,retry:N}` 接引擎已有 `failed_row`（`cli._record_history`）。验收：默认值 == 当前行为（向后兼容）、超时落 `failed_row`、目标窗口缺失给「窗口未找到」。成本 M。

### 3.3 「中文描述 → 流程」生成器

`src/ai_steps.py`，复用 `ai_client.py`/`ai_enhance.py`：

1. `build_catalog_prompt()` 由 `COMMAND_FIELDS` 生成按 category 分组的目录载荷（约 6–8 KB，可本地缓存）；
2. 强制模型只输出 `{"steps": [...]}`，失败重试 1 次后放弃（不插入）；
3. 三道校验：`op` 存在 → 必填齐全 → 枚举合法；不过则只在预览窗标红；
4. 通过后复用 `_insert_snippet_rows` 的「追加/插到选中行前/替换全部」。

验收：10 条离线夹具（中文描述 → 期望 op 序列），不依赖真实 API。成本 M（依赖 P0-1）。

## 4. P2

**扩展包元数据**：沿用 zip + sha256 + 白名单，仅补 `id/version/minAppVersion/dependencies`（安装前版本闸门与依赖提示）。不引 NuGet 格式、不引远程仓库协议（`marketplace-v2-design.md` 已定）。成本 S。

**编辑/执行分离：建议缓做**。A 的三进程解决「崩溃拖垮 IDE」「无人值守」，而 ACRPA 已有 `cli.py`（历史/失败行）与 `single_instance.py`（WM_COPYDATA），执行在子线程 + `_edit_blocked` 守卫。改造要动 `engine` 全局与 `state` 单例，回归成本远超收益。**触发条件**：① 多租户调度需互不影响；② headless 服务器部署；③ 调试暂停时仍需改脚本。

## 5. 可复用资源

### 5.1 可直接复用（注意署名）

| 资源 | 路径 | 用法 | 许可 |
| --- | --- | --- | --- |
| NSIS 安装脚本骨架 | `rpastudiobook/samples/RPAStudioSetup/make-setup/nsis-scripts/script.nsi` | 改造为 ACRPA 安装器（当前只有 onefile，无安装包） | BSD-3（保留声明） |
| 模板索引字段契约 | `Templates/Reference/template-index.json` | 照搬字段名，内容自写 | 通用结构 |
| 流程 JSON 四段式 | `Templates/Reference/微信发送信息.json` | `LoopSettings/ExecutionSettings/ConfigVariables/Operations` | 结构非表达 |

### 5.2 可借鉴思路（自己实现，勿拷文本）

| 主题 | 出处 | 借什么 | 落点 |
| --- | --- | --- | --- |
| 动作目录当提示词载荷 | `Assets/skills.bundle.json` | 「op + 字段语义 + 约束 + 反例」喂模型 | §3.3 |
| 字段语义密度 | 同文件 operation-catalog | 一句一字段的写法 | `fields[].help` |
| 窗口四元组 | `Templates/*.json` | Title/Process/Class/Hwnd 定位 | §3.2 |
| 输入通道枚举 | `config/appsettings.json` `AutomationMode`、`AdbDevices/`、`ExternalHidDevices/` | 前台/后台/ADB/HID | 未来后台点击 |
| Python 活动入参 | `RPA.Learn.Activities/Python/InvokePythonFileActivity.cs`（`PythonFilePath`/`WorkingDirectory`/`SysPathAppendList`/`Arguments`/`ContinueOnError` + stdout 重定向） | 用来自查 `src/py_sandbox.py` 缺项（尤其 sys.path 追加、错误继续） | 自查清单 |
| 工作流服务职责 | `RPA.Interfaces/Workflow/IWorkflow{Designer,Debug,Breakpoints}Service.cs` | 接口边界（非实现） | 断点已有，作自查 |
| 扩展包服务切分 | `RPA.Interfaces/Nupkg/IPackage{Export,Import,Repository}Service.cs` | 导入/导出/仓库三分 | `script_package.py`+`marketplace.py` |
| IDE 面板清单 | `books/zh/13.5.02~13.5.14` | 8 面板信息架构 | 工作流视图评审 |
| 三方库日志接管 | `RPA.Interfaces/Share/LogToOutputWindowTextWriter.cs` | TextWriter 适配器 | 确认 `log_dock` 已接管 |

### 5.3 不可用（明确不做）

| 资源 | 风险 |
| --- | --- |
| `rpastudiobook/tools/actipro-wpf-controls-*.zip`（57.6 MB） | 商业控件试用包，禁止再分发 |
| `samples/**` 的 338 个 C#/WPF 文件 | 栈不匹配，移植成本 ≫ 收益 |
| `books/zh/*.md` 正文/插图 | CC BY-SA 3.0 传染性，闭源文档不可抄 |
| `tools/*.exe`（CPython 3.7、.NET 4.6.1 离线包） | EULA 限制 + 版本过时 |
| `OCR_CPU/`（paddle_inference 99 MB + mkl 142 MB）、`inference/*.pdiparams` | 体积灾难 + 与 onnxruntime 不兼容 |
| `.playwright/node/node.exe`（87 MB）、`ffmpeg/`（96 MB）、`7z.dll` | LGPL/GPL 分发义务；Python 端应 pip 安装 |
| `config/appsettings.json` | 含 DPAPI `AiApiToken` 等隐私数据 |
| `integrity.manifest.json` 绕过 | 签名校验，破解涉法律风险 |
| `assets/skills.bundle.json` 提示词原文 | 受版权保护的表达，须自写 |
| `rapidocr_models/*.onnx`（≈21 MB） | 尺寸已不是障碍（红线 300 MB），但仍不建议随包：仅为「再换一个 OCR 后端」要拖进 onnxruntime（≈50–80 MB），而 ACRPA 已有 4 种 OCR 后端；如确需离线模型，走「用户自备模型 + 能力探测」，与 `paddle_dll` 模式一致 |

## 6. 批次与验收

| 批次 | 内容 | 判据 |
| --- | --- | --- |
| **B0（≈1 天）** | 字段字典 + 模板索引 | 88/88 覆盖、生成器幂等；模板 index 与磁盘一一对应、插入对话框可分组搜索 |
| **B1（≈2 天）** | `.acrpa.json` 读写 + 往返测试 | 12 个模板 xls 往返无损；未知命令不丢 |
| **B2（≈2 天）** | 变量表 + 执行设置 | 变量体检拦截缺必填；循环/超时/窗口绑定按语义生效且默认行为不变 |
| **B3（≈1 天）** | AI 生成器 | 10 条离线夹具通过；非法 op/字段被拦 |
| **B4（0.5 天）** | 扩展包元数据 | 旧客户端装新包给版本提示而非崩溃 |

全部为纯 Python 数据/文本改造，**总体积增量 < 200 KB**，不触碰打包依赖与体积红线。

## 7. 不做什么（防返工）

1. 不引入 `.xls` 之外的**唯一**真源——JSON 是平行格式，`.xls` 保持可读可写；
2. 不移植 WF/Activity 模型（`Sequence/If/While` 语义 ACRPA 已有 `if/while/loop` 命令与工作流视图）；
3. 不引入 ONNX/YOLO/内置 node/ffmpeg（体积与许可双否）；
4. 不照抄 B 的提示词与模板内容（许可 + 表达权）；
5. 不在本轮做「后台点击/HID」，因 ACRPA 无驱动依赖，收益边界不清（列入 `落地总清单` A 系列）。
