# ACRPA v0.1.29.0 发布与核验报告

生成时间：2026-10-03 16:10（UTC+8）
工作区：`D:/CodingEmber/ACRPA`　分支：`main`　发布 tag：`v0.1.29.0`
本地 HEAD：`aaa10a7a71cfa29e8e65010add01b076019a41eb`

---

## 1. 本轮目标与决策

| 项 | 决策 | 说明 |
|---|---|---|
| 版本号 | **仍为 `0.1.29-beta`** | 不 bump、不重建（按用户选择）：`dist/ACRPA v0.1.29-beta/ACRPA v0.1.29-beta.exe` 已是含本次全部源码改动的构建产物 |
| 发布 tag | **`v0.1.29.0`（四段）** | 仓库启用了 immutable release：`v0.1.29-beta` 的 tag 已被上一轮发布过的空 Release 永久占用，无法复用（实测新建/上传/PATCH 均 422） |
| GitHub Release | 新建 `v0.1.29.0`，以 **Pre-release** 发布，附件 `ACRPA-v0.1.29-beta.exe` | 见 §4「为什么必须是 Pre-release」 |
| Gitee | **只推代码与 tag**，不建 Release | 按用户选择；Gitee Release 的创建/上传需要私人令牌，当前 git 凭据存的是账号密码（API v5 返回 401） |

---

## 2. 提交（Commit）

- 前序：`d0543e8`（上一轮发布准备）→ 本次：**`aaa10a7`**
- 统计：**39 files changed, 5253 insertions(+), 479 deletions(-)**
- 内容：
  - 源码：`src/` 下 19 个文件改动 + 新增 `src/paddle_dll.py`
  - 打包：`ACRPA.spec`、`build.py`（依赖齐全才纳入 `lib/paddle_ocr`）
  - 发布：`VERSION`（直链改指 `v0.1.29.0`、sha256 更新为本构建）、`README.md`、`index.html`、`index.en.html`、`使用说明.txt`、`docs/releases/v0.1.29-beta.md`
  - 资源：`img/image1.png`、`img/image3.png`、`img/image5.png`
  - 文档：新增 UI 美化 / 设置窗口优化 / 底部常驻日志面板 / paddle-ocr-dll 四篇
  - 工具：`tools/_check_paddle_dll.py` 等 8 个调试/勘察脚本与报告
- 行尾/BOM 已保留原状：`VERSION` 维持 CRLF（用 `git -c core.autocrlf=false add`），`使用说明.txt` 维持 UTF-8 BOM；两文件的 diff 分别只有 2 行 / 1 行。

---

## 3. 推送（Push）

| 远端 | 结果 | 输出摘要 |
|---|---|---|
| GitHub (`github`) | ✅ | `d0543e8..aaa10a7  main -> main`；`* [new tag] v0.1.29.0 -> v0.1.29.0` |
| Gitee (`origin`) | ✅ | `d0543e8..aaa10a7  main -> main`；`* [new tag] v0.1.29.0 -> v0.1.29.0`（`Powered by GITEE.COM [1.1.23]`） |

复核（`git ls-remote`）：

```
GitHub  aaa10a7a71cfa29e8e65010add01b076019a41eb  refs/heads/main
GitHub  2d05af8a997b27c3deaab02490d0f1e44d322061  refs/tags/v0.1.29.0      (annotated tag 对象)
GitHub  aaa10a7a71cfa29e8e65010add01b076019a41eb  refs/tags/v0.1.29.0^{}
Gitee   aaa10a7a71cfa29e8e65010add01b076019a41eb  refs/heads/main
Gitee   2d05af8a997b27c3deaab02490d0f1e44d322061  refs/tags/v0.1.29.0
Gitee   aaa10a7a71cfa29e8e65010add01b076019a41eb  refs/tags/v0.1.29.0^{}
```

> 网络备注：本机 `github.com`（20.205.243.166）TCP 443 不通（`Connection was reset`），而 `api./uploads./codeload.github.com` 正常。推送与 `ls-remote` 通过
> `git -c http.curloptResolve=github.com:443:<可达IP>` 完成（可用 IP：`140.82.113.3`、`140.82.114.3`、`20.27.177.113`；`140.82.112.3`/`20.200.245.247` 时通时不通）。Release 相关操作走 `api.github.com`，不受影响。

---

## 4. GitHub Release（已发布）

- Release id：`402402598`
- 页面：https://github.com/yohoten/acrpa/releases/tag/v0.1.29.0
- `tag_name` = `v0.1.29.0`；`name` = `ACRPA v0.1.29.0 (Pre-release)`
- `draft` = **False**；`prerelease` = **True**；`immutable` = True
- 正文：取自 `docs/releases/v0.1.29-beta.md`（3,870 字符）
- 附件：`ACRPA-v0.1.29-beta.exe`

| 附件 | 本地 | 服务端 | digest |
|---|---|---|---|
| `ACRPA-v0.1.29-beta.exe` | 14,707,763 B / `0ffb41a1…b98fd` | size=14,707,763 | `sha256:0ffb41a18b6b55f44659068762bbddc8cc131af02ea70c76f5685ab1cffb98fd` ✅一致 |

直链：`https://github.com/yohoten/acrpa/releases/download/v0.1.29.0/ACRPA-v0.1.29-beta.exe`

### 为什么必须是 Pre-release

客户端的首选更新来源是 `GET /repos/yohoten/acrpa/releases/latest`（**不含预发布**）：

- 若本版以**正式版**发布：`latest` 会变成 `v0.1.29.0`，处于 `0.1.29-beta` 的客户端将被判定「有新版本」（`0.1.29.0 > 0.1.29-beta`）；
  而 Release API 分支只会把**本机 VERSION 里的 sha256** 当期望值（`src/updater.py:378`），与新包必然不符 → 下载校验失败；且该分支找不到名为 `ACRPA.zip` 的附件，会回退拼出 `…/v0.1.29.0/ACRPA.zip` → 404。
- 以 **Pre-release** 发布：`latest` 仍是 `v0.1.27.0`（已复核），更新改走「原始清单(VERSION)」通道，期望值取自**远端 VERSION 的 sha256**，与附件完全一致。

复核 `GET /releases/latest` → `v0.1.27.0`（`prerelease=False`），符合设计。

### 端到端下载校验（真实下载）

```
curl -L --resolve github.com:443:140.82.114.3 \
  https://github.com/yohoten/acrpa/releases/download/v0.1.29.0/ACRPA-v0.1.29-beta.exe
→ HTTP=200  size=14,707,763  用时 4.5s
→ SHA256 0FFB41A18B6B55F44659068762BBDDC8CC131AF02EA70C76F5685AB1CFFB98FD  ✅ 与 VERSION 声明一致
```

---

## 5. 清单（VERSION）通道核验

| 来源 | 结果 |
|---|---|
| `raw.githubusercontent.com/yohoten/acrpa/main/VERSION` | ✅ 返回新清单（`…/v0.1.29.0/ACRPA-v0.1.29-beta.exe` + 新 sha256） |
| `cdn.jsdelivr.net/gh/yohoten/acrpa@main/VERSION` | ✅ 已刷新为新清单 |
| `gitee.com/yohoten/acrpa/raw/master/VERSION` | ⚠️ 仍为旧内容（见 §6.3） |

> jsDelivr 缓存清理：本机 `purge.jsdelivr.net` 的 **POST 已返回 405**（`MethodNotAllowed`，接口改为 GET），
> 用 `GET https://purge.jsdelivr.net/gh/yohoten/acrpa@main/VERSION` 得到 `status: finished`（CF/FY 两个 provider 均已清理），
> 随后 CDN 即返回新清单。`tools/make_release.py --purge-cdn` 目前用的是 POST，建议同步改为 GET。

---

## 6. 遗留项与建议（本轮均未执行）

1. **空 Release `v0.1.29-beta`（id `401970038`）仍在 GitHub 上**：上一轮创建，`immutable=true`、**无任何附件**，页面可访问但下不到东西。
   因 tag 被 immutable 占用，无法补传附件；如需清理只能**删除该 Release**（tag 仍会保留且不可复用）。本轮未做任何删除操作，等你的指示。
2. **Gitee 无 Release 附件**：按你的选择，本轮 Gitee 只推送了代码与 tag `v0.1.29.0`；Gitee Release 的创建/上传需要私人令牌（PAT），
   当前 git 凭据是账号密码，`GET/POST https://gitee.com/api/v5/...` 均返回 `401 Access token does not exist`。需要时提供一个 PAT 即可补发。
3. **Gitee 默认分支 `master` 落后且与 `main` 分叉**：`origin/HEAD -> origin/master`（`cea8449`，behind 14 / ahead 36）。
   `src/version_info.py` 的第三条兜底清单源用的是 `gitee raw/master`，因此该源仍报旧版本（按注释「只会少报，不会误报」，不致命）。
   若要修正需 merge/force push，属改写历史，**未执行**。
4. **`tools/publish_release.py` 把 `prerelease` 硬编码为 `False`**（`src` 第 347 行），且没有 `--prerelease` 开关。
   直接用它发布会落进 §4 的坑。本轮改用一次性脚本 `tools/_publish_release_github_0129_0.py`（复用其 `git_token/api/curl_upload`）。
   建议给该工具补一个 `--prerelease` 参数。
5. **`dist/ACRPA.zip` 是手工压缩包，未上传**：其内部目录名为 `ACRPA v0.1.28-beta/`、内含 `ACRPA v0.1.29-beta.exe`，
   **包内没有 `VERSION`**，且 `使用说明.txt` 的文件名是 GBK 字节（乱码）。若需要发布 zip，请先跑
   `python tools/make_release.py --exe "dist/ACRPA v0.1.29-beta/ACRPA v0.1.29-beta.exe"` 重新生成（该工具会写入 UTF-8 文件名与包内 VERSION）。
6. **`src/version_info.py::_FALLBACK_VERSION` 仍是 `0.1.28-beta`**：仅在 VERSION 文件完全不可读时生效，本轮为避免"已提交源码 ≠ 已发布二进制"的漂移，
   **未改动 `src/`**。下次重建时会一并修正。
7. **附件内嵌 VERSION 的直链是旧的**：`ACRPA-v0.1.29-beta.exe` 内嵌 VERSION 第 2 行仍指向 `…/v0.1.29-beta/…`（打包时 VERSION 尚未更新），
   第 3 行 sha256 为上一版构建值。因为更新走远端清单通道，该内嵌副本不会被用于下载；如需彻底一致，需按新 VERSION 重新打包一次。

---

## 7. 本轮产出的文件（发布后新增，untracked→已随下列提交入库）

- `tools/_publish_release_github_0129_0.py` —— 一次性发布脚本（草稿 → 上传 → 发布 → 核验）
- `tools/_publish_release_github_0129_0_result.json` —— 发布结果与核验 JSON
- `tools/_git_release_v0129_0_report.md` —— 本报告
