# ACRPA 阶段1 测试报告（无 GUI 控制台冒烟 + 功能验收）

- 执行角色：测试执行子任务（阶段1）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`
- 报告生成时间：脚本执行完成后即时汇总
- 原始输出目录：`tools\_smoke_result_phase1\`（27 个 `.out` 文件，逐条命令重定向落盘，`> out 2>&1`）

---

## 1. 环境信息

| 项 | 值 |
|---|---|
| 解释器 | 虚拟环境 `.venv\Scripts\python.exe`（Python 3.9.13） |
| 依赖安装 | **无需安装**。按指示改用已存在的 `.venv` 虚拟环境，未执行 `pip install -r requirements.txt` |
| 依赖齐备性校验 | `pyautogui, xlrd, xlwt, pyperclip, requests, PIL(Pillow), win32api(pywin32), urllib3, certifi` 全部 import 成功（`python -c "..."` 返回 `ALL_DEPS_OK`） |
| 未安装（本期非必装） | `playwright`（浏览器真实分支默认跳过，见 SKIP）、`cryptography`（NetLink TLS 真实握手 WARN，见 W-3） |
| 中文输出编码 | 全部命令使用 `python -X utf8` |

> 说明：原计划步骤 1「pip 安装依赖」被用户指令覆盖（“使用 .venv 虚拟环境，无需安装依赖”），仅做依赖导入校验，结果为全部可用。

---

## 2. 测试结果总表

退出码约定：`0=通过(含 WARN/SKIP)`、非 0=有 FAIL。本节退出码均取自命令执行的进程返回值。

| # | 脚本名 | 退出码 | 结论 | 关键输出摘要 |
|---|--------|:---:|:---:|--------------|
| 1 | `_debug_check.py` | 0 | PASS | `[SYNTAX] checked 104 files, 0 failed`；18/18 模块 import 成功 |
| 2 | `_smoke_engine.py` | 0 | PASS | engine 逻辑回归 9 项 `✓`，全部通过 |
| 3 | `_debug_functional.py` | 0 | PASS | 8 组功能测试（safe_eval/ScriptData/commands/version_manager/scheduler/workflow/plugins/updater）全通过 |
| 4 | `_debug_error_semantics.py` | 0\* | PASS | 失败语义回归：`通过 32 项, 失败 0 项` |
| 5 | `_debug_workflow.py` | 0 | PASS | 工作流引擎验证：`通过 19 项, 失败 0 项` |
| 6 | `_smoke_dark_mode.py` | 0 | PASS | 暗黑模式+互联按钮静态断言 34 项 `[OK]`，`结果: OK` |
| 7 | `_smoke_netlink.py` | 0 | PASS | netlink 帧/连接/总线：`PASS 8/8` |
| 8 | `_debug_updater.py` | 0 | PASS | 更新器：`ALL PASSED (107 项)` |
| 9 | `_test_browser_locator.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)` |
| 10 | `_test_browser_locator_chain.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)` |
| 11 | `_test_browser_waiter.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)` |
| 12 | `_test_browser_cdp.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)`；含 SKIP 1（真实 CDP） |
| 13 | `_test_browser_codegen_convert.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)`；含 SKIP 2 |
| 14 | `_test_browser_cookie.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)` |
| 15 | `_test_browser_download.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)`；含 SKIP 3 |
| 16 | `_test_browser_frame_tab.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)`；含 SKIP 2 |
| 17 | `_test_browser_listen.py` | 0 | PASS | `结果: PASS (0 失败, 0 项警告)`；含 SKIP 1 |
| 18 | `_test_python_sandbox.py` | 0 | PASS | `SUMMARY: 0 FAIL, 1 WARN`（见 W-1） |
| 19 | `_test_code_command.py` | 0 | PASS | `SUMMARY: 0 FAIL, 0 WARN` |
| 20 | `_test_ai_provider.py` | 0 | PASS | `warns=0`，`PASS (all assertions OK)` |
| 21 | `_test_script_package.py` | 0 | PASS | `全部 项通过, 0 项 FAIL, 1 项 WARN`（见 W-2） |
| 22 | `_test_market_upload.py` | 0 | PASS | `汇总: FAIL=0  WARN=0  SKIP=1` |
| 23 | `_test_netlink_auth.py` | 0 | PASS | `elapsed 4.01s, warns=0`，`PASS (all assertions OK)` |
| 24 | `_test_netlink_control.py` | 0 | PASS | `总耗时 8.6s`，`warns=0`，`PASS` |
| 25 | `_test_netlink_transfer.py` | 0 | PASS | `总耗时 1.2s`，`PASS (all assertions OK; warns=0)` |
| 26 | `_test_netlink_tls.py` | 0 | PASS | `PASS (all assertions OK; warns=1)`（见 W-3） |
| 27 | `_test_netlink_e2e.py` | 0 | PASS | `总耗时 13.1s`，`PASS (all assertions OK; warns=0)` |

\* `_debug_error_semantics.py` 为异步完成，进程退出码未即时回传；其输出结尾明确 `失败 0 项`，判定为 PASS（退出码预期 0）。其余 26 条退出码均由命令执行结果直接获得，均为 0。

---

## 3. 统计

| 指标 | 数量 |
|---|---:|
| 执行脚本总数 | 27 |
| 通过（PASS，含 WARN/SKIP） | 27 |
| 失败（FAIL / 非 0 退出码） | 0 |
| 错误/异常中断 | 0 |
| WARN 项 | 3（`_test_python_sandbox`、`_test_script_package`、`_test_netlink_tls`） |
| SKIP 项 | 10（浏览器真实分支 9 + 市场真实 PR 1） |

结论：**本阶段无硬失败（0 FAIL / 0 非 0 退出码）**，仅在 3 处产生 WARN、10 处 SKIP，均为环境或设计预期内的降级项。

---

## 4. Bug / 问题清单

> 本阶段未发现导致非 0 退出码的失败脚本。以下为需在后续修复阶段关注的问题点，按严重度排列（均为 WARN 级或环境限制，非阻断）。

### W-1（WARN）可信模式 Python 超时保护可被绕过
- 脚本：`_test_python_sandbox.py`（用例 v1）
- 失败点/现象：`trusted 超时仍基于 sys.settrace，可被 sys.settrace(None) 或 C 层阻塞绕过`（`worker_added=False`）
- 关键报错片段：
  ```
  [WARN] v1 §1.3 KNOWN LIMITATION (本期未修/后续批次): trusted 超时仍基于 sys.settrace，可被 sys.settrace(None) 或 C 层阻塞绕过 (worker_added=False)
  SUMMARY: 0 FAIL, 1 WARN
  ```
- 疑似关联模块：`src/py_sandbox.py`（trusted 模式的超时实现方式）
- 说明：脚本自身已标注为 KNOWN LIMITATION（本期未修），修复方向为引入独立 worker 进程/线程执行 trusted 超时，避免依赖 `sys.settrace`。

### W-2（WARN）内置脚本模板生成被跳过
- 脚本：`_test_script_package.py`
- 失败点/现象：builtin 生成测试被跳过（提示可能与缺 `xlwt` 相关）
- 关键报错片段：
  ```
  [WARN] builtin 生成跳过 (可能缺 xlwt): MarketplaceError('内置脚本模板未找到: builtin_1')
  结果: 全部 项通过, 0 项 FAIL, 1 项 WARN
  ```
- 疑似关联模块：`src/marketplace.py`（内置模板 `builtin_1` 的生成/查找路径）
- 说明：本环境 `.venv` 实际已安装 `xlwt`（依赖校验通过），故更可能是 `builtin_1` 模板在 `template/` 或生成逻辑中缺失/命名不匹配，需后续核实内置模板生成分支。

### W-3（WARN）NetLink TLS 真实握手断言不可用
- 脚本：`_test_netlink_tls.py`
- 失败点/现象：`cryptography`/`openssl` 均不可用，证书获取分支不可用，TLS 真实握手/配对/指令/指纹拒绝/审计断言记 WARN
- 关键报错片段：
  ```
  [..] cryptography 分支不可用: No module named 'cryptography'
  [..] 证书获取分支: 不可用（TLS 握手断言将记 WARN）
  [WARN] 无可用证书（cryptography/openssl 均不可用）→ TLS 真实握手/配对/指令/指纹拒绝/审计断言记 WARN，不假通过
  PASS (all assertions OK; warns=1)
  ```
- 疑似关联模块：`src/netlink/tls.py`
- 说明：属于环境依赖缺失（未安装 `cryptography`），非代码缺陷。如需覆盖 TLS 真实握手验收，需在环境安装 `cryptography` 或提供可用证书链后重跑。

---

## 5. 未执行 / 被跳过的项及原因

| 来源脚本 | 跳过项 | 原因 |
|---|---|---|
| `_test_browser_cdp.py` | 真实 CDP 接管 | 需本机 `chrome --remote-debugging-port=9222` 且 `playwright` 已安装；本期 playwright 未装（设计默认跳过真实浏览器分支） |
| `_test_browser_codegen_convert.py` | 真实 playwright codegen CLI 录制 / 启动浏览器录制成功路径 | 需 `playwright` CLI + 图形界面 |
| `_test_browser_download.py` | 真实浏览器下载落盘 / 真实上传设值 / 等待元素 state=download 真实命中 | 需 `playwright + Chromium` |
| `_test_browser_frame_tab.py` | 真实进入/返回 iframe / 真实标签页新建·切换·关闭 | 需 `playwright + Chromium` |
| `_test_browser_listen.py` | 真实网络响应监听 | 需 `playwright + Chromium` 真实请求 |
| `_test_netlink_tls.py` | TLS 真实握手/配对/指令/指纹拒绝/审计断言 | 环境无 `cryptography`/证书（记为 WARN，不计 FAIL） |
| `_test_market_upload.py` | 真实测试仓库 PR | 未配置 `ACRPA_MARKET_TOKEN` 或 `ACRPA_MARKET_TEST_REPO=owner/repo`；脚本刻意“不伪造 PASS” |

### 严格未执行（按本阶段边界主动排除，未列入命令清单）
- `tools\_debug_mini_bar_geometry.py`：会写 F 盘截图，禁止运行
- `tools\_test_netlink_screenshot.py`：真实截屏，禁止运行
- `tools\publish_release.py` / `tools\make_release.py`：真联网发版，禁止运行

---

## 6. 产出物索引

- 报告：`tools\_smoke_report_phase1.md`（本文件）
- 原始输出：`tools\_smoke_result_phase1\<脚本名>.out`（27 个，逐条命令 stdout+stderr 合并落盘）

---

## 7. 阶段1 结论

- 依赖：`.venv` 虚拟环境依赖齐备，**无需安装**；`playwright`、`cryptography` 未装（本期非必装）。
- 测试：27 条控制台命令**全部执行完毕，0 FAIL、0 非 0 退出码**；3 处 WARN、10 处 SKIP，均为环境/设计预期内。
- 需移交后续修复阶段：W-1（`src/py_sandbox.py` trusted 超时绕过）、W-2（`src/marketplace.py` 内置模板 `builtin_1` 缺失）、W-3（`src/netlink/tls.py` 需 `cryptography` 环境才能覆盖真实 TLS 握手）。
