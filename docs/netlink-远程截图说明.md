# NetLink 远程截图说明（Phase 4-1）

> 适用版本：ACRPA NetLink 多设备局域网互联 · Phase 4-1「远程截图观察（内嵌）」

## 1. 功能说明

「设备互联」窗口在选中一台**已认证**设备后，可点击 `[📷 截图]` 请求该设备抓取**主屏**画面：

* 被控端抓屏 → 等比缩小到指定宽度 → JPEG 编码 → base64 → 通过 `SCREENSHOT_DAT` 回传；
* 控制端在独立窗口 `远程截图 — <设备名>` 中内嵌显示（PIL + ImageTk）；
* 窗口顶部展示：`分辨率 WxH · 质量 60 · 大小 xx KB · 时间 HH:MM:SS`；
* 窗口按钮：`[刷新]`（重新请求）、`[保存为…]`（另存 `.jpg`）、`[关闭]`；
* 若运行环境缺少 Pillow / ImageTk（打包裁剪或无显示），自动**降级**：把 JPEG 写入临时文件，
  在窗口中显示路径并提供 `[打开文件]`，底部状态行提示 `已保存到 <路径>`。

协议消息（均沿用 4 字节长度前缀 + JSON 信封）：

| 类型 | 方向 | 说明 |
| --- | --- | --- |
| `SCREENSHOT_REQ` | 控制端 → 被控端 | 请求截图，`data` 可含 `max_width` / `quality` |
| `SCREENSHOT_DAT` | 被控端 → 控制端 | 截图结果，`data` 为 `screen.capture()` 的返回结构 |

控制端总线上发布事件主题 `netlink.screenshot`（`TOPIC_SCREENSHOT`），载荷：
`{node_id, ok, ts, width, height, format, data_b64, bytes, error}`。

## 2. 权限要求

* `SCREENSHOT_REQ` 属**操控类**消息，需要 `允许操控`（`control`）或更高（`script`）权限；
  仅有 `仅观察`（`observe`）权限时会被拒绝，控制端收到 `CMD_ERR{reason:"permission denied"}`。
* 在「已配对设备」区把目标设备的权限调整为 `允许操控` 或 `允许接收脚本` 后，`[📷 截图]` 按钮才会可用。
* 未认证设备无法截图（`未认证`）。

## 3. 节流与上限

* **节流**：同一被控端 1 秒内最多 1 张（`MIN_INTERVAL = 1.0`）。过频请求返回
  `ok=False, error="rate limited"`。
* **分辨率**：`max_width` 夹取到 `[64, 1280]`，默认 640；仅**缩小**，绝不放大小图。
* **质量**：JPEG 质量夹取到 `[10, 80]`，默认 60。
* **帧大小**：编码后 JPEG 原始字节 > 5 MB 时返回 `ok=False, error="payload too large"`
  （保证整帧 < 协议 `MAX_FRAME_BYTES` = 8 MB）。

## 4. 图像不落盘（仅内存编码）

被控端截图**全程在内存中编码**（`io.BytesIO`），不向磁盘写入任何文件；
base64 直接随 `SCREENSHOT_DAT` 回传。

仅在**控制端**窗口渲染遇到「无 Pillow / 无 ImageTk」的降级路径时，才会把 JPEG 写入系统临时目录
（文件名形如 `acrpa_shot_xxxx.jpg`），且窗口关闭时会自动删除该临时文件。

## 5. 隐私与合规提示

> ⚠️ **远程截图会采集被控端屏幕的全部内容**（可能包含密码、聊天、文档等敏感信息）。

* 请**仅对自有设备**或已获得明确书面授权的设备使用该功能；
* 请仅授予可信设备 `允许操控` 及以上权限，定期在「已配对设备」区复核并在必要时「移除」；
* 每次截图请求（包括被拒绝的请求）都会记录到被控端的审计日志
  （`logs/netlink_audit_YYYYMMDD.log`，字段 `cmd=SCREENSHOT_REQ`），请妥善保管审计文件；
* 请遵守所在地区法律法规及组织内部的信息安全制度。

## 6. 故障排查

| 现象 / `error` | 可能原因 | 处理建议 |
| --- | --- | --- |
| `pillow unavailable` | 被控端缺少 Pillow / ImageGrab | 安装/启用 Pillow（依赖 `Pillow>=9.0,<10.0`），或改用带 Pillow 的完整安装包 |
| `grab failed` | 无桌面会话（服务/无头运行）、会话被锁定、权限不足 | 在已登录的交互式桌面会话中运行被控端 |
| `rate limited` | 距上次截图不足 1 秒 | 稍候 1 秒后重试（窗口 `[刷新]` 间隔即可满足） |
| `payload too large` | 编码结果超过 5 MB（极少见） | 调低 `max_width` / `quality` 后重试 |
| `permission denied` | 当前设备权限为「仅观察」 | 在被控端「已配对设备」区调整为「允许操控」或以上 |
| 控制端窗口空白 | 控制端缺 Pillow / ImageTk | 走降级路径（临时文件 + `[打开文件]`），或安装带 Pillow 的完整包 |
| 状态行 `未找到到该设备的连接` | 目标设备未连接/已下线 | 确认设备在线并已完成配对认证后重试 |

## 7. 相关模块

* [`src/netlink/screen.py`](../src/netlink/screen.py)：截图内核（`capture()`）；
* [`src/netlink/node.py`](../src/netlink/node.py)：`SCREENSHOT_REQ/DAT` 分派、`request_screenshot()`、`TOPIC_SCREENSHOT`；
* [`src/netlink/security.py`](../src/netlink/security.py)：`SCREENSHOT_REQ → control` 权限映射；
* [`src/netlink_window.py`](../src/netlink_window.py)：`[📷 截图]` 按钮与查看窗口。
