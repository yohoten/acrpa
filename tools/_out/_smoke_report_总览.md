# ACRPA 测试总报告（阶段3 · 汇总 + 源码级根因确认）

- 执行角色：测试汇总子任务（阶段3，由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`
- 汇总输入：阶段1 报告 [`tools\_smoke_report_phase1.md`](tools/_smoke_report_phase1.md)、阶段2 报告 [`tools\_smoke_report_phase2.md`](tools/_smoke_report_phase2.md)
- 原始输出：`tools\_smoke_result_phase1\`（27 个 `.out`）、`tools\_smoke_result_phase2\`（10 个 `.out` + 启动/内省报告）
- 证据截图：`tools\_ui_screenshots\app_main.png`
- 本阶段边界：**未修改任何 `src/` 业务源码**；仅新建本报告与一份极短的单点 API 验证命令。

---

## 1. 概述

### 1.1 测试范围

| 阶段 | 范围 | 规模 |
|---|---|---|
| 阶段1 | 无 GUI 控制台冒烟 + 功能验收（语法/引擎/浏览器/沙箱/AI/市场/NetLink） | 27 个脚本，全部执行 |
| 阶段2 | UI 冒烟 + `run.py` 应用启动存活 + UI 显示/体验结构化验证 | 10 个脚本 + 启动验证 + 内省 + 截图 |
| 阶段3（本阶段） | 汇总统计 + UI-01~UI-04 / W-1~W-3 源码级根因确认 + 统一 Bug 清单 | 只读源码与输出，不重跑耗时测试 |

### 1.2 测试环境

| 项 | 值 |
|---|---|
| 解释器 | `.venv\Scripts\python.exe`（**Python 3.9.13**，`tags/v3.9.13:6de2ca5`） |
| 运行方式 | `.venv\Scripts\python.exe -X utf8 <脚本>`（结果重定向落盘） |
| Shell | `C:\WINDOWS\system32\cmd.exe` |
| 操作系统 | Windows 10/11 桌面 |
| 应用入口 | `run.py` → `from ACRPA import root; root.mainloop()`（tkinter） |
| 屏幕 | 2560 x 1600（虚拟屏 0,0 2560x1600） |
| tk scaling | 1.6662566625666255 |
| `utils.dpi_factor()` | 1.2496924969249692（≈125% DPI，取自 UI 内省） |
| `state.UI_SCALE` | 1.0 |
| 依赖 | `.venv` 已装 `pyautogui/xlrd/xlwt/pyperclip/requests/PIL/pywin32/urllib3/certifi`；**未装** `playwright`、`cryptography` |

> 注：阶段1 未执行 `pip install -r requirements.txt`（按指令改用既有 `.venv`），仅做导入校验，`ALL_DEPS_OK`。

---

## 2. 总体结果统计

| 维度 | 数量 | 说明 |
|---|---:|---|
| 控制台功能脚本（阶段1） | 27 | 执行 27 / PASS 27 / **FAIL 0** / 非 0 退出码 0 |
| UI 冒烟脚本（阶段2） | 10 | **PASS 8 / FAIL 2 / SKIP 0** |
| 应用启动存活验证 | 1 | **成功**（`run.py` PID 13576 存活 ≥15s，无 Traceback） |
| UI 显示/体验内省 | 1 | 完成，产出 `app_main.png`（1000x680） |
| **合计失败（FAIL）** | **2** | `_smoke_ui_scale.py`、`_smoke_marketplace.py` |
| **合计 WARN** | **3** | W-1 sandbox 超时、W-2 内置模板、W-3 TLS 证书（阶段1） |
| **UI 问题** | **4** | UI-01 几何溢出、UI-02 未纳入 DPI、UI-03 字体改配失效、UI-04 市场静态断言 |

### 2.1 阶段2 FAIL 细分（修正阶段2报告的偏差）

阶段2 报告称 `_smoke_marketplace.py`「动态部分全 OK」，但原始输出 [`_smoke_marketplace.out`](tools/_smoke_result_phase2/_smoke_marketplace.out:40) 自报 **FAIL=3**，而可见 `[FAIL]` 仅 1 条。逐行核对测试脚本后确认：

| # | FAIL 来源 | 输出可见性 | 定性 |
|---|---|---|---|
| 1 | 静态断言「工具栏『市场』按钮绑定被改动」 | 有 `[FAIL]` 行（out 第 16 行） | **测试脚本缺陷** |
| 2 | 动态断言「ui_scale 增大但 FONT_BODY 未变大」（1.0→1.25） | **无 `[FAIL]` 打印**（仅计入 FAILS） | 产品 bug（同 UI-03 根因） |
| 3 | 动态断言「ui_scale 增大但 FONT_BODY 未变大」（1.25→1.5） | **无 `[FAIL]` 打印**（仅计入 FAILS） | 产品 bug（同 UI-03 根因） |

证据：动态断言处 [`tools/_smoke_marketplace.py:396`](tools/_smoke_marketplace.py:396) `FAILS.append(...)` **未配套 `_p("FAIL", ...)`**；`FONT_BODY` 实测三档恒为 9pt：[`_smoke_marketplace.out:27-29`](tools/_smoke_result_phase2/_smoke_marketplace.out:27)。故阶段2「动态全 OK」结论不准确，需以本报告为准。

---

## 3. 分模块结果

| 模块 | 代表脚本 | 结果 |
|---|---|---|
| 引擎 / 逻辑 | `_debug_check.py`、`_smoke_engine.py`、`_debug_functional.py`、`_debug_error_semantics.py`、`_debug_workflow.py`、`_debug_updater.py` | 全 PASS（104 文件语法 0 失败；engine 9 项、功能 8 组、失败语义 32 项、工作流 19 项、更新器 107 项） |
| 浏览器 | `_test_browser_locator*.py`、`_test_browser_waiter.py`、`_test_browser_cdp.py` 等 9 个 | 全 PASS；真实浏览器分支 SKIP 9（未装 playwright） |
| 沙箱 / AI / 市场 | `_test_python_sandbox.py`、`_test_code_command.py`、`_test_ai_provider.py`、`_test_script_package.py`、`_test_market_upload.py` | 全 PASS；W-1（沙箱超时）、W-2（内置模板）各 1 条 WARN；市场真实 PR SKIP 1 |
| NetLink | `_smoke_netlink.py`、`_test_netlink_auth/control/transfer/tls/e2e.py` | 全 PASS；W-3（TLS 证书）1 条 WARN |
| UI | 阶段2 10 脚本 + 启动验证 + 内省 | PASS 8 / FAIL 2（UI-03、UI-04）；主窗口/Notebook 纵向 +23px 溢出（UI-01/UI-02） |

---

## 4. 统一 Bug / 问题清单（按严重度排序）

> 定性口径：`产品bug` = 需修改 `src/` 业务源码；`测试缺陷` = 仅需修测试脚本；`环境限制` = 补齐依赖即可覆盖，非代码缺陷。

| 编号 | 严重度 | 现象 | 证据（脚本/输出:行） | 根因结论 | 涉及文件:行 | 建议修复方向 |
|---|---|---|---|---|---|---|
| **BUG-01** | **P1** | Python 3.9 下 `nametofont(root=...)` 抛 `TypeError` 被吞 → **切换「界面缩放」档位时字号不变**（1.0/1.25/1.5 恒为 9pt）；`init_fonts` 幂等刷新路径退化 | [`_smoke_marketplace.out:27-29`](tools/_smoke_result_phase2/_smoke_marketplace.out:27)；源码 [`src/utils.py:257`](src/utils.py:257)、[`src/utils.py:291`](src/utils.py:291)；验证命令输出 `def nametofont(name)`（无 root 参数） | **产品 bug** | [`src/utils.py:257`](src/utils.py:257)（`init_fonts`）、[`src/utils.py:291`](src/utils.py:291)（`set_ui_scale`）、[`src/ACRPA.py:263`](src/ACRPA.py:263)（`_mb_content_height`） | 兼容 3.9：改用 `tkinter.font.Font(name=role, exists=True, root=root)`，或 `nametofont(role)` 不带 `root=`（命名空间全局唯一）；并给改配失败加日志而非静默吞异常 |
| **BUG-02** | **P1** | 内置脚本模板下载/安装恒失败：`MarketplaceError('内置脚本模板未找到: builtin_1')` | [`_test_script_package.out:120`](tools/_smoke_result_phase1/_test_script_package.out:120)、[:144](tools/_smoke_result_phase1/_test_script_package.out:144) | **产品 bug** | [`src/marketplace.py:642`](src/marketplace.py:642)（`BUILTIN_TEMPLATES.get(script_info.id, [])`）与 [`src/marketplace.py:560`](src/marketplace.py:560) 起的键（`builtin_notepad` 等）**键名不匹配**；抛错 [`src/marketplace.py:644`](src/marketplace.py:644) | 查表键改为 `script_info.filename`（与 [`src/marketplace.py:646`](src/marketplace.py:646) 一致），或把 `BUILTIN_TEMPLATES` 键统一为 `id`（`builtin_1..8`） |
| **BUG-03** | **P2** | 默认启动几何硬编码 `1000x680`，未含 `dpi_factor`（125%）→ 内容 req 703 > 680，**主窗口/Notebook 纵向溢出 23px**（底部疑似裁剪） | [`_smoke_ui_introspect.out`]（`[MAIN] req=897x703`、`[OVERFLOW] over=(−103,+23)`）；[`_smoke_report_phase2.md:81`](tools/_smoke_report_phase2.md:81) | **产品 bug** | [`src/ACRPA.py:1459`](src/ACRPA.py:1459)（`_main_center_geometry(1000, 680)`）、[:1460](src/ACRPA.py:1460)（`minsize(640,520)`）；设计说明 [:1405-1406](src/ACRPA.py:1405)；[`src/utils.py:228`](src/utils.py:228)（`scaled()`） | 默认几何经 `utils.scaled()`（含 `dpi_factor`）派生，或构建后按 `root.winfo_reqheight()` 兜底 `minsize`；亦可在超高时启用滚动/自适应 |
| **BUG-04** | **P2** | 可信模式（trusted）超时基于 `sys.settrace`，可被 `sys.settrace(None)` 或 C 层阻塞绕过 | [`_test_python_sandbox.out:90`](tools/_smoke_result_phase1/_test_python_sandbox.out:90) | **产品 bug（已知限制，设计标注「本期未修」）** | [`src/py_sandbox.py:321`](src/py_sandbox.py:321)-[:322](src/py_sandbox.py:322)、[:222](src/py_sandbox.py:222)（`_make_tracer`）；[:334](src/py_sandbox.py:334) 清理 | 后续批次引入独立 worker 进程/线程执行 trusted 超时；当前 sandbox 与 full 模式不受影响（full 走 subprocess） |
| **TEST-01** | **P2** | `_smoke_ui_scale.py` 动态断言抛 `TypeError("nametofont() got an unexpected keyword argument 'root'")` → 脚本 FAIL(1) | [`_smoke_ui_scale.out:31`](tools/_smoke_result_phase2/_smoke_ui_scale.out:31)；测试源码 [`tools/_smoke_ui_scale.py:287`](tools/_smoke_ui_scale.py:287) | **测试缺陷**（同样的 3.9 不兼容 API，未被 try/except 包住） | [`tools/_smoke_ui_scale.py:287`](tools/_smoke_ui_scale.py:287) | 测试侧改用 `tkinter.font.Font(name=role, exists=True, root=r)` 或 `nametofont(role)`（不带 `root=`）；修 BUG-01 后该用例可复跑验证 |
| **TEST-02** | **P2** | `_smoke_marketplace.py` 静态断言「工具栏『市场』按钮绑定被改动」FAIL（实为测试用例过期） | [`_smoke_marketplace.out:16`](tools/_smoke_result_phase2/_smoke_marketplace.out:16)；测试源码 [`tools/_smoke_marketplace.py:154`](tools/_smoke_marketplace.py:154) | **测试缺陷**（断言旧工厂名 `_btn`，实际已改为 `_tbtn`，绑定目标 `_open_marketplace` **未变**） | 测试 [`tools/_smoke_marketplace.py:154`](tools/_smoke_marketplace.py:154) vs 源码 [`src/ACRPA.py:3587`](src/ACRPA.py:3587) / 工厂 [`src/ACRPA.py:3536`](src/ACRPA.py:3536) | 断言更新为 `_tbtn(toolbar_inner,"市场",_open_marketplace`；并给动态 `FAILS.append` 补 `_p("FAIL", ...)` 便于定位 |
| **ENV-01** | **P2** | `_test_netlink_tls.py` 无法生成自签证书 → TLS 真实握手/配对/指纹拒绝/审计断言记 WARN | [`_test_netlink_tls.out:4-5`](tools/_smoke_result_phase1/_test_netlink_tls.out:4)、[:15](tools/_smoke_result_phase1/_test_netlink_tls.out:15) | **环境限制（非产品 bug）**：产品只用标准库 `ssl`，明确不依赖 `cryptography`；测试侧需 `cryptography`/`openssl` 生成证书 | [`src/netlink/tls.py:5`](src/netlink/tls.py:5)（docstring「绝不引入 cryptography」）；测试 [`tools/_test_netlink_tls.py:172`](tools/_test_netlink_tls.py:172)（`_make_cert`） | 在测试环境安装 `cryptography`（或确保 `openssl` 在 PATH）后重跑；产品代码无需改动 |

### 4.1 根因确认要点（逐条）

- **UI-01**：`_apply_main_geometry` 默认分支使用字面量 `1000x680`（[`src/ACRPA.py:1459`](src/ACRPA.py:1459)），而命名字体按 pt 经 `tk scaling=1.667` 渲染，内容在 125% DPI 下被撑高到 703 → **确由该代码导致**，属产品 bug（P2）。
- **UI-02**：同一函数仅在**紧凑模式**且 `ui_scale != 1.0` 时才走 `utils.scaled()`（[`src/ACRPA.py:1449`](src/ACRPA.py:1449)）；**非紧凑默认分支完全不含 `dpi_factor`** → 与 UI-01 同根因，合并为 BUG-03。
- **UI-03**：**须区分**——
  - 产品侧：[`src/utils.py:257`](src/utils.py:257)、[`src/utils.py:291`](src/utils.py:291)、[`src/ACRPA.py:263`](src/ACRPA.py:263) 以 `root=` 调用 `nametofont`，在 3.9.13（实测签名 `nametofont(name)`）抛 `TypeError`，被 `except` 吞掉 → **真 bug（BUG-01）**，实机现象为「切档位字号不变」。
  - 测试侧：[`tools/_smoke_ui_scale.py:287`](tools/_smoke_ui_scale.py:287) 同一调用未包 `try`，直接 FAIL → **测试缺陷（TEST-01）**。
- **UI-04**：**须拆分**——静态部分（[`tools/_smoke_marketplace.py:154`](tools/_smoke_marketplace.py:154)）属**测试缺陷（TEST-02）**，市场按钮绑定实际未变；动态 2 条静默 FAIL（[`tools/_smoke_marketplace.py:396`](tools/_smoke_marketplace.py:396)）实为 BUG-01（字号不随档位变化）的**产品 bug 外显**。
- **W-1**：`_exec_inprocess` 对 sandbox/trusted 均用 `sys.settrace` 行级计时（[`src/py_sandbox.py:321`](src/py_sandbox.py:321)）；trusted 用真实 `builtins`（[`src/py_sandbox.py:313`](src/py_sandbox.py:313)），脚本可 `import sys; sys.settrace(None)` 关闭追踪，C 层阻塞亦不触发行事件 → **确认可绕过**，属已知设计限制（BUG-04，P2）。
- **W-2**：`BUILTIN_TEMPLATES.get(script_info.id, [])`（[`src/marketplace.py:642`](src/marketplace.py:642)）用 `id`（`builtin_1`）查以 `filename`（`builtin_notepad`）为键的表 → 恒空 → 抛错（[`src/marketplace.py:644`](src/marketplace.py:644)），**确由该代码导致**，产品 bug（BUG-02，P1）。与 `xlwt` 缺失无关（`.venv` 已装）。
- **W-3**：产品 `tls.py` 只用 stdlib `ssl`，不依赖 `cryptography`；WARN 源于测试脚本为做真实握手需自行生成自签证书 → **环境依赖缺失，非产品缺陷**（ENV-01）。

---

## 5. 未覆盖 / 跳过项

| 项目 | 状态 | 原因 |
|---|---|---|
| 浏览器真实浏览器分支（CDP 接管、codegen 录制、下载落盘、iframe/标签页、网络监听） | SKIP 9 | 未装 `playwright` + 无 Chromium；设计默认跳过真实分支 |
| 市场真实测试仓库 PR | SKIP 1 | 未配置 `ACRPA_MARKET_TOKEN` / `ACRPA_MARKET_TEST_REPO`；脚本刻意「不伪造 PASS」 |
| NetLink TLS 真实握手/配对/指令/指纹拒绝/审计 | WARN | 环境无 `cryptography`/`openssl` 与证书（ENV-01） |
| `tools\_debug_mini_bar_geometry.py` | 主动排除 | 会写 F 盘硬编码截图 |
| `tools\_test_netlink_screenshot.py` | 主动排除 | 真实截屏 |
| `tools\publish_release.py` / `tools\make_release.py` | 主动排除 | 真联网发版 |
| 启动时 Toplevel 窗口扫描 | 无对象 | 启动无独立 Toplevel（按需打开），改为校验打开入口存在性 |

---

## 6. 后续修复优先级与验证方式

### 6.1 修复优先级

1. **P1 · BUG-01**（[`src/utils.py`](src/utils.py:257)、[`src/ACRPA.py`](src/ACRPA.py:263)）：3.9 字体改配路径修复 → 恢复「界面缩放」档位有效。
2. **P1 · BUG-02**（[`src/marketplace.py`](src/marketplace.py:642)）：内置模板键名错配 → 恢复内置脚本市场安装。
3. **P2 · BUG-03**（[`src/ACRPA.py`](src/ACRPA.py:1459)）：主几何纳入 `dpi_factor` → 消除 125% DPI 下 23px 纵向溢出。
4. **P2 · BUG-04**（[`src/py_sandbox.py`](src/py_sandbox.py:321)）：trusted 超时改为独立 worker（后续批次）。
5. **P2 · TEST-01 / TEST-02**（[`tools/_smoke_ui_scale.py`](tools/_smoke_ui_scale.py:287)、[`tools/_smoke_marketplace.py`](tools/_smoke_marketplace.py:154)）：修正过期/不兼容测试断言。
6. **P2 · ENV-01**：测试环境补装 `cryptography` 以覆盖 TLS 真实握手。

### 6.2 修复后应重跑的脚本

| 目标 | 脚本 | 关注点 |
|---|---|---|
| BUG-01 / TEST-01 | `tools\_smoke_ui_scale.py` | C 段动态断言（字号随 `set_ui_scale` 变化）全 [OK] |
| BUG-01 / TEST-02 | `tools\_smoke_marketplace.py` | 静态「市场」绑定断言 + 三档 `FONT_BODY` 递增 |
| BUG-02 | `tools\_test_script_package.py` | `builtin 生成` 分支不再 WARN |
| BUG-03 | `tools\_smoke_ui_introspect.py` | 主窗口/Notebook `req h − actual h ≤ 0` |
| BUG-04 | `tools\_test_python_sandbox.py` | v1 §1.3 由 WARN 转 [OK]（worker 生效） |
| ENV-01 | `tools\_test_netlink_tls.py` | 证书分支可用，TLS 握手断言由 WARN 转 [OK] |
| 回归 | `tools\_smoke_launch_app.py`、`tools\_debug_dark_theme.py`、`tools\_debug_theme_trigger.py`、`tools\_smoke_netlink_window.py` | 启动存活 / 主题往返 / 窗口开闭回归 |

---

## 7. 附：可复跑命令清单（cmd.exe）

> 逐条执行，**勿用 `&&`**；统一 `.venv\Scripts\python.exe -X utf8`；建议落盘以便比对 `[FAIL]`/`[WARN]`。

```bat
.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_scale.py > tools\_smoke_result_phase3\_smoke_ui_scale.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_smoke_marketplace.py > tools\_smoke_result_phase3\_smoke_marketplace.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_test_script_package.py > tools\_smoke_result_phase3\_test_script_package.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_test_python_sandbox.py > tools\_smoke_result_phase3\_test_python_sandbox.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_test_netlink_tls.py > tools\_smoke_result_phase3\_test_netlink_tls.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_introspect.py > tools\_smoke_result_phase3\_smoke_ui_introspect.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_smoke_launch_app.py > tools\_smoke_result_phase3\_smoke_launch_app.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_debug_dark_theme.py > tools\_smoke_result_phase3\_debug_dark_theme.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_debug_theme_trigger.py > tools\_smoke_result_phase3\_debug_theme_trigger.out 2>&1
```
```bat
.venv\Scripts\python.exe -X utf8 tools\_smoke_netlink_window.py > tools\_smoke_result_phase3\_smoke_netlink_window.out 2>&1
```

根因确认用单点 API 验证（只读，不涉源码改动）：

```bat
.venv\Scripts\python.exe -X utf8 -c "import sys, inspect, tkinter.font; print(sys.version); print(inspect.signature(tkinter.font.nametofont))"
```

---

## 8. 阶段3 结论

- **总体**：控制台 27 脚本 0 FAIL；UI 冒烟 PASS 8 / FAIL 2 / SKIP 0；应用启动成功。合计 **FAIL 2、WARN 3、UI 问题 4**。
- **产品 bug（需改 `src/`）**：**BUG-01**（`nametofont` 3.9 不兼容 → 缩放字号失效，P1）、**BUG-02**（内置模板键名错配，P1）、**BUG-03**（主几何未纳入 DPI，P2）、**BUG-04**（trusted 超时可绕过，P2，已知限制）。
- **测试脚本缺陷（勿改产品，仅修测试）**：**TEST-01**（[`tools/_smoke_ui_scale.py:287`](tools/_smoke_ui_scale.py:287) 用 3.9 不支持的 `nametofont(root=)`）、**TEST-02**（[`tools/_smoke_marketplace.py:154`](tools/_smoke_marketplace.py:154) 断言过期工厂名 `_btn`）。
- **环境限制（非缺陷）**：**ENV-01**（W-3，测试侧缺 `cryptography`/`openssl`）。
- 关键纠偏：阶段2 报告「`_smoke_marketplace` 动态部分全 OK」不成立——其 2 条动态 FAIL（字号不随 `ui_scale` 变化）为 **BUG-01 的产品外显**，因测试未打印 `[FAIL]` 而被漏计。

---

## 修复后全量回归（v2）

- 执行角色：回归验证子任务（阶段5，由主任务 Orchestrator 委派）
- 范围：ENV-01 依赖补齐 + 全量 39 脚本回归（修复-A 的 BUG-01/02/03、TEST-01/02 与 修复-B 的 BUG-04 之后）
- 原始输出：`tools\_smoke_result_regression\`（每脚本一份 `.out`）
- 明细报告：[`tools\_fix_report_regression.md`](tools/_fix_report_regression.md)
- 边界：**未修改任何 `src/` 业务源码，也未修改任何测试脚本**；仅安装依赖、运行命令、采集输出、更新报告。

### 9.1 ENV-01 与环境

| 项 | 结果 |
|---|---|
| `pip install cryptography -i 清华源` | 退出码 0；`Successfully installed cffi-2.0.0 cryptography-50.0.2 pycparser-2.23 typing-extensions-4.16.0` |
| 导入验证 | `cryptography 50.0.2` |
| TLS 真实握手 | `_test_netlink_tls.py` 打印「证书获取分支: cryptography」，4a–4h/5a 全 `[OK]` → **W-3 WARN 消除** |

### 9.2 回归总表（脚本级，39 个）

| 分组 | 脚本数 | PASS | FAIL | 备注 |
|---|---:|---:|---:|---|
| A 基线/逻辑 | 8 | 8 | 0 | `_debug_check` 106 文件语法 0 失败；engine/functional/error_semantics(32)/workflow(19)/updater(107) 全通过 |
| B 浏览器纯逻辑 | 9 | 9 | 0 | 内部各含 1 条真实浏览器 `[SKIP]`（无 playwright） |
| C 沙箱/AI/市场 | 5 | 5 | 0 | 沙箱 `0 FAIL/1 WARN`（v5 已知限制）；`market_upload SKIP=1` |
| D NetLink 验收 | 5 | 5 | 0 | **`_test_netlink_tls.py` 已修复转绿**（TEST-03，见 9.4） |
| E UI 冒烟 | 10 | 10 | 0 | 含 `_smoke_mini_bar.py --launch` |
| F 应用级 | 2 | 2 | 0 | 启动存活 `rc=0`；内省 `OVERFLOW 命中=0` |
| **合计** | **39** | **39** | **0** | 脚本级 SKIP=0；含 WARN 的脚本 1（`_test_python_sandbox.py` v5 已知限制） |

### 9.3 已修复项转绿确认

| 修复项 | 回归实测 | 状态 |
|---|---|---|
| BUG-01 / TEST-01 | [`_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) 退出码 0；`set_ui_scale(1.2) 字号 9→11` | **已闭环** 🟢 |
| BUG-01 / TEST-02 | [`_smoke_marketplace.py`](tools/_smoke_marketplace.py) `OK (FAIL=0 WARN=0)`；`FONT_BODY 9/11/14pt` | **已闭环** 🟢 |
| BUG-02 | [`_test_script_package.py`](tools/_test_script_package.py) `0 项 WARN`，无「内置脚本模板未找到」 | **已闭环** 🟢 |
| BUG-03 | [`_smoke_ui_introspect.py`](tools/_smoke_ui_introspect.py) 主窗 `1250x850`、`reqh-h=-147`、`OVERFLOW 命中=0` | **已闭环** 🟢 |
| BUG-04 | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py) v1–v4 均在 1.00s 内被中断；v5 C 层阻塞已知 WARN；v6–v8 OK | **已闭环（含 1 已知限制）** 🟢 |
| ENV-01 | TLS 证书分支=cryptography，真实握手全 `[OK]` | **WARN 已消除** 🟢 |

### 9.4 TEST-03 闭环（`_test_netlink_tls.py` 转绿）

- **脚本**：[`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py)（修复后退出码 **0**）
- **原报错**：`NameError: name 'TOPIC_CMD_RESULT' is not defined`（`main()` 第 403 行）→ 修复前 `FAIL (1 assertion(s) failed; warns=0)`
- **根因（测试脚本缺陷 TEST-03）**：第 222 行导入 [`tools/_test_netlink_tls.py:222`](tools/_test_netlink_tls.py:222) 漏 `TOPIC_CMD_RESULT`，却在第 403 行 [:403](tools/_test_netlink_tls.py:403) 使用（产品侧常量确实存在：[`src/netlink/node.py:58`](src/netlink/node.py:58)）。
- **触发链**：修复前无 `cryptography` → 真实握手段短路记 WARN，未执行到 403 行；ENV-01 装好后进入真实 TLS 段即触发。
- **修复**（修复-C 子任务，仅动测试脚本）：在 [`tools/_test_netlink_tls.py:222`](tools/_test_netlink_tls.py:222) 的导入中补齐 `TOPIC_CMD_RESULT`（同一组 `TOPIC_*` 常量核对无其它遗漏）。
- **复验**：`.venv\Scripts\python.exe -X utf8 tools\_test_netlink_tls.py` → 退出码 **0**，`PASS (all assertions OK; warns=0)`（全部断言 OK，含 4a–4h/5a/5b/5c）。
- **连带复跑**：`_debug_check.py`（退出码 0，`syntax_failed=0 import_failed=0`）、`_test_netlink_e2e.py`（退出码 0，PASS）、`_test_netlink_control.py`（退出码 0，PASS）→ 无连带影响。
- **关联修复文件**：仅 [`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py)；**未改动任何 `src/` 业务源码**，非产品回归。

### 9.5 各修复项最终状态结论

| 项 | 最终状态 |
|---|---|
| BUG-01 字体缩放（Py3.9） | **已闭环**（字号随档位 9→11→14 生效） |
| BUG-02 内置脚本模板键名 | **已闭环**（builtin 生成零回归，无 WARN） |
| BUG-03 主几何含 DPI | **已闭环**（`reqh≤actual`，溢出=0） |
| BUG-04 trusted 超时加固 | **已闭环**（`settrace(None)` 绕过/纯 Python 死循环均被按时中断；C 层阻塞为已知限制，如实 WARN） |
| TEST-01 / TEST-02 | **已闭环**（对应脚本转绿） |
| ENV-01 cryptography | **已补齐**（TLS WARN 消除） |
| TEST-03（新增，ENV-01 暴露） | **已闭环**：`_test_netlink_tls.py` 补齐 `TOPIC_CMD_RESULT` 导入，退出码 0（`PASS`，warns=0） |

> 结论：修复-A/修复-B 的 6 个修复点全部转绿且无副作用；修复-C 闭环 TEST-03（补齐测试脚本漏导入）后，**全量回归 39 脚本全 PASS（0 FAIL）**，唯一残余为沙箱 v5 C 层阻塞的已知限制（如实标注为 WARN，非 FAIL），**非产品代码回归**。

---

## 本轮全部改动后全量回归（v3）

- 执行角色：最终全量回归子任务（阶段6，由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`（逐条执行；未用 `&&` / `|` / 反引号 / `$()`）
- 解释器：`.venv\Scripts\python.exe`（Python 3.9.13）；中文输出统一加 `-X utf8`
- 原始输出落盘：`tools\_smoke_result_regression2\`（每脚本一份 `.out`，共 42 份）
- 明细报告：[`tools\_fix_report_regression2.md`](tools/_fix_report_regression2.md)
- 覆盖本轮改动：修复-A/B/C（BUG-01~04、TEST-01~03）、标题可编辑、几何补强（几何公共函数抽取）、版本统一（netlink/node.py + marketplace.py + version_info.py + tools/README.md + index*.html）
- 危险脚本排除：`_debug_mini_bar_geometry.py`（写 F 盘截图）、`_test_netlink_screenshot.py`（真实截屏）、`publish_release.py` / `make_release.py`（联网发版）
- 边界：**未修改任何 `src/` 业务源码，亦未修改任何测试脚本**；仅运行命令、采集输出、更新报告。

### 10.1 回归总表（脚本级，42 个）

| 分组 | 脚本数 | PASS | FAIL | 备注 |
|---|---:|---:|---:|---|
| A 基线/逻辑 | 8 | 8 | 0 | `_debug_check` 109 文件语法 0 失败；engine/functional/error_semantics(32)/workflow(19)/updater(107) 全通过 |
| B 浏览器纯逻辑 | 9 | 9 | 0 | 真实浏览器分支 `[SKIP]`（无 playwright） |
| C 沙箱/AI/市场 | 5 | 5 | 0 | 沙箱 `0 FAIL/1 WARN`（v5 已知限制）；`market_upload SKIP=1` |
| D NetLink 验收 | 5 | 5 | 0 | 含修复后的 `_test_netlink_tls.py`（PASS，warns=0） |
| E UI 冒烟 | 12 | 12 | 0 | 含 `_smoke_mini_bar --launch`、标题可编辑、几何、`_smoke_ui_scale`、`_smoke_marketplace` |
| F 应用级 | 2 | 2 | 0 | 启动存活 `rc=0`；内省 `OVERFLOW 命中=0` |
| G 版本一致性 | 1 | 1 | 0 | `_test_version_unify` 16/0/0 |
| **合计** | **42** | **42** | **0** | 脚本级 SKIP=0；含 WARN 的脚本 1 |

### 10.2 统计

| 维度 | 数量 | 说明 |
|---|---:|---|
| 总脚本数 | **42** | A 8 + B 9 + C 5 + D 5 + E 12 + F 2 + G 1 |
| PASS | **42** | 脚本级退出码均为 0 且断言全通过 |
| FAIL | **0** | 无 |
| SKIP（脚本级） | **0** | 无整脚本被跳过 |
| WARN（含 WARN 的脚本） | **1** | `_test_python_sandbox.py`（v5 C 层阻塞，已知限制） |
| 子用例 SKIP | **10** | 浏览器真实分支 9（无 playwright）+ 市场真实 PR 1（无 token） |
| 非零退出码 | **0** | — |

### 10.3 本轮目标项转绿确认

| 目标项 | 目标观察点 | 回归实测 | 状态 |
|---|---|---|---|
| **标题可编辑** | `_smoke_editable_title.py` OK，且 config 未被写入 | `=== 结果: OK  通过 16/16, 失败 0 ===`；`config.json` 编辑前后 `(True,'c54d2933f172b8259c699ddd923c710d',3307,1790953480.88)` **完全一致**→ 未被写入（[`_smoke_editable_title.out:24`](tools/_smoke_result_regression2/_smoke_editable_title.out:24)） | 🟢 |
| **几何/最大化/紧凑/越界** | `_smoke_main_geometry.py` 全绿 | `FAIL=0 WARN=0`；默认分支/屏内记忆/越界记忆回退居中/最大化(`zoomed`)/紧凑(`500x625`) 全 `[OK]`（[`_smoke_main_geometry.out:22`](tools/_smoke_result_regression2/_smoke_main_geometry.out:22)） | 🟢 |
| **溢出** | `_smoke_ui_introspect.py` 无 `[OVERFLOW]` | 主窗 `1250x850`，req `897x703`（`reqh-h=-147`）；`[OVERFLOW] 命中溢出=0`（[`_smoke_ui_introspect.out:40`](tools/_smoke_result_regression2/_smoke_ui_introspect.out:40)） | 🟢 |
| **版本一致性** | `_test_version_unify.py` PASS | `通过 16 / 失败 0 / 警告 0`，结论 `PASS`（[`_test_version_unify.out:23`](tools/_smoke_result_regression2/_test_version_unify.out:23)） | 🟢 |
| 既有修复 BUG-01 | `_smoke_ui_scale.py` 字号随档位变化 | `=== 结果: OK ===`；`set_ui_scale(1.2) → 字号 9→11`（[`_smoke_ui_scale.out:33`](tools/_smoke_result_regression2/_smoke_ui_scale.out:33)） | 🟢 |
| 既有修复 BUG-01/02 | `_smoke_marketplace.py` FAIL=0 | `=== 结果: OK (FAIL=0 WARN=0) ===`（[`_smoke_marketplace.out:41`](tools/_smoke_result_regression2/_smoke_marketplace.out:41)） | 🟢 |
| 既有修复 BUG-02 | `_test_script_package.py` 无模板 WARN | `结果: 全部 项通过, 0 项 FAIL, 0 项 WARN`；`[OK] builtin 脚本生成路径零回归`（[`_test_script_package.out:120`](tools/_smoke_result_regression2/_test_script_package.out:120)） | 🟢 |
| 既有修复 BUG-04 | `_test_python_sandbox.py` 对抗性用例按预期 | `SUMMARY: 0 FAIL, 1 WARN`（v5 C 层阻塞已知限制）（[`_test_python_sandbox.out:101`](tools/_smoke_result_regression2/_test_python_sandbox.out:101)） | 🟢（含已知 WARN） |
| 既有修复 TEST-03 | `_test_netlink_tls.py` 转绿 | `PASS (all assertions OK; warns=0)`，退出码 0（[`_test_netlink_tls.out:36`](tools/_smoke_result_regression2/_test_netlink_tls.out:36)） | 🟢 |

### 10.4 新引入回归

**无。** 全量 42 脚本退出码均为 0，结果文件中**无任何 `[FAIL]`**。

- 唯一 `[WARN]`：`_test_python_sandbox.py` v5「C 层阻塞 `time.sleep` 无法按时中断」，为本轮之前即存在的**已知设计限制**（BUG-04 收尾时如实标注），**非本轮改动引入**。
- `_smoke_launch_app.out` 顶部有一条 `UnicodeDecodeError`（Thread-1，subprocess reader thread，`0xb3` 非 UTF-8 起始字节）——经与 v2 基线 [`tools\_smoke_result_regression\_smoke_launch_app.out`](tools/_smoke_result_regression/_smoke_launch_app.out:1) **逐行比对完全一致**，属**校验脚本（harness）读取子进程输出的既有伪影**；脚本最终 `子进程输出含 Traceback: False`、`=== 结果: OK (rc=0) ===`（[`_smoke_launch_app.out:41`](tools/_smoke_result_regression2/_smoke_launch_app.out:41)），**非本轮引入、非产品问题**。

### 10.5 本轮全部改动项最终状态

| 改动项 | 涉及文件 | 最终状态 |
|---|---|---|
| BUG-01 字体缩放（Py3.9 `nametofont`） | [`src/utils.py`](src/utils.py)、[`src/ACRPA.py`](src/ACRPA.py) | 🟢 已闭环（`_smoke_ui_scale`/`_smoke_marketplace` 转绿） |
| BUG-02 内置脚本模板键名 | [`src/marketplace.py`](src/marketplace.py) | 🟢 已闭环（`_test_script_package` 零 WARN） |
| BUG-03 主几何含 DPI/越界回退 | [`src/ACRPA.py`](src/ACRPA.py)、[`src/utils.py`](src/utils.py) | 🟢 已闭环（`_smoke_main_geometry` 全绿；`OVERFLOW=0`） |
| BUG-04 trusted 超时加固 | [`src/py_sandbox.py`](src/py_sandbox.py) | 🟢 已闭环（含 v5 C 层阻塞已知 WARN） |
| TEST-01 | [`tools/_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) | 🟢 已闭环（退出码 0） |
| TEST-02 | [`tools/_smoke_marketplace.py`](tools/_smoke_marketplace.py) | 🟢 已闭环（`FAIL=0`） |
| TEST-03 | [`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py) | 🟢 已闭环（退出码 0，`warns=0`） |
| 标题可编辑 | [`src/ACRPA.py`](src/ACRPA.py) + [`tools/_smoke_editable_title.py`](tools/_smoke_editable_title.py) | 🟢 已闭环（16/16，config 未被写入） |
| 几何公共函数抽取 | [`src/utils.py`](src/utils.py)、[`src/ACRPA.py`](src/ACRPA.py)、[`src/settings_window.py`](src/settings_window.py) + [`tools/_smoke_main_geometry.py`](tools/_smoke_main_geometry.py) | 🟢 已闭环（全绿） |
| 版本统一 | [`src/netlink/node.py`](src/netlink/node.py)、[`src/marketplace.py`](src/marketplace.py)、[`src/version_info.py`](src/version_info.py)、[`tools/README.md`](tools/README.md)、[`index.html`](index.html)、[`index.en.html`](index.en.html) + [`tools/_test_version_unify.py`](tools/_test_version_unify.py) | 🟢 已闭环（16/0/0 PASS） |

> 结论（v3）：本轮全部改动后，**全量 42 脚本全 PASS（退出码 0，0 FAIL）**；脚本级 SKIP=0，子用例 SKIP=10（浏览器真实分支 9 + 市场真实 PR 1），含 WARN 的脚本仅 1（沙箱 v5 已知限制）。**标题可编辑、几何/最大化/紧凑/越界、版本一致性**三大目标项全部转绿且无副作用；**未发现任何跨模块回归**。`src/` 业务源码与测试脚本全程未被本子任务改动。

---

## 帮助窗口重构后全量回归（v4）

- 执行角色：最终全量回归子任务（由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`；执行 Shell：`C:\WINDOWS\system32\cmd.exe`（逐条执行；未用 `&&` / `|` / 反引号 / `$()`）
- 解释器：`.venv\Scripts\python.exe`（Python 3.9.13）；中文输出统一加 `-X utf8`
- 原始输出落盘：`tools\_smoke_result_help\`（每脚本一份 `.out`，共 **45** 份）
- 明细报告：[`tools\_fix_report_regression3.md`](tools/_fix_report_regression3.md)
- 覆盖本轮改动：**帮助窗口重构（阶段0 内容层 / 阶段1-2 渲染层+委托化 / 阶段4 收尾）**
  - 新增 [`src/help_content.py`](src/help_content.py)、[`src/help_window.py`](src/help_window.py)、`res/help/*`、[`tools/_test_help_consistency.py`](tools/_test_help_consistency.py)、[`tools/_smoke_help_window.py`](tools/_smoke_help_window.py)、[`tools/_gen_help_command_table.py`](tools/_gen_help_command_table.py)
  - 修改 [`src/dialogs.py`](src/dialogs.py)（`show_help_dialog` 薄委托 + legacy fallback）、[`src/state.py`](src/state.py)（`help_geometry`/`help_modal`/`help_maximized`）、[`src/ACRPA.py`](src/ACRPA.py)（主题切换追加 help refresh）、[`src/settings_window.py`](src/settings_window.py)（「帮助窗口使用模态」复选框）、[`ACRPA.spec`](ACRPA.spec)（datas 追加 `res/help` 与 docs）、`使用说明.txt`（命令区对齐+横幅）
- 危险脚本排除：`_debug_mini_bar_geometry.py`（写 F 盘截图）、`_test_netlink_screenshot.py`（真实截屏）、`publish_release.py` / `make_release.py`（联网发版）
- 边界：**未修改任何 `src/` 业务源码，亦未修改任何测试脚本**；仅运行命令、采集输出、更新/新建报告

### 11.1 回归总表（脚本级，45 个）

| 分组 | 脚本数 | PASS | FAIL | 备注 |
|---|---:|---:|---:|---|
| H 帮助子系统 | 3 | 3 | 0 | `consistency`（内容层 27 项全 OK，68==68，结论 PASS）；`smoke_help_window`（A1–A11 全 OK，FAIL=0 WARN=0）；`gen --check`（使用说明.txt 68/68） |
| A 基线/逻辑 | 8 | 8 | 0 | `_debug_check` 114 文件语法 0 失败（较 v3 +5：新增 help 源/工具）；engine/functional/error_semantics(32)/workflow(19)/updater(107) 全通过 |
| B 浏览器纯逻辑 | 9 | 9 | 0 | 真实浏览器分支 `[SKIP]` 9（无 playwright） |
| C 沙箱/AI/市场 | 5 | 5 | 0 | 沙箱 `0 FAIL/1 WARN`（v5 已知限制）；`market_upload SKIP=1` |
| D NetLink 验收 | 5 | 5 | 0 | 含 `_test_netlink_tls.py`（PASS，warns=0） |
| E UI 冒烟 | 12 | 12 | 0 | 含 `_smoke_mini_bar --launch`、`_smoke_style_*`、标题可编辑、几何、`ui_scale`、`marketplace`、主题触发/暗黑 |
| F 应用级 | 2 | 2 | 0 | 启动存活 `rc=0`；内省 `OVERFLOW 命中=0` |
| G 版本一致性 | 1 | 1 | 0 | `_test_version_unify` 16/0/0 |
| **合计** | **45** | **45** | **0** | 脚本级 SKIP=0；含 WARN 的脚本 1（沙箱 v5 已知限制） |

### 11.2 统计

| 维度 | 数量 | 说明 |
|---|---:|---|
| 总脚本数 | **45** | H 3 + A 8 + B 9 + C 5 + D 5 + E 12 + F 2 + G 1 |
| PASS | **45** | 脚本级退出码均为 0 且断言全通过 |
| FAIL | **0** | 无 |
| SKIP（脚本级） | **0** | 无整脚本被跳过 |
| WARN（含 WARN 的脚本） | **1** | `_test_python_sandbox.py`（v5 C 层阻塞，已知限制，非本轮引入） |
| 子用例 SKIP | **10** | 浏览器真实分支 9（无 playwright）+ 市场真实 PR 1（无 token） |
| 非零退出码 | **0** | — |

### 11.3 目标项确认（帮助窗口重构）

| 目标项 | 回归实测 | 状态 |
|---|---|---|
| **帮助窗口可开** | `_smoke_help_window.py` A1 `[OK] 帮助窗口可开（Toplevel 存活）` | 🟢 |
| **13 章** | `[OK] A2 章节数 13 (>=13)` 且正文非空；H1 `13 章标题与顺序符合预期` | 🟢 |
| **命令 68==68** | H1 `注册表 68 条 == 渲染 68 条`；H2 A3 `注册表 68 == 渲染 68` | 🟢 |
| **搜索** | `[OK] A5 搜索 '循环' → ['循环开始','循环结束','跳出循环']` | 🟢 |
| **主题实时** | `[OK] A6 text.bg #fdfdfd -> #1a2332` | 🟢 |
| **缩放** | `[OK] A7 scaled(100) 100 -> 150（scale 1.0->1.5）` | 🟢 |
| **几何记忆** | `[OK] A8 记忆几何恢复 900x640+574+213`（+写回/越界回退居中） | 🟢 |
| **向后兼容** | `[OK] A11 show_help_dialog() 委托成功（legacy fallback 保留）` | 🟢 |
| **`使用说明.txt --check`** | `注册表命令 68 条，文档命中 68 条`；`[OK] 集合 ⊆ 文档命令名集合` | 🟢 |

既有项同步确认保持转绿：**UI 缩放**（`_smoke_ui_scale` OK）、**市场**（`_smoke_marketplace` FAIL=0）、**内置模板**（`_test_script_package` 0 WARN）、**TLS**（`_test_netlink_tls` warns=0）、**沙箱**（0 FAIL/1 WARN 已知）、**几何/最大化/紧凑/越界**（`_smoke_main_geometry` FAIL=0 WARN=0）、**标题可编辑**（16/16）、**版本一致性**（16/0/0）。

### 11.4 新引入回归

**无。** 全量 **45 脚本退出码均为 0**，`tools\_smoke_result_help\` 内**无任何 `[FAIL]`**。

- 唯一 `[WARN]`：`_test_python_sandbox.py` v5「C 层阻塞 `time.sleep` 无法按时中断（elapsed=4.01s）」——本轮之前即存在的**已知设计限制**（BUG-04 收尾标注），**非本轮引入**。
- `_smoke_launch_app.out` 顶部 `UnicodeDecodeError`（Thread-1 subprocess reader）：与 v2/v3 基线**逐行一致**的 harness 伪影，脚本最终 `rc=0`、`子进程输出含 Traceback: False`，**非产品问题**。
- 跨模块回归风险评估：**ACRPA 主题切换追加 help refresh** 未增加刷新次数（`_debug_theme_trigger` `T4 refresh 增量 got 1 / expected 1`）；**settings_window 模态复选框 / state 三键 / dialogs 委托化** 相关脚本（`_smoke_dark_mode`、`_debug_functional`、`_smoke_help_window`、`_smoke_launch_app`）全绿 → **无回归**。

### 11.5 帮助窗口重构各阶段最终状态

| 阶段 | 范围 | 代表脚本 | 最终状态 |
|---|---|---|---|
| 阶段0（内容层） | `res/help/*` + [`src/help_content.py`](src/help_content.py) | `_test_help_consistency.py` | 🟢 已闭环（27 项 OK，68==68，结论 PASS） |
| 阶段1-2（渲染层+委托化） | [`src/help_window.py`](src/help_window.py) + [`src/dialogs.py`](src/dialogs.py) | `_smoke_help_window.py` | 🟢 已闭环（A1–A11 全 OK，legacy fallback 保留） |
| 阶段4（收尾） | `使用说明.txt` + [`ACRPA.spec`](ACRPA.spec) + [`src/settings_window.py`](src/settings_window.py) + [`src/ACRPA.py`](src/ACRPA.py) | `_gen_help_command_table.py --check` + `_debug_theme_trigger.py` | 🟢 已闭环（68/68；主题刷新无副作用） |

> 结论（v4）：帮助窗口重构（阶段0/1-2/4）全部改动后，**全量 45 脚本全 PASS（退出码 0，0 FAIL）**；脚本级 SKIP=0，子用例 SKIP=10，含 WARN 的脚本仅 1（沙箱 v5 已知限制，非本轮引入）。**帮助窗口可开 / 13 章 / 命令 68==68 / 搜索 / 主题实时 / 缩放 / 几何记忆 / 向后兼容**八项全部转绿，`使用说明.txt --check` 命中 `68/68`；既有项（UI 缩放/市场/内置模板/TLS/沙箱/几何/标题可编辑/版本一致性）全部保持转绿。**未发现任何跨模块回归**。`src/` 业务源码与测试脚本全程未被本子任务改动。
