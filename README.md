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

**官方镜像及静态配方已准备，尚未完成生产部署验收。** 镜像锁只记录公开 Registry 与源码证据，不能证明账号注册、房间消息或设备恢复可用。实际发布、TLS 和双账号结果应另记生产回执。

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

## 后续边界

现有 Chat 继续提供原有能力。本阶段不预做接入层或保留未使用的功能分支；后续若要统一入口、账号或加入 Bot，应按具体需求另核原生接口和授权，不重写房间与消息系统。
