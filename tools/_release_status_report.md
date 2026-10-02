# ACRPA Release 状态只读核验报告

> 本报告为**只读核验**产出，核验过程未执行任何写操作（未创建/修改/删除 Release、未上传附件、未 push、未改动仓库代码）。本文件是本任务唯一新增的文件。

## 1. 环境与核验信息

- 工作区：`d:/CodingEmber/ACRPA`
- 操作系统 / Shell：Windows 10/11 + cmd.exe
- 核验时间（本机）：2026-10-02 23:56:49（UTC+8）
- 仓库与远端：
  - GitHub：`yohoten/acrpa`（远端名 `github`）
  - Gitee：`yohoten/ACRPA`（远端名 `origin`）
- 访问方式：公开仓库只读读取，**未使用任何 token**（下方 URL 均不含凭据）。
- GitHub 访问使用 `curl --ssl-no-revoke`（规避本机 schannel 吊销检查误报 exit 35）；Gitee 直连。
- 本次核验的只读请求 URL：
  - `GET https://api.github.com/repos/yohoten/acrpa/releases?per_page=30`
  - `GET https://api.github.com/repos/yohoten/acrpa/releases/tags/v0.1.28-beta`
  - `GET https://api.github.com/repos/yohoten/acrpa/releases/latest`
  - `GET https://gitee.com/api/v5/repos/yohoten/ACRPA/releases`
  - `GET https://gitee.com/api/v5/repos/yohoten/ACRPA/tags`

## 2. GitHub Release 现状

### 2.1 releases 总表（`?per_page=30`，共返回 3 条）

| tag_name | name | prerelease | draft | immutable | published_at (UTC) | html_url | assets（name / size bytes） |
|---|---|---|---|---|---|---|---|
| `v0.1.28-beta` | ACRPA v0.1.28-beta (Pre-release) | **true** | false | **true** | 2026-10-01T04:17:26Z | https://github.com/yohoten/acrpa/releases/tag/v0.1.28-beta | `ACRPA-v0.1.28-beta.exe` / 14,047,384 |
| `v0.1.27.0` | ACRPA v0.1.27.0 | false | false | true | 2026-09-17T08:15:50Z | https://github.com/yohoten/acrpa/releases/tag/v0.1.27.0 | `ACRPA.zip` / 13,461,962 |
| `v0.1.25.0` | ACRPA v0.1.25 | false | false | true | 2026-08-10T03:30:18Z | https://github.com/yohoten/acrpa/releases/tag/v0.1.25.0 | `ACRPA.exe` / 13,592,068；`ACRPA.v0.1.13.exe` / 9,935,802；`ACRPA.v0.1.14.exe` / 10,172,924；`ACRPA.v0.1.17.exe` / 11,420,721；`ACRPA.v0.1.19.exe` / 12,431,556；`ACRPA.v0.1.20.exe` / 12,873,577；`ACRPA.v0.1.23.exe` / 13,055,961；`ACRPA.v0.1.24.exe` / 13,571,833；`ACRPA.v0.1.25.exe` / 13,592,068；`ACRPA.zip` / 13,408,130 |

说明：GitHub 侧共 3 个 Release；除 `v0.1.28-beta` 为预发布外，其余为非预发布。全部为 `immutable=true`（不可变 Release）。

### 2.2 `v0.1.28-beta` 详情（`/releases/tags/v0.1.28-beta`）

- `tag_name`：`v0.1.28-beta`
- `name`：ACRPA v0.1.28-beta (Pre-release)
- `prerelease`：**true**
- `draft`：false
- `immutable`：**true**
- `published_at`：2026-10-01T04:17:26Z（`created_at` 2026-10-01T04:01:40Z）
- `html_url`：https://github.com/yohoten/acrpa/releases/tag/v0.1.28-beta
- 附件：`ACRPA-v0.1.28-beta.exe`
  - 字节数（`size`）：**14,047,384**（正文描述为 13.4 MB）
  - sha256（`digest`）：`473f1db3da347a1310213e4fd70955f111f0fc397eb9e1ee7f5913d2b2606d54`
  - 下载直链：https://github.com/yohoten/acrpa/releases/download/v0.1.28-beta/ACRPA-v0.1.28-beta.exe
  - `download_count`：5

> 结论：GitHub 侧 `v0.1.28-beta` 已发布，**预发布 + 不可变**，附件齐全，**无需任何操作**。

### 2.3 latest（`/releases/latest`）

- `tag_name`：**`v0.1.27.0`**（`prerelease=false`，`immutable=true`）
- `published_at`：2026-09-17T08:15:50Z
- 附件：`ACRPA.zip` / 13,461,962 bytes
- `html_url`：https://github.com/yohoten/acrpa/releases/tag/v0.1.27.0

> 说明：GitHub `latest` 接口不含 prerelease，因此返回的是最近的稳定版 `v0.1.27.0`（与预期一致），而非 `v0.1.28-beta`。

## 3. Gitee Release / Tag 现状

### 3.1 releases 列表（`/repos/yohoten/ACRPA/releases`，共返回 2 条）

| tag_name | name | prerelease | created_at (UTC+8) | html_url（资产页） | assets（name） |
|---|---|---|---|---|---|
| `v0.1.24` | ACRPA v0.1.24 | false | 2026-08-09T02:49:32+08:00 | https://gitee.com/yohoten/acrpa/releases/tag/v0.1.24 | ACRPA.exe、ACRPA v0.1.13/14/17/19/20/22/23/24.exe、v0.1.24.zip、v0.1.24.tar.gz |
| `v0.1.27` | ACRPA v0.1.27 | false | 2026-09-17T16:19:24+08:00 | https://gitee.com/yohoten/acrpa/releases/tag/v0.1.27 | ACRPA.zip、v0.1.27.zip、v0.1.27.tar.gz |

> 关键结论：Gitee releases 列表中**不存在 `v0.1.28-beta`**（预期一致）。

### 3.2 tag 核对（`/repos/yohoten/ACRPA/tags`）

返回的 tag（共 5 个）：`v0.1.22`、`v0.1.23`、`v0.1.24`、`v0.1.27`、**`v0.1.28-beta`**。

- `v0.1.28-beta`：
  - commit sha：`d80b474d4cbf3b2ab2a593d3c454f3349f99ba46`（date 2026-10-01T12:00:55+08:00）
  - tagger：`yohoten`（date 2026-10-01T12:01:40+08:00）
  - message：`ACRPA v0.1.28-beta (Pre-release): NetLink 多设备互联 + 六项体验优化`

> 关键结论：Gitee 侧 `v0.1.28-beta` **只有 tag、无 Release**（tag 存在，Release 缺失）。

## 4. 缺口结论

- **GitHub**：已具备 `v0.1.28-beta`（预发布 Pre-release、`immutable=true`），附件 `ACRPA-v0.1.28-beta.exe`（14,047,384 bytes，sha256 `473f1db3...606d54`）完整，`html_url` 可访问。→ **无需操作**（按用户决策仅验证不动）。
- **Gitee**：tag `v0.1.28-beta` 已存在，但**缺少对应的 Release**；已存在的 Release 仅有 `v0.1.24`、`v0.1.27`。→ 本轮按用户决策**暂缓、不做任何操作**。
- 缺口本质：GitHub 侧 v0.1.28-beta 已发布完毕；Gitee 侧仅完成了 tag 推送，尚未创建对应 Release（因此 Gitee 无该版本的下载资产页）。

## 5. 后续可选项（仅描述，本轮均不执行）

1. **补发 Gitee Release（需 PAT）**：在 Gitee 上以 tag `v0.1.28-beta` 创建 Release 并上传 `ACRPA-v0.1.28-beta.exe` 等附件。Gitee 写操作需访问令牌（PAT），本任务为只读，未执行，也未使用任何 token。
2. **发新版本**：待 beta 验证通过后，按既有发布工具链（`tools/make_release.py` / `tools/publish_release.py` / `bump_version.py`）发布新的稳定版，并同步至两平台。
3. 若后续需要两平台对齐，建议先明确 Gitee 是否承载预发布版本（Gitee Release 无原生 `prerelease` 标志），再决定补发策略。

## 6. 核验失败项

- 无。所有只读请求均成功（HTTP 正常返回），未发生限流或网络失败，无需重试，无需记录错误原文。
