# ACRPA 本轮全部改动后全量回归明细（阶段6 · 最终全量回归子任务）

- 执行角色：最终全量回归子任务（阶段6，由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`（逐条执行；未用 `&&` / `|` / 反引号 / `$()`）
- 解释器：`.venv\Scripts\python.exe`（**Python 3.9.13**）；中文输出统一加 `-X utf8`
- 危险脚本排除：`_debug_mini_bar_geometry.py`（写 F 盘截图）、`_test_netlink_screenshot.py`（真实截屏）、`publish_release.py` / `make_release.py`（联网发版）
- 原始输出落盘：`tools\_smoke_result_regression2\`（每脚本一份 `.out`，共 **42** 份）
- 边界：**未修改任何 `src/` 业务源码，亦未修改任何测试脚本**；仅运行命令、采集输出、更新/新建报告。
- 覆盖本轮改动：修复-A/B/C（BUG-01~04、TEST-01~03）、标题可编辑、几何补强（几何公共函数抽取）、版本统一。
- 关联：总报告 [`tools\_smoke_report_总览.md`](tools/_smoke_report_总览.md)（新增「本轮全部改动后全量回归（v3）」章节）；前置回归 [`tools\_fix_report_regression.md`](tools/_fix_report_regression.md)（v2，39 脚本）。

---

## 0. 命令与退出码记录

> 每条命令统一格式（cmd.exe 逐条执行，无 `&&`）：
> `.venv\Scripts\python.exe -X utf8 tools\<脚本> > tools\_smoke_result_regression2\<脚本>.out 2>&1`
>
> 退出码取自每条命令的终端返回，**42 条命令全部为 0**。较 v2 的 39 脚本，本轮新增 3 个「目标项」验证脚本：`_smoke_editable_title.py`、`_smoke_main_geometry.py`、`_test_version_unify.py`，故总数为 **42**。

---

## 1. 全量回归总表（脚本级判定，42）

> 退出码来自每条命令的终端返回；PASS/FAIL/SKIP/WARN 为脚本级判定（脚本内部子用例的 SKIP 另行注明）。

### A. 基线与逻辑（8）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| A1 | [`_debug_check.py`](tools/_debug_check.py) | 0 | **PASS** | `checked 109 files, 0 failed`；`18/18 modules imported`；`syntax_failed=0 import_failed=0`（[`out`](tools/_smoke_result_regression2/_debug_check.out:1)） |
| A2 | [`_smoke_engine.py`](tools/_smoke_engine.py) | 0 | **PASS** | `全部通过 ✓`（9 项：IF/嵌套/循环/跳出）（[`out`](tools/_smoke_result_regression2/_smoke_engine.out:13)） |
| A3 | [`_debug_functional.py`](tools/_debug_functional.py) | 0 | **PASS** | `全部通过 ✓`（8 组：safe_eval/ScriptData/commands/version_manager/scheduler/workflow/plugins/updater）（[`out`](tools/_smoke_result_regression2/_debug_functional.out:39)） |
| A4 | [`_debug_error_semantics.py`](tools/_debug_error_semantics.py) | 0 | **PASS** | `失败语义回归: 通过 32 项, 失败 0 项`（[`out`](tools/_smoke_result_regression2/_debug_error_semantics.out:41)） |
| A5 | [`_debug_workflow.py`](tools/_debug_workflow.py) | 0 | **PASS** | `工作流引擎验证: 通过 19 项, 失败 0 项`（[`out`](tools/_smoke_result_regression2/_debug_workflow.out:30)） |
| A6 | [`_smoke_dark_mode.py`](tools/_smoke_dark_mode.py) | 0 | **PASS** | `=== 结果: OK ===`；暗黑模式/互联按钮静态断言全过（[`out`](tools/_smoke_result_regression2/_smoke_dark_mode.out:34)） |
| A7 | [`_smoke_netlink.py`](tools/_smoke_netlink.py) | 0 | **PASS** | `PASS 8/8`（[`out`](tools/_smoke_result_regression2/_smoke_netlink.out:10)） |
| A8 | [`_debug_updater.py`](tools/_debug_updater.py) | 0 | **PASS** | `ALL PASSED  (107 项)`（[`out`](tools/_smoke_result_regression2/_debug_updater.out:121)） |

### B. 浏览器纯逻辑套件（9）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| B1 | [`_test_browser_locator.py`](tools/_test_browser_locator.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_regression2/_test_browser_locator.out:150)） |
| B2 | [`_test_browser_locator_chain.py`](tools/_test_browser_locator_chain.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_regression2/_test_browser_locator_chain.out:115)） |
| B3 | [`_test_browser_waiter.py`](tools/_test_browser_waiter.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_regression2/_test_browser_waiter.out:62)） |
| B4 | [`_test_browser_cdp.py`](tools/_test_browser_cdp.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 1（真实 CDP 接管）（[`out`](tools/_smoke_result_regression2/_test_browser_cdp.out:46)） |
| B5 | [`_test_browser_codegen_convert.py`](tools/_test_browser_codegen_convert.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 2（真实录制）（[`out`](tools/_smoke_result_regression2/_test_browser_codegen_convert.out:111)） |
| B6 | [`_test_browser_cookie.py`](tools/_test_browser_cookie.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_regression2/_test_browser_cookie.out:40)） |
| B7 | [`_test_browser_download.py`](tools/_test_browser_download.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 3（真实下载/上传/命中）（[`out`](tools/_smoke_result_regression2/_test_browser_download.out:71)） |
| B8 | [`_test_browser_frame_tab.py`](tools/_test_browser_frame_tab.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 2（真实 iframe/标签页）（[`out`](tools/_smoke_result_regression2/_test_browser_frame_tab.out:91)） |
| B9 | [`_test_browser_listen.py`](tools/_test_browser_listen.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 1（真实网络监听）（[`out`](tools/_smoke_result_regression2/_test_browser_listen.out:97)） |

> B 组 9 个脚本内部对「真实浏览器」分支共含 **9 条 `[SKIP]`**（未装 playwright/Chromium），与修复前一致，**非回归**。

### C. 沙箱 / AI / 市场（5）— 全 PASS（含 1 已知 WARN）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| C1 | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py) | 0 | **PASS（含 1 WARN）** | `SUMMARY: 0 FAIL, 1 WARN`（唯一 WARN 为 v5 C 层阻塞已知限制）（[`out`](tools/_smoke_result_regression2/_test_python_sandbox.out:101)） |
| C2 | [`_test_code_command.py`](tools/_test_code_command.py) | 0 | **PASS** | `SUMMARY: 0 FAIL, 0 WARN`（[`out`](tools/_smoke_result_regression2/_test_code_command.out:39)） |
| C3 | [`_test_ai_provider.py`](tools/_test_ai_provider.py) | 0 | **PASS** | `PASS (all assertions OK)`，warns=0（[`out`](tools/_smoke_result_regression2/_test_ai_provider.out:62)） |
| C4 | [`_test_script_package.py`](tools/_test_script_package.py) | 0 | **PASS** | `结果: 全部 项通过, 0 项 FAIL, 0 项 WARN`；`[OK] builtin 脚本生成路径零回归`（[`out`](tools/_smoke_result_regression2/_test_script_package.out:144)） |
| C5 | [`_test_market_upload.py`](tools/_test_market_upload.py) | 0 | **PASS** | `汇总: FAIL=0  WARN=0  SKIP=1`（真实 PR SKIP，未配置 token）（[`out`](tools/_smoke_result_regression2/_test_market_upload.out:88)） |

### D. NetLink 验收（5）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| D1 | [`_test_netlink_auth.py`](tools/_test_netlink_auth.py) | 0 | **PASS** | `PASS (all assertions OK)`，`elapsed 4.04s, warns=0`（[`out`](tools/_smoke_result_regression2/_test_netlink_auth.out:45)） |
| D2 | [`_test_netlink_control.py`](tools/_test_netlink_control.py) | 0 | **PASS** | `PASS (all assertions OK)`，`warns=0`（[`out`](tools/_smoke_result_regression2/_test_netlink_control.out:52)） |
| D3 | [`_test_netlink_transfer.py`](tools/_test_netlink_transfer.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`（[`out`](tools/_smoke_result_regression2/_test_netlink_transfer.out:39)） |
| D4 | [`_test_netlink_tls.py`](tools/_test_netlink_tls.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`，`耗时 6.94s`（修复后的 TEST-03 保持转绿）（[`out`](tools/_smoke_result_regression2/_test_netlink_tls.out:36)） |
| D5 | [`_test_netlink_e2e.py`](tools/_test_netlink_e2e.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`，`总耗时 13.1s`（[`out`](tools/_smoke_result_regression2/_test_netlink_e2e.out:28)） |

### E. UI 冒烟（12）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| E1 | [`_smoke_mini_bar.py`](tools/_smoke_mini_bar.py) | 0 | **PASS** | `=== 结果: OK ===`（[`out`](tools/_smoke_result_regression2/_smoke_mini_bar.out:16)） |
| E2 | [`_smoke_mini_bar.py --launch`](tools/_smoke_mini_bar.py) | 0 | **PASS** | `[OK] 启动存活冒烟: run.py 存活 10s (无导入/构建期异常)`（[`out`](tools/_smoke_result_regression2/_smoke_mini_bar_launch.out:16)） |
| E3 | [`_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) | 0 | **PASS** | `=== 结果: OK ===`；`set_ui_scale(1.2) 字号 9 → 11`（修复项 🟢）（[`out`](tools/_smoke_result_regression2/_smoke_ui_scale.out:33)） |
| E4 | [`_smoke_marketplace.py`](tools/_smoke_marketplace.py) | 0 | **PASS** | `=== 结果: OK (FAIL=0 WARN=0) ===`（修复项 🟢）（[`out`](tools/_smoke_result_regression2/_smoke_marketplace.out:41)） |
| E5 | [`_smoke_netlink_window.py`](tools/_smoke_netlink_window.py) | 0 | **PASS** | `all window smoke checks passed`（[`out`](tools/_smoke_result_regression2/_smoke_netlink_window.out:19)） |
| E6 | [`_smoke_netlink_pairing_ui.py`](tools/_smoke_netlink_pairing_ui.py) | 0 | **PASS** | `PASS (all assertions OK)`，`elapsed 8.47s, warns=0`（[`out`](tools/_smoke_result_regression2/_smoke_netlink_pairing_ui.out:28)） |
| E7 | [`_smoke_netlink_control_ui.py`](tools/_smoke_netlink_control_ui.py) | 0 | **PASS** | `PASS (all assertions OK)`，`warns=0`（[`out`](tools/_smoke_result_regression2/_smoke_netlink_control_ui.out:29)） |
| E8 | [`_smoke_netlink_transfer_ui.py`](tools/_smoke_netlink_transfer_ui.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`（[`out`](tools/_smoke_result_regression2/_smoke_netlink_transfer_ui.out:25)） |
| E9 | [`_debug_dark_theme.py`](tools/_debug_dark_theme.py) | 0 | **PASS** | `=== 结果: OK (共 57 项检查) ===`（[`out`](tools/_smoke_result_regression2/_debug_dark_theme.out:78)） |
| E10 | [`_debug_theme_trigger.py`](tools/_debug_theme_trigger.py) | 0 | **PASS** | `=== 结果: OK ===`（T1–T4 触发收窄实测通过）（[`out`](tools/_smoke_result_regression2/_debug_theme_trigger.out:7)） |
| E11 | [`_smoke_editable_title.py`](tools/_smoke_editable_title.py) | 0 | **PASS** | `=== 结果: OK  通过 16/16, 失败 0 ===`；config 未被写入（目标项 🟢）（[`out`](tools/_smoke_result_regression2/_smoke_editable_title.out:35)） |
| E12 | [`_smoke_main_geometry.py`](tools/_smoke_main_geometry.py) | 0 | **PASS** | `FAIL=0 WARN=0`；默认/记忆/越界回退/最大化/紧凑全绿（目标项 🟢）（[`out`](tools/_smoke_result_regression2/_smoke_main_geometry.out:22)） |

### F. 应用级验证（2）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| F1 | [`_smoke_launch_app.py`](tools/_smoke_launch_app.py) | 0 | **PASS** | `=== 结果: OK (rc=0) ===`；run.py 存活 ≥10s，`子进程输出含 Traceback: False`（[`out`](tools/_smoke_result_regression2/_smoke_launch_app.out:41)） |
| F2 | [`_smoke_ui_introspect.py`](tools/_smoke_ui_introspect.py) | 0 | **PASS** | 主窗 `1250x850`，req `897x703`（`reqh-h=-147`）；`[OVERFLOW] 命中溢出=0`（目标项 🟢）（[`out`](tools/_smoke_result_regression2/_smoke_ui_introspect.out:40)） |

### G. 版本一致性（1）— PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| G1 | [`_test_version_unify.py`](tools/_test_version_unify.py) | 0 | **PASS** | `通过 16 / 失败 0 / 警告 0`，结论 `PASS`（V1 权威源一致性、V2 node.py 收敛、V3 回退版本同步、V4 网页单一源、V5 README 指令修正）（[`out`](tools/_smoke_result_regression2/_test_version_unify.out:23)） |

---

## 2. 统计

| 维度 | 数量 | 说明 |
|---|---:|---|
| 总脚本数 | **42** | A 8 + B 9 + C 5 + D 5 + E 12 + F 2 + G 1 |
| PASS | **42** | 脚本级退出码 0 且断言全通过 |
| FAIL | **0** | 无 |
| SKIP（脚本级） | **0** | 无整脚本被跳过 |
| WARN（含 WARN 的脚本） | **1** | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py)（v5 C 层阻塞，已知限制） |
| 子用例 SKIP | **10** | 浏览器真实分支 9（无 playwright）+ 市场真实 PR 1（无 token） |
| 非零退出码 | **0** | — |

---

## 3. 本轮目标项转绿确认

| 目标项 | 目标观察点 | 回归实测 | 状态 |
|---|---|---|---|
| **标题可编辑** | `_smoke_editable_title.py` OK 且 config 未被写入 | `通过 16/16, 失败 0`；`config.json` 编辑前后 `(True,'c54d2933f172b8259c699ddd923c710d',3307,1790953480.8812804)` **完全一致** → **未被写入**（[`_smoke_editable_title.out:24`](tools/_smoke_result_regression2/_smoke_editable_title.out:24)） | **已闭环** 🟢 |
| **几何/最大化/紧凑/越界** | `_smoke_main_geometry.py` 全绿 | `FAIL=0 WARN=0`；虚拟屏边界、`geometry_in_screen` 越界判定、`center_geometry`、默认分支、屏内记忆、越界记忆回退居中、最大化(`zoomed`)、紧凑(`500x625`) 全 `[OK]`（[`_smoke_main_geometry.out:22`](tools/_smoke_result_regression2/_smoke_main_geometry.out:22)） | **已闭环** 🟢 |
| **溢出** | `_smoke_ui_introspect.py` 无 `[OVERFLOW]` | 主窗 `1250x850`、req `897x703`（`reqh-h=-147`）；扫描 169 控件，`[OVERFLOW] 命中溢出=0`（[`_smoke_ui_introspect.out:40`](tools/_smoke_result_regression2/_smoke_ui_introspect.out:40)） | **已闭环** 🟢 |
| **版本一致性** | `_test_version_unify.py` PASS | `通过 16 / 失败 0 / 警告 0`，结论 `PASS`（[`_test_version_unify.out:23`](tools/_smoke_result_regression2/_test_version_unify.out:23)） | **已闭环** 🟢 |
| 既有修复 BUG-01 | `_smoke_ui_scale.py` 字号随档位 | `=== 结果: OK ===`；`set_ui_scale(1.2) → 9→11`；clamp/复位/`scaled` 全 OK（[`_smoke_ui_scale.out:33`](tools/_smoke_result_regression2/_smoke_ui_scale.out:33)） | **保持转绿** 🟢 |
| 既有修复 BUG-01/02 | `_smoke_marketplace.py` FAIL=0 | `=== 结果: OK (FAIL=0 WARN=0) ===`（[`_smoke_marketplace.out:41`](tools/_smoke_result_regression2/_smoke_marketplace.out:41)） | **保持转绿** 🟢 |
| 既有修复 BUG-02 | `_test_script_package.py` 无模板 WARN | `0 项 FAIL, 0 项 WARN`；`[OK] builtin 脚本生成路径零回归`（[`_test_script_package.out:144`](tools/_smoke_result_regression2/_test_script_package.out:144)） | **保持转绿** 🟢 |
| 既有修复 BUG-04 | `_test_python_sandbox.py` | `SUMMARY: 0 FAIL, 1 WARN`（v5 C 层阻塞已知限制）（[`_test_python_sandbox.out:101`](tools/_smoke_result_regression2/_test_python_sandbox.out:101)） | **保持转绿（含已知 WARN）** 🟢 |
| 既有修复 TEST-03 | `_test_netlink_tls.py` | 退出码 0；`PASS (all assertions OK; warns=0)`（[`_test_netlink_tls.out:36`](tools/_smoke_result_regression2/_test_netlink_tls.out:36)） | **保持转绿** 🟢 |

---

## 4. 新引入 FAIL / 回归

**无。**

- 全量 **42 脚本退出码均为 0**；`tools\_smoke_result_regression2\` 内**无任何 `[FAIL]`** 行。
- 唯一 `[WARN]`：`_test_python_sandbox.py` v5「C 层阻塞 `time.sleep` 无法按时中断」——为本轮之前即存在的**已知设计限制**（BUG-04 收尾时如实标注），非本轮改动引入。
- `_smoke_launch_app.out` 顶部存在一条 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb3 in position 0: invalid start byte`：
  - 位置：校验脚本自身的 `Thread-1`（`subprocess._readerthread`），非被测应用线程；
  - 性质：读子进程输出时的编码伪影（子进程输出非 UTF-8 字节）；
  - 回归性：与 v2 基线 [`tools\_smoke_result_regression\_smoke_launch_app.out:1`](tools/_smoke_result_regression/_smoke_launch_app.out:1) **逐行完全一致** → **既有伪影，非本轮引入**；
  - 影响：脚本最终判定 `存活至阈值(>=10s): True`、`子进程输出含 Traceback: False`、`=== 结果: OK (rc=0) ===` → **不影响 PASS 判定，非产品问题**。

> 结论：**本轮未引入任何新的 FAIL 或跨模块回归。**

---

## 5. 与 v2 回归（39 脚本）的对比

| 维度 | v2（阶段5） | v3（本轮，阶段6） | 变化 |
|---|---:|---:|---|
| 总脚本数 | 39 | **42** | +3（`_smoke_editable_title`、`_smoke_main_geometry`、`_test_version_unify`） |
| PASS | 39 | **42** | 新增 3 项全绿 |
| FAIL | 0 | **0** | 无新增 FAIL |
| 脚本级 SKIP | 0 | **0** | 无变化 |
| WARN 脚本数 | 1（沙箱 v5 已知限制） | **1** | 无变化 |
| 子用例 SKIP | 10 | **10** | 无变化 |
| UI 冒烟脚本数 | 10 | **12** | +2（标题可编辑、几何） |

- 与 v2 重叠的 39 个脚本**全部保持 PASS**，逐条核对其 `.out` 摘要无退化。
- v2 曾记录的 TEST-03（`_test_netlink_tls.py` 漏导入）在本轮 `D4` 复跑中**保持转绿**（退出码 0，`warns=0`）。
- 本轮「标题可编辑 / 几何 / 版本统一」三项新目标验证脚本首次纳入回归，均 **PASS**。

---

## 6. 结论

- **全量回归（v3）**：42 脚本 **PASS 42 / FAIL 0 / SKIP（脚本级）0**；含 WARN 的脚本 1（[`_test_python_sandbox.py`](tools/_test_python_sandbox.py) v5 C 层阻塞**已知限制**）。**未发现任何跨模块回归。**
- **目标项**：标题可编辑（16/16，config 未被写入）、几何/最大化/紧凑/越界（`_smoke_main_geometry` 全绿）、溢出（`[OVERFLOW] 命中=0`）、版本一致性（`_test_version_unify` 16/0/0 PASS）**全部转绿且无副作用**。
- **既有修复项**：BUG-01~04、TEST-01/02/03 **全部保持转绿**。
- **边界遵守**：`src/` 业务源码与测试脚本**全程未被本子任务改动**；危险脚本继续排除；原始输出全部落盘于 `tools\_smoke_result_regression2\`。
- **新引入回归**：**无**（唯一 `UnicodeDecodeError` 经比对为 v2 既有 harness 伪影，非本轮引入、非产品问题）。
