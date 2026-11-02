# _fix_report_geom.md — 主窗口几何公共函数抽取 + 设置窗口越界复用

> 子任务：需求一「验证 + 补强」（不重复实现已完成的几何/最大化/紧凑功能）。
> 范围：仅修改 `src/utils.py`、`src/ACRPA.py`、`src/settings_window.py`，新增 `tools/_smoke_main_geometry.py`。
> 未改动 `state.py` schema（`MAIN_GEOMETRY` / `MAIN_MAXIMIZED` / `COMPACT_MODE` / `WIN_GEOMETRY` 键均已存在）及其它模块。

---

## 1. 改动位置（文件:行）

### 1.1 `src/utils.py`
| 位置 | 内容 |
| --- | --- |
| `src/utils.py:6` | `import queue, time, os, glob, re`（新增 `re`，供越界正则使用） |
| `src/utils.py:776-780` | 新增区块注释「窗口几何工具（主窗口 / 设置窗口复用）」 |
| `src/utils.py:782` | `def win_virtual_bounds(root)` |
| `src/utils.py:794` | `def geometry_in_screen(root, geo)` |
| `src/utils.py:813` | `def center_geometry(root, w, h)` |

三函数由 `src/ACRPA.py` 原局部函数 `_main_win_bounds` / `_main_geometry_in_screen` / `_main_center_geometry` **逐行等价**搬移（含异常回退、`w<200 or h<150` 拒绝、标题栏与 ≥40px 可见判定、按虚拟屏夹取），行为完全一致。

### 1.2 `src/ACRPA.py`
| 位置 | 内容 |
| --- | --- |
| `src/ACRPA.py:1410-1412` | 原三个局部函数删除，替换为指向 utils 公共函数的注释 |
| `src/ACRPA.py:1414` | `def _apply_main_geometry():`（保留，分支语义不变） |
| `src/ACRPA.py:1429` | `if geo and utils.geometry_in_screen(root, geo):`（原 `_main_geometry_in_screen(geo)`） |
| `src/ACRPA.py:1434-1435` | `root.geometry(utils.center_geometry(root, utils.scaled(1000), utils.scaled(680)))`（原 `_main_center_geometry(...)`） |
| `src/ACRPA.py:1443` | `_apply_main_geometry()`（import 时调用，位置不变） |

未改动分支：紧凑模式 `500x625`（ui_scale≠1.0 时按 `utils.scaled` 派生）、`state.MAIN_GEOMETRY` 校验通过才应用、`state.MAIN_MAXIMIZED → root.state("zoomed")`、异常兜底 `960x680`、`root.minsize(640,520)`。

### 1.3 `src/settings_window.py`
| 位置 | 内容 |
| --- | --- |
| `src/settings_window.py:440-455` | 设置窗口几何加载块重写：记忆几何先经越界校验，越界/空回退居中 `680x680` |
| `src/settings_window.py:444` | `saved_geo = getattr(state, "WIN_GEOMETRY", "") or ""` |
| `src/settings_window.py:445` | `if saved_geo and utils.geometry_in_screen(_win, saved_geo):` ← **新增越界校验** |
| `src/settings_window.py:446` | 校验通过：`_win.geometry(saved_geo)` |
| `src/settings_window.py:447-453` | 空/越界：沿用原「相对主窗口居中」算法，尺寸 `680x680`（保持原居中尺寸） |

保存时机不变：`_close()`（`src/settings_window.py:218`）仍写 `state.WIN_GEOMETRY = _win.geometry()` 并 `state.save_config()`。

---

## 2. 抽取的公共函数签名

```python
utils.win_virtual_bounds(root)            -> (vx, vy, vw, vh)   # 虚拟屏；不支持时回退主屏 (0,0,屏宽,屏高)
utils.geometry_in_screen(root, geo)       -> bool               # 校验 "WxH+X+Y" 是否在虚拟屏内（≥40px 可见、w≥200、h≥150）
utils.center_geometry(root, w, h)         -> "WxH+X+Y"          # 居中到虚拟屏（纵向偏上 1/3）+ 按屏夹取上限
```

行为等价性：三函数与 `src/ACRPA.py` 原实现逐语句一致（正则 `^(\d+)x(\d+)([+-]\d+)([+-]\d+)$`、`w<200 or h<150` 拒绝、`(x+w)<(vx+40) or x>(vx+vw-40)`、`(y+40)<vy or y>(vy+vh-40)`、`min(w, max(200, vw-40))`、`x=vx+(vw-w)//2`、`y=vy+(vh-h)//3`）。

---

## 3. 设置窗口复用点

- 复用函数：`utils.geometry_in_screen(_win, saved_geo)`（`src/settings_window.py:445`）。
- 逻辑：非空 `state.WIN_GEOMETRY` 且校验通过 → 原样应用；空或越界 → 回退居中 `680x680`；整体异常 → 兜底 `680x680+450+60`。
- 收益：显示器拔插 / DPI 或缩放变化后，避免设置窗口落到屏幕外无法操作。

---

## 4. 冒烟断言清单与结果（`tools/_smoke_main_geometry.py`）

机制：`src/ACRPA.py` 在 import 时即执行 `_apply_main_geometry()` 并创建完整 GUI，故各分支以**子进程**验证：子进程内把 `state.load_config/save_config` 置为 no-op（防止真实 `config.json` 覆盖预设 / 写回真实配置），预设 `MAIN_GEOMETRY/MAIN_MAXIMIZED/COMPACT_MODE` 后 `import ACRPA`，读取 `root.geometry()/root.state()` 并打印 `SMOKEJSON::<json>`，随后 `root.destroy()` + `os._exit(0)`（无 GUI 残留）。

| 组 | 断言 | 实测结果 |
| --- | --- | --- |
| A | `win_virtual_bounds` 返回正尺寸 | `(0, 0, 2048, 1280)` ✅ |
| A | `geometry_in_screen("400x300+10+10")` → True | True ✅ |
| A | 越界/过小/空/畸形 5 例 → False | `900x700+99999+99999` / `100x100+10+10` / `""` / `"abc"` / `"400x300"` 全 False ✅ |
| A | `center_geometry(800,600)` 落在虚拟屏内 | `800x600+624+226` ✅ |
| B | 默认分支尺寸 ≥ 设计派生下限（`min(scaled(1000), vw-40)`×…） | `1000x680 ≥ 1000x680` ✅ |
| B | 默认分支几何落在虚拟屏内 + `geometry_in_screen` True | `1000x680+780+306`，True ✅ |
| C1 | `MAIN_GEOMETRY` 设为屏内几何 → 应用后与写入值一致 | `700x600+20+20` 一致 ✅ |
| C2 | `MAIN_GEOMETRY="900x700+99999+99999"` → 回退居中且屏内 | `1000x680+780+306`（== 默认分支）✅ |
| D | `MAIN_MAXIMIZED=True` → `root.state()=="zoomed"` | `'zoomed'` ✅ |
| E | `COMPACT_MODE=True` → 约 500×625（ui_scale=1.0） | `500x625` ✅ |

结果：`FAIL=0 WARN=0` → `结论: [OK] 全部通过`，退出码 **0**。

> 说明：实测环境虚拟屏 2048×1280、ui_scale=1.0；`_smoke_ui_introspect` 另在 2560×1600 / dpi_factor≈1.25 下验证默认几何 `1250x850`（= scaled(1000)×scaled(680)），确认 DPI 派生生效。

---

## 5. 自验证命令退出码 / 结论

| # | 命令 | 退出码 | 结论 |
| --- | --- | --- | --- |
| 1 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_main_geometry.py` | **0** | `[OK] 全部通过 (WARN=0)`；16 项断言全绿 |
| 2 | `.venv\Scripts\python.exe -X utf8 tools\_debug_check.py` | **0** | `checked 108 files, 0 failed`；`18/18 modules imported` |
| 3 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_introspect.py` | **0** | `控件溢出命中=0`（无 `[OVERFLOW]`）；`超窗=False`；主窗口 `1250x850` |
| 4 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_launch_app.py` | **0** | `判定: 启动成功（GUI 已建窗）`；存活 ≥10s；`含 Traceback: False` |
| 5 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_mini_bar.py` | **0** | 14 项静态断言全 `[OK]`；NetLink 集成块未见改动 |
| 6 | `.venv\Scripts\python.exe -X utf8 tools\_debug_dark_theme.py` | **0** | `结果: OK (共 57 项检查)`；亮/暗往返无回归 |

---

## 6. 残留问题

1. **`_smoke_launch_app.py` 自身 reader 线程报 `UnicodeDecodeError`**（`Thread-1`，以 utf-8 解码子进程 GBK 输出，`0xb3` 起始字节）：为**既有脚本固有现象**（输出为空时触发），与本次改动无关；不影响其判定，最终仍 `[OK] / rc=0`。
2. 本次未改动 `state.py` schema、序列化、其它模块；几何/最大化/紧凑既有行为保持不变。
3. 子进程冒烟每次会短暂创建/销毁真实主窗口（与 `_smoke_launch_app` 同性质），窗口可能在测试期间一闪而过，属预期行为。
