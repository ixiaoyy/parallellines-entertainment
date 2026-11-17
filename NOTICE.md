# 上游来源与许可证

本项目维护部署和平台接入材料，不将上游聊天前后端改写为自有产品。

- 应用：[agnaistic/agnai](https://github.com/agnaistic/agnai)。上游许可证为 AGPL-3.0；固定源码引用见 `upstream.lock.json`。
- 应用镜像：由上游官方 GitHub Actions 构建，使用 `ghcr.io/agnaistic/agnaistic` 的完整提交标签与不可变摘要，不使用漂移的 `latest`。
- 数据库与缓存：使用 MongoDB 和 Redis 的官方镜像。各镜像附带组件与许可证仍按其自身来源处理，不将全部组件概括为同一种许可证。

保留上游品牌与许可信息。若后续提供修改版网络服务，须一并处理相应修改源码的交付要求；本目录中的部署材料不能替代修改版应用的对应源码。
