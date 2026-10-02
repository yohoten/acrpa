# ACRPA UI 冒烟测试报告（阶段2）

> 范围：UI 冒烟测试 + `run.py` 应用启动存活验证 + UI 显示/体验结构化验证。
> 本阶段**不修改任何 `src/` 源码或业务文件**；仅新建报告 / 截图 / 辅助脚本。
> 仅执行指定清单内脚本；未运行 `_debug_mini_bar_geometry.py`、`_test_netlink_screenshot.py`、`publish_release.py`、`make_release.py`。

---

## 1. 环境信息

| 项目 | 值 |
| --- | --- |
| 工作目录 | `d:\CodingEmber\ACRPA` |
| 解释器 | `.venv\Scripts\python.exe` |
| Python 版本 | **3.9.13** |
| 当前 Shell | `C:\WINDOWS\system32\cmd.exe` |
| 运行方式 | `.venv\Scripts\python.exe -X utf8 <脚本>`（结果重定向落盘） |
| 应用入口 | `run.py` → `from ACRPA import root; root.mainloop()`（GUI 框架 tkinter） |
| 屏幕 | 2560 x 1600（虚拟屏 0,0 2560x1600） |
| tk scaling | 1.6662566625666255 |
| dpi_factor() | 1.2496924969249692（≈125% DPI） |
| state.UI_SCALE | 1.0 |
| 结果目录 | `tools\_smoke_result_phase2\` |

---

## 2. UI 冒烟脚本结果总表

| # | 脚本 | 退出码 | 判定 | 摘要 |
| --- | --- | --- | --- | --- |
| 1 | `tools\_smoke_mini_bar.py` | 0 | **PASS** | 16 项静态 AST / 依赖断言全 OK（mini_bar_height/pos、_mb_animate_width、after_cancel、NetLink 集成块等） |
| 2 | `tools\_smoke_mini_bar.py --launch` | 0 | **PASS** | 同 1，附加「启动存活冒烟：run.py 存活 10s（无导入/构建期异常）」OK |
| 3 | `tools\_smoke_ui_scale.py` | 1 | **FAIL** | A/B/B2/D/E/F 静态断言全 OK；**C 动态断言异常**：`TypeError("nametofont() got an unexpected keyword argument 'root'")` |
| 4 | `tools\_smoke_marketplace.py` | 1 | **FAIL** | 动态部分全 OK（窗口创建/四态/主题/账号区/安装流程）；**静态断言 FAIL**：`工具栏「市场」按钮绑定被改动`（脚本自报 `FAIL=3`） |
| 5 | `tools\_smoke_netlink_window.py` | 0 | **PASS** | 窗口 open/close 幂等 3 轮 + 网页面板 8 项 全 OK |
| 6 | `tools\_smoke_netlink_pairing_ui.py` | 0 | **PASS** | 配对/权限 UI 集成 全 OK（PIN、AUTH、权限切换、设备区刷新，elapsed 8.05s, warns=0） |
| 7 | `tools\_smoke_netlink_control_ui.py` | 0 | **PASS** | 远程操控 UI 集成 全 OK（按钮态、远程运行、暂停/恢复/停止、审计，总耗时 8.8s） |
| 8 | `tools\_smoke_netlink_transfer_ui.py` | 0 | **PASS** | 脚本分发 UI 集成 全 OK（推脚本 sha256、进度、远端脚本、失败态，总耗时 5.4s） |
| 9 | `tools\_debug_dark_theme.py` | 0 | **PASS** | 暗黑模式往返切换运行时验证 全 OK（共 57 项检查） |
| 10 | `tools\_debug_theme_trigger.py` | 0 | **PASS** | save_config→_refresh_theme 触发收窄实测 全 OK（T1~T4 增量符合预期） |

**统计：PASS = 8，FAIL = 2，SKIP = 0。**

原始输出：`tools\_smoke_result_phase2\_smoke_*.out` / `_debug_*.out`。

---

## 3. 应用启动存活验证

辅助脚本：`tools\_smoke_launch_app.py`（仅新建，未改源码）。
输出：`tools\_smoke_result_phase2\_smoke_launch_app.out`（子进程日志 `_smoke_launch_app_child.log`）。

- 启动方式：以项目根为 cwd，`subprocess.Popen([.venv\Scripts\python.exe, -X, utf8, run.py])`，stdout/stderr 合并落盘。
- 轮询：PID=13576；`t=2/4/6/8s alive=True`，`t=10.2s 仍存活（≥10s 阈值）`，`t=15.3s 达到最长轮询仍存活`。
- 清理：`taskkill /F /T /PID 13576` → rc=0；终止后 `poll()=1`，无残留。
- 子进程 stdout/stderr：**空**（无异常输出）。
- Traceback 检测：**无**。

> **结论：启动成功（GUI 已建窗）**。进程在 15s 内持续存活且无致命 Traceback，符合 tkinter `mainloop()` 阻塞的预期。

---

## 4. UI 显示/体验验证数据

辅助脚本：`tools\_smoke_ui_introspect.py`（仅新建，未改源码）。
输出：`tools\_smoke_result_phase2\_smoke_ui_introspect.out`。

### 4.1 主窗口几何

| 指标 | 值 |
| --- | --- |
| title | `A/C RPA` |
| geometry | `1000x680+780+306` |
| 实际 size | **1000 x 680** |
| 内容所需 req | **897 x 703** |
| req − actual | reqw−w = **−103**；reqh−h = **+23** |
| state | `normal` |
| minsize | `640 x 520` |
| compact_mode | `False` |

> ⚠️ **内容纵向超出窗口高度 23px**（req 703 > 680）——疑似底部 23px 被裁剪 / 需滚动。宽度充足（req 897 < 1000）。

### 4.2 缩放 / DPI

| 指标 | 值 |
| --- | --- |
| tk scaling | 1.6662566625666255 |
| dpi_factor() | 1.2496924969249692 |
| utils.current_ui_scale() | 1.0 |
| state.UI_SCALE | 1.0 |

### 4.3 字体（`utils._FONT_SPECS` 角色 → 实测）

| 角色 | 实测 family | size | weight | 结果 |
| --- | --- | --- | --- | --- |
| ACRPA_TITLE | Microsoft YaHei UI | 10 | bold | OK |
| ACRPA_BODY | Microsoft YaHei UI | 9 | normal | OK |
| ACRPA_SMALL | Microsoft YaHei UI | 8 | normal | OK |
| ACRPA_SMALL_BOLD | Microsoft YaHei UI | 8 | bold | OK |
| ACRPA_TINY | Microsoft YaHei UI | 7 | normal | OK |
| ACRPA_BUTTON | Microsoft YaHei UI | 9 | bold | OK |
| ACRPA_LOG | Consolas | 9 | normal | OK |
| ACRPA_ICON | Segoe UI Symbol | 9 | normal | OK |
| ACRPA_ICON_MD | Segoe UI Symbol | 11 | normal | OK |
| ACRPA_ICON_LG | Segoe UI Symbol | 12 | normal | OK |

所有 10 个命名字体均成功解析，family/size/weight 与基准表一致。

### 4.4 顶层窗口 / 标签页清单

- **顶层窗口（Toplevel）**：启动时 **无**（均按需打开，符合设计）。
- **Notebook #0 标签页（3 个）**：`脚本编辑`、`执行控制`、`工作流`。
  （「设置」已按设计分离为独立窗口，不占 Tab。）
- **关键按需窗口打开入口存在性**：
  - `netlink_window.open_netlink_window` → 存在
  - `market_window.open_market_window` → 存在
  - `settings_window.open_settings_window` → 存在

### 4.5 控件几何溢出扫描（req > actual）

扫描控件数 = 169，命中溢出（可见且 req>actual）= **2**：

| # | 类 | actual | req | over | text |
| --- | --- | --- | --- | --- | --- |
| 1 | `Tk`（主窗口） | 1000x680 | 897x703 | (−103, **+23**) |  |
| 2 | `TNotebook` | 984x594 | 881x617 | (−103, **+23**) |  |

> 结论：唯一溢出集中在**主窗口 / Notebook 的纵向 +23px**，与 4.1 一致；无其它子控件被裁剪。

### 4.6 屏幕 / DPI / 越界风险

- screen = 2560 x 1600；vroot = (0,0 2560x1600)。
- 主窗口右下角 = (1791, 1027)，均在屏幕内 → **无越界风险**。

---

## 5. 截图证据

- 路径：`tools\_ui_screenshots\app_main.png`
- 像素尺寸：**1000 x 680**
- 采集方式：PIL `ImageGrab.grab(bbox=(791,347,1791,1027))`（主窗口 bbox）
- 结果：**OK**（非 SKIP）。

---

## 6. UI / 体验问题清单

> 供后续修复阶段使用；每条含 现象 / 证据 / 疑似关联模块。

### [UI-01] 主窗口内容纵向超出窗口高度 23px（内容疑似被裁剪）
- **现象**：默认启动几何 1000x680，但主窗口内容 reqheight=703、Notebook reqheight=617，均比实际高 23px（宽度充足）。
- **证据**：`_smoke_ui_introspect.out` → `[MAIN] geometry='1000x680+780+306' size=1000x680 req=897x703`、`[OVERFLOW] #1/#2 over=(−103,+23)`。
- **疑似关联模块**：`src/ACRPA.py`（`_apply_main_geometry`：默认 `1000x680` 居中，仅当 `ui_scale != 1.0` 时才按 `utils.scaled()` 放大）、`src/utils.py`（字体 pt / `dpi_factor`）。

### [UI-02] 主窗口几何未纳入 DPI 因子（125% 缩放下不自适应）
- **现象**：`dpi_factor()=1.2497`，但主窗口仍按字面 `1000x680` 应用；内容因 pt 字体放大而变高，导致 UI-01 溢出。
- **证据**：`_smoke_ui_introspect.out` → `[MAIN] tk_scaling=1.666 dpi_factor=1.2497 ui_scale=1.0`、`minsize=(640,520)`；`src/ACRPA.py:1449` 仅在 `current_ui_scale()!=1.0` 时缩放几何。
- **疑似关联模块**：`src/ACRPA.py`（`_apply_main_geometry` / `_main_center_geometry`）、`src/utils.py`（`scaled()`/`dpi_factor()`）。

### [UI-03] Python 3.9 下 `tkinter.font.nametofont(root=...)` 不兼容，字体改配路径静默失效
- **现象**：`_smoke_ui_scale.py` 动态断言抛 `TypeError("nametofont() got an unexpected keyword argument 'root'")` → 脚本 FAIL(1)。同一调用形式存在于产品源码，被 `except` 静默吞掉。
- **证据**：`_smoke_ui_scale.out` 第 31 行；`tools\_smoke_ui_scale.py:287`（测试侧）与 `src/utils.py:257`（`init_fonts`）、`src/utils.py:291`（`set_ui_scale`）均以 `root=` 关键字调用 `nametofont`。此外 `_smoke_ui_scale.out` 第 14 行确认常量 默认1.0/下限0.8/上限1.5 均 OK，说明问题只在动态改配路径。
- **影响（待后续核实）**：
  - `init_fonts()` 重复调用（`src/ACRPA.py:1398`、`1403`）时无法走"重配"分支，退化为"新建"分支并在第二次起被 TclError 吞掉 → 改 `tk scaling` 后的字体刷新实际未生效（本次实测字号仍正确，疑因命名字体按 pt 渲染自动跟随 scaling，故未暴露视觉差异）。
  - `set_ui_scale()` 的字体改配被 `except: pass` 吞掉 → **在 Python 3.9 下切换"界面缩放档位"时字体可能不随之变化**。
- **疑似关联模块**：`src/utils.py`（`init_fonts`、`set_ui_scale`）。
- **备注**：本阶段禁改源码，仅记录，供修复阶段判定。

### [UI-04] 脚本市场静态断言失败：工具栏「市场」按钮绑定被改动
- **现象**：`_smoke_marketplace.py` 退出码 1；静态断言 `工具栏「市场」按钮绑定被改动` FAIL（脚本汇总自报 `FAIL=3`，输出中可见 1 条 [FAIL] 于此项）。
- **证据**：`_smoke_marketplace.out` 第 16 行 `[FAIL] 工具栏「市场」按钮绑定被改动`、第 40 行 `=== 结果: FAIL (FAIL=3 WARN=0) ===`。
- **对照**：动态部分全部 OK（`open_market_window` 建窗、四态→list/empty/error、主题切换底色、ui_scale 1.0/1.25/1.5、账号区、安装导入 89 行、关闭窗口 等）。
- **疑似关联模块**：`src/ACRPA.py`（工具栏「市场」按钮绑定处）、`src/market_window.py`。
- **备注**：需修复阶段比对"期望绑定 vs 实际绑定"的具体差异。

---

## 7. 未执行 / 跳过项及原因

| 项目 | 状态 | 原因 |
| --- | --- | --- |
| `tools\_debug_mini_bar_geometry.py` | 未执行 | 边界要求禁止（会写 F 盘硬编码截图） |
| `tools\_test_netlink_screenshot.py` | 未执行 | 边界要求禁止（真实截屏） |
| `tools\publish_release.py` / `tools\make_release.py` | 未执行 | 边界要求禁止（联网发版） |
| UI 截图 | **已执行** | `ImageGrab` 成功，非 SKIP |
| 启动 Toplevel 窗口扫描 | 无对象 | 启动时无独立 Toplevel（按需打开），已改为校验打开入口存在性 |

---

## 8. 产出物清单

| 文件 | 说明 |
| --- | --- |
| `tools\_smoke_report_phase2.md` | 本报告 |
| `tools\_smoke_result_phase2\_smoke_mini_bar.out` | 脚本 1 输出 |
| `tools\_smoke_result_phase2\_smoke_mini_bar_launch.out` | 脚本 2 输出 |
| `tools\_smoke_result_phase2\_smoke_ui_scale.out` | 脚本 3 输出（FAIL） |
| `tools\_smoke_result_phase2\_smoke_marketplace.out` | 脚本 4 输出（FAIL） |
| `tools\_smoke_result_phase2\_smoke_netlink_window.out` | 脚本 5 输出 |
| `tools\_smoke_result_phase2\_smoke_netlink_pairing_ui.out` | 脚本 6 输出 |
| `tools\_smoke_result_phase2\_smoke_netlink_control_ui.out` | 脚本 7 输出 |
| `tools\_smoke_result_phase2\_smoke_netlink_transfer_ui.out` | 脚本 8 输出 |
| `tools\_smoke_result_phase2\_debug_dark_theme.out` | 脚本 9 输出 |
| `tools\_smoke_result_phase2\_debug_theme_trigger.out` | 脚本 10 输出 |
| `tools\_smoke_result_phase2\_smoke_launch_app.out` | 启动存活验证报告 |
| `tools\_smoke_result_phase2\_smoke_launch_app_child.log` | 子进程日志（空） |
| `tools\_smoke_result_phase2\_smoke_ui_introspect.out` | UI 内省数据 |
| `tools\_ui_screenshots\app_main.png` | 主窗口截图 1000x680 |
| `tools\_smoke_launch_app.py` | 新建辅助脚本（启动存活验证） |
| `tools\_smoke_ui_introspect.py` | 新建辅助脚本（UI 内省 + 截图） |
