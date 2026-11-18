# 原生部署与恢复

## 1. 范围与状态

用户已选定 Matrix + Element，并确认两个域名；首期使用原生邀请码注册。2026-11-18 已确认本地主站登录接入，后续只使用主站登录。接入配置和固定发布、回退动作准备不代表线上生效，生产更新与重启仍需另行授权，详见 [主站登录说明](MAIN_SITE_LOGIN.md)。不修改 Tavern 界面、主题、品牌、翻译、Labs 或设备验证，不接 Bot、Key 或平台余额，不全局关闭加密。Chat 侧栏入口独立发布。

按用户要求直接在生产运行，不在本机启动应用、构建、执行 Compose 校验或业务测试。站点已发布，实际检查结果见 `DEPLOYMENT-20261117.md`；本文件与镜像锁不能代替真实账号、房间消息或设备恢复验收。

生产固定边界：

| 对象 | 值 |
| --- | --- |
| 项目根 | `/data/services/parallellines-entertainment` |
| Compose project | `parallellines-entertainment` |
| Element 网页 | `https://tavern.pingxingxian.space` |
| Synapse 公网基址 | `https://matrix.pingxingxian.space/` |
| Synapse `server_name` | `matrix.pingxingxian.space`，初始化后不能直接改名 |
| 回环端口 | Element `127.0.0.1:13101`；Synapse `127.0.0.1:13102` |

## 2. 固定组件与数据

`浏览器 → Element 原生页面 → Synapse 原生 Client API → 本项目 PostgreSQL / 媒体目录`

- `upstream.lock.json` 为三镜像版本依据，均已只读核对 `linux/amd64`；正式发布仍须核目标机架构、Compose 版本、端口与资源。
- Element 为官方静态客户端；指定本实例 homeserver，主站登录切换后固定该服务器并启用原生 SSO 自动跳转。其 `/app/config.json` 为公开非秘密配置，宿主文件可用 0644；保持品牌、主题和设备验证默认值。
- Synapse 为单进程原生 homeserver，`./data/synapse` 挂到 `/data`，保存完整私有配置、signing key 和媒体。无须部署 worker 或额外消息总线。
- PostgreSQL 17 的 `./data/postgres` 挂到 `/var/lib/postgresql/data`，只在本项目内部网提供连接，不映射宿主端口。
- Compose 的三服务内存上限合计约 2.125 GiB，仅是隔离上限，不是官方最低要求或容量保证；发布前须给现有服务保留余量。
- 三镜像所选平台压缩层合计 354,369,877 字节，不等于解压后、可写层和数据空间需求。

不连接、迁移或初始化现有 Chat、主站、身份桥和模型网关的数据。数据库 schema 由新实例的官方 Synapse 原生初始化管理，不增加自定义表或应用补丁。

## 3. 首次 prepare 消费者契约

以下为首期初始化合同，只在生产项目私有目录中执行，由固定发布通道负责；不能直接用缺少私有运行文件的根 Compose 启动。既有 `prepare` 仍锁定首期不可变来源 `5b96163a00f7c4877150cd5185fc510436ed6697`，不会因为本仓库根配置已改为主站登录就自动取最新版本。已运行实例不得重跑初始化来更新登录方式，必须使用第 8 节固定配置更新/恢复操作。

1. 确认本项目目录、端口、容器和网络没有陌生前像；为本次部署保存身份、资源和现有服务健康记录。不能覆盖已有目录或复用其他系统的数据。
2. 在生产主机拉取镜像锁中的三个不可变引用。先创建本项目 `runtime/`、`data/synapse/` 和 `data/postgres/`，拒绝软链或目录逃逸；`create_host_path: false` 防止 Compose 自动生成错误挂载路径。
3. 只在私有 `runtime/postgres-password` 生成一个独立随机数据库口令，目录 0700、文件 root 所有且 0600，不打印、不提交、不使用示例秘密。PostgreSQL 使用官方 `POSTGRES_PASSWORD_FILE` 原生入口读取；不要改成明文 Compose 环境或覆盖官方入口/默认用户。
4. 用固定 Synapse 镜像的官方 `generate` 模式初始化新的 `data/synapse`：设置 `SYNAPSE_SERVER_NAME=matrix.pingxingxian.space`、`SYNAPSE_REPORT_STATS=no`，挂载到 `/data`。官方入口生成 `homeserver.yaml`、日志配置和 signing key；默认服务 UID/GID 为 `991:991`。
5. 读取锁定首期来源的 `config/synapse-public.yaml`，以顶层字段合并到新生成的完整配置，再把刚生成的数据库口令仅写入最终私有配置的 `database.args.password`。公开文件本身没有假口令，也不是可单独启动的完整配置。当前根配置包含 OIDC，不能在没有独立客户端密钥时直接替换首期来源。
6. 保留官方生成的所有其他字段和秘密，包括 signing key、macaroon/form/注册共享秘密及其实际路径；不重置、复制、向日志展开或替换为其他项目值。最终私有配置须可由 UID 991 读取，数据目录可由其写入；对宿主无关用户保持不可访问。
7. PostgreSQL 只在全新数据目录执行原生初始化。`POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C` 为 Synapse 必需条件；不使用 `allow_unsafe_locale` 或不安全密码认证绕过。
8. 首次生成完成后记录私有准备回执。后续 prepare 不得重新生成 signing key、替换数据库密码或再次初始化已有数据；未知前像停止，由操作者核对。

首期锁定来源的 `config/synapse-public.yaml` 设置域名、公网基址、容器内反代 listener、PostgreSQL 连接非秘密字段和邀请码注册；当前根配置在同一部署边界内改为原生 OIDC。Listener 保留原生 `client / federation` 资源语义，没有加入全局联邦黑白名单。是否开放额外联邦委派或端口由实际入口范围确定，不能把未验收的跨服务器聊天列为首版完成能力。

## 4. 首期原生账号与切换后的权限

- 首期使用 `enable_registration: true` 和 `registration_requires_token: true`，保持 `enable_registration_without_verification` 默认 false；不开放无验证注册。主站登录切换后关闭独立注册，仅允许已认证主站用户经 OIDC 原生创建本站账号，不使用邀请码。
- 初始管理员通过 Synapse 原生 `register_new_matrix_user` 或原生共享秘密注册接口创建，凭据只留服务器私有记录；不得在命令日志或交付材料中输出口令、共享秘密和管理员 token。
- 注册邀请码由 Synapse 原生 Registration Tokens Admin API 管理；Element 使用原生注册验证步骤，不编写新注册页面。邀请码的实际用途次数和有效期须在发放时明确，不在公开仓库保存真实令牌。
- 保留 Element 原生设备验证与加密恢复流程。新浏览器可能要求已有设备或恢复密钥验证，不用关闭 `force_verification` 来跳过验收。
- 主站登录以稳定 `sub` 绑定本站身份。旧 Matrix 账号（含管理员）不需要保留原身份的登录入口；不做旧身份映射、数据迁移、自动升权或主动注销。若后续确需 Synapse 管理权限，另经授权向指定主站账号对应的 Matrix ID 授权，不复用主站管理员角色。

## 5. 生产发布与公网入口

既有发布通道只操作各自固定服务，本模块使用主站 `.github/workflows/entertainment-ops.yml` 和 `.github/scripts/entertainment_ops.py`；主站登录更新由独立固定扩展 `.github/scripts/entertainment_sso.py` 复用原有守卫。原基础程序、已安装续期程序与 cron 不因新增登录动作而改写。只接受固定操作和已审核来源，不提供任意 shell、路径、域名或配置正文参数。现有 Actions Secrets 和主机指纹保留在所属通道，不复制到本仓库。

首次启动只处理本项目的 `element / synapse / postgres`。公开入口通过本项目独立 edge overlay 将两个新服务加入现有网关已经连接的网络，不改变旧网关的网络或默认路由，不重建现有服务。

两个域名与证书必须分别真实核对。Element 和 homeserver 使用不同 origin；不把 Client API、媒体 API 或 `/_synapse/admin/` 转发到静态客户端。公网反代使用固定真实 upstream，保留规范化前的 Matrix 路径、查询串和 Authorization；代理需按官方文档传递 Host、X-Forwarded-For 与 X-Forwarded-Proto。管理员 API 仅留独立运维路径，不默认向公网发布。

生产盘点确认根 Nginx 会加载 `/etc/nginx/conf.d/*.conf`，但没有整个目录的持久挂载。优先新增本模块独立配置，不修改 `default.conf` 或圈子已有配置；`nginx -t` 通过后才 reload，不重建共享网关。旧服务的配置和证书续期守卫不能因本模块被放宽或悄悄失效。

用户已确认首期独立配置、不重建共享网关。配置只能保留到网关容器被重建前；容器 restart 不会丢失，但 recreate 会丢失。服务器侧配置源与固定 `publish` 恢复操作已保留，不能宣称已有跨网关重建的持久挂载。如果要求增加持久挂载并重建网关，必须另行批准共享网关及旧续期记录的配套变更。

专用续期消费者已在生产安装并核验：

- 私有固定恢复程序：`/data/services/parallellines-entertainment/operations.py`，root 所有、0600；不含部署连接 Secrets。
- 调度：`/etc/cron.d/parallellines-entertainment-renew`，root 所有、0644，`37 */6 * * *`，按服务器 cron 时区运行；不修改已有 cron。
- 只处理证书名 `parallellines-entertainment`，原生 `certbot renew` 关闭旧 hooks 和随机 sleep，不强制轮换。证书变更或 loaded 身份落后时，才按既有归属守卫合法 reload；旧证书已经过期也可进入原生续期，不以旧公网 TLS 健康为前置。
- 初次真实调用结果为 `not_due`，证书、网关均未无故改变；独立 `renewal-status` 确认调度活跃、安装来源和权限正确、最近调用成功且没有待加载证书。这不是实际证书轮换证明。
- 网关被重建、自有配置层丢失，或旧 Tools reload 源码/配置归属发生变化时会明确停止并保留服务；由操作者先核对来源与前像，必要时 `publish` 恢复，不能放宽旧守卫。

服务器管理员可脱离 GitHub 连接执行以下只读检查：

```sh
sudo /usr/bin/python3 -B /data/services/parallellines-entertainment/operations.py health
sudo /usr/bin/python3 -B /data/services/parallellines-entertainment/operations.py renewal-status
```

同一固定程序也保留 `publish / retract / stop / renew`，均只处理本模块，并继续执行原有身份及归属检查；不要把它改成任意脚本入口。

本阶段不另部署 TURN、MatrixRTC 或其他音视频服务，也不修改界面隐藏按钮。未配置或验收的音视频、联邦功能不作为上线承诺。

## 6. 真实验收与日志

仅在生产授权范围内做低负载真实入口检查，不压测、不故障注入、不调用模型或产生费用。

1. PostgreSQL 原生 `pg_isready` 就绪后启动 Synapse；核对新数据库的 UTF8、C locale 和原生 schema 初始化，不连接其他数据库。
2. Element 保留官方镜像 healthcheck `/config.json`；Synapse 保留官方镜像 healthcheck `/health`。这两者不能代替数据库、跨域或聊天验收。
3. 独立读取 `/_matrix/client/versions` 与原生登录/注册能力，核 HTTPS、Element 配置中的 homeserver、CORS 和两个域的路由；真实页面可加载不等于已经能聊天。
4. 首期原生邀请码注册和登录的验收状态见部署记录，不作为主站登录切换前置。切换后按 [主站登录说明](MAIN_SITE_LOGIN.md) 分别验证主站已登录和未登录入口。获准后用两个主站验收账号检查设备验证、创建房间、邀请/加入、双方发收消息、刷新后历史、退出与恢复，记录实际页面截图和结果。不得在既有系统造无关业务数据。
5. 核现有 Chat、主站、身份桥和模型网关的服务身份及健康没有被本次改变，再记录本次真实发布范围与未验收项。

不输出完整 Compose 环境、容器环境、私有配置、数据库口令、signing key、注册共享秘密、邀请码、用户或管理员 token。不启用 `SYNAPSE_LOG_SENSITIVE` / `SYNAPSE_LOG_TESTING`，不把 SQL 日志升至 DEBUG；必要错误只收集有界、审查过的状态和稳定错误码。

## 7. 停止、备份与恢复

网关变更前必须已有独立 `health / stop` 操作，既有入口异常时仍能使用。

- 启动失败仅停止本项目服务，保留 `runtime/`、`data/synapse/`、`data/postgres/` 和回执，不删库、不换密码、不重新生成 signing key。
- 对外开放后撤回，仅处理本模块新增入口及本次附加网络，核网关健康后停止本项目；不恢复或重建整套主站。
- 正常停止使用固定 `stop` 操作，它会先合法撤回本模块入口。只有人工确认公网入口已撤回、运行目录归属无误后，才可直接用 `docker compose --project-name parallellines-entertainment --file compose.yaml stop` 停止本项目。归属守卫拒绝时不能强停仍可能被代理的后端；严禁 `down -v`、全局 prune 或任何其他服务停止。
- 升级前保留旧镜像、Compose、Element 配置、完整 Synapse 私有配置与 signing key、媒体和 PostgreSQL 一致备份；数据库备份使用 PostgreSQL 支持的方式，不能把运行中的目录拷贝冒充一致备份。
- 不自动回灌旧数据库抹掉后续消息或账号，不默认旧镜像兼容新 schema。恢复需明确目标版本与数据兼容性；后续镜像升级不沿用首次部署授权。

## 8. 主站登录配置更新

本地接入仅准备配置、身份桥新客户端和固定运维消费者，不改变生产状态。获准上线后先准备独立密钥并执行 `publish-tavern-bridge`，确认新客户端正常注册后再执行 `enable-main-login`。线上主站登录人工验收通过后，才执行 `publish-tavern-frontend`。

Element 自动跳转、Synapse 原生 OIDC、旧账号处理、真实验收及三个组件的回退顺序统一维护在 [主站登录说明](MAIN_SITE_LOGIN.md)。`restore-main-login` 是精确配置恢复，不提供并行第二套登录。不要用首次 `prepare/deploy`、重建网关或改写已安装证书续期程序代替登录更新。

## 官方依据

- [Synapse Docker 生成与启动](https://github.com/element-hq/synapse/blob/v1.165.0/docker/README.md)
- [Synapse PostgreSQL 与 locale](https://element-hq.github.io/synapse/latest/postgres.html)
- [Synapse 配置](https://github.com/element-hq/synapse/blob/v1.165.0/docs/usage/configuration/config_documentation.md)
- [邀请码 Admin API](https://github.com/element-hq/synapse/blob/v1.165.0/docs/usage/administration/admin_api/registration_tokens.md)
- [Synapse 反代与健康](https://github.com/element-hq/synapse/blob/v1.165.0/docs/reverse_proxy.md)
- [Element 配置](https://github.com/element-hq/element-web/blob/v1.12.35/docs/config.md)
- [Synapse 原生 OIDC](https://github.com/element-hq/synapse/blob/v1.165.0/docs/openid.md)
- [PostgreSQL 官方容器](https://hub.docker.com/_/postgres)
