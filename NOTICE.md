# 上游来源与许可证

本项目维护原生产品的部署配置，不将上游聊天前后端改写为自有产品。镜像均固定为官方发布的不可变引用，来源和提交见 `upstream.lock.json`。

- [Element Web](https://github.com/element-hq/element-web)：AGPL-3.0；保留原生 Element 品牌和许可信息。
- [Synapse](https://github.com/element-hq/synapse)：上游提供 `AGPL-3.0-or-later OR LicenseRef-Element-Commercial` 许可选择；本项目使用公开官方版本，不因此取得厂商商业支持。
- [PostgreSQL](https://www.postgresql.org/about/licence/)：PostgreSQL License；[官方容器材料](https://github.com/docker-library/postgres)及镜像附带组件按各自许可证处理。

不将全部镜像组件概括为同一种许可证。未来若修改应用并提供网络服务，须处理相应修改源码的交付要求；本仓库的部署材料不能替代修改版应用的对应源码。
