# 2026-11-17 首次部署记录

## 当前结果

**官方 Element、Synapse、PostgreSQL 已在生产独立运行，公网及服务器端续期收尾已完成，原生管理员与邀请码已初始化。** 源站证书、两域 HTTPS、Matrix 接口与跨域检查已通过；人工聊天验收仍未完成，不能将接口成功当作完整聊天验收。

- 运行配方：`5b96163a00f7c4877150cd5185fc510436ed6697`。
- 首次内部部署操作来源：主站 `0e85ca37`，仅本次新增的两份娱乐模块运维文件；未包含主站其他已有改动。
- 项目根：`/data/services/parallellines-entertainment`。
- 项目：`parallellines-entertainment`；服务：`element / synapse / postgres`。
- Element：`tavern.pingxingxian.space`；homeserver 与身份域：`matrix.pingxingxian.space`，用户已确认不可直接改名的身份边界。
- 第一阶段保留官方界面、品牌、语言、主题、设备验证和加密流程；不接 Bot、SSO、Key、平台余额或 Chat 导航。
- 原生邀请码注册已由用户确认。没有创建或使用其他系统账号、真实模型 Key 或余额。

## 已完成的生产操作

1. [只读资源与网关盘点](https://github.com/ixiaoyy/ParallelLines/actions/runs/44105422099)：成功。宿主 x86_64、4 CPU、约 5.74 GiB 内存，当时可用约 3.12 GiB；两个回环端口空闲，项目目录与容器尚不存在。
2. [生产准备](https://github.com/ixiaoyy/ParallelLines/actions/runs/44109000325)：成功。固定官方镜像已拉取，Synapse 原生配置与签名密钥已生成，独立数据库口令私有保存；其他容器身份与健康未改变。
3. [独立启动](https://github.com/ixiaoyy/ParallelLines/actions/runs/44109642536)：成功。Element 首页、Synapse `/health`、Matrix client versions 与容器健康均通过；其他服务前后状态一致，公网入口仍为 false。
4. [原生管理员与邀请码初始化](https://github.com/ixiaoyy/ParallelLines/actions/runs/44111688227)：成功。原生密码登录与 Registration Tokens Admin API 完成，临时引导会话已注销，其他服务未改变。首批邀请码总共可用 10 次、有效 7 天；实际值仅在服务器私有 `data/synapse/bootstrap-private.json`，不进入本记录。
5. [两域证书准备](https://github.com/ixiaoyy/ParallelLines/actions/runs/44114216143)：成功。两个新域各自 HTTP01 公开往返均为 200 且正文精确匹配；使用新证书名 `parallellines-entertainment`，SAN 仅包含两个新域，有效期至 2027-02-15。没有改动或重载网关，也没有修改其他证书或服务。
6. [公网发布](https://github.com/ixiaoyy/ParallelLines/actions/runs/44125300096)：成功。源站真实 SNI/CA/叶证书、两域 HTTPS、Matrix CORS 和管理员 API 公网 404 均通过。只重建本模块两个 Web 服务接入已有 edge，PostgreSQL、稳定密钥与其他容器未改变；共享网关没有重建，原 `default.conf` 未改变。发布报告明确 `temporary_conf_layer=true`、`renewal_ready=false`。
7. [2026-11-18 安装专用续期消费者](https://github.com/ixiaoyy/ParallelLines/actions/runs/44131862700)：成功。root 私有恢复程序及专用 cron 完成安装，现有 cron 调度活跃，并真实执行已保存程序的独立只读 `health`，证明不依赖 GitHub 注入或部署 Secrets。
8. [首次原生续期检查](https://github.com/ixiaoyy/ParallelLines/actions/runs/44132261796)：成功。证书未到期，原生结果 `not_due`；没有实际换证或无故 HUP，源站证书、公网路由、Matrix CORS 和管理员 API 隔离仍通过。报告 `renewal_ready=true`、`certificate_reload_pending=false`、`other_services_changed=false`。
9. [独立续期状态核验](https://github.com/ixiaoyy/ParallelLines/actions/runs/44132557605)：成功。安装 source 与权限已验证，调度活跃、最近调用成功、没有待加载证书，`renewal_ready=true`。

2026-11-18 恢复工作时，从客户端所在机器只读访问 Element `/config.json`、Matrix `/_matrix/client/versions` 和原 Chat `/health`，均返回 HTTP 200；没有控制浏览器或进行账号操作。

最终运维来源为主站 `1abefac5b9b20af63f9821d010cfe7efb1b88752`，仅本次两份运维文件。公开发布与续期从隔离 worktree 交付，未混入其他任务的暂存内容；当时另一项界面语言提交已由其 owner 发布，本次只以实际远端作为基线，不替其他任务提交或推送。

部署返回复用了健康结果中的 `read_only: true`，这只是旧报告字段错误，不表示部署没有写入。实际执行了本项目三服务启动；运维提交 `6f3037db` 已修正首次部署的返回字段，没有为修正报告重复重建服务。

## 网关事实与未决项

- 现有 Nginx 根配置会加载 `/etc/nginx/conf.d/*.conf`。
- 现场只挂载几个指定配置文件，没有整个 `conf.d` 的持久目录绑定。
- 现有圈子续期消费者锁定 `default.conf` 的完整前像，但不锁整个 `conf.d` 清单；本模块不能改写或放宽旧守卫。
- 新服务可加入网关已经连接的 `pl-official-candidate-20261030_default`，不需要改变旧网关网络或默认路由。
- 用户已正式确认首期独立配置、不重建共享网关。本模块已加载独立 vhost；跨网关重建需要 `publish` 恢复，不增加挂载或改造共享网关结构。

## 仍须完成

- 原生邀请注册、登录、双账号房间消息、设备验证与恢复；健康检查不能代替这些业务验收。
- 浏览器验收被用户以物理 Escape 中止。本轮只恢复服务器工作，账号与设备验证由用户手动完成；当前不对语音、视频或跨服务器聊天作验收承诺。

遵守用户要求，未在本机启动应用、构建、运行测试或 Compose 校验。已有 Chat、主站、身份桥和模型网关未被本次重建，既有配置、密钥和数据未修改。
