# git 提交并推送报告 — v0.1.29-beta 发布准备

生成时间：2026-10-03（本地）
工作区：`d:/CodingEmber/ACRPA`（Shell: cmd.exe，命令逐条执行，未使用 `&&`/`|`）

---

## 1. 提交前侦察（Pre-commit）

- 当前分支：`main`
- 上一提交：`00cc4de feat(help): 帮助窗口重构(内容/渲染分离)+命令单一来源; 修复与UI增强`
- 跟踪关系：`Your branch is up to date with 'github/main'`
- `git diff --stat`（未暂存跟踪文件）：
  - `README.md | 24 +++++++++++++-----------`
  - `VERSION   |  6 +++---`
  - 合计：`2 files changed, 16 insertions(+), 14 deletions(-)`
- `VERSION` 内容确认：第1行 `0.1.29-beta`，第2行 GitHub 直链 `.../v0.1.29-beta/ACRPA-v0.1.29-beta.exe`，第3行 `sha256:f57f1687452affaf2acccb13b67cdf810e28e6ed319eaf1cf37994dd3e283790`

### 待提交清单（`git status --porcelain`）
```
 M README.md
 M VERSION
?? docs/releases/v0.1.29-beta.md
?? tools/_diag_net.py
?? tools/_gh_protection_probe.py
?? tools/_gh_rules_probe.py
?? tools/_gh_status_probe.py
?? tools/_gh_tag_test.py
?? tools/_git_commit_report.md
?? tools/_git_push_report.md
?? tools/_git_release_github_report.md
?? tools/_git_release_report.md
?? tools/_publish_github_result_0129.json
?? tools/_publish_github_result_0129_finish.json
?? tools/_publish_release_github_0129.py
?? tools/_publish_release_github_0129_finish.py
?? tools/_recover_github_result.json
?? tools/_recover_release.py
?? tools/_recreate_github_result.json
?? tools/_recreate_github_result2.json
?? tools/_recreate_release_github.py
?? tools/_recreate_release_github2.py
?? tools/_release_status_report.md
?? tools/_verify_github_0129.py
?? tools/_verify_github_result_0129.json
?? tools/_win_ca_bundle.pem
```

### 被忽略项说明（未暂存，符合预期）
- `.venv` → 命中 `.gitignore:30:.venv`
- `dist/`（含 exe）→ 命中 `.gitignore:71:dist/`
- 验证命令：`git check-ignore -v .venv dist` 输出上述两条规则，确认二者不会被纳入提交。
- 说明：任务预期清单中的 `tools/_gitpush_tag.log` 在本工作区**不存在**（本轮未生成该日志），其余预期脚本/报告/JSON 均存在并已纳入提交。

---

## 2. 暂存（Staged）

- 执行：`git add -A`
- 复核（`git status --short`）：共 **26** 项（2 修改 M + 24 新增 A），无被忽略项混入。
- `git diff --cached --stat`：`26 files changed, 6904 insertions(+), 14 deletions(-)`

暂存明细：
```
M  README.md
M  VERSION
A  docs/releases/v0.1.29-beta.md
A  tools/_diag_net.py
A  tools/_gh_protection_probe.py
A  tools/_gh_rules_probe.py
A  tools/_gh_status_probe.py
A  tools/_gh_tag_test.py
A  tools/_git_commit_report.md
A  tools/_git_push_report.md
A  tools/_git_release_github_report.md
A  tools/_git_release_report.md
A  tools/_publish_github_result_0129.json
A  tools/_publish_github_result_0129_finish.json
A  tools/_publish_release_github_0129.py
A  tools/_publish_release_github_0129_finish.py
A  tools/_recover_github_result.json
A  tools/_recover_release.py
A  tools/_recreate_github_result.json
A  tools/_recreate_github_result2.json
A  tools/_recreate_release_github.py
A  tools/_recreate_release_github2.py
A  tools/_release_status_report.md
A  tools/_verify_github_0129.py
A  tools/_verify_github_result_0129.json
A  tools/_win_ca_bundle.pem
```

---

## 3. 提交（Commit）

- 方式：UTF-8 信息文件 `tools/_commit_msg_v0129.txt` + `git commit -F`（避免 GBK 乱码）；提交后已删除该临时文件，未入库。
- 命令：`git commit -F tools/_commit_msg_v0129.txt`
- 结果：`[main d0543e8] chore(release): v0.1.29-beta 发布准备(VERSION/README/说明) + 发布与核验记录`
- **commit hash（完整）：`d0543e86f346f769295e13d993c4005e2994c670`**
- 统计：`26 files changed, 6904 insertions(+), 14 deletions(-)`

提交信息全文：
```
chore(release): v0.1.29-beta 发布准备(VERSION/README/说明) + 发布与核验记录

- VERSION → 0.1.29-beta（第2行直链、第3行 sha256 更新为 f57f1687…3790）
- README/docs 同步 v0.1.29-beta；新增 docs/releases/v0.1.29-beta.md
- 新增发布/核验脚本与报告(tools/_*)
- 备注: v0.1.28-beta tag 因仓库 immutable releases 不可恢复; v0.1.29-beta Release 已建但附件受 immutable 限制未挂上
```

---

## 4. 推送（Push）

| 远端 | 命令 | 退出码 | 输出 |
|------|------|--------|------|
| GitHub (`github`) | `git -c credential.interactive=false push github main` | 0 | `To https://github.com/yohoten/acrpa.git` / `00cc4de..d0543e8  main -> main` |
| Gitee (`origin`) | `git -c credential.interactive=false push origin main` | 0 | `To https://gitee.com/yohoten/ACRPA.git` / `00cc4de..d0543e8  main -> main` |

两平台均 **推送成功**，非交互、无 force、未改历史。Gitee 回显 `Powered by GITEE.COM [1.1.23]`。

---

## 5. 复核（Verify）

- `git status -sb` → `## main...github/main`（工作区 clean，无 ahead/behind）
- `git ls-remote github refs/heads/main` → `d0543e86f346f769295e13d993c4005e2994c670  refs/heads/main`
- `git ls-remote origin refs/heads/main` → `d0543e86f346f769295e13d993c4005e2994c670  refs/heads/main`

结论：**两远端 `main` SHA 与本地 HEAD 一致（`d0543e8…c670`）**。

> 备注：本报告文件（`tools/_git_commit_push_v0129_report.md`）在提交/推送完成之后生成，属于 post-commit 产物，故以 untracked 状态存在于工作区，未纳入本次提交。

---

## 附：本轮改动说明是否已入库

- `VERSION → 0.1.29-beta`：✅ 已入库（`f57f1687…3790`）
- `README.md` / `docs/releases/v0.1.29-beta.md`：✅ 已入库
- 发布/核验脚本与报告（`tools/_*`）：✅ 已入库
- 关于 `v0.1.28-beta` tag 不可恢复、`v0.1.29-beta` Release 附件受 immutable 限制的说明：✅ 已随提交信息入库（正文第 4 条）
