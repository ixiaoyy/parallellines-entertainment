# 从原角色卡准备聊天频道机器人

本目录是独立的角色机器人接入。Element 仍用现有邀请和 `@` 菜单；只给已接入费用确认的角色增加邀请步骤，普通成员和其他机器人仍走原生邀请。首发付费角色是 Flux the Cat；原卡只供服务器私有导入，不提供下载，Matrix 只显示去掉角色卡数据的头像。

## 预览和准备

管理员从原作者或 [SillyTavern 官方角色卡](https://github.com/SillyTavern/SillyTavern-Content/blob/main/assets/character/default_FluxTheCat.png) 取得 PNG 文件后，先在私有终端检查原卡，不要把文件放到公开仓库。

```sh
python3 -B bot/import_card.py preview /path/to/original-card.png
```

同样接受 SillyTavern V2 JSON。普通 PNG、不是 V2 的卡和结构破损的卡会失败，不创建角色。预览标出作者、卡版本、开场白、生效字段和未启用字段；会输出角色正文，不要把预览重定向到公开日志。

| 字段 | 处理 |
| --- | --- |
| 名字、描述、性格、场景、示例对话 | 保留角色名，使用原卡文本；只替换 `{{char}}` 和 `{{user}}` 两个称呼 |
| `post_history_instructions` | 先审阅预览，再将其作为角色补充说明；不作为新的系统权限，不读取房间历史 |
| 开场白、作者说明、版本 | 只供预览、来源核对，不自动发到房间；作者说明不发送给模型 |
| PNG 头像 | 导出不含原卡文本、扩展和备注的 PNG；JSON 不获取外部头像地址，也不清除账号原有头像 |
| `system_prompt`、世界书、备选开场白、脚本与其他扩展 | 原卡不修改，不启用这些字段，也不执行或下载其中的资源 |

Flux 原卡署名 `@kallmeflocc on Discord`。按本次确认范围只在服务端导入，不把原卡或头像提交公开仓库或提供素材下载；若要另行分发素材，须核权利人许可，不能把代码许可证当作素材许可。参见 [上游素材使用说明](https://github.com/SillyTavern/SillyTavern-Content/blob/main/README.md?plain=1#L5)。

`prepare` 使用独立 Matrix 账号及设备。`--role-id flux-the-cat` 对应 `@bot_flux-the-cat:matrix.pingxingxian.space`，不能借真人令牌代替。`--settings` 是服务管理员预先准备的私有 UTF-8 JSON，只读输入，勿进入 Git 或公开终端。它需要这些字段：

| 配置 | 要求 |
| --- | --- |
| `matrix_access_token`、`matrix_device_id` | 两者属于上述同一个专用 Bot 账号及设备；不用管理员令牌、不恢复公网密码登录 |
| `model_id`、`bot_service_token` | New API `/tavern-flux/v1` 核准模型及 Flux 专用服务令牌；不借用群成员的个人 Key 或 Chat 登录会话，专用入口按房间授权确定付款人 |
| `session_encryption_key`、`config_encryption_key` | 两个独立随机的 32 字节密钥，各填 64 位十六进制；与角色设备状态一并私有保存 |

```sh
python3 -B bot/import_card.py prepare /path/to/original-card.png \
    --role-id flux-the-cat \
    --settings /path/to/private-role-settings.json \
    --authorization-reference '批准私有导入和房间试用的内部记录位置'
```

产物为仓库根 `runtime/bot/<role-id>/` 中的 `config.yml`、`compose.env`、`source.json` 和（仅 PNG 卡）`avatar.png`。运行目录不入 Git。相同原卡和配置重复导入是无操作；同名内容、费用配置或凭据不同会拒绝覆盖，不能借重复导入更换已运行实例的设备。命令不联网、不创建账号、不上传图片或启动服务。

## 获准上线时

先完成付费授权、硬上限、结算与撤销集成验证，并用 `bot/baibot/` 为构建上下文准备 Linux amd64 镜像。构建锁定 [Baibot v2026.11.18.0](https://github.com/etkecc/baibot/tree/a5944371b6c4a952b350eb9d561202934735a475)，应用私密加密房邀请及单条 `@` 消息补丁；未经该补丁的官方镜像不能代替。机器人为 `AGPL-3.0-or-later`，公开提供修改版服务时应向用户提供对应源代码入口，至少包含固定上游源码、本补丁和构建所需材料；角色卡内容许可另行处理。

若现网已有仅内网可用的 [Synapse 原生共享秘密注册入口](https://github.com/element-hq/synapse/blob/v1.165.0/docs/admin_api/register_api.md)，获准的运维人员可用它只创建非管理员 Bot，直接把返回的 Bot 令牌和设备 ID 写入私有配置；不用真人会话或不带设备的代登录令牌。不输出注册共享秘密和凭据，不开放外网注册，不重置已有账号或设备。入口不可用、账号已存在但归属不明或 Key 无额度上限时停止，不改造现有登录配置来绕过。

使用 `bot/compose.yaml`，独立 Compose 项目名 `parallellines-role-<role-id>`，只启动 `role-bot`。它没有对外端口，不加入数据库网络。`TAVERN_BOT_IMAGE` 填审核后的不可变镜像，`TAVERN_BOT_UID`、`TAVERN_BOT_GID` 填独立非 root 运行账号；`--env-file` 指向该角色的 `compose.env`。现有聊天项目的 Compose 不加载该文件。

运行账号应拥有私有角色目录、配置文件和预先创建的 `data/bot/<role-id>`；目录仅该账号可读写，秘密文件仅该账号可读。设备和加密状态必须持久化，不能为了修复重连而清空 `data/` 或换设备。房间成员须事先知道 `@` 机器人的文本会以明文发给模型，房间的端到端加密并不能保护送到模型的副本。

启动后，有邀请权限的成员在 Element 的私密加密房间分别选择同一个 Flux。邀请人通过 New API 本人登录确认该房钱包额度上限后返回原邀请窗启用。Flux 只使用邀请人的 New API 钱包额度，不使用订阅额度或其他成员的额度。无房间授权、已撤销或额度不足则不继续调用模型，也不自动切换付款人。无需预填房间 ID 或登记成员账号，公开房和未加密房不加入。Bot 通过原生房间成员关系判断是否可回复；付款授权由 New API 维护，不存于 Bot 本地。

验收覆盖：两个不同邀请人分别启用两个私密加密房，成员真 `@` 得到原房间加密回复；实测各房只扣对应付款人，普通聊天、旧消息、未授权房都不扣，撤销和移出一间都不影响另一间；同一消息不得重复扣，不能超上限。模型只收到当前文字和角色说明，不读取前文或串到另一房。各房间模型调用至少间隔 10 秒，全局只有一个请求在途；受限请求只在发起房间提示，不排队、不额外调用模型。整体停用只停止这个 `role-bot` 服务，不删设备状态、不重启聊天服务。
