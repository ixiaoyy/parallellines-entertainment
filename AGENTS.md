# 娱乐模块项目规则

## 定位与版本

- 本项目只维护官方 Matrix / Element 部署及必要接入材料，不重新开发聊天产品。
- 产品固定为 Element Web、Synapse、PostgreSQL；正式来源、提交与不可变镜像见 `upstream.lock.json`。
- 生产根为 `/data/services/parallellines-entertainment`，Compose project 为 `parallellines-entertainment`；不得复用现有 Chat、主站或网关的数据和密钥。
- 前端为 `tavern.pingxingxian.space`；homeserver 与不可直接改名的身份域为 `matrix.pingxingxian.space`。

## 已确认范围

- 用户已授权独立公开项目、前期独立部署与原生账号，之后才考虑平台接入。
- 用户明确要求直接生产运行，不做本地应用试用、构建或业务测试；静态审查不代表真实验收。
- 第一阶段完整保留原生界面和产品行为，仅做必要域名、TLS、数据库、密钥和原生邀请码注册配置。
- 不改主题、品牌、翻译、Labs 或设备验证，不添加 Bot、SSO、模型费用或 Chat 入口；不全局关闭加密。
- 不修改现有服务，不把其他仓库的已有改动纳入本项目。不在父目录初始化仓库。

## 部署与恢复

- 先读 `README.md`、`docs/OPERATIONS.md`、Compose 与镜像锁；官方原生生成与启动能力优先，不另造部署平台。
- `config/synapse-public.yaml` 不是完整运行配置。先由官方 `generate` 生成私有配置和 signing key，再按运维契约合并公开字段与数据库口令。
- `runtime/`、`data/` 不入 Git，不复制或输出 Actions Secrets、数据库口令、signing key、注册共享秘密、邀请令牌或用户 token。
- 不展开完整 Compose 环境、容器环境或运行日志；必要诊断仅输出经过审查的稳定状态。
- 不修改 Synapse 原生 schema，不自行迁移或播种既有系统数据。镜像升级、开放额外能力须另核授权及兼容性。
- 回退只处理本项目服务、入口和网络；保留数据和密钥，不执行 `down -v`、全局 prune 或其他服务重启。
