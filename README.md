# 平行线多人聊天模块

基于官方 [Element Web](https://github.com/element-hq/element-web) 与 [Synapse](https://github.com/element-hq/synapse) 的独立部署项目，PostgreSQL 保存原生账号和聊天数据。完整使用上游聊天界面、房间、私聊、成员权限和加密流程，不重新实现聊天前后端。

## 部署边界

- 网页入口：`https://tavern.pingxingxian.space`。
- Homeserver：`https://matrix.pingxingxian.space`；身份形如 `@用户名:matrix.pingxingxian.space`，此身份域初始化后不能直接改名。
- 首期使用原生独立账号和邀请码注册；现已确认接入主站登录，接入材料仍待发布。保留 Element 原生布局、品牌、语言选择、主题、Labs 与设备验证。
- 不接入 Bot、模型 Key 或平台余额，不全局关闭加密。Chat 侧栏入口由 Chat 独立发布，Tavern 不接管现有 Chat 导航。
- 不替换现有 Chat、主站、身份桥或模型网关，不复用它们的数据和密钥。
- 按用户要求直接生产运行和验收，不在本机启动应用、构建或业务测试。

## 当前状态

**站点与服务器端收尾已完成。** 两域 HTTPS、源站证书、Matrix 接口、跨域及管理员 API 公网隔离检查已通过；原生管理员和首批邀请码已初始化。专用证书续期任务已安装并完成原生命令、来源权限、调度与当前路由检查；新证书未到期，本次没有强制换证或无故重载。浏览器注册、双账号房间消息及设备恢复尚未验收，不能把接口健康当成完整聊天验收。

实际版本、操作结果和限制见 [首次部署记录](docs/DEPLOYMENT-20261117.md)。镜像锁保留配方准备时的公开来源证据，不冒充运行回执。

**主站登录切换。** 2026-11-18 用户确认只用主站登录，不再独立注册或输入 Matrix 密码。配置采用 Synapse 原生 OIDC 和 Element 自动 SSO 跳转，复用已有身份桥的独立 `parallellines-tavern` 客户端。聊天频道尚未登录时，主站已有会话则自动接续，否则只在主站登录；设备验证和加密恢复仍保留。旧 Matrix 账号（含管理员）无需以原身份重新登录，数据和已有会话保留；新身份不自动继承旧权限。仓库配置不代表线上已切换，接入、验收和回退见 [主站登录说明](docs/MAIN_SITE_LOGIN.md)。

用户已授权维护公开仓库 `ixiaoyy/parallellines-entertainment`，并在主站 Actions 仅增加固定娱乐模块运维入口，复用现有连接，不读取或复制 Secrets。不能拿旧 Chat 的发布参数创建本模块。

## 文件

| 文件 | 职责 |
| --- | --- |
| `upstream.lock.json` | 官方组件版本、源码提交、不可变镜像及核对层级 |
| `compose.yaml` | 独立 Element、Synapse、PostgreSQL；应用仅映射服务器回环，数据库无宿主端口 |
| `config/element.json` | 固定本实例 homeserver、启用原生 SSO 自动跳转，并将认证页风景背景换为纯色；保留原生密钥和设备验证 |
| `deploy/element-appearance.py` | 精确发布或回退认证页背景；不重启容器或回退主站登录 |
| `config/synapse-public.yaml` | 必要原生非秘密配置及主站 OIDC；不含运行密钥，不可单独启动 |
| `docs/OPERATIONS.md` | 原生生成、私有配置、生产验收与停止恢复契约 |
| `docs/MAIN_SITE_LOGIN.md` | 单一登录、账号与权限边界、固定发布和回退 |
| `NOTICE.md` | 上游来源和许可证说明 |

生产根固定为 `/data/services/parallellines-entertainment`。所有运行凭据、signing key、媒体和数据库均留在其私有 `runtime/` / `data/` 内，不能随源码发布。

已有实例只改背景时，由获准的服务器操作者把同一已审提交的 `deploy/element-appearance.py` 与 `config/element.json` 放入同一私有发布目录，保留相对路径，以 root 执行 `python3 -B deploy/element-appearance.py apply`。动作同时同步 Element 实际提供的副本和已有 SSO 恢复登记，最终核公网配置。恢复只执行同目录的 `python3 -B deploy/element-appearance.py rollback`，保留登录、账号、加密与房间权限；意外中断先用同一入口续做或回退，拒绝覆盖未知变动。

## 已部署首期的原生使用

主站登录切换前，已部署首期仍使用原生账号和邀请码；服务器应为 `matrix.pingxingxian.space`。切换后只使用主站登录，新主站用户由 Synapse 原生 SSO 自动创建本站身份，不再需要邀请码。

仅首期原生注册使用邀请码；主站登录切换后不再发放或使用。首批邀请码最多 10 次，初始有效期 7 天，具体到期值以私有引导回执 `/data/services/parallellines-entertainment/data/synapse/bootstrap-private.json` 和原生 API 为准。不要分享私有回执或在公开 Actions 日志中输出邀请码。

首次登录按 Element 原生提示完成设备验证并妥善保管恢复密钥；本项目没有关闭或改写这一流程。

## 首期入口恢复

用户已确认第一阶段使用独立网关配置，不重建共享网关。它会在网关容器 recreate 后丢失，届时需通过固定 `publish` 操作恢复；普通 restart 不受此限制。本期不宣称已有跨网关重建的持久挂载，也不在共享网关归属检查失败时强行覆盖。

## 后续边界

现有 Chat 继续提供原有能力。此次只统一新登录，不自动合并既有 Matrix 账号、不迁移消息、不自动授予 Synapse 管理员、不实现跨站统一注销。已有 Matrix 会话不会随主站退出而失效，切换主站账号也不会自动切换已有 Matrix 会话。
