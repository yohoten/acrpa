# UI 性能与线程优化 — 验收清单（v5 配套）

> 配套方案：[`UI性能与线程优化方案-v5.md`](UI性能与线程优化方案-v5.md)
> 基线测量日期：2026-11-02 ｜ 环境：Windows 10/11 + Python(Tk 8.6) ｜ Shell：cmd.exe

---

## 0. 执行摘要（实测数字）

| 指标 | 改造前 | 改造后 | 结论 |
|---|---|---|---|
| 主窗空闲唤醒 | **38.17 次/秒** | **12.33 次/秒** | 达标（目标 ≤18） |
| 1920×1080 单次缩放 | LANCZOS **52.50 ms** | BOX **21.30 ms** | 降 41%（自动分级） |
| `load_bg_photo` 首次合成 | **38.27 ms** | **27.91 ms** | 降 27% |
| `load_bg_photo` 缓存命中 | 0.22 ms | **0.25 ms** | 保持命中（≈110×） |
| 日志色条 Tcl 往返/批 | ≈1200 次 | **≤2 次** | 降 3 个数量级 |
| 设置条件断点（5000 行） | ≈58 ms（全量重建） | **≈0.01 ms**（就地） | 降 3 个数量级 |
| 单行编辑（`_update_row_inplace`） | 0.010 ms | 0.010 ms | 基线，未退化 |
| 回归用例 | 21 支 | **29 支全 PASS** | 0 FAIL |

---

## 1. 自动化验收（必跑，全部 `EXIT=0` 才算通过）

### 1.1 语法体检

```bat
chcp 65001 >nul & set "PYTHONUTF8=1"
python -m py_compile src\ui\bg_task.py src\ui\log_dock.py src\core\log.py ^
  src\ui\exec_bar.py src\ui\workflow_view.py src\mini_bar.py ^
  src\ui\netlink\shot.py src\ui\bg_image.py src\utils.py src\ACRPA.py
```

### 1.2 回归子集（**必须加 `-u`**，否则 stdout 块缓冲表现为「卡死」）

```bat
python -u tools\run_tests.py --only "^_test_(log|ui|script|editor|bg_task|theme|settings|import_side_effect|command_schema|param_form|minibar|atomic_save|browser_manage|hotkey_win|cancel|resample|cbar)"
```

期望：`通过 29 / 失败 0 / 超时 0`。

### 1.3 本轮新增的回归用例（7 支，均免显示器）

| 用例 | 覆盖缺陷/能力 |
|---|---|
| `tests/_test_log_level_runs.py` | D1：连续同级日志的级别不许丢（色条 + 过滤） |
| `tests/_test_scroll_bind_recursive.py` | D4：二次绑定后新增子控件必须绑上滚轮，且不重复叠加 |
| `tests/_test_log_trim_zebra.py` | D6：奇数 `excess` 裁剪后斑马纹不反转、无连续同行纹 |
| `tests/_test_bg_task_error.py` | D7/D8：异常必落 traceback 日志；`shutdown` 不污染下一轮 |
| `tests/_test_bg_task_cancel.py` | `CancelToken` 取消后回调不执行；`submit_chunked` 顺序；队满不阻塞主线程 |
| `tests/_test_log_cbar_batch.py` | 每批 `tag_add` ≤2 次；行首仍为 `▌`；导出文本不含 `▌` |
| `tests/_test_img_resample_pick.py` | 重采样分级纯函数（大图 BOX/BILINEAR、小图标 LANCZOS） |

---

## 2. 手工验收（GUI，逐项勾选）

### 2.1 日志面板

- [ ] 连续输出 3 条 ERROR → 三条**均为红色**，左缘色条为 `cbar_error`，无一条被涂成 info 色
- [ ] 级别过滤选 `ERROR` → 3 条 error **全部可见**（修复前只显示第 1 条）
- [ ] 级别过滤选 `INFO` → 上述 3 条 error **不出现**
- [ ] 日志超过 `LOG_MAX_LINES`（默认 6000）后，斑马纹不明显错乱、无连续两行同底色
- [ ] 日志面板行首 `▌` 显示正常；`导出日志` 得到文本**不含** `▌`
- [ ] 空闲 10 秒无日志输入 → CPU 占用应明显低于修复前（唤醒 38→12 次/秒）

### 2.2 编辑器表格

- [ ] 打开 2000+ 行脚本，双击单元格改参数 → 无明显停顿（走 O(1) 就地刷新）
- [ ] 右键「设置条件断点」→ 该行 `[C:...]` **立即出现**，其他行不受影响，无卡顿
- [ ] 右键「移除条件断点」→ 标记立即消失，无卡顿
- [ ] 行首分类色条（B2）颜色与命令类型一致，选中态高亮正确
- [ ] 加行 / 删除 / 上移下移 / 撤销重做后，行号、断点 `●`、条件 `[C:]`、失败角标**全部正确**
- [ ] 表格全选、查找、复制到剪贴板、导出后内容与界面一致

### 2.3 多线程与关闭

- [ ] 打开大脚本（xlsx/acrpas）期间界面**不冻结**，加载完成后表格正确
- [ ] 加载期间按 Ctrl+S → 提示「脚本正在加载，请稍候再保存」，**原文件行数不变**
- [ ] 新建空脚本后「另存为」→ **允许**保存空脚本
- [ ] 编辑热字串 `script` 类型并触发 → 执行的是**该热字串绑定的脚本**（修复前会跑上一个脚本或完全不执行）
- [ ] 后台任务抛异常时，日志出现带 `traceback` 的 ERROR 行（不再无声失败）
- [ ] 关闭主窗口时若后台任务在飞 → **1.5 秒内退出**，不卡死

### 2.4 截图与图片

- [ ] 截图工具：点「区域截图」→ 界面先隐藏、遮罩出现、**不出现 0.3 秒硬冻结感**
- [ ] 截图保存到磁盘后，图片内容、尺寸与框选一致
- [ ] 工作流「导出流程图」不阻塞界面
- [ ] 远端截图（NetLink）连续接收多帧 → 内存不持续增长（单张 `PhotoImage` 复用）
- [ ] Mini Bar 折叠/展开反复切换 → 图标清晰、切换无卡顿
- [ ] 切换主题（深/浅）→ 待机背景约 28 ms 内重合成，无白屏闪烁

### 2.5 跨屏 DPI

- [ ] 主窗在 100% 屏与 150% 屏之间拖动 → 字体/行高/间距**自动重算**（修复前需重启）
- [ ] 拖动窗口缩放时**无布局抖动、无自激闪烁**

### 2.6 浏览器持久化

- [ ] 设置里勾选「持久化登录态」且 `user_data_dir` **留空** → 浏览器正常启动（修复前 `FileNotFoundError`）
- [ ] 默认落点目录为 `<DATA_ROOT>/browser_profile`（已被 `.gitignore` 忽略）

---

## 3. 回退开关（默认全开；置 `0/false/off` 即回退）

| 开关（环境变量） | 默认 | 关闭后行为 |
|---|---|---|
| `BG_TASK_V2` | 开 | 关闭双队列/优先级/取消/分块，回到单队列 `submit` |
| `USE_TASK_POOLS` | 开 | 回到「每任务一 daemon 线程」，无并发上限（IO=4 / COMPUTE=2） |
| `BG_WAKEUP_BACKOFF` | 开 | 哨兵回固定 `poll_ms` 轮询，无 60/250/500 ms 退避 |
| `LOG_FLUSH_IDLE_SKIP` | 开 | 日志队列空时仍每轮无条件 `flush`（旧行为，4 次/秒空转） |

> 模块内还有 `_TREE_AUTO_COLS`（大脚本收窄可见列）、`_DPICHECK_ON`（跨屏 DPI 钩子）等代码级开关，见对应函数注释。

---

## 4. 已知未完成项（不在本轮范围）

| 项 | 归属 | 说明 |
|---|---|---|
| 表格虚拟化 / 窗口化渲染 | **P3（不做）** | 58 ms 中 88% 来自「一次要插多少行」，非 Tk 渲染能力；虚拟化要动 19 处 `tree.index()` + 16 处 `get_children()`，并破坏「所有行都是真实 item」契约 |
| 参数列 → Inspector 分流 | P2 | 只能把 58 → ≈28 ms，收益低于已落地的就地刷新路径 |
| 事件总线替换 100/250 ms 心跳 | P2 | 本轮只做「队列空短路 + 三分组」，未整体改事件总线 |
| Playwright `SessionThread` | P2 | sync API 有线程亲和性，本轮只做「不跨线程建/用/关」的约束 |
| 全量 `tools/run_tests.py --safe` | — | >6 分钟且 stdout 块缓冲，验证请用 `-u` + `--only <regex>` 跑子集 |

---

## 5. 已知工具缺陷（**勿误判为业务回归**）

- `tools/static_checks.py` 的打包门禁 **96 条 FAIL** 是工具缺陷：`tools/_check_spec_excludes.py:32` 硬编码 `ACRPA.spec`，而仓库内实际文件名为 `ACRPA v0.1.30-beta.spec`。
- 新增的 `L0-3` 图片/截图 API 门禁**只报 WARN 不计入退出码**，当前 10 处告警均为已缓存或已在后台池的路径。
