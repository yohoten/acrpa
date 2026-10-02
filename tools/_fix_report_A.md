# ACRPA 修复报告 A（BUG-01 / BUG-02 / BUG-03 / TEST-01 / TEST-02）

- 执行角色：修复-A 子任务（由主任务 Orchestrator 委派）
- 工作区：`d:/CodingEmber/ACRPA`
- 执行 Shell：`C:\WINDOWS\system32\cmd.exe`
- 解释器：`.venv\Scripts\python.exe`（Python 3.9.13）
- 修复依据：测试报告 [`tools\_smoke_report_总览.md`](tools/_smoke_report_总览.md)（根因已确认）
- 边界：仅修复下列 5 项，未改动其它无关代码；遵循项目风格（中文注释、最小改动）。

---

## 1. BUG-01（P1，产品 bug）字体缩放失效

### 改动点 1：`src/utils.py` init_fonts（原 256-257 行）
- 改动前：
  ```python
  # 已存在: 仅改属性 (幂等; nametofont 自带 delete_font=False)
  f = tkinter.font.nametofont(role, root=root)
  ```
- 改动后：
  ```python
  # 已存在: 仅改属性 (幂等; exists=True 包装既有命名, 不删底层字体)。
  # 不能用 nametofont(role, root=root): Python 3.9 签名为 nametofont(name),
  # 传 root= 会抛 TypeError 并被吞掉; Font(..., exists=True) 跨 3.8~3.10+ 等价。
  f = tkinter.font.Font(root=root, name=role, exists=True)
  ```

### 改动点 2：`src/utils.py` init_fonts 改配异常（原 268-271 行）
- 改动前：`except Exception: pass`（静默吞掉）
- 改动后：改为 `except Exception as e:` + `log1("命名字体改配失败: {} -> {}pt ({})".format(role, pt, e))`，核心逻辑不再被静默吞掉。

### 改动点 3：`src/utils.py` set_ui_scale（原 288-294 行）
- 改动前：
  ```python
  tkinter.font.nametofont(role, root=_font_root).configure(size=fit_pt(size, v))
  # except Exception: pass
  ```
- 改动后：
  ```python
  tkinter.font.Font(root=_font_root, name=role, exists=True).configure(size=fit_pt(size, v))
  # except Exception as e: log1("界面缩放字体改配失败: ...")
  ```

### 改动点 4：`src/ACRPA.py:263` `_mb_content_height`
- 改动前：`_f = _tkfont.nametofont(font or FONT_BUTTON, root=root)`
- 改动后：`_f = _tkfont.Font(root=root, name=font or FONT_BUTTON, exists=True)`

### 修复原理与方案选型
根因：Python 3.9 的 `tkinter.font.nametofont` 签名为 `nametofont(name)`，不接受 `root=` 关键字，调用即抛 `TypeError`，被外层 `except` 静默吞掉 → 切档位时字号恒定。
采用方案 ②`tkinter.font.Font(root=..., name=..., exists=True)`：它正是 `nametofont` 内部实现，能显式绑定到目标 root（多 root 场景正确），且跨 Python 3.8/3.9/3.10+ 均可用；相较方案 ①`nametofont(role)`（依赖默认 root）更贴近原始意图（用同一 root 取命名句柄）。`exists=True` 保证只包装既有命名字体、不会删除底层字体，与 `nametofont` 语义等价。

### 自验证
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_scale.py` → 退出码 0 / `=== 结果: OK ===`
  - `[OK] set_ui_scale(1.2) -> state.UI_SCALE=1.2, 字号 9 -> 11`（字号随档位变化）
  - 不再出现 `TypeError`（TEST-01 修好后该动态断言复用同一命名句柄）。
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_smoke_marketplace.py` → `FONT_BODY` 三档 `9pt → 11pt → 14pt` 递增，`[OK] 三档 ui_scale 下卡片重建、字体与间距同步变化`。

---

## 2. BUG-02（P1，产品 bug）内置脚本市场安装恒失败

### 改动点 1：`src/marketplace.py:642-648` 查表键名
- 改动前：
  ```python
  rows = BUILTIN_TEMPLATES.get(script_info.id, [])
  if not rows:
      raise MarketplaceError("内置脚本模板未找到: {}".format(script_info.id))
  ```
- 改动后：
  ```python
  # 兼容两种键: 先试 id (builtin_1..8) 再回退 filename (builtin_notepad...)
  rows = BUILTIN_TEMPLATES.get(script_info.id) or BUILTIN_TEMPLATES.get(
      getattr(script_info, "filename", ""))
  if not rows:
      raise MarketplaceError("内置脚本模板未找到: {} / {}".format(
          script_info.id, getattr(script_info, "filename", "")))
  ```

### 改动点 2：`src/marketplace.py:650-655` 落点目录兜底
- 改动前：直接 `wb.save(save_path)`（save_dir 不存在时 xlwt 抛 `FileNotFoundError`）
- 改动后：
  ```python
  if save_dir and not os.path.isdir(save_dir):
      os.makedirs(save_dir, exist_ok=True)
  ```
  与包模式（`market_install_root`/`_download_and_install_package` 的 `makedirs`）保持一致。

### 修复原理
`BUILTIN_TEMPLATES` 以 `filename`（`builtin_notepad` 等）为键，而安装流程以 `id`（`builtin_1`）查表 → 恒空 → 抛 `MarketplaceError('内置脚本模板未找到: builtin_1')`（与 xlwt 是否安装无关）。改为「先 id 后 filename」两级兼容检索，`builtin_1..8` 均能命中且不破坏其它模板 ID 解析；同时补目录兜底，使「解析成功 → 安装真正完成」。此两点同属 BUG-02 范围（使 `builtin_1` 正确解析并**完成安装**）。

### 自验证
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_test_script_package.py` → 退出码 0 / `0 项 FAIL, 0 项 WARN`
  - 不再出现 `内置脚本模板未找到` WARN；
  - `[OK] builtin 脚本生成路径零回归`（此前为 `FileNotFoundError` 的 WARN，现已消除）。

---

## 3. BUG-03（P2，产品 bug）主窗口几何未含 DPI 因子导致纵向裁剪

### 改动点：`src/ACRPA.py` `_apply_main_geometry()`（原 1458-1460 行）与相关注释（原 1405-1407 行）
- 改动前：
  ```python
  else:
      root.geometry(_main_center_geometry(1000, 680))
  root.minsize(640, 520)
  ```
- 改动后：
  ```python
  else:
      # 默认几何须纳入 dpi_factor: 125% DPI 下内容 req 高 703 > 硬编码 680,
      # 会纵向裁剪底部; 经 utils.scaled() 派生保证 reqheight <= winfo_height。
      root.geometry(_main_center_geometry(utils.scaled(1000),
                                          utils.scaled(680)))
  root.minsize(640, 520)
  ```
  同步更新函数 docstring 与 1405-1408 行段注释（默认几何由 `utils.scaled()` 从设计值 1000x680 派生）。

### 修复原理
`utils.scaled(v) = round(v * dpi_factor() * ui_scale)`（下限 1），与既有 `ui_scale` 逻辑同源。默认（非紧凑）分支改为经 `scaled()` 派生：125% DPI 下 1000x680 → 1250x850，含内容需求（req 897x703），消除 23px 纵向溢出。**保持不变**：上次几何记忆恢复、越界回退、最大化状态恢复、紧凑模式 500x625 约定，以及异常兜底 `960x680`。

### 自验证
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_introspect.py` → 退出码 0 / `=== 结果: OK ===`
  - `[MAIN] geometry='1250x850+655+250' size=1250x850 req=897x703`
  - `[MAIN] 内容需求-实际: reqw-w=-353 reqh-h=-147 -> OK(未超窗)`
  - `[OVERFLOW] 已扫描控件数=171 ; 命中溢出(可见且req>actual)=0`
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_smoke_mini_bar.py` → 退出码 0 / `=== 结果: OK ===`（mini bar 未受影响）。

---

## 4. TEST-01（测试脚本缺陷）

### 改动点：`tools/_smoke_ui_scale.py:286-289`（原 287 行）
- 改动前：`return int(tkinter.font.nametofont(role, root=r).actual()["size"])`
- 改动后：
  ```python
  # Python 3.9 的 nametofont 不接受 root=; Font(exists=True) 精确取同一命名。
  return int(tkinter.font.Font(root=r, name=role, exists=True).actual()["size"])
  ```

### 修复原理
测试自身使用了 Py3.9 不支持的 `nametofont(role, root=r)`（未被 try/except 包住 → 直接 FAIL）。改用 `tkinter.font.Font(root=r, name=role, exists=True)`，与产品修复同一等价写法，精确读取同一命名句柄，动态断言可真实反映字号随缩放变化。

### 自验证
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_scale.py` → 退出码 0 / `=== 结果: OK ===`（C 段动态断言全 `[OK]`，字号 `9 -> 11`）。

---

## 5. TEST-02（测试脚本缺陷）

### 改动点 1：`tools/_smoke_marketplace.py:154` 过期工厂名
- 改动前：`if '_btn(toolbar_inner,"市场",_open_marketplace' in a_src:`
- 改动后：`if '_tbtn(toolbar_inner,"市场",_open_marketplace' in a_src:`
  （实际工具栏工厂已改用 [`src/ACRPA.py:3536`](src/ACRPA.py:3536) 的 `_tbtn`，绑定目标 `_open_marketplace` 未变，见 [`src/ACRPA.py:3587`](src/ACRPA.py:3587)。）

### 改动点 2：`tools/_smoke_marketplace.py:393-410` 漏报缺陷
- 改动前：B5 三档缩放循环内 4 处 `FAILS.append(...)` **未配套 `_p("FAIL", ...)`**，导致动态 FAIL 被计入 `FAILS` 但不打印，被漏计/难以定位。
- 改动后：为每处 `FAILS.append(...)` 补上对应的 `_p("FAIL", ...)`，确保 FAIL 会被正确统计与输出；**未弱化任何断言**。

### 修复原理
静态断言引用了已重命名的工具栏工厂；动态块 FAIL 缺打印导致计数与可见输出不一致（报告 §2.1 指出的阶段2 偏差来源）。均属测试脚本缺陷，仅修测试。

### 自验证
- 命令：`.venv\Scripts\python.exe -X utf8 tools\_smoke_marketplace.py` → 退出码 0 / `=== 结果: OK (FAIL=0 WARN=0) ===`
  - `[OK] 工具栏「市场」按钮绑定保持不变`
  - `[OK] 三档 ui_scale 下卡片重建、字体与间距同步变化`（无静默 FAIL）。

---

## 6. 七条自验证命令与结果（cmd.exe 逐条执行）

| # | 命令 | 退出码 | 关键结论 |
|---|---|---|---|
| 1 | `.venv\Scripts\python.exe -X utf8 tools\_debug_check.py` | 0 | `checked 106 files, 0 failed`；`18/18 modules imported`；PASS |
| 2 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_scale.py` | 0 | `=== 结果: OK ===`；`set_ui_scale(1.2) 字号 9 -> 11`（字号随档位变化） |
| 3 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_marketplace.py` | 0 | `=== 结果: OK (FAIL=0 WARN=0) ===`；`FONT_BODY 9/11/14pt` |
| 4 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_ui_introspect.py` | 0 | 主窗 `1250x850`，`reqh-h=-147`（未超窗），`[OVERFLOW] 命中溢出=0` |
| 5 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_mini_bar.py` | 0 | `=== 结果: OK ===`（mini bar 未受影响） |
| 6 | `.venv\Scripts\python.exe -X utf8 tools\_test_script_package.py` | 0 | `0 项 FAIL, 0 项 WARN`；`[OK] builtin 脚本生成路径零回归` |
| 7 | `.venv\Scripts\python.exe -X utf8 tools\_debug_dark_theme.py` | 0 | `=== 结果: OK (共 57 项检查) ===`（主题往返未回归） |

---

## 7. 变更文件清单

| 文件 | 变更摘要 |
|---|---|
| [`src/utils.py`](src/utils.py) | BUG-01：`init_fonts`/`set_ui_scale` 改用 `Font(exists=True)`，改配失败改记日志 |
| [`src/ACRPA.py`](src/ACRPA.py) | BUG-01：`_mb_content_height` 改用 `Font(exists=True)`；BUG-03：默认几何经 `utils.scaled()` 派生 |
| [`src/marketplace.py`](src/marketplace.py) | BUG-02：模板查表兼容 id/filename；落点目录不存在时 `makedirs` |
| [`tools/_smoke_ui_scale.py`](tools/_smoke_ui_scale.py) | TEST-01：改用 `Font(root=r, name=role, exists=True)` |
| [`tools/_smoke_marketplace.py`](tools/_smoke_marketplace.py) | TEST-02：断言 `_btn` → `_tbtn`；动态 FAIL 补 `_p("FAIL", ...)` |

## 8. 遗留问题

- 无本次范围内的遗留问题。范围外的 BUG-04（trusted 超时可绕过，已知设计限制，未修）、ENV-01（测试环境缺 `cryptography`/`openssl`，非产品缺陷）不在本子任务范围，未改动。
