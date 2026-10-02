# NetLink 网页面板说明（只读 + 可选有限控制）（Phase 4-2 演进）

> 文件名 `netlink-网页只读面板说明.md` 为**兼容既有链接**而保留；本文件内容已覆盖
> 「默认只读」与「可选有限控制（run/stop）」两种形态。

## 一、功能定位

网页面板让**不安装 ACRPA 的人**（主管 / 同事）用**手机、平板或电脑浏览器**即可
实时查看本机与已连接设备的运行状态，适合「远程看进度」这类轻量场景：

- 本机名称、运行状态、当前连接设备数；
- 每台设备的：状态（运行中 / 已暂停 / 空闲）、当前脚本、进度（行 / 总行）、
  循环（第 N 轮 / 总轮数）、已运行时长、定时任务下次执行时间、权限；
- 点击任意设备行，展开该设备**最近 200 条日志**；
- 页面每 2 秒自动刷新；服务停止时页面顶部显示「连接已断开」；
- **默认只读**；可由本机所有者在 GUI 中**可选**开启「有限控制」——
  **仅** `run`（运行当前已加载脚本）/ `stop`（停止当前运行），
  且必须先用**控制 PIN** 解锁并处于**强制 HTTPS** 之下。

> ⛔ **始终不开放**：`pause` / `resume`、远程脚本推送、远程截图、配对/权限变更。
> 这些只能在本机 ACRPA「设备互联」窗口（或需相应权限的远程设备）中操作。

---

## 二、启用步骤（只读）

1. 打开「设置 → 网络互联」卡片；
2. 先勾选 **启用设备互联**（面板依赖互联节点，未启动时提示「请先启动互联」）；
3. 勾选 **启用浏览器只读监控面板**；
4. 填写 **端口**（默认 `19712`）；
5. 选择 **监听范围**：
   - `所有网卡（同网段可访问）` = `0.0.0.0`：局域网内其它设备可访问（默认）；
   - `仅本机` = `127.0.0.1`：只有本机浏览器可访问（最安全）。

设置会自动保存并即时生效。也可以打开「设备互联」窗口，点击底部 **[🌐 网页面板]**，
在弹窗中直接启用 / 停用、复制地址、打开浏览器、重新生成令牌，
并管理**有限控制**（见第五节）。

---

## 三、访问链接与令牌

- 链接形如：`http://192.168.1.5:19712/?token=xxxxxxxx`
  （启用面板 HTTPS / 有限控制后为 `https://…`）
- 复制方式：在 **[🌐 网页面板]** 弹窗中选中地址栏 → 点 **[复制地址]**（写入剪贴板），
  或点 **[在浏览器打开]** 直接在本机打开。
- 令牌保存在哪里：**Windows 凭据库**（target 为 `ACRPA/netlink/web-token`），
  **绝不写入 `config.json`**，配置文件里看不到明文。
- 令牌如何使用：三种方式任选其一，实际只需把整条链接发给对方即可——
  1. 链接里的 `?token=xxxx`（浏览器访问后会自动写入 Cookie）；
  2. Cookie `nl_token`；
  3. 请求头 `Authorization: Bearer xxxx`。
- **重新生成令牌**：在弹窗点 **[重新生成令牌]** 并确认后，**旧链接立即失效**，
  必须把新链接重新发给对方。

---

## 四、只读边界（GET 接口）

面板暴露以下 **GET** 接口，全部需要令牌（L1）：

| 路径 | 返回 |
|---|---|
| `/` | 单页 HTML 监控页（内联样式与脚本，无外部资源） |
| `/api/summary` | `{"node": 本机快照, "counts": {"total": N, "online": M}}` |
| `/api/peers` | `{"peers": [{设备信息, "state": 状态快照, "perm": 权限}]}` |
| `/api/state` | `{"node": 本机快照, "peers": {node_id: 状态快照}}` |
| `/api/logs?node=..&n=200` | `{"node_id": .., "lines": [...]}`（`n` 上限 500） |
| `/api/sched` | `{"node": 本机定时, "peers": {node_id: 定时状态}}` |
| `/api/status` | `{"status": 最新状态提示, "cmd_results": [...], "transfers": [...]}` |
| `/api/control/status` | 控制状态（见第五节；**不需要**解锁） |

- 除控制端点外的**写请求**（POST / PUT / DELETE 等）→ `405 method not allowed`；
- 未知路径 → `404 not found`；
- 无令牌 / 错误令牌 → `401 unauthorized`（**不回显期望令牌**）。

> **免令牌静态端点（PWA 资源，无数据、无密钥）**：`/manifest.webmanifest`、`/sw.js`、
> `/offline.html`、`/favicon.ico`、`/icons/*`。这些资源不含任何业务数据，浏览器获取
> manifest 与注册 Service Worker 时默认不带凭据，因此**不要求令牌**；所有数据仍走
> `/api/*` 并需令牌。

---

## 五、可选有限控制（Phase 4-2 演进）

### 5.1 三层门槛

1. **L1 令牌（读）**：同第四节，完全沿用。
2. **L3 强制 HTTPS**：开启控制即强制面板 HTTPS（复用 `netlink_tls_cert` / `netlink_tls_key`）。
   **证书不可用时拒绝启用控制，绝不回落明文**。
3. **L2 控制会话（写）**：由**控制 PIN** 解锁的短期会话（HttpOnly + Secure + SameSite=Strict
   Cookie `nl_ctl`），并叠加 `/api/control` 请求必须携带的 **CSRF 头 `X-NL-CSRF`**。

### 5.2 控制 PIN（仅 GUI 设置）

- 规格：**6 位数字**；存储于 **Windows 凭据库** target
  `ACRPA/netlink/web-control-pin`，格式 `pbkdf2$<salt>$<hash>`（PBKDF2-HMAC-SHA256，
  10 万轮），**绝不落 `config.json`、绝不落日志**。
- 设置/重置入口：**仅** [设备互联 → 🌐 网页面板] 弹窗的 **[设置/重置控制 PIN]**
  与「设置 → 网络互联」卡。**面板自身不提供任何 PIN 设置接口**（避免未解锁即可改 PIN）。
- 防暴力：连续失败 5 次触发锁定（300s 起指数退避，上限 1h）；锁定期间**正确 PIN 也被拒**
  （返回 `429` + `Retry-After`）。

### 5.3 配置键

| 配置键 | 默认 | 说明 |
| --- | --- | --- |
| `netlink_web_control` | `false` | 启用面板有限控制（`run`/`stop`） |
| `netlink_web_control_ttl` | `300` | 控制会话 TTL（秒），滑动续期；范围 60–3600 |
| `netlink_web_tls` | `false` | 面板 HTTPS；**控制开启时被强制为 `true`** |
| `netlink_web_confirm_control` | `false` | 本地面板控制是否也需桌面弹窗二次确认 |
| `netlink_web_allow_remote_control` | `false` | 控制开启且 `bind=0.0.0.0` 时是否允许局域网控制（默认收窄为 `127.0.0.1`） |

### 5.4 控制接口

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/control/status` | GET | 状态：`enabled/tls/scheme/ttl/unlocked/expires_in/csrf_required/last_action/pin_locked/retry_after/bind/remote_control_allowed/running/has_script` |
| `/api/control/unlock` | POST | `{"pin":"123456"}` → `200 {"ok":true,"csrf":"…","expires_in":300}` + `Set-Cookie: nl_ctl=…` |
| `/api/control/lock` | POST | `{}` → `200 {"ok":true,"locked":true}`（幂等） |
| `/api/control` | POST | `{"action":"run"\|"stop"}` + 头 `X-NL-CSRF: <csrf>` → `{"ok":…,"action":…,"status":…,"detail":…,"mode":…}` |

**错误码（稳定英文 reason）**

| 条件 | HTTP | `error` |
| --- | --- | --- |
| 无 / 错误令牌 | 401 | `unauthorized` |
| 明文 HTTP 写请求（防御层） | 403 | `plaintext rejected` |
| 面板未启用控制 | 404 | `control disabled` |
| PIN 错误 | 401 | `pin invalid` |
| PIN 锁定中 | 429 | `pin locked`（含 `Retry-After`） |
| 请求体非法 / PIN 格式非法 | 400 | `bad request` |
| 未解锁 / 会话过期 | 403 | `control locked` |
| CSRF 缺失或不匹配 | 403 | `csrf mismatch` |
| `action` 不在白名单（含 `pause`/`resume`） | 400 | `action not allowed` |
| `run` 时已在运行 | 409 | `already running` |
| `run` 时正在录制 | 409 | `recording in progress` |
| `run` 时无脚本 | 409 | `no script selected` |
| 执行等待超时 | 504 | `execution timeout` |
| 执行抛错 | 200 | `status="failed"` |
| `stop` 而未运行 | 200 | 幂等成功（`detail=not running`） |

### 5.5 审计

复用既有 `AuditLog`（`<config>/logs/netlink_audit_YYYYMMDD.log`），单点写盘、不新增日志文件。
事件：`WEB_UNLOCK`、`WEB_PIN_FAIL`、`WEB_LOCK`、`WEB_REJECT`、`WEB_PIN_SET`、
`WEB_SESSION_IP_CHANGED`；`run`/`stop` 实到内核后由内核产出 `CMD_RUN` / `CMD_STOP`。
面板来源的 `actor` 统一为 `web-panel|<ip>`。

---

## 六、PWA（安装到主屏）与离线快照

- 面板提供 `manifest.webmanifest`（`display=standalone`）、Service Worker、
  `offline.html` 与 192/512 图标，满足「添加到主屏」条件。
- **安全上下文要求**：Service Worker 与安装仅在 `https://` 或 `http://localhost`
  （`127.0.0.1` 同）下可用。**明文 + 局域网 IP** 时安装按钮隐藏并给出提示。
- **自签证书**：浏览器会提示「不安全」，需手动信任/继续（**不影响**其被视为安全上下文，
  SW 仍可注册）。
- **离线快照**：页面每次成功刷新后，把 `{ts, summary, peers, sched}` 写入
  **`localStorage`（键 `acrpa.snap.v1`）**；离线打开时展示「⚠ 离线：显示最后快照」，
  并注明快照时间。**快照不含访问令牌，也不含日志明细**。
- 缓存策略：`/api/*` **一律 network-only（不缓存、不回退）**；导航请求 network-first，
  失败回退 `offline.html`；`/sw.js` 不缓存以便更新；其它同源静态资源 cache-first。

> PWA 首次从主屏打开时链接**不含令牌**（`start_url=/?src=pwa`），会得到 `401`。
> 这是预期的：本机生成的完整链接（含 `token=`）请从 GUI 复制后使用；
> 安装能力与令牌鉴权相互独立。

---

## 七、安全提示

- 面板**默认关闭**；只有手动启用后才会监听端口。
- **令牌等同密码**：拿到链接的人即可查看运行状态与日志，请勿外泄、勿发到公开群。
- 建议**仅在可信内网**启用；如无跨设备需求，把监听范围设为 `仅本机`（`127.0.0.1`）。
- **默认明文 HTTP**（未启用 TLS 时）；**启用有限控制时强制 HTTPS**，不会明文暴露控制。
- 控制开启且 `bind=0.0.0.0` 时**默认收窄为 `127.0.0.1`**；确需局域网控制必须显式开启
  `netlink_web_allow_remote_control`（并在 UI 二次确认）。
- 定期点 **[重新生成令牌]** 可让历史链接失效；遗忘控制 PIN 可在 GUI 中**重置/清除**
  （重置会同时清空全部控制会话）。

---

## 八、故障排查

1. **浏览器 / curl 返回 401 unauthorized**
   - 令牌错误或已过期（被重新生成过）。用弹窗 **[复制地址]** 重新获取整条含 `token=`
     的链接；或核对 Cookie `nl_token` / 请求头 `Authorization: Bearer`。

2. **启用失败 / 启动无反应**
   - 端口被占用（如已被其它程序或上一次未释放）。换一个端口，或先停用再启用；
     也可在「设备互联」窗口底部状态行查看失败提示。
   - 若已启用「有限控制」，需先配置**证书与私钥 PEM**，否则面板**拒绝启用**
     （弹窗会显示具体原因，如 `cert missing` / `key missing` / `cert load failed`）。

3. **页面显示「连接已断开」**
   - 说明面板服务已停止（被停用、互联已停止，或进程退出）。重新在设置里启用，
     或再次点 **[🌐 网页面板] → [启用]**。

4. **手机 / 其它设备打不开**
   - 检查 Windows 防火墙是否放行该端口（入站规则）；
   - 确认监听范围不是 `仅本机`（`127.0.0.1` 只允许本机，必须是
     `所有网卡（同网段可访问）`）；
   - 确认手机与电脑在同一网段，并使用电脑的**局域网 IP**（链接中的主机名）。

5. **控制按钮点击无效 / 提示未解锁**
   - 先输入 6 位控制 PIN 点 **[解锁]**；会话有 TTL（默认 300s），到期自动回到「已锁定」。
   - 若提示 `already running` / `no script selected`，说明当前状态不允许该动作（属预期）。

6. **无法安装到主屏 / 无离线**
   - 需通过 `https://` 或 `http://localhost` 访问；明文 + 局域网 IP 不满足安全上下文。

---

## 九、相关文档

* [`docs/netlink-网页控制使用说明.md`](netlink-网页控制使用说明.md) —— 手机看进度 / 一键停止的开启步骤
* [`docs/netlink-TLS加密说明.md`](netlink-TLS加密说明.md) —— 面板与互联共用的 TLS/自签证书
* [`docs/netlink-总览与部署指南.md`](netlink-总览与部署指南.md) —— 端口、配置键、部署
* [`docs/netlink-配对与权限说明.md`](netlink-配对与权限说明.md) —— 配对与权限层次

> **未实现项（明确标注）**：本期**不含**自签证书自动生成（需用户用 `openssl` 生成）、
> 面板侧的作者签名/脚本签名校验。控制白名单**仅** `run`/`stop`。
