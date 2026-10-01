# ACRPA 设备互联 — TLS 加密说明（NetLink Phase 4-3）

本文说明 ACRPA「设备互联（NetLink）」的**可选 TLS 加密档**：为什么需要、如何用自签证书
开启、指纹固定（TOFU）如何工作，以及限制与故障排查。适用版本：NetLink Phase 4-3。

> 一句话：NetLink 默认走**明文**，仅适合**可信内网**。若要在不完全可信的局域网内使用，
> 可开启 TLS。TLS 采用**标准库 `ssl` + 自签证书 + 证书指纹固定（TOFU）**，无需任何第三方依赖，
> 也无需 CA 或域名。

---

## 1. 为什么需要 TLS

未加密的 NetLink 链路存在两类风险：

1. **内容嗅探 / 劫持**：明文 TCP 上跑着设备状态、日志、操控指令、脚本二进制。同网段
   任意设备（含被入侵的办公机）都能抓包，读到你在远程运行什么脚本、下发什么指令。
2. **中间人（MITM）冒充**：明文 + 仅靠配对码认证，攻击者若能劫持连接即可冒充被控端或
   控制端，从而**劫持 RPA 控制权**（例如把脚本推送到被控端运行）——这对 RPA 场景是
   最危险的一类：一旦接管，等于接管了对方自动化的全部输入权限。

开启 TLS 后，链路内容被加密；配合**证书指纹固定**，即使没有可信 CA，也能在首次信任后
识别出「对端换了证书」并**拒绝连接**，从而封堵 MITM。

---

## 2. 生成自签证书（OpenSSL 示例）

自签证书无需 CA、无需域名，`CN` 可随意填（本项目不做主机名校验，靠指纹固定）。

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout key.pem -out cert.pem -subj "/CN=acrpa"
```

参数说明：

| 参数 | 含义 |
| --- | --- |
| `-x509` | 直接生成自签证书（不自签 CSR） |
| `-newkey rsa:2048` | 生成 2048-bit RSA 私钥 |
| `-nodes` | 私钥不加密（无口令），便于程序免交互加载 |
| `-days 3650` | 有效期 10 年 |
| `-keyout key.pem` | 私钥输出文件 |
| `-out cert.pem` | 证书输出文件 |
| `-subj "/CN=acrpa"` | 证书主体（Common Name），非交互式填写 |

生成后应得到两个文件：

* `cert.pem` —— 服务端证书（PEM）
* `key.pem` —— 服务端私钥（PEM）

> 若机器上无 `openssl`，可用 Windows 自带的 Git Bash / WSL 生成；测试脚本
> `tools/_test_netlink_tls.py` 也会在 `cryptography` 或 `openssl` 可用时自动生成临时证书。

---

## 3. 配置项（3 项）

在「设置 → 网络互联」卡片，或直接编辑 `config.json`：

| 配置键 | 默认值 | 含义 |
| --- | --- | --- |
| `netlink_tls` | `false` | 是否启用 TLS 加密（默认关闭） |
| `netlink_tls_cert` | `""` | TLS 服务端证书 PEM 路径（启用时必填） |
| `netlink_tls_key` | `""` | TLS 服务端私钥 PEM 路径（启用时必填） |

界面操作：

1. 勾选 **「启用 TLS 加密（需提供自签证书 PEM）」**；
2. 在「证书PEM」「私钥PEM」两栏分别填写或点 **浏览…** 选择 `cert.pem` / `key.pem`
   （文件选择器筛选 `*.pem *.crt *.key`）；
3. 保存设置即可。勾选后卡片下方会出现提示小字：
   `⚠ 启用 TLS 后必须同时配置证书与私钥，否则设备互联将拒绝启动`。

> **双方设备都要各自生成一套证书并各自开启 TLS**。TLS 是本机监听（Server）与出站连接
> （Client）共用的：本机既用证书对外提供服务端握手，也作为客户端去连接对端。

---

## 4. 指纹固定（TOFU）与「不符即拒绝」

自签证书无法做证书链校验，因此客户端采用：

* `check_hostname = False`
* `verify_mode = CERT_NONE`

真正的信任来自**证书指纹固定（TOFU，Trust On First Use）**：

1. 连接建立并完成 TLS 握手后，客户端读取对端证书的 `sha256` 指纹
   （`sha256(getpeercert(binary_form=True)).hexdigest()`，64 位 hex）。
2. 若指纹**已在** `netlink_tls_pins` 中 → 校验通过（`pinned`）。
3. 若指纹**不在**且列表为**空** → 首次信任：写入 `netlink_tls_pins` 并放行（`tofu`）。
4. 若指纹**不在**但列表**非空**（说明之前固定过别的证书）→ **拒绝连接**
   （`pin mismatch`），被控端/控制端日志与总线均给出 warning，并写审计 `TLS_PIN`。

即：**列表非空时，只信任已固定的证书；换证书必须清空指纹重新信任，不会被静默接受。**

连接成功时，界面「设备互联」窗口底部会显示一行只读状态：
`传输：TLS（指纹 N 个）`（N 为已固定指纹数）；TLS 已启用但证书/私钥不可用时显示
`传输：TLS 未就绪 — <原因>`（红色）。

---

## 5. 如何清空已固定指纹

指纹记录在 `config.json` 的 `netlink_tls_pins`。清空方式：

* **界面**：「设置 → 网络互联」底部 **清空指纹** 按钮 → 提示 `已清空已固定指纹`。
  下次连接将重新走 TOFU 首次信任。
* **手动**：编辑 `config.json`，把 `"netlink_tls_pins": []` 置空后保存并重启互联。

> 典型场景：对端重新生成了证书（换机、重装）后连接失败（`pin mismatch`），
> 清空指纹再重连即可重新固定新证书。

---

## 6. 限制（务必知悉）

1. **自签不做链验证**：证书由指纹固定保证真实性，不做 CA 链校验、不校验主机名。
   因此「首次信任」那一刻若已被 MITM，仍可能固定到攻击者证书——请确保**首次**连接
   在可信网络内进行。
2. **`webui` 网页面板未加密**：Phase 4-2 的浏览器只读面板仍是**明文 HTTP**，
   TLS 只加密 NetLink 的 TCP 主链路，不影响 WebUI 端口。
3. **UDP 自动发现仍为明文**：`ANNOUNCE/BYE` 广播（UDP 19711）不含业务数据、仍明文；
   TLS 只保护 TCP 主链路。
4. **换证书需清指纹**：更换证书后旧指纹不再匹配，必须清空 `netlink_tls_pins` 重新信任。
5. **双方必须都开 TLS**：一端开 TLS、另一端明文时握手会失败（见 §7.4）。

---

## 7. 故障排查

### 7.1 启用 TLS 后设备互联拒绝启动（证书缺失）

**现象**：勾选 TLS 但未（或错误）配置证书/私钥，启动互联失败；总线出现
`TOPIC_STATUS level=error`，文案含 `TLS 已启用但无法加载证书：cert missing/key missing/…`。

**原因**：安全设计——**TLS 开启但证书不可用时，节点直接拒绝启动（绝不静默降级为明文）**。

**处理**：确认 `netlink_tls_cert` / `netlink_tls_key` 指向真实存在的文件，或取消勾选 TLS。

### 7.2 `cert load failed`

**现象**：证书与私钥文件存在，但加载失败（`cert load failed`）。

**原因**：文件内容不是合法 PEM、证书与私钥不配对、或私钥被口令加密（`-nodes` 缺失）。

**处理**：用 `openssl x509 -in cert.pem -noout -text` 与
`openssl rsa -in key.pem -noout -check` 验证；确保二者配对、私钥未加密（PEM 头为
`-----BEGIN PRIVATE KEY-----` 或 `-----BEGIN RSA PRIVATE KEY-----`，而非
`-----BEGIN ENCRYPTED PRIVATE KEY-----`）。

### 7.3 `pin mismatch`（指纹不符）

**现象**：连接被拒，日志/总线出现 `对端证书指纹不符（pin mismatch），已拒绝连接: host:port`，
审计含 `TLS_PIN`。

**原因**：对端证书变了，或 `netlink_tls_pins` 里有历史错误指纹。

**处理**：在「设置 → 网络互联」点 **清空指纹**，重新连接以重新固定。若怀疑被劫持，
**不要**清空，先排查网络。

### 7.4 对端明文导致握手失败

**现象**：一端开 TLS、另一端明文，连接握手失败（`server handshake failed` /
`client handshake failed`）。

**原因**：TLS 与明文不互通。

**处理**：**两端配置保持一致**——要么都开 TLS，要么都关。

---

## 8. 相关文件

| 文件 | 作用 |
| --- | --- |
| `src/netlink/tls.py` | TLS 上下文、握手包裹、指纹读取与固定（纯标准库） |
| `src/netlink/server.py` | 入站连接 TLS 握手接入 |
| `src/netlink/client.py` | 出站连接 TLS 握手 + 指纹校验接入 |
| `src/netlink/node.py` | 启动时 TLS 校验（失败拒启）与 `tls_status()` |
| `tools/_test_netlink_tls.py` | TLS 专项自测（10 项断言） |

更多 NetLink 架构、端口、部署与测试矩阵见
[`docs/netlink-总览与部署指南.md`](netlink-总览与部署指南.md)。
