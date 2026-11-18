# 平行线多人聊天模块

基于官方 [Element Web](https://github.com/element-hq/element-web) 与 [Synapse](https://github.com/element-hq/synapse) 的独立部署项目，PostgreSQL 保存原生账号和聊天数据。完整使用上游聊天界面、房间、私聊、成员权限和加密流程，不重新实现聊天前后端。

## 部署边界

- 网页入口：`https://tavern.pingxingxian.space`。
- Homeserver：`https://matrix.pingxingxian.space`；身份形如 `@用户名:matrix.pingxingxian.space`，此身份域初始化后不能直接改名。
- 首期使用原生独立账号和邀请码注册；现已确认接入主站登录，接入材料仍待发布。保留 Element 原生布局、品牌、语言选择、主题、Labs 与设备验证。
- 不接入 Bot、模型 Key、平台余额或现有 Chat 导航，不全局关闭加密。
- 不替换现有 Chat、主站、身份桥或模型网关，不复用它们的数据和密钥。
- 按用户要求直接生产运行和验收，不在本机启动应用、构建或业务测试。

## 当前状态

**站点与服务器端收尾已完成。** 两域 HTTPS、源站证书、Matrix 接口、跨域及管理员 API 公网隔离检查已通过；原生管理员和首批邀请码已初始化。专用证书续期任务已安装并完成原生命令、来源权限、调度与当前路由检查；新证书未到期，本次没有强制换证或无故重载。浏览器注册、双账号房间消息及设备恢复尚未验收，不能把接口健康当成完整聊天验收。

实际版本、操作结果和限制见 [首次部署记录](docs/DEPLOYMENT-20261117.md)。镜像锁保留配方准备时的公开来源证据，不冒充运行回执。

**主站登录切换。** 2026-11-18 用户确认使用主站登录，不再额外注册或输入本站密码。配置采用 Synapse 原生 OIDC 和 Element 自动 SSO 跳转，复用已有身份桥的独立 `parallellines-tavern` 客户端。聊天频道尚未登录时，主站已有会话则自动接续，否则只在主站登录；设备验证和加密恢复仍保留。已有 Matrix 会话不自动切换账号或随主站退出而失效。仓库配置不代表线上已切换，接入、旧账号处理和恢复见 [主站登录说明](docs/MAIN_SITE_LOGIN.md)。

用户已授权维护公开仓库 `ixiaoyy/parallellines-entertainment`，并在主站 Actions 仅增加固定娱乐模块运维入口，复用现有连接，不读取或复制 Secrets。不能拿旧 Chat 的发布参数创建本模块。

## 文件

| 文件 | 职责 |
| --- | --- |
| `upstream.lock.json` | 官方组件版本、源码提交、不可变镜像及核对层级 |
| `compose.yaml` | 独立 Element、Synapse、PostgreSQL；应用仅映射服务器回环，数据库无宿主端口 |
| `config/element.json` | 固定本实例 homeserver，并启用原生 SSO 自动跳转，不改主题或设备验证 |
| `config/synapse-public.yaml` | 必要原生非秘密配置及主站 OIDC；不含运行密钥，不可单独启动 |
| `docs/OPERATIONS.md` | 原生生成、私有配置、生产验收与停止恢复契约 |
| `docs/MAIN_SITE_LOGIN.md` | 主站登录合同、生产切换前置、原生账号兼容与配置恢复 |
| `NOTICE.md` | 上游来源和许可证说明 |

生产根固定为 `/data/services/parallellines-entertainment`。所有运行凭据、signing key、媒体和数据库均留在其私有 `runtime/` / `data/` 内，不能随源码发布。

## 已部署首期的原生使用

主站登录切换前，已部署首期仍使用原生账号和邀请码；服务器应为 `matrix.pingxingxian.space`。切换后只使用主站登录，新主站用户由 Synapse 原生 SSO 自动创建本站身份，不再需要邀请码。

注册需要邀请码。服务器管理员可在私有文件 `/data/services/parallellines-entertainment/data/synapse/bootstrap-private.json` 中取用 `invite_token`，只私下发给注册者，不要分享整个文件或在公开 Actions 日志中输出。首批邀请码最多 10 次，初始有效期 7 天，具体到期值以该文件和原生 API 为准。

首次登录按 Element 原生提示完成设备验证并妥善保管恢复密钥；本项目没有关闭或改写这一流程。

## 首期入口恢复

用户已确认第一阶段使用独立网关配置，不重建共享网关。它会在网关容器 recreate 后丢失，届时需通过固定 `publish` 操作恢复；普通 restart 不受此限制。本期不宣称已有跨网关重建的持久挂载，也不在共享网关归属检查失败时强行覆盖。

## 后续边界

现有 Chat 继续提供原有能力。此次只统一新登录，不自动合并既有 Matrix 账号、不迁移消息、不实现跨站统一注销。后续若要增加账号绑定、统一注销或 Bot，应另核原生接口和授权，不重写房间与消息系统。
