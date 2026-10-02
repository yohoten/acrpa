# ACRPA 帮助窗口重构后 · 最终全量回归明细（v4）

- 执行角色：最终全量回归子任务（由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`（逐条执行；未用 `&&` / `|` / 反引号 / `$()`）
- 解释器：`.venv\Scripts\python.exe`（**Python 3.9.13**）；中文输出统一加 `-X utf8`
- 原始输出落盘：`tools\_smoke_result_help\`（每脚本一份 `.out`，共 **45** 份）
- 覆盖本轮改动：**帮助窗口重构（阶段0 内容层 / 阶段1-2 渲染层+委托化 / 阶段4 收尾）**
  - 新增 [`src/help_content.py`](src/help_content.py)、[`src/help_window.py`](src/help_window.py)、`res/help/*`、[`tools/_test_help_consistency.py`](tools/_test_help_consistency.py)、[`tools/_smoke_help_window.py`](tools/_smoke_help_window.py)、[`tools/_gen_help_command_table.py`](tools/_gen_help_command_table.py)
  - 修改 [`src/dialogs.py`](src/dialogs.py)（`show_help_dialog` 薄委托 + legacy fallback）、[`src/state.py`](src/state.py)（`help_geometry`/`help_modal`/`help_maximized`）、[`src/ACRPA.py`](src/ACRPA.py)（主题切换追加 help refresh）、[`src/settings_window.py`](src/settings_window.py)（「帮助窗口使用模态」复选框）、[`ACRPA.spec`](ACRPA.spec)（datas 追加 `res/help` 与 docs）、`使用说明.txt`（命令区对齐+横幅）
- 危险脚本排除：`_debug_mini_bar_geometry.py`（写 F 盘截图）、`_test_netlink_screenshot.py`（真实截屏）、`publish_release.py` / `make_release.py`（联网发版）
- 边界：**未修改任何 `src/` 业务源码，亦未修改任何测试脚本**；仅运行命令、采集输出、更新/新建报告
- 关联：总报告 [`tools\_smoke_report_总览.md`](tools/_smoke_report_总览.md)（新增「帮助窗口重构后全量回归（v4）」章节）；前置回归 [`tools\_fix_report_regression2.md`](tools/_fix_report_regression2.md)（v3，42 脚本）

---

## 0. 命令与退出码记录

> 每条命令统一格式（cmd.exe 逐条执行，无 `&&`）：
> `.venv\Scripts\python.exe -X utf8 tools\<脚本> > tools\_smoke_result_help\<脚本>.out 2>&1`
>
> 退出码取自每条命令的终端返回，**45 条命令全部为 0**。较 v3 的 42 脚本，本轮新增 3 个「帮助子系统」验证脚本：`_test_help_consistency.py`、`_smoke_help_window.py`、`_gen_help_command_table.py --check`，故总数为 **45**。

| 组 | 脚本数 | 说明 |
|---|---:|---|
| H 帮助子系统 | 3 | 内容层一致性 / 渲染层冒烟 / 使用说明命令表 `--check` |
| A 基线/逻辑 | 8 | 语法+导入、引擎、功能、失败语义、工作流、暗黑静态、netlink 逻辑、更新器 |
| B 浏览器纯逻辑 | 9 | locator/chain/waiter/cdp/codegen/cookie/download/frame_tab/listen |
| C 沙箱/AI/市场 | 5 | python_sandbox/code_command/ai_provider/script_package/market_upload |
| D NetLink 验收 | 5 | auth/control/transfer/tls/e2e |
| E UI 冒烟 | 12 | mini_bar(+launch)/ui_scale/marketplace/nl_window/nl_pairing/nl_control/nl_transfer/dark_theme/theme_trigger/editable_title/main_geometry |
| F 应用级 | 2 | launch_app / ui_introspect |
| G 版本一致性 | 1 | version_unify |
| **合计** | **45** | — |

---

## 1. 全量回归总表（脚本级判定，45）

> 退出码来自每条命令的终端返回；PASS/FAIL/SKIP/WARN 为脚本级判定（脚本内部子用例的 SKIP 另行注明）。

### H. 帮助子系统（3）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| H1 | [`_test_help_consistency.py`](tools/_test_help_consistency.py) | 0 | **PASS** | 帮助内容层 27 项全 `[OK]`；`注册表命令数: 68 | FAIL=0 | WARN=0`；`结论: PASS`（[`out`](tools/_smoke_result_help/_test_help_consistency.out:28)） |
| H2 | [`_smoke_help_window.py`](tools/_smoke_help_window.py) | 0 | **PASS** | A1–A11 全 `[OK]`；`FAIL=0 WARN=0`；`结论: PASS`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:20)） |
| H3 | [`_gen_help_command_table.py --check`](tools/_gen_help_command_table.py) | 0 | **PASS** | `注册表命令 68 条，文档命中 68 条`；`[OK] 注册表命令集合 ⊆ 文档命令名集合`（[`out`](tools/_smoke_result_help/_gen_help_command_table_check.out:2)） |

### A. 基线与逻辑（8）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| A1 | [`_debug_check.py`](tools/_debug_check.py) | 0 | **PASS** | `checked 114 files, 0 failed`；`18/18 modules imported`；`syntax_failed=0 import_failed=0`（[`out`](tools/_smoke_result_help/_debug_check.out:1)） |
| A2 | [`_smoke_engine.py`](tools/_smoke_engine.py) | 0 | **PASS** | `全部通过 ✓`（9 项：IF/嵌套/循环/跳出）（[`out`](tools/_smoke_result_help/_smoke_engine.out:13)） |
| A3 | [`_debug_functional.py`](tools/_debug_functional.py) | 0 | **PASS** | `全部通过 ✓`（8 组：safe_eval/ScriptData/commands/version_manager/scheduler/workflow/plugins/updater）（[`out`](tools/_smoke_result_help/_debug_functional.out:39)） |
| A4 | [`_debug_error_semantics.py`](tools/_debug_error_semantics.py) | 0 | **PASS** | `失败语义回归: 通过 32 项, 失败 0 项`（[`out`](tools/_smoke_result_help/_debug_error_semantics.out:41)） |
| A5 | [`_debug_workflow.py`](tools/_debug_workflow.py) | 0 | **PASS** | `工作流引擎验证: 通过 19 项, 失败 0 项`（[`out`](tools/_smoke_result_help/_debug_workflow.out:30)） |
| A6 | [`_smoke_dark_mode.py`](tools/_smoke_dark_mode.py) | 0 | **PASS** | `=== 结果: OK ===`；暗黑模式/互联按钮静态断言全过（[`out`](tools/_smoke_result_help/_smoke_dark_mode.out:34)） |
| A7 | [`_smoke_netlink.py`](tools/_smoke_netlink.py) | 0 | **PASS** | `PASS 8/8`（[`out`](tools/_smoke_result_help/_smoke_netlink.out:10)） |
| A8 | [`_debug_updater.py`](tools/_debug_updater.py) | 0 | **PASS** | `ALL PASSED  (107 项)`（[`out`](tools/_smoke_result_help/_debug_updater.out:121)） |

> A1 文件数由 v3 的 109 升至 **114**（+5）：新增 `src/help_content.py`、`src/help_window.py` 与 3 个 help 工具脚本，均被语法/导入扫描覆盖。

### B. 浏览器纯逻辑套件（9）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| B1 | [`_test_browser_locator.py`](tools/_test_browser_locator.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_help/_test_browser_locator.out:150)） |
| B2 | [`_test_browser_locator_chain.py`](tools/_test_browser_locator_chain.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_help/_test_browser_locator_chain.out:115)） |
| B3 | [`_test_browser_waiter.py`](tools/_test_browser_waiter.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_help/_test_browser_waiter.out:62)） |
| B4 | [`_test_browser_cdp.py`](tools/_test_browser_cdp.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 1（真实 CDP 接管）（[`out`](tools/_smoke_result_help/_test_browser_cdp.out:46)） |
| B5 | [`_test_browser_codegen_convert.py`](tools/_test_browser_codegen_convert.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 2（真实录制）（[`out`](tools/_smoke_result_help/_test_browser_codegen_convert.out:111)） |
| B6 | [`_test_browser_cookie.py`](tools/_test_browser_cookie.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`（[`out`](tools/_smoke_result_help/_test_browser_cookie.out:40)） |
| B7 | [`_test_browser_download.py`](tools/_test_browser_download.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 3（真实下载/上传/命中）（[`out`](tools/_smoke_result_help/_test_browser_download.out:71)） |
| B8 | [`_test_browser_frame_tab.py`](tools/_test_browser_frame_tab.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 2（真实 iframe/标签页）（[`out`](tools/_smoke_result_help/_test_browser_frame_tab.out:91)） |
| B9 | [`_test_browser_listen.py`](tools/_test_browser_listen.py) | 0 | **PASS** | `结果: PASS (0 失败, 0 项警告)`；子用例 SKIP 1（真实网络监听）（[`out`](tools/_smoke_result_help/_test_browser_listen.out:97)） |

> B 组 9 个脚本内部对「真实浏览器」分支共含 **9 条 `[SKIP]`**（未装 playwright/Chromium），与 v2/v3 基线一致，**非回归**。

### C. 沙箱 / AI / 市场（5）— 全 PASS（含 1 已知 WARN）

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| C1 | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py) | 0 | **PASS（含 1 WARN）** | `SUMMARY: 0 FAIL, 1 WARN`（唯一 WARN 为 v5 C 层阻塞已知限制）（[`out`](tools/_smoke_result_help/_test_python_sandbox.out:101)） |
| C2 | [`_test_code_command.py`](tools/_test_code_command.py) | 0 | **PASS** | `SUMMARY: 0 FAIL, 0 WARN`（[`out`](tools/_smoke_result_help/_test_code_command.out:39)） |
| C3 | [`_test_ai_provider.py`](tools/_test_ai_provider.py) | 0 | **PASS** | `PASS (all assertions OK)`，warns=0（[`out`](tools/_smoke_result_help/_test_ai_provider.out:62)） |
| C4 | [`_test_script_package.py`](tools/_test_script_package.py) | 0 | **PASS** | `结果: 全部 项通过, 0 项 FAIL, 0 项 WARN`；`builtin 脚本生成路径零回归`（[`out`](tools/_smoke_result_help/_test_script_package.out:144)） |
| C5 | [`_test_market_upload.py`](tools/_test_market_upload.py) | 0 | **PASS** | `汇总: FAIL=0  WARN=0  SKIP=1`（真实 PR SKIP，未配置 token）（[`out`](tools/_smoke_result_help/_test_market_upload.out:88)） |

### D. NetLink 验收（5）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| D1 | [`_test_netlink_auth.py`](tools/_test_netlink_auth.py) | 0 | **PASS** | `PASS (all assertions OK)`，`elapsed 3.96s, warns=0`（[`out`](tools/_smoke_result_help/_test_netlink_auth.out:45)） |
| D2 | [`_test_netlink_control.py`](tools/_test_netlink_control.py) | 0 | **PASS** | `PASS (all assertions OK)`，`warns=0`（[`out`](tools/_smoke_result_help/_test_netlink_control.out:52)） |
| D3 | [`_test_netlink_transfer.py`](tools/_test_netlink_transfer.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`（[`out`](tools/_smoke_result_help/_test_netlink_transfer.out:39)） |
| D4 | [`_test_netlink_tls.py`](tools/_test_netlink_tls.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`，`耗时 6.74s`（修复后 TEST-03 保持转绿）（[`out`](tools/_smoke_result_help/_test_netlink_tls.out:36)） |
| D5 | [`_test_netlink_e2e.py`](tools/_test_netlink_e2e.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`，`总耗时 13.1s`（[`out`](tools/_smoke_result_help/_test_netlink_e2e.out:28)） |

### E. UI 冒烟（12）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| E1 | [`_smoke_mini_bar.py`](tools/_smoke_mini_bar.py) | 0 | **PASS** | `=== 结果: OK ===`（[`out`](tools/_smoke_result_help/_smoke_mini_bar.out:16)） |
| E2 | [`_smoke_mini_bar.py --launch`](tools/_smoke_mini_bar.py) | 0 | **PASS** | `[OK] 启动存活冒烟: run.py 存活 10s (无导入/构建期异常)`（[`out`](tools/_smoke_result_help/_smoke_mini_bar_launch.out:16)） |
| E3 | [`_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) | 0 | **PASS** | `=== 结果: OK ===`；字号随档位（既有修复项 🟢）（[`out`](tools/_smoke_result_help/_smoke_ui_scale.out:60)） |
| E4 | [`_smoke_marketplace.py`](tools/_smoke_marketplace.py) | 0 | **PASS** | `=== 结果: OK (FAIL=0 WARN=0) ===`（既有修复项 🟢）（[`out`](tools/_smoke_result_help/_smoke_marketplace.out:41)） |
| E5 | [`_smoke_netlink_window.py`](tools/_smoke_netlink_window.py) | 0 | **PASS** | `all window smoke checks passed`（[`out`](tools/_smoke_result_help/_smoke_netlink_window.out:19)） |
| E6 | [`_smoke_netlink_pairing_ui.py`](tools/_smoke_netlink_pairing_ui.py) | 0 | **PASS** | `PASS (all assertions OK)`，`elapsed 11.24s, warns=0`（[`out`](tools/_smoke_result_help/_smoke_netlink_pairing_ui.out:28)） |
| E7 | [`_smoke_netlink_control_ui.py`](tools/_smoke_netlink_control_ui.py) | 0 | **PASS** | `PASS (all assertions OK)`，`warns=0`（[`out`](tools/_smoke_result_help/_smoke_netlink_control_ui.out:29)） |
| E8 | [`_smoke_netlink_transfer_ui.py`](tools/_smoke_netlink_transfer_ui.py) | 0 | **PASS** | `PASS (all assertions OK; warns=0)`（[`out`](tools/_smoke_result_help/_smoke_netlink_transfer_ui.out:25)） |
| E9 | [`_debug_dark_theme.py`](tools/_debug_dark_theme.py) | 0 | **PASS** | `=== 结果: OK (共 57 项检查) ===`（[`out`](tools/_smoke_result_help/_debug_dark_theme.out:78)） |
| E10 | [`_debug_theme_trigger.py`](tools/_debug_theme_trigger.py) | 0 | **PASS** | `=== 结果: OK ===`；T1–T4 触发收窄实测通过（`T4 toggle_dark() refresh 增量: got 1 / expected 1`）（[`out`](tools/_smoke_result_help/_debug_theme_trigger.out:7)） |
| E11 | [`_smoke_editable_title.py`](tools/_smoke_editable_title.py) | 0 | **PASS** | `=== 结果: OK  通过 16/16, 失败 0 ===`（既有目标项 🟢）（[`out`](tools/_smoke_result_help/_smoke_editable_title.out:35)） |
| E12 | [`_smoke_main_geometry.py`](tools/_smoke_main_geometry.py) | 0 | **PASS** | `FAIL=0 WARN=0`；默认/记忆/越界回退/最大化/紧凑全绿（既有目标项 🟢）（[`out`](tools/_smoke_result_help/_smoke_main_geometry.out:22)） |

### F. 应用级验证（2）— 全 PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| F1 | [`_smoke_launch_app.py`](tools/_smoke_launch_app.py) | 0 | **PASS** | `=== 结果: OK (rc=0) ===`；run.py 存活 ≥10s，`子进程输出含 Traceback: False`（[`out`](tools/_smoke_result_help/_smoke_launch_app.out:41)） |
| F2 | [`_smoke_ui_introspect.py`](tools/_smoke_ui_introspect.py) | 0 | **PASS** | 主窗 `1250x850`，req `897x703`（`超窗=False`）；`[OVERFLOW] 命中溢出=0`（既有目标项 🟢）（[`out`](tools/_smoke_result_help/_smoke_ui_introspect.out:40)） |

### G. 版本一致性（1）— PASS

| # | 脚本 | 退出码 | 判定 | 摘要 |
|---|---|---:|---|---|
| G1 | [`_test_version_unify.py`](tools/_test_version_unify.py) | 0 | **PASS** | `通过 16 / 失败 0 / 警告 0`；`结论: PASS`（[`out`](tools/_smoke_result_help/_test_version_unify.out:23)） |

---

## 2. 统计

| 维度 | 数量 | 说明 |
|---|---:|---|
| 总脚本数 | **45** | H 3 + A 8 + B 9 + C 5 + D 5 + E 12 + F 2 + G 1 |
| PASS | **45** | 脚本级退出码 0 且断言全通过 |
| FAIL | **0** | 无 |
| SKIP（脚本级） | **0** | 无整脚本被跳过 |
| WARN（含 WARN 的脚本） | **1** | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py)（v5 C 层阻塞，已知限制，**非本轮引入**） |
| 子用例 SKIP | **10** | 浏览器真实分支 9（无 playwright）+ 市场真实 PR 1（无 token） |
| 非零退出码 | **0** | — |

---

## 3. 本轮目标项转绿确认（帮助窗口重构）

| 目标项 | 目标观察点 | 回归实测 | 状态 |
|---|---|---|---|
| **帮助窗口可开** | `_smoke_help_window.py` A1 | `[OK] A1 帮助窗口可开（Toplevel 存活）`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:2)） | **已闭环** 🟢 |
| **13 章** | A2 / H1 章节数 | `[OK] A2 章节数 13 (>=13)` 且全部正文非空；H1 `13 章标题与顺序符合预期`、`sections.json order 生效`（[`out`](tools/_smoke_result_help/_test_help_consistency.out:4)） | **已闭环** 🟢 |
| **命令 68==68** | H1 双向一致 / H2 A3 / H3 | H1 `命令双向一致：注册表 68 条 == 帮助渲染 68 条`；H2 `[OK] A3 命令集合一致（注册表 68 == 渲染 68）`；H3 `注册表命令 68 条，文档命中 68 条`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:5)） | **已闭环** 🟢 |
| **搜索** | H2 A5 | `[OK] A5 搜索 '循环' 命中命令: ['循环开始','循环结束','跳出循环']`；`'netlink' 命中章节 ['netlink','releases']`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:7)） | **已闭环** 🟢 |
| **主题实时** | H2 A6 | `[OK] A6 主题跟随: text.bg #fdfdfd -> #1a2332 (nav #ffffff -> #1a2332)`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:9)） | **已闭环** 🟢 |
| **缩放** | H2 A7 | `[OK] A7 渲染层使用 utils.scaled（同源令牌）`；`scaled(100) 100 -> 150（scale 1.0->1.5）`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:11)） | **已闭环** 🟢 |
| **几何记忆** | H2 A8 | `[OK] A8 记忆几何恢复 900x640+574+213 落在虚拟屏内`；关闭写回 + 越界回退居中均 `[OK]`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:12)） | **已闭环** 🟢 |
| **向后兼容** | H2 A11 / [`src/dialogs.py`](src/dialogs.py) | `[OK] A11 show_help_dialog() 委托成功（legacy fallback 保留）`（[`out`](tools/_smoke_result_help/_smoke_help_window.out:18)）；委托 [`src/dialogs.py:104`](src/dialogs.py:104)、回退 [`src/dialogs.py:122`](src/dialogs.py:122)、legacy [`src/dialogs.py:125`](src/dialogs.py:125) | **已闭环** 🟢 |
| **`使用说明.txt --check`** | H3 | `注册表命令 68 条，文档命中 68 条`；`[OK] 注册表命令集合 ⊆ 文档命令名集合（文档未漏命令）`（[`out`](tools/_smoke_result_help/_gen_help_command_table_check.out:3)） | **已闭环** 🟢 |

### 3.1 既有项保持转绿确认

| 既有项 | 脚本 | 回归实测 | 状态 |
|---|---|---|---|
| UI 缩放 | [`_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) | `=== 结果: OK ===`（[`out`](tools/_smoke_result_help/_smoke_ui_scale.out:60)） | **保持** 🟢 |
| 市场 | [`_smoke_marketplace.py`](tools/_smoke_marketplace.py) | `OK (FAIL=0 WARN=0)`（[`out`](tools/_smoke_result_help/_smoke_marketplace.out:41)） | **保持** 🟢 |
| 内置脚本模板 | [`_test_script_package.py`](tools/_test_script_package.py) | `0 项 FAIL, 0 项 WARN`；`builtin 脚本生成路径零回归`（[`out`](tools/_smoke_result_help/_test_script_package.out:144)） | **保持** 🟢 |
| TLS | [`_test_netlink_tls.py`](tools/_test_netlink_tls.py) | `PASS (all assertions OK; warns=0)`（[`out`](tools/_smoke_result_help/_test_netlink_tls.out:36)） | **保持** 🟢 |
| 沙箱 | [`_test_python_sandbox.py`](tools/_test_python_sandbox.py) | `SUMMARY: 0 FAIL, 1 WARN`（v5 已知限制）（[`out`](tools/_smoke_result_help/_test_python_sandbox.out:101)） | **保持（含已知 WARN）** 🟢 |
| 几何/最大化/紧凑/越界 | [`_smoke_main_geometry.py`](tools/_smoke_main_geometry.py) | `FAIL=0 WARN=0`（[`out`](tools/_smoke_result_help/_smoke_main_geometry.out:22)） | **保持** 🟢 |
| 标题可编辑 | [`_smoke_editable_title.py`](tools/_smoke_editable_title.py) | `通过 16/16, 失败 0`（[`out`](tools/_smoke_result_help/_smoke_editable_title.out:35)） | **保持** 🟢 |
| 版本一致性 | [`_test_version_unify.py`](tools/_test_version_unify.py) | `通过 16 / 失败 0 / 警告 0`（[`out`](tools/_smoke_result_help/_test_version_unify.out:23)） | **保持** 🟢 |

---

## 4. 新引入 FAIL / 回归

**无。** 全量 **45 脚本退出码均为 0**；`tools\_smoke_result_help\` 内**无任何 `[FAIL]`** 行。

- 唯一 `[WARN]`：[`_test_python_sandbox.py`](tools/_test_python_sandbox.py) v5「C 层阻塞 `time.sleep` 无法按时中断（elapsed=4.01s）」——为本轮之前即存在的**已知设计限制**（BUG-04 收尾时如实标注），**非本轮改动引入**。
- [`_smoke_launch_app.out`](tools/_smoke_result_help/_smoke_launch_app.out:1) 顶部存在一条 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb3 in position 0: invalid start byte`：
  - 位置：校验脚本自身的 `Thread-1`（`subprocess._readerthread`），非被测应用线程；
  - 性质：读子进程输出时的编码伪影（子进程输出非 UTF-8 字节）；
  - 回归性：与 v2 基线 [`tools\_smoke_result_regression\_smoke_launch_app.out:1`](tools/_smoke_result_regression/_smoke_launch_app.out:1)、v3 基线[`tools\_smoke_result_regression2\_smoke_launch_app.out:1`](tools/_smoke_result_regression2/_smoke_launch_app.out:1) **逐行一致** → **既有伪影，非本轮引入**；
  - 影响：脚本最终判定 `存活至阈值(>=10s): True`、`子进程输出含 Traceback: False`、`=== 结果: OK (rc=0) ===` → **不影响 PASS 判定，非产品问题**。

### 4.1 跨模块回归风险评估（针对本轮改动）

| 本轮改动 | 关联回归脚本 | 观察点 | 结果 |
|---|---|---|---|
| [`src/ACRPA.py`](src/ACRPA.py) 主题切换追加 help refresh（[`src/ACRPA.py:2037`](src/ACRPA.py:2037) → [`src/help_window.py:989`](src/help_window.py:989) `refresh_theme`，try/except 包裹） | [`_debug_theme_trigger.py`](tools/_debug_theme_trigger.py)、[`_debug_dark_theme.py`](tools/_debug_dark_theme.py)、[`_smoke_dark_mode.py`](tools/_smoke_dark_mode.py)、[`_smoke_help_window.py`](tools/_smoke_help_window.py) A6 | 主题往返刷新次数不增加（`T4 toggle_dark() refresh 增量 got 1 / expected 1`）；暗黑往返 57 项全过；帮助窗口主题跟随 `[OK]` | **无回归** 🟢 |
| [`src/settings_window.py`](src/settings_window.py) 新增「帮助窗口使用模态」复选框（[`src/settings_window.py:1442`](src/settings_window.py:1442)） | [`_smoke_dark_mode.py`](tools/_smoke_dark_mode.py)、[`_smoke_marketplace.py`](tools/_smoke_marketplace.py) | 设置窗口结构/主题化静态断言全过 | **无回归** 🟢 |
| [`src/state.py`](src/state.py) 新增 `help_geometry`/`help_maximized`/`help_modal`（[`src/state.py:157`](src/state.py:157)） | [`_debug_functional.py`](tools/_debug_functional.py)、[`_smoke_help_window.py`](tools/_smoke_help_window.py) A8 | ScriptData/配置读写全过；帮助几何记忆恢复/写回/越界回退全 `[OK]` | **无回归** 🟢 |
| [`src/dialogs.py`](src/dialogs.py) `show_help_dialog` 委托化（[`src/dialogs.py:104`](src/dialogs.py:104)） | [`_smoke_help_window.py`](tools/_smoke_help_window.py) A11、[`_smoke_launch_app.py`](tools/_smoke_launch_app.py) | 委托成功且 legacy fallback 保留；应用启动存活 `rc=0` | **无回归** 🟢 |
| 新增 `src/help_content.py` / `src/help_window.py` | [`_debug_check.py`](tools/_debug_check.py) | `checked 114 files, 0 failed`、`18/18 modules imported` | **无回归** 🟢 |
| [`ACRPA.spec`](ACRPA.spec) datas 追加 `res/help` 与 docs | [`_smoke_launch_app.py`](tools/_smoke_launch_app.py) | 源码模式启动存活、无导入/构建期异常 | **无回归** 🟢 |

---

## 5. 本轮帮助窗口重构各阶段最终状态

| 阶段 | 范围 | 代表脚本 | 最终状态 |
|---|---|---|---|
| 阶段0（内容层） | `res/help/*` + [`src/help_content.py`](src/help_content.py) + [`tools/_test_help_consistency.py`](tools/_test_help_consistency.py) | H1 | 🟢 已闭环（27 项 `[OK]`，`68/68`，`结论: PASS`） |
| 阶段1-2（渲染层+委托化） | [`src/help_window.py`](src/help_window.py) + [`src/dialogs.py`](src/dialogs.py) + [`tools/_smoke_help_window.py`](tools/_smoke_help_window.py) | H2 | 🟢 已闭环（A1–A11 全 `[OK]`，legacy fallback 保留） |
| 阶段4（收尾） | `使用说明.txt`（命令区对齐+横幅）+ [`ACRPA.spec`](ACRPA.spec) datas + [`src/settings_window.py`](src/settings_window.py) 模态开关 + [`src/ACRPA.py`](src/ACRPA.py) 主题追加 refresh | H3 + E10 + C4 | 🟢 已闭环（`使用说明.txt --check 68/68`；主题刷新无副作用；内置模板零回归） |

> 结论（v4）：帮助窗口重构（阶段0/1-2/4）全部改动后，**全量 45 脚本全 PASS（退出码 0，0 FAIL）**；脚本级 SKIP=0，子用例 SKIP=10（浏览器真实分支 9 + 市场真实 PR 1），含 WARN 的脚本仅 1（沙箱 v5 已知限制，非本轮引入）。**帮助窗口可开 / 13 章 / 命令 68==68 / 搜索 / 主题实时 / 缩放 / 几何记忆 / 向后兼容**八项均转绿，`使用说明.txt --check` 命中 `68/68`；**UI 缩放、市场、内置模板、TLS、沙箱、几何、标题可编辑、版本一致性**等既有项全部保持转绿。**未发现任何跨模块回归**。`src/` 业务源码与测试脚本全程未被本子任务改动。
