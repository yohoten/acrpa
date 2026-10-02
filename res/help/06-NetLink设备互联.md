# NetLink 设备互联

NetLink 支持多台设备实时互联与远程操控：

- 实时多机监控，零配置 UDP 发现（含静态对端兜底）。
- PIN 配对 + `PBKDF2` / `nonce` 挑战应答 + 三档权限（仅观察 / 允许操控 / 允许接收脚本）。
- 远程运行 / 暂停 / 恢复 / 停止（脚本与工作流自动分叉）、远程截图。
- 脚本分发（分块 + `sha256` 校验 + 批量下发）、浏览器只读面板（令牌鉴权）。
- TLS 可选加密（自签 + TOFU 指纹固定）、审计日志。

详见：

- [NetLink 总览与部署指南](docs/netlink-总览与部署指南.md)
- [NetLink 配对与权限说明](docs/netlink-配对与权限说明.md)
- [NetLink 远程操控使用说明](docs/netlink-远程操控使用说明.md)
- [NetLink 脚本分发使用说明](docs/netlink-脚本分发使用说明.md)
- [NetLink 远程截图说明](docs/netlink-远程截图说明.md)
- [NetLink 网页只读面板说明](docs/netlink-网页只读面板说明.md)
- [NetLink 网页控制使用说明](docs/netlink-网页控制使用说明.md)
- [NetLink TLS 加密说明](docs/netlink-TLS加密说明.md)
