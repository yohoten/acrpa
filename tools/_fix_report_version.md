# 需求二 · 版本号统一修复报告

- 任务：把 ACRPA 中残留的版本号硬编码/旁路来源收敛为**单一权威来源**，实现「改一处即全局生效」，并覆盖网页侧。
- 权威来源：**保持为 `src/version_info.py`（运行时读根 `VERSION` 文件）**，不改为写死在代码里。
- 环境：Windows + cmd.exe；解释器 `.venv\Scripts\python.exe`。

---

## 一、版本权威来源说明

- **唯一事实来源**：项目根目录 [`VERSION`](VERSION) 文件首行。
- **运行时权威入口**：[`src/version_info.py`](src/version_info.py) —— 模块级常量 `VERSION = get_version()`（[`src/version_info.py:173`](src/version_info.py:173)）。
- **读取链路**：`VERSION` 文件 → `version_info.read_manifest()`（多级查找：EXE 同级目录 → `_MEIPASS` 解压目录 → 项目根）→ `version_info.get_version()` → `VERSION` 常量。
- **发版改一处**：仅编辑根 `VERSION` 文件，或使用 `tools/bump_version.py`，全应用（更新器/关于页/设置页/市场/脚本分发/NetLink 节点/网页）即全部生效。

---

## 二、逐项改动（文件:行 + 要点）

### 1. `src/netlink/node.py` —— 消除直接读 VERSION 文件的旁路

- 新增 [`_import_version_info()`](src/netlink/node.py:136)（[`src/netlink/node.py:136-148`](src/netlink/node.py:136)）：懒 import `version_info`，与既有 `_import_state()` 同口径；失败仅记日志并返回 `None`，**避免顶层硬依赖与循环导入**（`version_info` 只依赖 os/re/sys，无反向依赖）。
- 重写 [`NetLinkNode._read_version()`](src/netlink/node.py:411)（[`src/netlink/node.py:411-428`](src/netlink/node.py:411)）：
  - 由「`open(root/"VERSION")` 直接读取」改为 `version_info.get_version()`，**复用其多级查找与冻结路径处理**，对外暴露的 `self._version`（经 `node.info["version"]` 暴露，[`src/netlink/node.py:218`](src/netlink/node.py:218)）语义不变；
  - 不再直接 `open(.../"VERSION")`（静态检查可通过）；
  - 极端情况（`version_info` 不可导入）返回空串，交由上层忽略版本字段，不再自行旁路读文件。

### 2. `src/marketplace.py` —— 保留兼容下限的独立语义

- [`MIN_APP_VERSION`](src/marketplace.py:32)（[`src/marketplace.py:26-32`](src/marketplace.py:26)）：
  - **选择「保持常量 + 注释」而非派生**。理由：其语义是「脚本市场所需的最低兼容应用版本（兼容下限）」，**不是应用当前版本**；应用升级并不自动意味着兼容下限提升，若派生自 `version_info.VERSION` 会导致每次发版都无谓抬高安装门槛。
  - 补充清晰中文注释，明确「兼容下限、需手动提升、勿与 `version_info.VERSION` 混用」。

### 3. `src/version_info.py` —— 陈旧回退值同步

- [`_FALLBACK_VERSION`](src/version_info.py:35)（[`src/version_info.py:32-35`](src/version_info.py:32)）：由 `"0.1.26"` 更新为 `"0.1.28-beta"`，与当前实际版本一致；加注释说明「仅当 VERSION 文件全部缺失/损坏时使用，需随发版同步」，否则 VERSION 不可读时全应用自报旧版本号、更新检查结论失真。

### 4. `tools/README.md` —— 修正过时指令

- [`版本管理`](tools/README.md:265)（[`tools/README.md:265-273`](tools/README.md:265)）：删除过时的「修改 `src/updater.py` 中的 VERSION」，改为：
  - 版本号唯一来源为根 `VERSION` 文件（首行）；
  - 改版本请编辑 `VERSION` 或使用 `tools/bump_version.py`；
  - 运行时由 `src/version_info.py` 读取并统一暴露。

### 5. `index.html` / `index.en.html` —— 网页侧版本号收敛为页面内单一源

改动（两页对称）：

- 单一源：页面内 [`ACRPA_RELEASE`](index.html:1848)，仅保留唯一权威字面量 `version`（[`index.html:1848`](index.html:1848) / [`index.en.html:1851`](index.en.html:1851)）；`tag`、`asset` 由 `version` 派生（[`index.html:1853`](index.html:1853) / [`index.en.html:1856`](index.en.html:1856)），不再重复写字面量。
- `<head>` 内所有版本字面量被移除并由内联 JS 于加载时注入：
  - [`<title>`](index.html:6)：去掉 `· v0.1.28-beta`，由脚本设置完整标题；
  - [`og:title`](index.html:16) / [`twitter:title`](index.html:21)：去掉版本后缀，运行时同步；
  - [`JSON-LD`](index.html:28)：`<script id="ldjson">`（[`index.html:28`](index.html:28)），`softwareVersion` / `downloadUrl` 置空，脚本解析后回填（[`index.html:1876`](index.html:1876) 区块）。
- 正文中的散落字面量改为标记注入：
  - 下载直链（`data-download`）由脚本统一填 `href`，静态 `href` 改为 Releases 页兜底（[`index.html:885`](index.html:885)）；
  - 附件名 `<small>` 改 `<span data-asset>`（[`index.html:887`](index.html:887)）；
  - hero 下方「最新版」段落改 `<span data-ver>` + `<span data-asset>`（[`index.html:902`](index.html:902)）。
- 注入脚本（[`index.html:1856-1880`](index.html:1856) / [`index.en.html:1859-1883`](index.en.html:1859)）：统一 `fill('data-ver'|'data-date'|'data-asset', ...)`、设置 `document.title`、`og:title` / `twitter:title`，并注入 JSON-LD 的 `softwareVersion` / `downloadUrl`。
- 两页各自维护一份单一源，**中/英互不共享**。

> **SEO 局限（已在代码注释中如实标注）**：meta / JSON-LD 由 JS 运行时写入，对爬虫及无 JS 环境**无即时效果**；如需 SEO 精确，应在发版时另行维护站点侧静态快照。本次不因此保留任何字面量。

### 6. 新增 `tools/_test_version_unify.py`

- 沿用项目 `[OK]/[WARN]/[FAIL]` 与退出码 0/1 约定（[`tools/_test_version_unify.py`](tools/_test_version_unify.py)）。
- 断言覆盖：V1 权威一致性；V2 netlink 节点版本 == `version_info.VERSION` + 静态检查 node.py 不再直接读 `"VERSION"`；V3 `_FALLBACK_VERSION` 同步；V4 两页单一源存在且除单一源外无字面量；V5 README 过时指令消失。

---

## 三、验证结果（8 条自验证命令 · 退出码/结论）

| # | 命令 | 退出码 | 结论 |
|---|------|--------|------|
| 1 | `.venv\Scripts\python.exe -X utf8 tools\_test_version_unify.py` | **0** | PASS（16/16，通过16/失败0/警告0） |
| 2 | `.venv\Scripts\python.exe -X utf8 tools\_debug_check.py` | **0** | PASS（syntax 109/109，import 18/18） |
| 3 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_netlink.py` | **0** | PASS（8/8） |
| 4 | `.venv\Scripts\python.exe -X utf8 tools\_test_netlink_auth.py` | **0** | PASS（all assertions OK, warns=0） |
| 5 | `.venv\Scripts\python.exe -X utf8 tools\_test_netlink_e2e.py` | **0** | PASS（all assertions OK, warns=0） |
| 6 | `.venv\Scripts\python.exe -X utf8 tools\_test_netlink_control.py` | **0** | PASS（all assertions OK, warns=0） |
| 7 | `.venv\Scripts\python.exe -X utf8 tools\_debug_updater.py` | **0** | PASS（ALL PASSED 107 项） |
| 8 | `.venv\Scripts\python.exe -X utf8 tools\_smoke_launch_app.py` | **0** | OK（GUI 建窗、存活至阈值，判定启动成功） |

- 3-6 说明 `netlink/node.py` 改动无回归；7 说明更新器版本引用无回归。

---

## 四、残留与风险

- **SEO 静态快照**：网页 meta / JSON-LD 改为 JS 注入后，对爬虫/无 JS 环境不即时生效（已在页面注释标注）。若需 SEO 精确，需在发版时另行维护站点侧静态文案。
- **无 JS 兜底**：下载链接静态 `href` 回落到 Releases 页；附件名/版本号在无 JS 时为空（原为硬编码字面量，属有意取舍）。
- **`_FALLBACK_VERSION` 需人工随发版同步**：无法自动跟随 `VERSION` 文件（否则失去「VERSION 不可读时兜底」的意义），已加注释提醒。
- **`MIN_APP_VERSION` 保持独立常量**：提升兼容下限需手动修改，属预期行为（已加注释说明）。
- 冲突规避：未改动 `acrpa_api.API_VERSION`、`netlink/protocol.PROTOCOL_VERSION`、`webui.CACHE_VERSION`、`script_package.SUPPORTED_MANIFEST_VERSIONS` 等语义独立的版本常量；未改 `docs/releases/*` 历史留档；未动打包 spec/build 的 VERSION 纳入方式。
- `tools/_smoke_launch_app.py` 输出中的 `UnicodeDecodeError` 线程异常来自该冒烟脚本自身读取子进程 stdout 的解码（harness 侧既有行为），与应用启动无关，最终判定 `OK (rc=0)`。

---

## 五、运维提示：发版流程（改一处全局生效）

1. 编辑根 [`VERSION`](VERSION) 首行（或运行 `tools/bump_version.py`）。
2. 应用侧（`version_info.VERSION`）、更新器（`updater`）、NetLink 节点、以及两个网页的版本展示自动跟随。
3. 若 `VERSION` 长期可能损坏，可同步 [`_FALLBACK_VERSION`](src/version_info.py:35)（仅作兜底）。
4. 网页发版：仅需修改 [`index.html`](index.html:1848) / [`index.en.html`](index.en.html:1851) 中 `ACRPA_RELEASE.version` 一处。
