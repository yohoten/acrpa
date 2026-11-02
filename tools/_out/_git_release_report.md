# Gitee Release 发布报告 — v0.1.28-beta（重试）

- 仓库：`yohoten/ACRPA`（remote：`origin`）
- 目标 tag：`v0.1.28-beta`（Gitee 侧已存在，尚无 Release）
- 目标动作：仅创建 Release Notes，**不上传附件**（不调用 `attach_files`）
- 凭据来源：**本机文件** `C:\Users\yohoten\.gitee_token`（本次改为文件方式，不再走环境变量）
- 执行时间：本次子任务会话（重试）
- **最终结果：失败（阻塞于前置条件 — token 文件不存在），未创建 Release**

---

## 1. 凭据读取（仅本机文件；全程未打印任何 token 明文）

| 检查方式 | 命令（摘要） | 结果 |
|---|---|---|
| Python 路径存在性 | `.venv\Scripts\python.exe -X utf8 -c "...pathlib.Path(r'C:\Users\yohoten\.gitee_token').exists()..."` | `TOKEN_FILE_EXISTS False` |
| Python 内容长度 | 同上，`len(read_text(encoding='utf-8-sig').strip())` | `TOKEN_LEN 0` |
| 目录枚举 | `dir /a "C:\Users\yohoten\.gitee_token"` | `找不到文件`（Exit 1） |
| 通配枚举 | Python `glob.glob(r'C:\Users\yohoten\.gitee*')` | `[]`（空） |

结论：`C:\Users\yohoten\.gitee_token` **不存在**。已通过 **Python 存在性 + Python glob + cmd `dir /a`（含隐藏文件）** 三重独立确认，
可排除「编码 / 大小写 / 隐藏属性」等假阴性；父目录 `C:\Users\yohoten` 存在（`DIR_OK True`），排除路径拼写错误。

依据子任务规则「若文件不存在或内容为空 → 停止并如实报告，不要臆造、不要尝试其它凭据」，
**在读取阶段即停止**，未进行任何网络或写操作。全程未回显/打印任何 token 明文（文件不存在，无值可取）。

## 2. PAT 有效性校验（`GET https://gitee.com/api/v5/user`）

- 状态：**未执行**（被第 1 步阻塞）。
- 原因：无 token 可读取，未发起任何 HTTP 请求（以免泄露空/占位凭据或产生无效调用）。

## 3. tag 存在性 / 同名 Release 校验

- 状态：**未执行**（被第 1 步阻塞）。
- 计划调用：`GET .../repos/yohoten/ACRPA/tags`（确认 `v0.1.28-beta`）、`GET .../releases/tags/v0.1.28-beta`（应 404）。

## 4. 创建 Release（POST）

- 状态：**未执行**（被第 1 步阻塞）。
- 计划：`POST https://gitee.com/api/v5/repos/yohoten/ACRPA/releases`，form 参数
  `tag_name=v0.1.28-beta`、`target_commitish=main`、`name=ACRPA v0.1.28-beta`、
  `body=<docs/releases/v0.1.28-beta.md 全文>`、`prerelease=true`。
- 未发生任何写操作，未使用强制/覆盖语义，未创建 Release。

## 5. 附件上传

- 按要求**跳过**（用户决策：不上传附件），未调用 `attach_files`。

## 6. 复核（`GET .../releases/tags/v0.1.28-beta`）

- 状态：**未执行**（无 Release 可复核）。

## 7. html_url

- **N/A**（Release 未创建）。

## 8. 未改动项

- 未改动任何仓库文件、未提交、未推送；未触碰 GitHub 侧。

---

## 偏离说明

1. **改用 Gitee API v5（而非 `ge` CLI）**：`ge`（Gitee CLI）未安装，且其安装脚本为 bash，在 Windows `cmd.exe` 下不适用。
   故按主任务约定改用 **Gitee API v5**（通过 `.venv\Scripts\python.exe` + `requests`，`access_token` 作为参数或
   `Authorization: token <PAT>` 头）实现同等能力。此为**合理回退**。（注：因第 1 步阻塞，该回退路径本次未被实际执行。）
2. **凭据方式变更**：本次由「环境变量 `GITEE_TOKEN`」改为「本机文件 `C:\Users\yohoten\.gitee_token`」（Windows `%USERPROFILE%` 下的 dotfile）。
   上一轮报告记录的 `GITEE_TOKEN` 环境变量缺失问题已不再作为阻塞点；本轮阻塞点变为**该文件不存在**。

## 安全提示

- 发布完成后，建议删除本机凭据文件 `C:\Users\yohoten\.gitee_token`，避免访问令牌长期驻留本地明文存储。
- 该文件应确保不被提交入库（确认其不在仓库工作区内，且 `%USERPROFILE%` 路径天然位于仓库之外）。

## 遗留问题 / 需用户处理

- **阻塞根因**：本机文件 `C:\Users\yohoten\.gitee_token` 不存在（三重确认）。
- **建议操作**：在该路径创建文件，仅写入单行 PAT（可含 BOM/换行，脚本会以 `utf-8-sig` 读取并 `strip()`），例如：
  - 用记事本保存为 `C:\Users\yohoten\.gitee_token`（内容仅一行 PAT），或
  - PowerShell：`Set-Content -Path "$env:USERPROFILE\.gitee_token" -Value "<你的PAT>" -NoNewline -Encoding ascii`
- 重跑时请确保 PAT 具 `projects`（或等效）scope，对 `yohoten/ACRPA` 的 Release 拥有写权限。
- 完成后重跑本子任务；其余步骤（tag 校验 → 创建 → 复核）均已就绪，仅待凭据就位。
