# Tavern 接入主站登录

## 状态与用户流程

2026-11-18 用户确认：Tavern 只有主站一套登录，旧 Matrix 账号（含管理员）无需以原身份重新登录。先切换并验收主站登录，再发布 Chat 侧栏 `聊天频道`。源码推送、静态检查和首期健康回执均不能代替线上登录验收。

`Element → Synapse 原生 SSO → 现有 OIDC 身份桥 → 主站 /auth/chat 自动审批 → Synapse → Element`

- Tavern 尚未登录、主站已登录：直接使用当前主站身份完成授权，无需另输 Tavern 账号或密码。
- Tavern 尚未登录、主站未登录：只在主站登录，保留审批上下文并自动返回 Tavern。
- 首次进入：Synapse 为该主站主体创建本站账号，不再使用邀请码或要求另设密码。
- 新设备仍走 Element 原生设备验证和加密恢复；恢复密钥不是额外账号密码，不关闭该流程。

本次只统一登录。已有 Matrix 会话继续使用其原账号，切换主站账号不会自动切换已登录的 Tavern 账号；主站退出也不会自动撤销已经签发的 Matrix 原生设备会话。不增加统一注销或 OIDC 后端注销，也不承诺主站会话与 Matrix 会话同步到期。

## 固定原生配置

| 项目 | 值与含义 |
| --- | --- |
| OIDC 发行方 | `https://pingxingxian.space/auth/oidc`，复用现有共享发行方，不启动第二实例 |
| 新客户端 | `parallellines-tavern`，与 Chat/New API 分开使用独立密钥 |
| 唯一回调 | `https://matrix.pingxingxian.space/_synapse/client/oidc/callback` |
| 认证和授权码 | `client_secret_basic`、PKCE S256；Synapse `pkce_method: always` |
| 授权范围 | `openid profile`，不要求邮箱、邮箱验证、平台角色或离线刷新权限 |
| 会话复核 | `user_profile_method: userinfo_endpoint`，登录时通过身份桥核当前主站会话 |
| Synapse 提供方 | `idp_id: parallellines`；原生登录能力中的 ID 为 `oidc-parallellines`，投入使用后不得随意改名 |
| 本站身份 | 以主站稳定 `sub` 绑定，新账号 localpart 为 `pl_{{ user.sub }}`；显示名来自 `name` |
| 原生注册/登录 | `enable_registration: false`；密码只保留 `only_for_reauth`，不作为独立登录入口；OIDC 提供方允许首次原生创建账号 |
| Element | 固定本实例 homeserver，`sso_redirect_options.immediate: true` 自动跳转，不改原生布局或加密设置 |
| 客户端确认 | `sso.client_whitelist` 仅加入 `https://tavern.pingxingxian.space/`，保留结尾斜线 |

`config/synapse-public.yaml` 是公开目标配置，不是完整私有配置。运行时必须保留数据库口令、signing key、macaroon/form/注册共享秘密及其他已有配置；不得整份覆盖生产 `homeserver.yaml`。

身份桥仅在私有环境中显式设置 `TAVERN_OIDC_CLIENT_SECRET` 时注册新客户端；省略时维持旧客户端配置。该客户端不继承 `OIDC_MAIN_PROFILE_SYNC_ENABLED`，不会给原有主站用户新增已验证邮箱前置条件。主站内部桥接 `client_id=parallellines-chat` 不变。

## 既有账号与权限

保留现有 `matrix.pingxingxian.space` 身份域、账号、管理员、房间、消息、媒体、设备和密钥。`allow_existing_users: false` 防止按同名自动接管旧账号；邮箱也不作为身份合并键。

切换不以绑定或恢复旧账号访问为前置。旧账号和已有会话不主动删除、停用或注销；新登录进入对应主站主体的 Matrix 身份，不自动继承旧身份的历史、房间成员资格或管理权限。本次不写入旧身份映射、不合并账号、不迁移数据。

主站管理员也不会自动成为 Synapse 管理员。若后续需要管理本实例，由部署负责人另获授权，通过 [Synapse 原生用户管理](https://github.com/element-hq/synapse/blob/v1.165.0/docs/admin_api/user_admin_api.md#change-whether-a-user-is-a-server-administrator-or-not) 从私有运维路径为指定主站账号对应的 Matrix ID 精确授权，不开放公网管理员接口，不保留原生密码登录作为日常运维入口。服务器管理员与房间管理员权限分别处理。

## 获得本次生产授权后的顺序

以下操作在主站 `Entertainment module operations` workflow 选择执行，仅适用于获准的目标环境和动作。局部回退走对应操作，不重跑首期部署。

1. 核当前身份桥与 Tavern 容器、版本、文件、权限、数据挂载和健康，保留原身份桥镜像/环境前像及 Tavern 配置恢复入口。不以首期历史回执代替当前状态。
2. 为 `parallellines-tavern` 单独生成至少 32 字符的新随机密钥，不与 Chat/New API 共用。仅在服务器私有配置中写入身份桥 `TAVERN_OIDC_CLIENT_SECRET`，并把同一新密钥预置于 `/data/services/parallellines-entertainment/data/synapse/tavern-oidc-client-secret`。Synapse 容器内对应 `/data/tavern-oidc-client-secret`，文件归 `991:991`、权限 `0600`。不输出秘密，不改其他密钥，不在公开 Compose 或命令参数中传输秘密。
3. 先执行 `publish-tavern-bridge`，只在既有单实例身份桥发布新客户端。共享身份桥替换会短暂影响 Chat/New API 的新授权，必须使用确认窗口；不沿用历史发布配方覆盖当前桥能力，不重建 Chat、New API、主站或共享网关。身份桥合同见相邻 Chat 仓库 `docs/tavern-sso-release.md`。
4. 新客户端注册和发行方健康确认后，才执行 `enable-main-login`。该固定扩展保留原基础来源与已安装证书续期消费者，只保存并变更本模块登录配置/准备回执，最多重启同一已登记 Synapse 容器。不重新初始化数据库，不重建 Element/PostgreSQL 或网关。
5. 执行 `main-login-status`，核原生健康、公开 Element 配置和 Matrix SSO 登录能力；再按下一节人工验收。状态探针不能证明浏览器已完成授权。两种主站会话场景均验收成功后，才执行 `publish-tavern-frontend`，启用 Chat 侧栏 `聊天频道`。

原网关已经代理 `/_synapse/client/`，无需为此新增路由、改主站 `default.conf`、放开管理员 API 或重建网关。首期 `prepare` 仍锁定初始部署来源，不能重跑它来更新已运行实例的登录配置。

## 验收与验证层级

本轮仅做本地 JSON/YAML/Python/JavaScript 与 workflow 的静态检查，不启动应用、运行构建、Compose 校验或业务测试，也不连接生产、操作用户浏览器或创建真实账号。

获准发布后，由部署负责人核服务和配置，由用户手动完成账号与设备验收：

- Tavern 无现有会话、主站已有登录的浏览器打开 Tavern：无独立登录/注册页，进入正确主站主体对应的本站账号。
- Tavern 和主站均无现有会话：仅在主站登录，并返回 Tavern，不丢审批上下文；未验证邮箱的有效主站账号也不新增邮箱门槛。
- 首次、再次及刷新后登录主体一致；使用稳定 `sub`，不得根据姓名或邮箱变化创建错绑。
- 原生 `/_matrix/client/v3/login` 的用户登录提供 SSO/token、不提供密码登录，提供方为 `oidc-parallellines`；保留上游固定公布的 `m.login.application_service`，它不是额外用户登录入口。客户端回调必须精确匹配并强制 PKCE S256。
- 无第二套邀请码或密码要求；设备验证、加密消息、当前 Matrix 身份的历史和恢复仍按原生流程验收，不用旧账号登录作为通过条件。
- Chat/New API 原回调、身份、授权和 Chat 刷新机制保持；不调用模型或消耗余额，不自动清理其他客户端 grant。
- 原证书续期独立 `health / renewal-status`、管理员 API 公网隔离仍有效。

## 精确配置恢复

固定 `enable-main-login` 在写入前保存本模块原 Element 配置、完整私有 Synapse 配置、准备回执及文件归属/权限，保存待处理回执与独立恢复入口。备份位于本模块私有 `runtime/main-login-backup/`，状态回执为 `main-login.json`，均不进入仓库或公共输出。Element 是单文件 bind mount，更新须保留 inode；不能只原子换宿主文件后宣称容器已生效。

切换失败时明确停止并保留状态。`restore-main-login` 按已登记前后像恢复本次配置与准备回执，只重启同一 Synapse 容器并核对原生登录能力和配置。它会恢复首期邀请码和密码登录配置，只用于明确授权的应急回退，不是并行或常驻的第二登录入口。中断写只在持久写意图与精确中间像一致时允许恢复；未知文件或容器漂移应停止交给部署负责人，不强行覆盖。

启用前保存并验证的独立程序不依赖 GitHub 或身份桥登录。服务器管理员可执行只读状态命令；恢复命令有配置写入和 Synapse 重启副作用，仅在获准恢复时执行：

```sh
sudo /usr/bin/python3 -B /data/services/parallellines-entertainment/sso-operations.py main-login-status
sudo /usr/bin/python3 -B /data/services/parallellines-entertainment/sso-operations.py restore-main-login
```

- **只回退 Chat 侧栏**：执行 `rollback-tavern-frontend`，恢复记录的 Chat 前端版本，不触碰 Tavern 或身份桥。
- **整套回退**：如已发布侧栏，先执行 `rollback-tavern-frontend`；使用 `retract` 撤回 Tavern 的公网入口，避免应急恢复把首期登录重新开放给用户。再执行 `restore-main-login`，最后执行 `rollback-tavern-bridge`。仍依赖 `parallellines-tavern` 登录时，不回退到缺少该客户端的桥版本。恢复对外服务时，先重新启用主站登录，再执行 `publish` 恢复 Tavern 公网入口；人工验收通过后才重发 Chat 侧栏。

配置及镜像回退不改已安装证书续期程序或 cron，不回灌数据库或媒体，不删除切换后创建的 SSO 账号、消息或设备会话，也不撤销其他客户端 grant。

## 官方依据

- [Element v1.12.35 SSO 配置](https://github.com/element-hq/element-web/blob/v1.12.35/docs/config.md#sso-setup)
- [Synapse v1.165.0 OIDC](https://github.com/element-hq/synapse/blob/v1.165.0/docs/openid.md)
- [Synapse v1.165.0 配置](https://github.com/element-hq/synapse/blob/v1.165.0/docs/usage/configuration/config_documentation.md)
