"""仅为 Flux 衔接已认证的 Matrix 邀请与 New API 的本人额度确认。"""

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
    no_duplicate_keys,
    reject,
    room_id_valid,
)


ROUTE = "/_synapse/client/tavern-flux/v1"
NEW_API = "https://api.pingxingxian.space"
INTENTS = f"{NEW_API}/api/tavern-flux/intents"
ONE_CLICK_INTENT = f"{INTENTS}/invite"
AUTHORIZATION_PAGE = f"{NEW_API}/tavern-flux/authorize?intent_id="
OIDC_PROVIDER = "oidc-parallellines"
FLUX = f"@bot_flux-the-cat:{HOMESERVER}"
MARKER = "space.pingxingxian.tavern_flux_intent"
ISSUE_NONCE = "space.pingxingxian.tavern_flux_issue"
KEY_FILE = Path("/data/tavern-flux-issuer-key")
KEY = re.compile(rb"[A-Za-z0-9._~-]{32,256}\Z")
INTENT = re.compile(r"[A-Za-z0-9_-]{22,128}\Z")
REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
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


def private_encrypted_room(events, inviter):
    """只认明确邀请制与原生 Megolm；普通成员/其他 Bot 不走本判断。"""
    rule = events.get((EventTypes.JoinRules, ""))
    crypto = events.get((EventTypes.RoomEncryption, ""))
    return bool(
        rule is not None and rule.content.get("join_rule") == JoinRules.INVITE
        and crypto is not None and crypto.content.get("algorithm") == "m.megolm.v1.aes-sha2"
        and invite_allowed(events, inviter, FLUX)
    )


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
        if status == 409:
            state_changed()
        if status == 429:
            reject(429, "M_LIMIT_EXCEEDED")
        if url == ONE_CLICK_INTENT and isinstance(value, dict):
            # 只映射固定签发入口的约定错误；不把上游内容或钱包信息交给浏览器。
            if status == 400 and value.get("error") == "flux_invalid_quota_limit":
                reject(400, "M_TAVERN_FLUX_LIMIT_INVALID")
            if status == 403 and value.get("error") == "flux_wallet_unavailable":
                reject(403, "M_TAVERN_FLUX_WALLET_UNAVAILABLE")
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
        # 即使上游返回错误也消费有界正文；仅新签发入口解释约定的失败码。
        await read_body_with_max_size(response, buffer, MAX_GATEWAY_BYTES)
        if response.code not in (200, 201) and (url != ONE_CLICK_INTENT or response.code not in (400, 403)):
            return response.code, None
        content_type = response.headers.getRawHeaders(b"Content-Type", [])
        if len(content_type) != 1 or content_type[0].split(b";", 1)[0].strip().lower() != b"application/json":
            return response.code, None
        return response.code, json.loads(buffer.getvalue().decode("utf-8"), object_pairs_hook=no_duplicate_keys)


class TavernFlux:
    """私密加密房内为固定 Flux 角色发起额度授权，不修改其他邀请入口。"""

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
        for action in ("prepare", "finish", "invite"):
            path = f"{ROUTE}/{action}"
            # 复用固定 Element Origin、Matrix bearer、有界 JSON 及 no-store 处理。
            api.register_web_resource(path, InviteResource(self, path, action))
        api.register_third_party_rules_callbacks(check_event_allowed=self.check_flux_invite)

    async def check_flux_invite(self, event, state):
        """Flux 只接受本次签发中的已确认邀请，普通成员及其他角色不受影响。"""
        if (event.type == EventTypes.Member and event.state_key == FLUX
                and event.content.get("membership") == Membership.INVITE):
            marker = event.content.get(MARKER)
            nonce = event.content.get(ISSUE_NONCE)
            return bool(isinstance(marker, str) and INTENT.fullmatch(marker)
                        and isinstance(nonce, str) and NONCE.fullmatch(nonce)
                        and (event.room_id, event.sender, marker, nonce) in self.issuing_invites
                        and private_encrypted_room(state, event.sender)), None
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

    async def room_state(self, room_id, inviter):
        """查询只包含决定付款、房间资格及该角色成员状态所需事件。"""
        events = await self.api.get_room_state(room_id, (
            (EventTypes.Create, ""), (EventTypes.JoinRules, ""), (EventTypes.RoomEncryption, ""),
            (EventTypes.PowerLevels, ""), (EventTypes.Name, ""),
            (EventTypes.Member, inviter), (EventTypes.Member, FLUX),
        ))
        if not private_encrypted_room(events, inviter):
            forbidden()
        return events

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
        self.claim_room(room_id)
        try:
            return await self.finish_room(inviter, room_id, intent)
        finally:
            self.finishing_rooms.remove(room_id)

    def claim_room(self, room_id):
        """串行处理同一房间的新旧签发入口，并限制同时处理的房间数。"""
        if room_id in self.finishing_rooms or len(self.finishing_rooms) >= MAX_ACTIVE_FINISH:
            reject(429, "M_LIMIT_EXCEEDED")
        self.finishing_rooms.add(room_id)

    async def invite(self, inviter, body):
        """用当前 Matrix 登录人完成一次费用确认和邀请；重试只复用同一个授权。"""
        room_id, request_id, limit = (body.get(key) for key in ("room_id", "request_id", "limit_usd_micros"))
        if (set(body) != {"room_id", "request_id", "limit_usd_micros"} or not room_id_valid(room_id)
                or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
                or type(limit) is not int or not 0 <= limit <= MAX_SAFE_INTEGER):
            reject(400, "M_INVALID_PARAM")
        self.claim_room(room_id)
        try:
            subject = await self.subject(inviter)
            events = await self.room_state(room_id, inviter)
            # 只传原生账号映射和已确认的费用选项；0 不设房间金额上限，钱包扣费仍由网关限制。
            value = await self.gateway.request("POST", ONE_CLICK_INTENT, {
                "room_id": room_id, "inviter_mxid": inviter, "subject": subject,
                "room_label": room_label(events, room_id), "request_id": request_id, "limit_usd_micros": limit,
            })
            intent, status = value.get("intent_id"), value.get("status")
            if not isinstance(intent, str) or not INTENT.fullmatch(intent) or status not in ("confirmed", "active"):
                unavailable()
            return await self.activate_room(inviter, room_id, intent, status)
        finally:
            self.finishing_rooms.remove(room_id)

    async def finish_room(self, inviter, room_id, intent):
        """只以匹配的原生事件结单，断线重试能对账已发送或已经被 Bot 接受的邀请。"""
        await self.room_state(room_id, inviter)
        subject = await self.subject(inviter)
        intent_url = f"{INTENTS}/{intent}"
        value = await self.gateway.request("GET", intent_url)
        if (value.get("intent_id") != intent or value.get("room_id") != room_id
                or value.get("inviter_mxid") != inviter or value.get("subject") != subject):
            forbidden()
        status = value.get("status")
        if status == "pending":
            return {"status": "pending"}
        if status not in ("confirmed", "active"):
            state_changed()
        return await self.activate_room(inviter, room_id, intent, status)

    async def activate_room(self, inviter, room_id, intent, status):
        """以同房同授权的原生邀请为准，确认成功但未返回时不重复发邀请。"""
        events = await self.room_state(room_id, inviter)
        member = events.get((EventTypes.Member, FLUX))
        membership = member.content.get("membership") if member is not None else None
        if membership in (Membership.INVITE, Membership.JOIN):
            event = await self.last_invite(member, room_id)
        else:
            if status == "active":
                state_changed()
            # 之前的邀请若已撤回或成员被移除，旧的超时请求不能把角色邀回房间。
            prior = await self.last_invite(member, room_id)
            if prior is not None and prior.content.get(MARKER) == intent:
                state_changed()
            await self.active_local_user(inviter)
            event = await self.issue_invite(inviter, room_id, intent)
        if not self.matches_invite(event, inviter, room_id, intent):
            state_changed()
        if status == "active":
            return {"status": "active"}
        activated = await self.gateway.request("POST", f"{INTENTS}/{intent}/activate", {
            "room_id": room_id, "inviter_mxid": inviter, "invite_event_id": event.event_id,
        })
        if activated.get("status") != "active":
            unavailable()
        return {"status": "active"}

    @staticmethod
    def matches_invite(event, inviter, room_id, intent):
        """只接受固定角色、同房、同人的原生邀请事件，不接收浏览器传入 event ID。"""
        proof = event.content.get(ISSUE_NONCE) if event is not None else None
        return bool(event is not None and event.type == EventTypes.Member and event.room_id == room_id
                    and event.sender == inviter and event.state_key == FLUX
                    and event.content.get("membership") == Membership.INVITE
                    and event.content.get(MARKER) == intent and isinstance(proof, str) and NONCE.fullmatch(proof)
                    and EVENT_ID.fullmatch(event.event_id))

    async def issue_invite(self, inviter, room_id, intent):
        """已确认这一个房间的费用后，为一次原生写入开启短暂、不可重放的签发许可。"""
        nonce = secrets.token_urlsafe(32)
        issue = (room_id, inviter, intent, nonce)
        self.issuing_invites.add(issue)
        try:
            return await self.api.update_room_membership(
                sender=inviter, target=FLUX, room_id=room_id, new_membership=Membership.INVITE,
                content={MARKER: intent, ISSUE_NONCE: nonce},
            )
        finally:
            self.issuing_invites.remove(issue)

    async def last_invite(self, member, room_id):
        """只沿原生成员事件替换链有限回溯，不读对话历史；链未知时拒绝再发送。"""
        if member is None:
            return None
        for _ in range(MAX_MEMBERSHIP_LOOKBACK):
            if member is None or member.type != EventTypes.Member or member.state_key != FLUX:
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
