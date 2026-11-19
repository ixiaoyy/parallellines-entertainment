"""衔接已认证的 Matrix 角色邀请与 New API 的本人额度确认。"""

from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import stat
import unicodedata

from synapse import __version__ as synapse_version
from synapse.api.constants import EventTypes, JoinRules, Membership
from synapse.api.errors import SynapseError
from synapse.http.client import BodyExceededMaxSize, SimpleHttpClient, read_body_with_max_size
from twisted.internet import defer, reactor
from twisted.internet.error import ConnectError, ConnectionLost
from twisted.web.client import RequestTransmissionFailed, ResponseFailed, ResponseNeverReceived
from twisted.web.http_headers import Headers

from .tavern_invites import (
    HOMESERVER,
    InviteResource,
    TavernInvites,
    invite_allowed,
    invite_room,
    no_duplicate_keys,
    reject,
    room_id_valid,
)


ROUTE = "/_synapse/client/tavern-flux/v1"
NEW_API = "https://api.pingxingxian.space"
INTENTS = f"{NEW_API}/api/tavern-flux/intents"
ONE_CLICK_INTENT = f"{INTENTS}/invite"
MODEL_OPTIONS = f"{INTENTS}/model-options"
SET_MODEL = f"{INTENTS}/set-model"
AUTHORIZATION_PAGE = f"{NEW_API}/tavern-flux/authorize?intent_id="
OIDC_PROVIDER = "oidc-parallellines"
FLUX_ROLE = "flux-the-cat"
FLUX = f"@bot_flux-the-cat:{HOMESERVER}"
ROLES = {FLUX_ROLE: FLUX, "sakana": f"@bot_sakana:{HOMESERVER}"}
ROLE_TARGETS = frozenset(ROLES.values())
MARKER = "space.pingxingxian.tavern_flux_intent"
ISSUE_NONCE = "space.pingxingxian.tavern_flux_issue"
KEY_FILE = Path("/data/tavern-flux-issuer-key")
KEY = re.compile(rb"[A-Za-z0-9._~-]{32,256}\Z")
INTENT = re.compile(r"[A-Za-z0-9_-]{22,128}\Z")
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
NONCE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
EVENT_ID = re.compile(r"\$[!-~]{1,254}\Z")
GATEWAY_TIMEOUT = 6
MAX_GATEWAY_BYTES = 8192
MAX_ACTIVE_FINISH = 32
MAX_MEMBERSHIP_LOOKBACK = 8
MAX_SAFE_INTEGER = (1 << 53) - 1


def forbidden():
    reject(403, "M_TAVERN_FLUX_FORBIDDEN")


def unavailable():
    reject(503, "M_TAVERN_FLUX_GATEWAY_UNAVAILABLE")


def state_changed():
    reject(409, "M_TAVERN_FLUX_STATE_CHANGED")


def private_encrypted_chat(events):
    """只有明确邀请制支持 Megolm 的普通房间可使用付费角色。"""
    rule = events.get((EventTypes.JoinRules, ""))
    crypto = events.get((EventTypes.RoomEncryption, ""))
    return bool(
        invite_room(events) and rule is not None and rule.content.get("join_rule") == JoinRules.INVITE
        and crypto is not None and crypto.content.get("algorithm") == "m.megolm.v1.aes-sha2"
    )


def private_encrypted_room(events, inviter, target=FLUX):
    """新邀请必须同时通过房间性质和原生邀请权限；其他 Bot 不走本判断。"""
    return private_encrypted_chat(events) and invite_allowed(events, inviter, target)


def requested_role(body):
    """旧客户端不发角色字段，仍只代表 Flux；不接受任意 Matrix 目标。"""
    role = body.get("role_id", FLUX_ROLE)
    if not isinstance(role, str) or role not in ROLES:
        reject(400, "M_INVALID_PARAM")
    return role


def gateway_role(role):
    """保持旧 Flux 的签发输入，新角色必须显式传递。"""
    return {} if role == FLUX_ROLE else {"role_id": role}


def model_id_valid(value):
    """只接收网关可核对的精确模型标识，不允许客户端传渠道、价格或付款人。"""
    return isinstance(value, str) and bool(MODEL_ID.fullmatch(value))


def room_label(events, room_id):
    """确认页显示 Synapse 取到的房名；不信任浏览器提交的标签。"""
    event = events.get((EventTypes.Name, ""))
    name = event.content.get("name") if event is not None else None
    if not isinstance(name, str):
        return room_id
    # 清理控制和方向字符，避免付款页面把另一个房间或域名显示成原房间。
    clean = "".join(c for c in name[:128] if not unicodedata.category(c).startswith("C")).strip()
    return clean or room_id


def private_issuer_key(path):
    """只读取预置于服务私有文件的签发凭据，不允许链接或共享权限。"""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and (info.st_uid, info.st_gid) == (os.geteuid(), os.getegid())
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 257):
            raise ValueError("Tavern Flux requires a private issuer credential file.")
        key = stream.read(258).removesuffix(b"\n")
    if not KEY.fullmatch(key):
        raise ValueError("Tavern Flux issuer credential format is invalid.")
    return key


class FluxGateway:
    """固定 HTTPS 签发/核验入口；不把 Matrix 令牌或响应正文透传。"""

    def __init__(self, api, key):
        self.client = SimpleHttpClient(api._hs, treq_args={"allow_redirects": False})
        self.key = key

    async def request(self, method, url, body=None):
        """收完整有界 JSON 响应；同一总限时覆盖连接、头和正文，不随重定向。"""
        try:
            status, value = await defer.ensureDeferred(
                self.request_response(method, url, body),
            ).addTimeout(GATEWAY_TIMEOUT, reactor)
        except (OSError, ValueError, RecursionError, defer.TimeoutError, BodyExceededMaxSize,
                ConnectError, ConnectionLost, RequestTransmissionFailed, ResponseFailed,
                ResponseNeverReceived, SynapseError):
            unavailable()
        if status == 404:
            reject(404, "M_TAVERN_FLUX_INTENT_NOT_FOUND")
        if status == 429:
            reject(429, "M_LIMIT_EXCEEDED")
        if url in (ONE_CLICK_INTENT, MODEL_OPTIONS, SET_MODEL) and isinstance(value, dict):
            # 只映射固定签发入口的约定错误；不把上游内容或钱包信息交给浏览器。
            code = value.get("error")
            if status == 400 and code == "flux_invalid_quota_limit":
                reject(400, "M_TAVERN_FLUX_LIMIT_INVALID")
            if status == 403 and code == "flux_wallet_unavailable":
                reject(403, "M_TAVERN_FLUX_WALLET_UNAVAILABLE")
            if status == 403 and code == "flux_authorization_denied":
                forbidden()
            if status == 409 and code == "flux_model_changed":
                reject(409, "M_TAVERN_FLUX_MODEL_CHANGED")
            if status == 409 and code == "flux_model_unavailable":
                reject(409, "M_TAVERN_FLUX_MODEL_UNAVAILABLE")
        if status == 409:
            state_changed()
        if status not in (200, 201) or not isinstance(value, dict):
            unavailable()
        return value

    async def request_response(self, method, url, body):
        """使用 Synapse 的受信 TLS 客户端；原始与解码响应均限制体积。"""
        headers = Headers({b"Authorization": [b"Bearer " + self.key], b"Accept": [b"application/json"]})
        data = None
        if body is not None:
            headers.setRawHeaders(b"Content-Type", [b"application/json"])
            data = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        response = await self.client.request(method, url, data=data, headers=headers)
        buffer = BytesIO()
        # 即使上游返回错误也消费有界正文；仅确认和模型接口解释约定失败码。
        await read_body_with_max_size(response, buffer, MAX_GATEWAY_BYTES)
        if response.code not in (200, 201) and (
            url not in (ONE_CLICK_INTENT, MODEL_OPTIONS, SET_MODEL) or response.code not in (400, 403, 409)
        ):
            return response.code, None
        content_type = response.headers.getRawHeaders(b"Content-Type", [])
        if len(content_type) != 1 or content_type[0].split(b";", 1)[0].strip().lower() != b"application/json":
            return response.code, None
        return response.code, json.loads(buffer.getvalue().decode("utf-8"), object_pairs_hook=no_duplicate_keys)


class TavernFlux:
    """私密加密房内按固定角色分别发起额度授权，不修改其他邀请入口。"""

    active_local_user = TavernInvites.active_local_user
    authenticate = TavernInvites.authenticate

    @staticmethod
    def parse_config(config):
        """未显式启用时不注册接口；启用只允许预置的固定密钥路径。"""
        if config == {"enabled": False} and config["enabled"] is False:
            return False
        if config == {"enabled": True, "issuer_key_file": str(KEY_FILE)} and config["enabled"] is True:
            return True
        raise ValueError("Use the fixed Tavern Flux enablement and issuer credential file.")

    def __init__(self, config, api):
        if config is False:
            return
        if config is not True or synapse_version.split("+", 1)[0] != "1.165.0" or api.server_name != HOMESERVER:
            raise ValueError("Tavern Flux requires the locked Synapse deployment.")
        if (os.geteuid(), os.getegid()) != (991, 991) or api._hs.config.worker.worker_app is not None:
            raise ValueError("Tavern Flux requires the single Synapse service process.")
        self.api = api
        self.gateway = FluxGateway(api, private_issuer_key(KEY_FILE))
        self.finishing_rooms = set()
        self.issuing_invites = set()
        for action in ("prepare", "finish", "invite", "model_options", "set_model"):
            path = f"{ROUTE}/{action.replace('_', '-')}"
            # 复用固定 Element Origin、Matrix bearer、有界 JSON 及 no-store 处理。
            api.register_web_resource(path, InviteResource(self, path, action))
        api.register_third_party_rules_callbacks(check_event_allowed=self.check_flux_invite)

    async def check_flux_invite(self, event, state):
        """付费角色只接受本次签发中目标明确的邀请，普通成员及其他角色不受影响。"""
        if (event.type == EventTypes.Member and event.state_key in ROLE_TARGETS
                and event.content.get("membership") == Membership.INVITE):
            marker = event.content.get(MARKER)
            nonce = event.content.get(ISSUE_NONCE)
            return bool(isinstance(marker, str) and INTENT.fullmatch(marker)
                        and isinstance(nonce, str) and NONCE.fullmatch(nonce)
                        and (event.room_id, event.state_key, event.sender, marker, nonce) in self.issuing_invites
                        and private_encrypted_room(state, event.sender, event.state_key)), None
        return True, None

    async def subject(self, inviter):
        """复用锁定版 Synapse 真实 OIDC account mapping，不从 `pl_` 本地段推断付款人。"""
        # ModuleApi 尚无反向读取方法，精确版本守卫后复用现成注册 datastore 的只读查询。
        candidates = [subject for provider, subject in await self.api._store.get_external_ids_by_user(inviter)
                      if provider == OIDC_PROVIDER]
        if len(candidates) != 1:
            forbidden()
        subject = candidates[0]
        if (not isinstance(subject, str) or not subject or len(subject) > 255
                or any(unicodedata.category(c).startswith("C") for c in subject)):
            forbidden()
        return subject

    async def room_events(self, room_id, inviter, target=FLUX):
        """只取房间资格、邀请权及该角色成员状态所需事件。"""
        return await self.api.get_room_state(room_id, (
            (EventTypes.Create, ""), (EventTypes.JoinRules, ""), (EventTypes.RoomEncryption, ""),
            (EventTypes.PowerLevels, ""), (EventTypes.Name, ""),
            (EventTypes.Member, inviter), (EventTypes.Member, target),
        ))

    async def room_state(self, room_id, inviter, target=FLUX):
        """要求当前用户具备在该房间新发目标角色邀请的原生权限。"""
        events = await self.room_events(room_id, inviter, target)
        if not private_encrypted_room(events, inviter, target):
            forbidden()
        return events

    async def model_room(self, room_id, inviter, target=FLUX, *, set_model=False):
        """只有该角色的原邀请人可改模型；查看未邀请角色的选项仍须有邀请权。"""
        events = await self.room_events(room_id, inviter, target)
        sender = events.get((EventTypes.Member, inviter))
        if not private_encrypted_chat(events) or sender is None or sender.content.get("membership") != Membership.JOIN:
            forbidden()
        member = events.get((EventTypes.Member, target))
        if member is not None and member.content.get("membership") in (Membership.INVITE, Membership.JOIN):
            event = await self.last_invite(member, room_id, target)
            intent = event.content.get(MARKER) if event is not None else None
            if not isinstance(intent, str) or not INTENT.fullmatch(intent) or not self.matches_invite(event, inviter, room_id, intent, target):
                forbidden()
        elif set_model:
            state_changed()
        elif not invite_allowed(events, inviter, target):
            forbidden()

    async def model_options(self, inviter, body):
        """由网关按当前付款人列出可用模型；读选项不建授权也不扣费。"""
        room_id = body.get("room_id")
        if set(body) - {"role_id"} != {"room_id"} or not room_id_valid(room_id):
            reject(400, "M_INVALID_PARAM")
        role = requested_role(body)
        await self.model_room(room_id, inviter, ROLES[role])
        value = await self.gateway.request("POST", MODEL_OPTIONS, {
            "room_id": room_id, "inviter_mxid": inviter, "subject": await self.subject(inviter),
            **gateway_role(role),
        })
        options, current, editable = value.get("options"), value.get("selected_model_id"), value.get("can_edit")
        if (set(value) != {"options", "selected_model_id", "can_edit"} or not isinstance(options, list)
                or len(options) > 16 or type(editable) is not bool
                or not (model_id_valid(current) or (current == "" and not editable))):
            unavailable()
        ids = set()
        for option in options:
            if (not isinstance(option, dict) or set(option) != {"id", "label"}
                    or not model_id_valid(option["id"]) or option["id"] in ids
                    or not isinstance(option["label"], str) or not 1 <= len(option["label"]) <= 96
                    or any(unicodedata.category(c).startswith("C") for c in option["label"])):
                unavailable()
            ids.add(option["id"])
        if current and not editable and current not in ids:
            unavailable()
        return value

    async def set_model(self, inviter, body):
        """只更改同房原授权的模型；网关负责精确钱包归属及并发检查。"""
        room_id, model_id, expected = (body.get(key) for key in ("room_id", "model_id", "expected_model_id"))
        if (set(body) - {"role_id"} != {"room_id", "model_id", "expected_model_id"} or not room_id_valid(room_id)
                or not model_id_valid(model_id) or not model_id_valid(expected)):
            reject(400, "M_INVALID_PARAM")
        role = requested_role(body)
        await self.model_room(room_id, inviter, ROLES[role], set_model=True)
        value = await self.gateway.request("POST", SET_MODEL, {
            "room_id": room_id, "inviter_mxid": inviter, "subject": await self.subject(inviter),
            "model_id": model_id, "expected_model_id": expected, **gateway_role(role),
        })
        if value != {"model_id": model_id}:
            unavailable()
        return value

    async def prepare(self, inviter, body):
        """仅签发待确认意图，不发送原生邀请或启用费用。"""
        room_id = body.get("room_id")
        if set(body) != {"room_id"} or not room_id_valid(room_id):
            reject(400, "M_INVALID_PARAM")
        subject = await self.subject(inviter)
        events = await self.room_state(room_id, inviter)
        value = await self.gateway.request("POST", INTENTS, {
            "room_id": room_id, "inviter_mxid": inviter, "subject": subject,
            "room_label": room_label(events, room_id),
        })
        intent = value.get("intent_id")
        if not isinstance(intent, str) or not INTENT.fullmatch(intent) or value.get("status") != "pending":
            unavailable()
        return {"intent_id": intent, "authorization_url": AUTHORIZATION_PAGE + intent}

    async def finish(self, inviter, body):
        """付款人确认后发送真正的原生邀请并激活；同房有限并发且不等待锁。"""
        room_id, intent = body.get("room_id"), body.get("intent_id")
        if (set(body) != {"room_id", "intent_id"} or not room_id_valid(room_id)
                or not isinstance(intent, str) or not INTENT.fullmatch(intent)):
            reject(400, "M_INVALID_PARAM")
        lock = self.claim_room(room_id, FLUX)
        try:
            return await self.finish_room(inviter, room_id, intent)
        finally:
            self.finishing_rooms.remove(lock)

    def claim_room(self, room_id, target):
        """只对同一房间的同一角色串行，新旧 Flux 入口共享同一把锁。"""
        key = (room_id, target)
        if key in self.finishing_rooms or len(self.finishing_rooms) >= MAX_ACTIVE_FINISH:
            reject(429, "M_LIMIT_EXCEEDED")
        self.finishing_rooms.add(key)
        return key

    async def invite(self, inviter, body):
        """用当前 Matrix 登录人完成一次费用确认和邀请；重试只复用同一个授权。"""
        room_id, request_id, limit = (body.get(key) for key in ("room_id", "request_id", "limit_usd_micros"))
        required = {"room_id", "request_id", "limit_usd_micros"}
        if (set(body) - {"model_id", "role_id"} != required or not room_id_valid(room_id)
                or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
                or type(limit) is not int or not 0 <= limit <= MAX_SAFE_INTEGER
                or ("model_id" in body and not model_id_valid(body["model_id"]))):
            reject(400, "M_INVALID_PARAM")
        role = requested_role(body)
        target = ROLES[role]
        lock = self.claim_room(room_id, target)
        try:
            subject = await self.subject(inviter)
            events = await self.room_state(room_id, inviter, target)
            # 只传原生账号映射和已确认的费用选项；0 不设房间金额上限，钱包扣费仍由网关限制。
            request = {
                "room_id": room_id, "inviter_mxid": inviter, "subject": subject,
                "room_label": room_label(events, room_id), "request_id": request_id, "limit_usd_micros": limit,
                **gateway_role(role),
            }
            if "model_id" in body:
                request["model_id"] = body["model_id"]
            value = await self.gateway.request("POST", ONE_CLICK_INTENT, request)
            intent, status = value.get("intent_id"), value.get("status")
            if not isinstance(intent, str) or not INTENT.fullmatch(intent) or status not in ("confirmed", "active"):
                unavailable()
            return await self.activate_room(inviter, room_id, intent, status, role)
        finally:
            self.finishing_rooms.remove(lock)

    async def finish_room(self, inviter, room_id, intent):
        """只以匹配的原生事件结单，断线重试能对账已发送或已经被 Bot 接受的邀请。"""
        await self.room_state(room_id, inviter)
        subject = await self.subject(inviter)
        intent_url = f"{INTENTS}/{intent}"
        value = await self.gateway.request("GET", intent_url)
        if (value.get("intent_id") != intent or value.get("room_id") != room_id
                or value.get("inviter_mxid") != inviter or value.get("subject") != subject
                or value.get("role_id", FLUX_ROLE) != FLUX_ROLE):
            forbidden()
        status = value.get("status")
        if status == "pending":
            return {"status": "pending"}
        if status not in ("confirmed", "active"):
            state_changed()
        return await self.activate_room(inviter, room_id, intent, status)

    async def activate_room(self, inviter, room_id, intent, status, role=FLUX_ROLE):
        """以同房同授权的原生邀请为准，确认成功但未返回时不重复发邀请。"""
        target = ROLES[role]
        events = await self.room_state(room_id, inviter, target)
        member = events.get((EventTypes.Member, target))
        membership = member.content.get("membership") if member is not None else None
        if membership in (Membership.INVITE, Membership.JOIN):
            event = await self.last_invite(member, room_id, target)
        else:
            if status == "active":
                state_changed()
            # 之前的邀请若已撤回或成员被移除，旧的超时请求不能把角色邀回房间。
            prior = await self.last_invite(member, room_id, target)
            if prior is not None and prior.content.get(MARKER) == intent:
                state_changed()
            await self.active_local_user(inviter)
            event = await self.issue_invite(inviter, room_id, intent, target)
        if not self.matches_invite(event, inviter, room_id, intent, target):
            state_changed()
        if status == "active":
            return {"status": "active"}
        activated = await self.gateway.request("POST", f"{INTENTS}/{intent}/activate", {
            "room_id": room_id, "inviter_mxid": inviter, "invite_event_id": event.event_id, **gateway_role(role),
        })
        if activated.get("status") != "active":
            unavailable()
        return {"status": "active"}

    @staticmethod
    def matches_invite(event, inviter, room_id, intent, target=FLUX):
        """只接受固定角色、同房、同人的原生邀请事件，不接收浏览器传入 event ID。"""
        proof = event.content.get(ISSUE_NONCE) if event is not None else None
        return bool(event is not None and event.type == EventTypes.Member and event.room_id == room_id
                    and event.sender == inviter and event.state_key == target
                    and event.content.get("membership") == Membership.INVITE
                    and event.content.get(MARKER) == intent and isinstance(proof, str) and NONCE.fullmatch(proof)
                    and EVENT_ID.fullmatch(event.event_id))

    async def issue_invite(self, inviter, room_id, intent, target=FLUX):
        """已确认这一个房间的费用后，为一次原生写入开启短暂、不可重放的签发许可。"""
        nonce = secrets.token_urlsafe(32)
        issue = (room_id, target, inviter, intent, nonce)
        self.issuing_invites.add(issue)
        try:
            return await self.api.update_room_membership(
                sender=inviter, target=target, room_id=room_id, new_membership=Membership.INVITE,
                content={MARKER: intent, ISSUE_NONCE: nonce},
            )
        finally:
            self.issuing_invites.remove(issue)

    async def last_invite(self, member, room_id, target=FLUX):
        """只沿原生成员事件替换链有限回溯，不读对话历史；链未知时拒绝再发送。"""
        if member is None:
            return None
        for _ in range(MAX_MEMBERSHIP_LOOKBACK):
            if member is None or member.type != EventTypes.Member or member.state_key != target:
                state_changed()
            if member.content.get("membership") == Membership.INVITE:
                return member
            previous = member.unsigned.get("replaces_state")
            if previous is None:
                return None
            if not isinstance(previous, str) or not EVENT_ID.fullmatch(previous):
                state_changed()
            # Synapse 用此字段记下被替换的状态，本查找只接受同房同角色的事件。
            member = await self.api._store.get_event(previous, allow_none=True, check_room_id=room_id)
        state_changed()
