# 平行线多人聊天模块

基于官方 [Element Web](https://github.com/element-hq/element-web) 与 [Synapse](https://github.com/element-hq/synapse) 的独立部署项目，PostgreSQL 保存原生账号和聊天数据。完整使用上游聊天界面、房间、私聊、成员权限和加密流程，不重新实现聊天前后端。

## 第一阶段

- 网页入口：`https://tavern.pingxingxian.space`。
- Homeserver：`https://matrix.pingxingxian.space`；身份形如 `@用户名:matrix.pingxingxian.space`，此身份域初始化后不能直接改名。
- 使用原生独立账号和邀请码注册，保留 Element 原生布局、品牌、语言选择、主题、Labs 与设备验证。
- 不接入 Bot、主站 SSO、模型 Key、平台余额或现有 Chat 导航，不全局关闭加密。
- 不替换现有 Chat、主站、身份桥或模型网关，不复用它们的数据和密钥。
- 按用户要求直接生产运行和验收，不在本机启动应用、构建或业务测试。

## 当前状态

**站点与服务器端收尾已完成。** 两域 HTTPS、源站证书、Matrix 接口、跨域及管理员 API 公网隔离检查已通过；原生管理员和首批邀请码已初始化。专用证书续期任务已安装并完成原生命令、来源权限、调度与当前路由检查；新证书未到期，本次没有强制换证或无故重载。浏览器注册、双账号房间消息及设备恢复尚未验收，不能把接口健康当成完整聊天验收。

实际版本、操作结果和限制见 [首次部署记录](docs/DEPLOYMENT-20261117.md)。镜像锁保留配方准备时的公开来源证据，不冒充运行回执。

用户已授权维护公开仓库 `ixiaoyy/parallellines-entertainment`，并在主站 Actions 仅增加固定娱乐模块运维入口，复用现有连接，不读取或复制 Secrets。不能拿旧 Chat 的发布参数创建本模块。

## 文件

| 文件 | 职责 |
| --- | --- |
| `upstream.lock.json` | 官方组件版本、源码提交、不可变镜像及核对层级 |
| `compose.yaml` | 独立 Element、Synapse、PostgreSQL；应用仅映射服务器回环，数据库无宿主端口 |
| `config/element.json` | 仅指定本实例 homeserver，不覆盖其他产品设置 |
| `config/synapse-public.yaml` | 必要原生非秘密配置；由发布流程合并到官方生成的完整运行配置 |
| `docs/OPERATIONS.md` | 原生生成、私有配置、生产验收与停止恢复契约 |
| `NOTICE.md` | 上游来源和许可证说明 |

生产根固定为 `/data/services/parallellines-entertainment`。所有运行凭据、signing key、媒体和数据库均留在其私有 `runtime/` / `data/` 内，不能随源码发布。

## 原生使用

打开 `https://tavern.pingxingxian.space`，在原生界面创建独立账号；服务器应为 `matrix.pingxingxian.space`，现有平行线账号尚未接入。

注册需要邀请码。服务器管理员可在私有文件 `/data/services/parallellines-entertainment/data/synapse/bootstrap-private.json` 中取用 `invite_token`，只私下发给注册者，不要分享整个文件或在公开 Actions 日志中输出。首批邀请码最多 10 次，初始有效期 7 天，具体到期值以该文件和原生 API 为准。

首次登录按 Element 原生提示完成设备验证并妥善保管恢复密钥；本项目没有关闭或改写这一流程。

## 首期入口恢复

用户已确认第一阶段使用独立网关配置，不重建共享网关。它会在网关容器 recreate 后丢失，届时需通过固定 `publish` 操作恢复；普通 restart 不受此限制。本期不宣称已有跨网关重建的持久挂载，也不在共享网关归属检查失败时强行覆盖。

## 后续边界

现有 Chat 继续提供原有能力。本阶段不预做接入层或保留未使用的功能分支；后续若要统一入口、账号或加入 Bot，应按具体需求另核原生接口和授权，不重写房间与消息系统。
