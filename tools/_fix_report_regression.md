# ACRPA 修复后全量回归明细（阶段5 · 回归验证子任务）

- 执行角色：回归验证子任务（阶段5，由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`（逐条执行，未用 `&&`/`|`/反引号/`$()`）
- 解释器：`.venv\Scripts\python.exe`（Python 3.9.13），中文输出统一加 `-X utf8`
- 原始输出落盘：`tools\_smoke_result_regression\`（每个脚本一份 `.out`）
- 边界：**未修改任何 `src/` 业务源码，亦未修改任何测试脚本**；仅安装依赖、运行命令、采集输出、更新/新建报告。
- 关联：修复报告 [`tools\_fix_report_A.md`](tools/_fix_report_A.md)、[`tools\_fix_report_B.md`](tools/_fix_report_B.md)；总报告 [`tools\_smoke_report_总览.md`](tools/_smoke_report_总览.md)

---

## 0. ENV-01 结果（补齐环境依赖）

| 项 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 安装 cryptography | `.venv\Scripts\python.exe -m pip install cryptography -i https://pypi.tuna.tsinghua.edu.cn/simple` | **0** | `Successfully installed cffi-2.0.0 cryptography-50.0.2 pycparser-2.23 typing-extensions-4.16.0` |
| 导入验证 | `.venv\Scripts\python.exe -X utf8 -c "import cryptography; print(...)"` | 0 | `cryptography 50.0.2` |

- 日志：[`tools\_smoke_result_regression\_env01_pip_install.out`](tools/_smoke_result_regression/_env01_pip_install.out)、[`_env01_verify.out`](tools/_smoke_result_regression/_env01_verify.out)
- **结论**：ENV-01 安装成功。`_test_netlink_tls.py` 实测打印 `[..] 证书获取分支: cryptography`，TLS 真实握手断言 4a–4h、5a 全部 `[OK]` → **原 W-3「TLS 真实握手记 WARN」已消除**。

---

## 1. 全量回归总表（脚本级判定）

> 退出码来自每条命令的终端返回；PASS/FAIL/SKIP/WARN 为脚本级判定（脚本内部子用例的 SKIP 另行注明）。

### A. 基线与逻辑（8）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| A1 | [`_debug_check.py`](tools/_debug_check.py) | 0 | **PASS** | `18/18 modules imported`；`syntax_failed=0 import_failed=0`；106 文件语法 0 失败 |
| A2 | [`_smoke_engine.py`](tools/_smoke_engine.py) | 0 | **PASS** | 引擎用例「全部通过 ✓」 |
| A3 | [`_debug_functional.py`](tools/_debug_functional.py) | 0 | **PASS** | 功能回归「全部通过 ✓」 |
| A4 | [`_debug_error_semantics.py`](tools/_debug_error_semantics.py) | 0 | **PASS** | 「失败语义回归: 通过 32 项, 失败 0 项」 |
| A5 | [`_debug_workflow.py`](tools/_debug_workflow.py) | 0 | **PASS** | 「工作流引擎验证: 通过 19 项, 失败 0 项」 |
| A6 | [`_smoke_dark_mode.py`](tools/_smoke_dark_mode.py) | 0 | **PASS** | `=== 结果: OK ===`；依赖探测通过 |
| A7 | [`_smoke_netlink.py`](tools/_smoke_netlink.py) | 0 | **PASS** | `PASS 8/8` |
| A8 | [`_debug_updater.py`](tools/_debug_updater.py) | 0 | **PASS** | `ALL PASSED (107 项)` |

### B. 浏览器纯逻辑套件（9）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| B1 | [`_test_browser_locator.py`](tools/_test_browser_locator.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)` |
| B2 | [`_test_browser_locator_chain.py`](tools/_test_browser_locator_chain.py) | 0 | **PASS** | PASS（真实浏览器分支 SKIP） |
| B3 | [`_test_browser_waiter.py`](tools/_test_browser_waiter.py) | 0 | **PASS** | PASS（真实浏览器分支 SKIP） |
| B4 | [`_test_browser_cdp.py`](tools/_test_browser_cdp.py) | 0 | **PASS** | PASS（真实 CDP 接管 SKIP） |
| B5 | [`_test_browser_codegen_convert.py`](tools/_test_browser_codegen_convert.py) | 0 | **PASS** | PASS（真实录制 SKIP） |
| B6 | [`_test_browser_cookie.py`](tools/_test_browser_cookie.py) | 0 | **PASS** | PASS |
| B7 | [`_test_browser_download.py`](tools/_test_browser_download.py) | 0 | **PASS** | PASS |
| B8 | [`_test_browser_frame_tab.py`](tools/_test_browser_frame_tab.py) | 0 | **PASS** | PASS |
| B9 | [`_test_browser_listen.py`](tools/_test_browser_listen.py) | 0 | **PASS** | PASS（真实网络监听 SKIP） |

> B 组 9 个脚本内部对「真实浏览器」分支各含 1 条 `[SKIP]`（未装 playwright/Chromium），与修复前一致，非回归。

### C. 沙箱 / AI / 市场（5）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| C1 | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py) | 0 | **PASS（含 1 WARN）** | `SUMMARY: 0 FAIL, 1 WARN`（唯一 WARN 为 v5 C 层阻塞已知限制） |
| C2 | [`_test_code_command.py`](tools/_test_code_command.py) | 0 | **PASS** | `SUMMARY: 0 FAIL, 0 WARN` |
| C3 | [`_test_ai_provider.py`](tools/_test_ai_provider.py) | 0 | **PASS** | `PASS (all assertions OK)`，warns=0 |
| C4 | [`_test_script_package.py`](tools/_test_script_package.py) | 0 | **PASS** | `结果: 全部 项通过, 0 项 FAIL, 0 项 WARN`；`[OK] builtin 脚本生成路径零回归` |
| C5 | [`_test_market_upload.py`](tools/_test_market_upload.py) | 0 | **PASS** | `汇总: FAIL=0 WARN=0 SKIP=1`（真实 PR SKIP，未配置 token） |

### D. NetLink 验收（5）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| D1 | [`_test_netlink_auth.py`](tools/_test_netlink_auth.py) | 0 | **PASS** | `PASS (all assertions OK)`，warns=0 |
| D2 | [`_test_netlink_control.py`](tools/_test_netlink_control.py) | 0 | **PASS** | `PASS (all assertions OK)`，总耗时 7.4s |
| D3 | [`_test_netlink_transfer.py`](tools/_test_netlink_transfer.py) | 0 | **PASS** | `PASS (all assertions OK)`，warns=0 |
| D4 | [`_test_netlink_tls.py`](tools/_test_netlink_tls.py) | **1** | **FAIL** | `FAIL (1 assertion(s) failed; warns=0)`；`[FAIL] 未捕获异常:` → `NameError: name 'TOPIC_CMD_RESULT' is not defined`（见 §3） |
| D5 | [`_test_netlink_e2e.py`](tools/_test_netlink_e2e.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`，总耗时 13.0s |

### E. UI 冒烟（10）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| E1 | [`_smoke_mini_bar.py`](tools/_smoke_mini_bar.py) | 0 | **PASS** | `=== 结果: OK ===` |
| E2 | [`_smoke_mini_bar.py --launch`](tools/_smoke_mini_bar.py) | 0 | **PASS** | `[OK] 启动存活冒烟: run.py 存活 10s (无导入/构建期异常)` |
| E3 | [`_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) | 0 | **PASS** | `=== 结果: OK ===`；`set_ui_scale(1.2) 字号 9 -> 11`（修复项 🟢） |
| E4 | [`_smoke_marketplace.py`](tools/_smoke_marketplace.py) | 0 | **PASS** | `=== 结果: OK (FAIL=0 WARN=0) ===`；`FONT_BODY 9pt→11pt→14pt`（修复项 🟢） |
| E5 | [`_smoke_netlink_window.py`](tools/_smoke_netlink_window.py) | 0 | **PASS** | `all window smoke checks passed` |
| E6 | [`_smoke_netlink_pairing_ui.py`](tools/_smoke_netlink_pairing_ui.py) | 0 | **PASS** | `PASS (all assertions OK)`，warns=0 |
| E7 | [`_smoke_netlink_control_ui.py`](tools/_smoke_netlink_control_ui.py) | 0 | **PASS** | `PASS (all assertions OK)`，总耗时 8.7s |
| E8 | [`_smoke_netlink_transfer_ui.py`](tools/_smoke_netlink_transfer_ui.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)` |
| E9 | [`_debug_dark_theme.py`](tools/_debug_dark_theme.py) | 0 | **PASS** | `=== 结果: OK (共 57 项检查) ===` |
| E10 | [`_debug_theme_trigger.py`](tools/_debug_theme_trigger.py) | 0 | **PASS** | `=== 结果: OK ===` |

### F. 应用级验证（2）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| F1 | [`_smoke_launch_app.py`](tools/_smoke_launch_app.py) | 0 | **PASS** | `=== 结果: OK (rc=0) ===`（run.py 启动存活，无 Traceback） |
| F2 | [`_smoke_ui_introspect.py`](tools/_smoke_ui_introspect.py) | 0 | **PASS** | 主窗 `1250x850`，`req=897x703`，`reqh-h=-147 → OK(未超窗)`；`[OVERFLOW] 命中溢出=0`（修复项 🟢） |

---

## 2. 统计

| 维度 | 数量 | 说明 |
|---|---:|---|
| 总脚本数 | **39** | A 8 + B 9 + C 5 + D 5 + E 10 + F 2 |
| PASS | **39** | 脚本级退出码 0 且断言全通过（修复-C 闭环 TEST-03 后） |
| FAIL | **0** | 无（TEST-03 已修复，见 §6） |
| SKIP（脚本级） | **0** | 无整脚本被跳过 |
| WARN（含 WARN 的脚本） | **1** | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py)（v5 C 层阻塞，已知限制） |
| 子用例 SKIP | 10 | 浏览器真实分支 9（无 playwright）+ 市场真实 PR 1（无 token） |

---

## 3. 修复项转绿确认

| 修复项 | 目标观察点 | 回归实测 | 结论 |
|---|---|---|---|
| **BUG-01 / TEST-01** | `_smoke_ui_scale.py` 不再 FAIL；字号随档位变化 | 退出码 0；`set_ui_scale(1.2) → 字号 9→11`；`init_fonts 幂等` OK（[`_smoke_ui_scale.out`](tools/_smoke_result_regression/_smoke_ui_scale.out:33)） | **已闭环** 🟢 |
| **BUG-01 / TEST-02** | `_smoke_marketplace.py` FAIL=0；三档字号递增 | `=== 结果: OK (FAIL=0 WARN=0) ===`；`FONT_BODY 9/11/14pt`（[`_smoke_marketplace.out:27-30`](tools/_smoke_result_regression/_smoke_marketplace.out:27)） | **已闭环** 🟢 |
| **BUG-02** | `_test_script_package.py` 无「内置脚本模板未找到」 | `0 项 FAIL, 0 项 WARN`；`[OK] builtin 脚本生成路径零回归`（[`_test_script_package.out:120`](tools/_smoke_result_regression/_test_script_package.out:120)） | **已闭环** 🟢 |
| **BUG-03** | `_smoke_ui_introspect.py` 溢出=0；主窗几何含 DPI | 主窗 `1250x850`，`reqh-h=-147`；`[OVERFLOW] 命中溢出=0`（[`_smoke_ui_introspect.out:10`](tools/_smoke_result_regression/_smoke_ui_introspect.out:10)、[:40](tools/_smoke_result_regression/_smoke_ui_introspect.out:40)） | **已闭环** 🟢 |
| **BUG-04** | `_test_python_sandbox.py` 对抗性用例按预期 | v0/v0b/v1–v4 全 `[OK]`（settrace(None)+死循环、纯 Python 死循环均在 1.00s 内被中断）；v5 C 层阻塞 **已知 WARN**；v6–v8 OK；`SUMMARY: 0 FAIL, 1 WARN`（[`_test_python_sandbox.out:90-101`](tools/_smoke_result_regression/_test_python_sandbox.out:90)） | **已闭环（含 1 项已知限制）** 🟢 |
| **ENV-01** | `_test_netlink_tls.py` TLS 真实握手 WARN 消除 | 证书分支=cryptography；4a–4h/5a 全 `[OK]`（[`_test_netlink_tls.out:8`](tools/_smoke_result_regression/_test_netlink_tls.out:8)） | **WARN 已消除** 🟢（但脚本整体退出码 1，见 §3 新问题） |

---

## 4. 新引入 FAIL / 回归（1 条）

> 定性：**环境变更（ENV-01）暴露的测试脚本缺陷（TEST-03）**，非产品 bug，与 修复-A/修复-B 均无关联。

| 项 | 内容 |
|---|---|
| 脚本名 | [`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py) |
| 退出码 | **1** |
| 报错 | `NameError: name 'TOPIC_CMD_RESULT' is not defined`（在 `main()` 第 403 行抛出，被顶层捕获打印为 `[FAIL] 未捕获异常:`，最终 `FAIL (1 assertion(s) failed; warns=0)`） |
| 证据 | 轨迹见 [`_test_netlink_tls.out:1-4`](tools/_smoke_result_regression/_test_netlink_tls.out:1)、[:27](tools/_smoke_result_regression/_test_netlink_tls.out:27)、[:31](tools/_smoke_result_regression/_test_netlink_tls.out:31) |
| 根因 | 测试脚本第 222 行 `from netlink.node import TOPIC_STATUS, TOPIC_PEER_STATE, TOPIC_AUTH` **[`tools/_test_netlink_tls.py:222`](tools/_test_netlink_tls.py:222)** 漏导入 `TOPIC_CMD_RESULT`，却在第 403 行 **[:403](tools/_test_netlink_tls.py:403)** 使用它。该常量在产品侧确实存在（[`src/netlink/node.py:58`](src/netlink/node.py:58)）。 |
| 触发链 | 修复前 `.venv` 无 `cryptography` → 证书分支不可用 → 4x/5x 真实握手段整体记 WARN 并**短路**，故从未执行到第 403 行；ENV-01 装上 `cryptography` 后进入真实 TLS 段，执行到第 403 行即触发 `NameError`。 |
| 关联修复文件 | **无**（既非 `src/utils.py`/`src/ACRPA.py`/`src/marketplace.py`，也非 `src/py_sandbox.py`；亦非本次任何源码改动）。 |
| 最小复现 | `.venv\Scripts\python.exe -X utf8 tools\_test_netlink_tls.py` → 退出码 1 |
| 建议修复方向（本次未实施，遵守边界） | 在 `tools/_test_netlink_tls.py:222` 的导入中补上 `TOPIC_CMD_RESULT`（例如 `from netlink.node import TOPIC_STATUS, TOPIC_PEER_STATE, TOPIC_AUTH, TOPIC_CMD_RESULT`）。仅 1 行、只动测试脚本，不影响产品代码。 |

> 说明：该 FAIL 的**其余断言全部通过**——即 ENV-01 想要验证的 TLS 真实握手（4a–4h、5a）已确认可用；缺陷仅出现在后续「TLS 下 CMD_RUN 指令回执」段（5b/5c）的订阅语句上。

---

## 5. 结论

- **ENV-01**：`cryptography 50.0.2` 安装成功，TLS 真实握手 WARN 消除。
- **全量回归**：39 脚本，**PASS 39 / FAIL 0 / SKIP 0**，含 WARN 的脚本 1（沙箱 v5 已知限制）。修复-A、修复-B 涉及的 6 个修复点**全部转绿且无副作用**。
- **TEST-03 已闭环**：[`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py) 的 `TOPIC_CMD_RESULT` 漏导入（测试脚本缺陷，ENV-01 诱导暴露）经修复-C 补齐后退出码 0、全断言 PASS（详见 §6），**非产品回归**。
- `src/` 业务源码全程未改动；仅修复-C 子任务修改了测试脚本 [`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py)（补齐 1 处导入）；原始输出全部落盘于 `tools\_smoke_result_regression\`。

---

## 6. TEST-03 闭环记录（修复-C 子任务 · 收尾）

> 定性：**测试脚本缺陷**（非产品 bug）。修复-C 仅修改 [`tools/_test_netlink_tls.py`](tools/_test_netlink_tls.py)，未触碰任何 `src/` 业务源码，未弱化任何断言。

| 项 | 内容 |
|---|---|
| 缺陷编号 | **TEST-03**（ENV-01 补齐 `cryptography` 后暴露） |
| 涉及文件:行（修复前） | [`tools/_test_netlink_tls.py:222`](tools/_test_netlink_tls.py:222)（导入区）与使用处 [:403](tools/_test_netlink_tls.py:403) |
| **修复前** | 第 222 行：`from netlink.node import TOPIC_STATUS, TOPIC_PEER_STATE, TOPIC_AUTH` —— **缺 `TOPIC_CMD_RESULT`**；第 403 行 `busB.subscribe(TOPIC_CMD_RESULT, ...)` → `NameError: name 'TOPIC_CMD_RESULT' is not defined` → 退出码 1 |
| **修复后** | 第 222 行改为多行导入：`from netlink.node import (TOPIC_STATUS, TOPIC_PEER_STATE, TOPIC_AUTH, TOPIC_CMD_RESULT)`（保持既有导入风格；同组 `TOPIC_*` 常量经核对无其它遗漏） |
| 复验命令 | `.venv\Scripts\python.exe -X utf8 tools\_test_netlink_tls.py` |
| 复验结果 | 退出码 **0**，`PASS (all assertions OK; warns=0)`；含 4a–4h、5a、5b/5c 全 `[OK]`；线程收敛 `nl-*→0` |
| 连带复跑 | `_debug_check.py` 退出码 0（`syntax_failed=0 import_failed=0`，18/18 模块导入）；`_test_netlink_e2e.py` 退出码 0（PASS）；`_test_netlink_control.py` 退出码 0（PASS）→ 无连带影响 |
| 产品侧常量来源 | [`src/netlink/node.py:58`](src/netlink/node.py:58)（`TOPIC_CMD_RESULT = "netlink.cmd_result"`，产品侧存在且未改动） |

> 结论：修复-C 完成后，全量 39 脚本 **全 PASS（FAIL 0）**；残留唯一 WARN 为 [`tools/_test_python_sandbox.py`](tools/_test_python_sandbox.py) 的 v5 C 层阻塞**已知限制**（如实标注为 WARN 而非 FAIL）。
