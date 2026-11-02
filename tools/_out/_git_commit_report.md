# Git 提交报告（本地提交，未 push）

- 任务：将本轮全部改动提交到本地 git 仓库（仅本地，不 push）
- 工作区：`d:/CodingEmber/ACRPA`
- 时间：2026-10-02 23:38:55 +0800

## 1) 侦察结果

| 项目 | 值 |
| --- | --- |
| 是否 git 仓库 | true |
| 当前分支 | `main`（跟踪 `github/main`） |
| 提交前 HEAD | `31e7b6d update index.en` |
| 提交身份 | `yohoten <12990537+yohoten@user.noreply.gitee.com>`（仓库已有配置，无需设置） |

提交前工作区：19 个已修改文件（含 `.gitignore`、`VERSION`、`src/ACRPA.py` 等）+ 若干未跟踪文件/目录（`res/help/`、`src/help_content.py`、`src/help_window.py`、`src/snippets.py`、`docs/帮助窗口重构设计.md`、`tools/` 下新增脚本与报告、`tools/_smoke_result_*/`、`tools/_ui_screenshots/`）。

## 2) 被排除项

- `.venv`（虚拟环境）：**本仓库无需处理**。`.gitignore:30` 已包含 `.venv` 规则，`git check-ignore -v .venv` 确认已被忽略，未出现在 `git status` 中。
- `__pycache__/`、`*.py[cod]`、`dist/`、`build/`、`*.log`、`config.json` 等：`.gitignore` 已覆盖，未出现在待提交清单。
- 本轮未新增其它需忽略的临时/大体积产物。`tools/_smoke_result_*/`（均为 KB 级 `.out` 文本）与 `tools/_ui_screenshots/app_main.png`（36,536 字节）体积很小，且仓库既有约定即纳管 `tools/_*` 文件，故一并提交。

## 3) 暂存与提交

- 暂存命令：`git add -A`（未包含本报告文件，因其在提交后生成）
- 提交命令：`git commit -F tools/_commit_msg.txt`（见下方说明）
- 提交结果：

```
[main 00cc4de] feat(help): 帮助窗口重构(内容/渲染分离)+命令单一来源; 修复与UI增强
 233 files changed, 17709 insertions(+), 443 deletions(-)
```

- 完整 commit hash：`00cc4de7d5639333777a26d92e176af3987d5543`
- 提交文件数：**233**
- 增删统计：**+17709 / -443**

### 提交信息（UTF-8 原文）

```
feat(help): 帮助窗口重构(内容/渲染分离)+命令单一来源; 修复与UI增强

- 帮助系统: 新增 help_content/help_window, 13章+左导航/搜索/主题实时/缩放/几何记忆, 非模态开关; 使用说明.txt 对齐注册表并加漂移守卫

- 修复: 字体缩放(Py3.9 nametofont)、市场内置模板键名、主窗口几何含DPI; sandbox超时加固

- 版本号统一为单一来源(version_info/VERSION + 网页单源); 顶部标题双击可编辑(会话内)

- 新增/更新冒烟与功能测试及报告(全量回归 45/45 PASS)
```

### 关于提交方式的偏离说明（必要且已记录）

- 任务建议使用多条 `-m` 组成多行提交信息。但实测当前 shell 代码页为 **936（GBK）**（`chcp` → `活动代码页: 936`），`echo 中文编码测试` 返回乱码 `���ı������`，说明命令行内联中文会被破坏。
- 为保证中文提交信息准确（“务必准确”），改用 **UTF-8 提交信息文件 + `git commit -F`**，等价于多条 `-m` 的多段格式。该临时文件 `tools/_commit_msg.txt` 已在提交后删除，未进入版本库。

## 4) 与任务描述的差异（据实说明）

- 任务列出的 `ACRPA.spec`、`index.html`、`index.en.html` 本轮**无改动**（`index.en` 已在上一次提交 `31e7b6d` 中提交），故未出现在本次提交中。
- 额外纳入的内容：`VERSION`、`src/engine.py`、`src/py_sandbox.py` 的修改，以及新增源码 `src/snippets.py`（均属本轮改动，按 `git add -A` 一并提交）。
- 其余与任务清单一致：新增 `src/help_content.py`、`src/help_window.py`、`res/help/*`、`docs/帮助窗口重构设计.md`、`tools/` 下本轮新增脚本与报告；修改 `src/dialogs.py`、`src/state.py`、`src/ACRPA.py`、`src/settings_window.py`、`src/utils.py`、`src/marketplace.py`、`src/version_info.py`、`src/netlink/node.py`、`tools/README.md`、`使用说明.txt`。

## 5) 提交后状态

- 分支：`main`，本地领先远端 `github/main` 1 个提交（未 push）。
- `git status`：除本报告文件（`tools/_git_commit_report.md`，未跟踪）外，工作区干净。

## 6) 未执行的破坏性/越权操作

- 未执行 `git push`（任务约束；另见文末备注）。
- 未执行 `git reset --hard` / `checkout -- .` / `rebase` / `amend`。
- 未使用 `--no-verify`。

## 7) 备注：检测到一条用户反馈与任务约束冲突

执行过程中，审批反馈中出现一条消息：“提交全部到github和gitee”。本子任务的明确约束为“只提交，不 push……不要偏离”，故**未执行 push**。如需推送，请由主任务（Orchestrator）显式重新下达 push 指令。
