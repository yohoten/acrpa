# ACRPA 设备互联（NetLink）— 总览与部署指南

> 收官文档。汇总 NetLink（Phase 1 → Phase 4-3）的架构、模块职责、配置全表、
> 权限矩阵、端口、双机部署步骤、测试矩阵与已知限制。

---

## 1. 架构总览

NetLink 是 ACRPA 的**多设备局域网互联**能力：任意一台设备既是「被控端」也是「控制端」，
节点之间**对等**（Peer-to-Peer），通过 UDP 广播互相发现，通过 TCP 双向连接交换状态、
指令、脚本与截图。

```
                 ┌──────────────────────── 设备 A（对等节点）────────────────────────┐
                 │                                                                  │
                 │   ┌───────────┐   ┌───────────┐   ┌───────────┐                  │
                 │   │ Discovery │   │  Server   │   │  Client   │                  │
                 │   │  UDP 19711│   │ TCP 19710 │   │ TCP 出站  │                  │
                 │   └─────┬─────┘   └─────┬─────┘   └─────┬─────┘                  │
                 │         │               │               │                        │
                 │         └──────┬────────┴───────┬───────┘                        │
                 │                ▼               ▼                                │
                 │            ┌───────── NetBus（发布/订阅）─────────┐               │
                 │            │                                        │               │
                 │      ┌─────┴─────┐  ┌─────────┐  ┌─────────┐  ┌────┴────┐          │
                 │      │   Agent   │  │ Control │  │Transfer │  │ Screen  │          │
                 │      │ 状态/日志 │  │ 远程操控 │  │ 脚本分发 │  │ 远程截图 │          │
                 │      └───────────┘  └─────────┘  └─────────┘  └─────────┘          │
                 │            ▲               ▲               ▲                      │
                 │      ┌─────┴───────────────┴───────────────┴─────┐                │
                 │      │   NetLinkNode（编排）+ Security + Audit    │                │
                 │      └───────────────────┬───────────────────────┘                │
                 │                          │  TLS（可选，tls.py）                     │
                 └──────────────────────────┼──────────────────────────────────────┘
                                            │
                        ┌───────────────────┴────────────────────┐
                        │  WebUI 面板（TCP 19712，默认明文/可 TLS） │
                        │  GUI：netlink_window / settings_window   │
                        └──────────────────────────────────────────┘

                 ┌──────────────────────── 设备 B（对等节点）────────────────────────┐
                 │        同构：Server + Client + Agent + Discovery + Control        │
                 │              + Transfer + Screen + WebUI                         │
                 └──────────────────────────────────────────────────────────────────┘
```

要点：

* **对等节点** = `Server`（入站）+ `Client`（出站）+ `Agent`（上报）+ `Discovery`（发现）
  + `Control` + `Transfer` + `Screen` + `WebUI`，由 `NetLinkNode` 统一编排。
* 进程内所有模块通过 `NetBus` 发布/订阅解耦；节点间通过 `protocol` 定义的消息交互。
* **TLS 为可选档**（默认关闭）：开启后 TCP 主链路加密；UDP 发现仍明文，
  WebUI 默认明文、可选 HTTPS（**有限控制开启时强制 HTTPS**）。

---

## 2. 模块职责表

| 文件 | 职责 |
| --- | --- |
| `src/netlink/__init__.py` | 包门面：`start_netlink / stop_netlink / get_node / get_bus / start_webui / stop_webui / send_command / push_script* / request_screenshot` 等对外 API |
| `src/netlink/protocol.py` | 消息编解码、分帧（`FrameReader`）、消息类型/主题白名单常量 |
| `src/netlink/connection.py` | 单条连接对象（`recv/sendall/close` + 读线程回调）与 `_nl_log` |
| `src/netlink/bus.py` | 进程内发布/订阅总线（`NetBus`） |
| `src/netlink/discovery.py` | UDP 广播发现（`ANNOUNCE/BYE`），维护对端表 |
| `src/netlink/server.py` | TCP 入站监听 + accept 循环（每条连接包成 `Connection`） |
| `src/netlink/client.py` | TCP 出站连接池 + 保活重连（`set_targets` 期望对端集合） |
| `src/netlink/agent.py` | 采集本机运行状态/日志/定时状态并按订阅上报 |
| `src/netlink/node.py` | 节点编排：装配上述子组件，处理消息路由、权限门控挂钩 |
| `src/netlink/security.py` | 配对码、认证（challenge/resume）、凭据库（Windows Credential）、指纹 |
| `src/netlink/control.py` | 远程操控内核：`CMD_*` 门控校验 + 串行执行 + 首次确认 + 审计 |
| `src/netlink/audit.py` | 审计日志（`logs/netlink_audit_YYYYMMDD.log`） |
| `src/netlink/transfer.py` | 脚本分发：清单请求 + 二进制分块（BLOB）传输 |
| `src/netlink/screen.py` | 远程截图：请求/回传（分块） |
| `src/netlink/webui.py` | 浏览器只读监控面板（HTTP，令牌鉴权） |
| `src/netlink/tls.py` | **TLS 可选档**：上下文构造、握手包裹、指纹读取与固定（TOFU） |
| `src/netlink_window.py` | GUI「设备互联」窗口（设备列表、配对、操控、传输、截图、TLS 状态行） |

---

## 3. 配置项全表（`netlink_*`）

均为 `config.json` 中的扁平键（`state.py` schema 定义），可在「设置 → 网络互联」界面修改。

| 配置键 | 默认值 | 含义 |
| --- | --- | --- |
| `netlink_enabled` | `false` | 启用设备互联 |
| `netlink_port` | `19710` | TCP 监听端口 |
| `netlink_device_name` | `""` | 本机显示名（空 = 自动取主机名） |
| `netlink_perm_level` | `"observe"` | 本机对外默认权限：`observe/control/script` |
| `netlink_peers` | `[]` | 已配对设备白名单 |
| `netlink_static_peers` | `[]` | 手动填写的对端 `IP:PORT`（发现失败时兜底） |
| `netlink_autodiscover` | `true` | UDP 自动发现开关 |
| `netlink_discovery_port` | `19711` | UDP 发现端口 |
| `netlink_tls` | `false` | **TLS 可选档（Phase 4-3）** |
| `netlink_ui_window` | `""` | 设备互联窗口几何记忆 |
| `netlink_require_auth` | `true` | 是否启用配对认证（默认开启） |
| `netlink_pin_ttl` | `600` | 配对码有效期（秒） |
| `netlink_confirm_control` | `true` | 首次远程操控需被控端弹窗确认 |
| `netlink_confirmed_peers` | `[]` | 已确认过操控的设备指纹（记住此设备） |
| `netlink_script_dir` | `""` | 允许远程运行的脚本目录（空 = 程序目录/scripts） |
| `netlink_audit_days` | `0` | 审计日志保留天数（0 = 跟随 `LOG_RETENTION_DAYS`） |
| `netlink_web_enabled` | `false` | 启用浏览器只读监控面板 |
| `netlink_web_port` | `19712` | 面板监听端口 |
| `netlink_web_bind` | `"0.0.0.0"` | 面板监听地址（`0.0.0.0` = 允许同网段访问） |
| `netlink_web_control` | `false` | **面板有限控制（Phase 4-2 演进）**：启用 `run`/`stop`（强制 HTTPS） |
| `netlink_web_control_ttl` | `300` | 控制会话 TTL（秒），滑动续期；范围 60–3600 |
| `netlink_web_tls` | `false` | 面板 HTTPS；**控制开启时被强制为 `true`** |
| `netlink_web_confirm_control` | `false` | 本地面板控制是否也需桌面弹窗二次确认 |
| `netlink_web_allow_remote_control` | `false` | 控制开启且 `bind=0.0.0.0` 时是否允许局域网控制（默认收窄 `127.0.0.1`） |
| `netlink_tls_cert` | `""` | TLS 服务端证书 PEM 路径（启用 TLS 时必填） |
| `netlink_tls_key` | `""` | TLS 服务端私钥 PEM 路径（启用 TLS 时必填） |
| `netlink_tls_pins` | `[]` | 已固定的对端证书指纹（sha256 hex，TOFU） |

> 同名的大写常量为运行时内存值（如 `state.NETLINK_PORT`）。详见
> [`docs/netlink-TLS加密说明.md`](netlink-TLS加密说明.md)。

---

## 4. 权限矩阵

被控端为**每台已配对设备**单独授予一档权限，方向为「远程设备 → 本机」。
新配对默认 `observe`。判定**实时进行**（每条消息现查，不缓存到连接建立时刻）。

| 消息类型（`protocol.T_*`） | `observe` | `control` | `script` |
| --- | :---: | :---: | :---: |
| `SUBSCRIBE` / `UNSUBSCRIBE`（订阅 state/log/sched） | ✅ | ✅ | ✅ |
| `STATE_SYNC` / `LOG_TAIL` / `SCHED_STATUS`（被动接收） | ✅ | ✅ | ✅ |
| `PING` / `PONG` / `HELLO` / `AUTH*` / `PEER_*`（会话类） | ✅ | ✅ | ✅ |
| `CMD_RUN` / `CMD_PAUSE` / `CMD_RESUME` / `CMD_STOP` | ❌ | ✅ | ✅ |
| `CMD_RUN_SCRIPT`（运行指定脚本，路径安全校验） | ❌ | ✅ | ✅ |
| `SCRIPT_LIST_REQ` / `SCRIPT_LIST_RES`（脚本清单） | ❌ | ❌ | ✅ |
| `BLOB_BEGIN` / `BLOB_CHUNK` / `BLOB_END`（脚本二进制分块） | ❌ | ❌ | ✅ |
| `SCREENSHOT_REQ` / `SCREENSHOT_DAT`（远程截图） | ❌ | ✅ | ✅ |

图例：✅ 允许；❌ 被拒（被控端回 `permission denied`）。

说明：`control` 含 `observe` 全部能力；`script` 含 `control` 全部能力（逐级包含）。
权限为 `observe` 时，控制端的操控/推脚本按钮保持灰禁用。

---

## 5. 端口表与防火墙

| 用途 | 协议 | 端口（默认） | 方向 | 说明 |
| --- | --- | --- | --- | --- |
| NetLink 主链路 | TCP | `19710` | 双向 | 状态/指令/脚本/截图（TLS 开启时在此加密） |
| UDP 自动发现 | UDP | `19711` | 双向广播 | `ANNOUNCE/BYE`（明文） |
| WebUI 面板 | TCP | `19712` | 入站 | 浏览器访问（**默认明文 HTTP**；可选 HTTPS；**控制开启时强制 HTTPS**；默认关闭） |

防火墙放行（Windows，管理员 cmd）：

```cmd
netsh advfirewall firewall add rule name="ACRPA NetLink TCP" dir=in action=allow protocol=TCP localport=19710
netsh advfirewall firewall add rule name="ACRPA NetLink UDP" dir=in action=allow protocol=UDP localport=19711
netsh advfirewall firewall add rule name="ACRPA NetLink WebUI" dir=in action=allow protocol=TCP localport=19712
```

> 仅在需要 WebUI 时放行 19712。若禁用自动发现，可只靠 `netlink_static_peers` 直连，
> 无需放行 UDP。

---

## 6. 双机部署步骤

### 设备 A（被控端）

1. 「设置 → 网络互联」勾选 **启用设备互联**，确认 TCP 端口（默认 19710）与显示名。
2. 如需加密：勾选 **启用 TLS**，填入本机生成的 `cert.pem` / `key.pem`（见 TLS 说明文档）。
3. 保存设置，打开「设备互联」窗口点 **启动互联**（状态条出现 `● 运行中`）。
4. 点 **配对码** 生成 6 位 PIN，告知操作者（默认权限 `observe`）。
5. 需要时在「已配对设备」区域上调权限并点 **应用**（即时生效）。

### 设备 B（控制端）

1. 「设置 → 网络互联」勾选 **启用设备互联**；**TLS 配置须与 A 一致**（都开或都关）。
2. 保存后打开「设备互联」窗口点 **启动互联**。
3. 在设备列表选中 A（自动发现，或「手动添加」A 的 `IP:19710`），点 **🔗 配对**，输入 A 的 PIN。
4. 配对成功后即可查看状态/日志；若 A 已授予 `control`，可远程运行/暂停/停止。
5. 若 A 授予 `script`，可选中 A 点 **推脚本** 分发脚本；**截图** 需 `control`。

> 两端防火墙需放行对应端口；跨网段时优先用 `netlink_static_peers` 或确保 UDP 广播可达。

---

## 7. 测试矩阵

`tools/` 下 16 个 NetLink 相关脚本（`python tools/<脚本>`，退出码 0 = PASS）：

| # | 脚本 | 覆盖点 | 当前结果 |
| --- | --- | --- | --- |
| 1 | `_test_netlink_tls.py` | **Phase 4-3**：TLS 上下文/握手/指纹固定/拒启/拒绝/明文回归（10 项） | PASS |
| 2 | `_test_netlink_webui.py` | Phase 4-2：WebUI 令牌、启停幂等、端口释放、数据联动 | PASS |
| 3 | `_test_netlink_screenshot.py` | Phase 3-2：远程截图请求/分块回传/缺失对端 | PASS |
| 4 | `_test_netlink_phase3_e2e.py` | Phase 3 端到端：脚本批量推送、隔离、skip、审计 | PASS |
| 5 | `_test_netlink_transfer.py` | Phase 3-1：脚本清单、分块、SHA 校验、并发、NL_MODULES=17 交叉 | PASS |
| 6 | `_test_netlink_phase2_e2e.py` | Phase 2 端到端：配对 + 操控 + 权限 + 审计（含 GUI） | PASS |
| 7 | `_test_netlink_control.py` | Phase 2-2：操控门控、worker、首次确认、审计路径 | PASS |
| 8 | `_test_netlink_auth.py` | Phase 2-1：配对码、挑战/证明、凭据库、锁定 | PASS |
| 9 | `_test_netlink_2node.py` | Phase 1：双节点握手、订阅、线程收敛 | PASS |
| 10 | `_test_netlink_e2e.py` | Phase 1 端到端：HELLO/SUBSCRIBE/STATE、bus 路由 | PASS |
| 11 | `_smoke_netlink.py` | 门面冒烟：`start_netlink/get_node/stop_netlink` | PASS |
| 12 | `_smoke_netlink_window.py` | 「设备互联」窗口冒烟（有显示时） | PASS |
| 13 | `_smoke_netlink_pairing_ui.py` | 配对 UI 冒烟 | PASS |
| 14 | `_smoke_netlink_control_ui.py` | 操控 UI 冒烟 | PASS |
| 15 | `_smoke_netlink_transfer_ui.py` | 脚本分发 UI 冒烟 | PASS |
| 16 | `_verify_netlink_package.py` | 打包交叉验证：分析产物含 **17/17** netlink 模块（exe 存活项允许 `[WARN]`） | PASS / 17:17 |

> 「当前结果」为 Phase 4-3 全量回归实测（详见同批次验收记录）。所有脚本均：不弹窗、
> 带超时、单脚本 < 60s、`state.CONFIG_PATH` 指向临时文件并还原、端口 199xx 段并释放。

---

## 8. 已知限制与后续路线

**已知限制**

1. **UDP 发现仍为明文；WebUI 默认明文、可选/强制 HTTPS**：TLS 覆盖 TCP 主链路（19710）；
   面板默认明文，勾选「面板启用 HTTPS」或开启**有限控制**时强制 HTTPS（复用同一套证书）。
2. **自签证书不做链验证**：信任建立在 TOFU 首次固定上；首次连接务必在可信网络进行。
3. **换证书需清空 `netlink_tls_pins`**：否则连接被拒（`pin mismatch`）。
4. **凭据库依赖 Windows**：免配对重连依赖 Windows 凭据库可写；受限账户下可能退化。
5. **仅面向内网**：跨公网 / 不可信网络不建议直接使用。

**后续路线（建议）**

* WebUI 可选/强制 HTTPS **已实现**（复用互联证书）；后续可加反向代理支持与证书自动生成。
* 面板「有限控制」**已实现**（仅 `run`/`stop`）；后续可评估更多白名单动作（需重新做威胁建模）。
* UDP 发现引入轻量签名/共享密钥，防止伪造 ANNOUNCE。
* 指纹固定支持「按对端分片」存储与管理界面。
* 引入证书自动轮换与「指纹变更二次确认」交互。

---

## 9. 相关文档

* [`docs/netlink-TLS加密说明.md`](netlink-TLS加密说明.md) —— TLS 开启、指纹固定、故障排查
* [`docs/netlink-配对与权限说明.md`](netlink-配对与权限说明.md) —— 配对码、权限分级、撤销
* [`docs/netlink-远程操控使用说明.md`](netlink-远程操控使用说明.md) —— 远程运行/暂停/停止
* [`docs/netlink-脚本分发使用说明.md`](netlink-脚本分发使用说明.md) —— 脚本清单与推送
* [`docs/netlink-远程截图说明.md`](netlink-远程截图说明.md) —— 远程截图
* [`docs/netlink-网页只读面板说明.md`](netlink-网页只读面板说明.md) —— WebUI 面板（只读 + 可选有限控制）
* [`docs/netlink-网页控制使用说明.md`](netlink-网页控制使用说明.md) —— 手机看进度 / 一键停止
* [`docs/netlink-phase1-验收清单.md`](netlink-phase1-验收清单.md) / [`docs/netlink-phase2-验收清单.md`](netlink-phase2-验收清单.md) —— 阶段验收
