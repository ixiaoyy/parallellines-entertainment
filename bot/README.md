# 从原角色卡准备聊天频道机器人

本目录是独立的角色机器人接入。Element 仍用现有邀请和 `@` 菜单；只有已经接入费用的角色会显示钱包扣费提示，普通成员和其他机器人仍走原生邀请。Flux the Cat 和 Sakana 使用独立账号，可加入同一个私密加密房。服务器私有导入原卡，界面只链接上游原卡。

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
| `first_mes` | 接受新邀请入房后，以纯文本发送原卡开场白；不调用模型，不给早已加入的房间补发 |
| `extensions.talkativeness` | 只读取 `0` 至 `1` 的接话倾向，用于选择未被点名的新消息；不增加房间开关 |
| PNG 头像 | 导出不含原卡文本、扩展和备注的 PNG；JSON 不获取外部头像地址，也不清除账号原有头像 |
| 作者说明、版本、`system_prompt`、世界书、备选开场白、脚本与其余扩展 | 不启用为行为，不执行脚本，不下载其中的资源 |

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

运行账号应拥有私有角色目录、配置文件和预先创建的 `data/bot/<role-id>`；目录仅该账号可读写，秘密文件仅该账号可读。设备和加密状态必须持久化，不能为了修复重连而清空 `data/` 或换设备。房间成员须事先知道触发机器人接话的当前文本会以明文发给模型，房间的端到端加密并不能保护送到模型的副本。

启动后，在 Element 的私密加密房间打开邀请框，有邀请权限的成员可搜索 `Flux`。选中后在同一窗口阅读扣费说明并邀请，不需要另登 New API。默认不设这个房间的金额上限，留空或 `0` 都如此；需要时可选填自定义上限。原卡开场白不调用模型；成员实际 `@` 或 Flux 命中原卡接话倾向时，模型用量只计入邀请人已绑定的 New API 钱包，不使用订阅或其他成员额度。授权仍按当前 24 小时有效期处理，不自动续期。没有房间授权、授权已撤销或额度不足时，不再调用模型或切换付款人。不用预填房间 ID 或登记成员账号，公开房和未加密房不加入。Bot 通过原生房间成员关系判断是否可回复；授权由 New API 维护，不存于 Bot 本地。

邀请时可从网关核对过的模型中选择。授权生效后，仍在房间的原付款人可从房间里的「Flux 模型」入口修改。改动只对该房间生效，不更换付款人，不重置用量、上限或有效期；已经预留的请求仍按旧模型结算。当前模型不可用时不会自动换用其他模型。现有角色配置的 `model_id` 仍供专用网关识别请求，真正送给模型服务的标识取自该房授权。

Sakana 使用 [官方 V2 原卡](https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/e3ca170989689c6eae8b5cba8b6c49390bdbebbf/assets/character/contest_Assistant_Sakana.png)，原文设定保留，默认生成简体中文回复。本期只在真正被 `@` 时回复，不自动发送开场白或主动接话。未取得独立头像再发布许可时使用 Matrix 默认头像。两个角色分别邀请、分别选模型：每个角色只使用自己在当前房间的原邀请人授权和可选金额上限；改模型或撤销一只不会改变另一只的授权。同一条消息对同一角色只计费一次。旧 Flux 的授权、模型、钱包、开场与主动接话规则保持原样。

验收覆盖：两个不同邀请人分别在私密加密房用默认 `0` 和自定义上限启用 Flux，成员真 `@` 得到原房间加密回复；旧的 `@` 联调已证明各房只扣对应付款人；新版还须覆盖无提及时按卡接话，以及原卡开场不扣、旧消息/机器人消息/未授权房都不调用模型，撤销和移出一间都不影响另一间。同一消息不得重复扣；默认 `0` 仍不可超过钱包余额，自定义上限也不可超出。模型只收到当前文字和角色说明，不读取前文或串到另一房。每个角色在同房的模型调用至少间隔 10 秒，每个角色实例最多一个请求在途；受限请求只在发起房间提示，不排队、不额外调用模型。停用只停止对应的 `role-bot` 服务，不删设备状态、不重启聊天服务。
