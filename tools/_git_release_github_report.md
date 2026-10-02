# GitHub Release 发布报告：v0.1.29-beta（附件受阻 / immutable release）

- 任务：在 GitHub `yohoten/acrpa` 发布 **`v0.1.29-beta`**（`prerelease=true`、`draft=false`、tag 指向 `main` HEAD `00cc4de…`），附件 `ACRPA-v0.1.29-beta.exe`（14,680,412 B，sha256 `f57f1687…3790`），并同步 `VERSION` / `docs/releases/v0.1.29-beta.md` / `README.md`。**Gitee 不动。**
- 约束：不打印 token；不使用 `verify=False`；不 commit / 不 push；不得谎报成功；上传失败后**不删除 Release**。
- **结论：Release 已创建并已发布（prerelease=true），但附件上传失败（HTTP 422 immutable release），无法补挂附件；未删除 Release。核心目标「可下载附件」未达成。**

> 本报告不含任何 token。全程 `verify` 使用 Windows 证书库导出的 CA bundle（`tools\_win_ca_bundle.pem`），**未使用 `verify=False`**。

---

## 1. 环境与凭据

- 解释器：`.venv\Scripts\python.exe`（Python 3.9.13）；`requests` 2.32.5。
- TLS：`tools\_win_ca_bundle.pem`（236,132 B，已复用）。
- PAT：经 `git credential fill`（`protocol=https`、`host=github.com`）获取成功（`***`，未打印）。
- 命令逐条执行，未使用 `&&` / `|` / 反引号 / `$()`；中文以 `-X utf8` 运行。

---

## 2. 文件改动清单

| 文件 | 改动 |
|------|------|
| [`VERSION`](VERSION) | 第 1 行 → `0.1.29-beta`；第 2 行 → `https://github.com/yohoten/acrpa/releases/download/v0.1.29-beta/ACRPA-v0.1.29-beta.exe`；第 3 行 → `sha256:f57f1687452affaf2acccb13b67cdf810e28e6ed319eaf1cf37994dd3e283790` |
| [`docs/releases/v0.1.29-beta.md`](docs/releases/v0.1.29-beta.md) | **新增**：`# ACRPA v0.1.29-beta` / `## 本版更新`（含「为何 0.1.29 而非 0.1.28」与「构建错配」说明）/ `## 下载` / `## 安装` / `## 校验`；下载段含文件名、14,680,412 B（约 14.0 MB）、SHA-256、直链 |
| [`README.md`](README.md) | 第 3 行 当前版本 → `v0.1.29-beta`；第 8 行 下载链接 → `v0.1.29-beta`；第 65 行 Release 页面 + 资产直链 → `v0.1.29-beta`；第 68 行 大小 → `14,680,412 字节`；第 70 行 SHA-256 → `f57f1687…3790`；第 75/79 行 校验文件名 → `ACRPA-v0.1.29-beta.exe`；第 82 行 摘要标题、第 84 行 文档链接 → `v0.1.29-beta`；第 129 行 exe 名 → `ACRPA-v0.1.29-beta.exe`；第 592 行 文档链接 → `v0.1.29-beta`；更新日志在第 622 行前**新增** v0.1.29-beta 条目 |
| [`tools/_publish_release_github_0129.py`](tools/_publish_release_github_0129.py) | **新增**：`status`/`gate`/`run` 三阶段发布脚本（gate=POST /git/refs；run=create→upload→verify） |
| [`tools/_publish_release_github_0129_finish.py`](tools/_publish_release_github_0129_finish.py) | **新增**：恢复脚本（PATCH→draft→upload→publish；本仓库不可用） |
| [`tools/_verify_github_0129.py`](tools/_verify_github_0129.py) | **新增**：只读复核脚本 |

> 未改动**其它版本条目**：`README.md` 第 400 行「v0.1.28 新增配置项」、第 424 行「已随 v0.1.28-beta 提供独立 EXE 下载」、第 624 行 v0.1.28-beta 更新日志为**历史条目**，按「其它版本条目不要动」原样保留。

---

## 3. 创建与上传请求响应关键字

| 步骤 | 请求 | 响应关键字 |
|------|------|------------|
| status | `GET /repos/yohoten/acrpa/releases/tags/v0.1.29-beta` | **404**（Release 不存在） |
| status | `GET /repos/yohoten/acrpa/git/ref/tags/v0.1.29-beta` | **404**（tag 不存在） |
| status | `GET /repos/yohoten/acrpa/commits/main` | **200**，sha=`00cc4de7d5639333777a26d92e176af3987d5543` |
| **gate** | `POST /repos/yohoten/acrpa/git/refs` `{"ref":"refs/tags/v0.1.29-beta","sha":"00cc4de…"}` | **201** —— tag **可创建**（`refs/tags/v0.1.29-beta` → `00cc4de…`） |
| **create** | `POST /repos/yohoten/acrpa/releases`（`tag_name=v0.1.29-beta`、`target_commitish=main`、`draft=false`、`prerelease=true`、`body=<docs 全文>`） | **201**：`id=401970038`、`html_url=https://github.com/yohoten/acrpa/releases/tag/v0.1.29-beta`、`prerelease=true`、`draft=false`、**`immutable=true`** |
| **upload** | `POST uploads.github.com/repos/yohoten/acrpa/releases/401970038/assets?name=ACRPA-v0.1.29-beta.exe`（`Content-Type: application/octet-stream`，body=文件二进制） | **HTTP 422 ×3**（含退避重试）：`{"message":"Cannot upload assets to an immutable release."}` |
| 恢复 | `PATCH /repos/yohoten/acrpa/releases/401970038` `{"draft":true}` | **HTTP 422**：`state cannot be changed when release is immutable` / `immutable cannot be changed when release is immutable` |

**关键判定**：tag 名 `v0.1.29-beta` **本可创建**（gate 返回 201，**未**受 tag 保留限制）。失败点在**附件上传** —— 本仓库启用了 GitHub **immutable releases**：**已发布（`draft=false`）的 Release 不可再挂附件**，且**不可改回草稿**。

---

## 4. 复核（read-only）

- `GET /repos/yohoten/acrpa/releases/tags/v0.1.29-beta` → **200**：`id=401970038`、`draft=false`、`prerelease=true`、`immutable=true`、**`assets=0`（无附件）**、`html_url=https://github.com/yohoten/acrpa/releases/tag/v0.1.29-beta`。
- 附件期望 vs 实测：`name=ACRPA-v0.1.29-beta.exe` / `size=14,680,412` / `digest=sha256:f57f1687…3790` → **全部未达成**（Release 无任何附件）。
- 下载 `HEAD`：`https://github.com/yohoten/acrpa/releases/download/v0.1.29-beta/ACRPA-v0.1.29-beta.exe` → **HTTP 404**（无资产）。
- 本地附件二进制校验（上传前 precheck）：[`dist\ACRPA v0.1.28-beta\ACRPA v0.1.28-beta.exe`](dist/ACRPA%20v0.1.28-beta/ACRPA%20v0.1.28-beta.exe) `size=14,680,412`、`sha256=f57f1687452affaf2acccb13b67cdf810e28e6ed319eaf1cf37994dd3e283790` —— **与预期一致**（二进制本身无问题，纯粹被上传策略拦截）。
- 仓库字段：`GET /repos/yohoten/acrpa` → 200，`private=false`、`visibility=public`（API 未暴露 immutability 开关字段）。

### 其它 Release 未受影响

`GET /repos/yohoten/acrpa/releases?per_page=30` → **200**：

| tag | id | draft | prerelease | immutable |
|-----|----|-------|-----------|-----------|
| **v0.1.29-beta**（本次新建） | 401970038 | false | true | true |
| v0.1.27.0 | 390536116 | false | false | true |
| v0.1.25.0 | 367595653 | false | false | true |

`v0.1.27.0` / `v0.1.25.0` **未受影响**。注意二者亦为 `immutable=true` —— 说明本仓库 **immutable releases 处于启用状态**（用户所述「已关闭」未生效）。

---

## 5. 最终状态

**部分完成 / 附件受阻（PARTIAL）。**

- `v0.1.29-beta` Release **已存在且已发布**（`prerelease=true`、[页面](https://github.com/yohoten/acrpa/releases/tag/v0.1.29-beta)），但**无附件**，且因 immutable **无法补挂**。
- 仓库仍启用 **immutable releases**：本次新建 Release 实测 `immutable=true`（与 `v0.1.27.0` / `v0.1.25.0` 相同）——用户所述「已关闭」**未生效 / 未追溯**。
- 因此 [`VERSION`](VERSION) 第 2 行直链与 [`README.md`](README.md) 的**资产直链当前 404**（Release 页面可达，**附件不可达**）；在附件补齐前，该直链不可用。
- **未 commit、未 push**；**Gitee 未触碰**；**未删除 Release**。

---

## 6. 为什么不能「删除后重建」（与 v0.1.28-beta 同因）

本仓库 immutable release 会**永久保留已使用过的 tag 名**（`v0.1.28-beta` 正是因此无法重建，实测 422）。若删除当前 `v0.1.29-beta`（immutable）Release，其 tag `v0.1.29-beta` **极可能同样被永久保留**，届时连该 tag 也无法再创建 —— 状态**更差**。故按任务约束**不删除 Release**。

---

## 7. 建议（供主任务 / 用户决策，本次未执行）

1. **在仓库 Settings 关闭「Immutable releases」并确认其真正生效**（判据：新建 Release 应为 `immutable=false`）。注意：关闭**不追溯**已发布 Release 的不可变状态。
2. **后续发布一律走「draft → upload → publish」** —— 本仓库 [`tools/publish_release.py`](tools/publish_release.py) 与 [`README.md`](README.md:493) 即为此顺序；**切勿**先 `draft=false` 再上传（会得到 `422 Cannot upload assets to an immutable release.`）。
3. 若要为 **v0.1.29-beta 交付可下载附件**：因 `v0.1.29-beta` 的 tag 已被 immutable Release 占用，需**改用新 tag**（如 `v0.1.30-beta` 或 `v0.1.29.0-beta`）以 draft-first 流程重新发布，并同步回滚 / 更新 `VERSION` 与 `README`。
4. 或**联系 GitHub 支持**申请释放 `v0.1.29-beta` 的 tag 保留。

---

## 8. 证据文件（本报告不含 token）

- `tools/_publish_github_result_0129.json` —— status / run 结果（`state=CREATED_NO_ASSET`；create 返回 `id=401970038`）。
- `tools/_publish_github_result_0129_finish.json` —— 恢复尝试（`state=IMMUTABLE_PATCH_BLOCKED`）。
- `tools/_verify_github_result_0129.json` —— 只读复核（release / download HEAD / release list）。
- `tools/_win_ca_bundle.pem` —— Windows 证书库导出的 CA bundle。
