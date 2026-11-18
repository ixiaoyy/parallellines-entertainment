"""Tavern 一次性邀请；Synapse 负责身份和实际的成员事件。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import time

from synapse import __version__ as synapse_version
from synapse.api.constants import EventTypes, JoinRules, Membership
from synapse.api.errors import ShadowBanError, SynapseError
from synapse.event_auth import get_named_level, get_user_power_level
from synapse.http.server import respond_with_json
from synapse.module_api import DirectServeJsonResource
from twisted.internet import defer, reactor


ORIGIN = b"https://tavern.pingxingxian.space"
HOMESERVER = "matrix.pingxingxian.space"
STORAGE_DIR = Path("/data/tavern-invites")
ROUTE = "/_synapse/client/tavern-invites/v1"
EVENT_MARKER = "space.pingxingxian.tavern_invite"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ROOM = re.compile(r"![!-~]{1,254}\Z")
BEARER = re.compile(rb"(?i:Bearer) [A-Za-z0-9._~+/-]+={0,2}\Z")
TTL = 24 * 60 * 60
MAX_RECORDS = 512
MAX_PER_INVITER = 20
MAX_LEDGER_BYTES = 512 * 1024
MAX_BODY_BYTES = 512
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
STATES = {"issued", "revoked"} | {
    f"{kind}:{attempt}"
    for kind in ("claimed", "complete")
    for attempt in range(MAX_RETRIES + 1)
}
logger = logging.getLogger(__name__)


class InviteError(Exception):
    """只向客户端返回稳定错误码，不传递房间、帐号、会话或邀请数据。"""

    def __init__(self, status, errcode):
        super().__init__(errcode)
        self.status = status
        self.errcode = errcode


def reject(status, errcode):
    raise InviteError(status, errcode)


def unavailable():
    reject(404, "M_TAVERN_INVITE_UNAVAILABLE")


def room_forbidden():
    reject(403, "M_TAVERN_INVITE_FORBIDDEN")


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def room_id_valid(value):
    return isinstance(value, str) and bool(ROOM.fullmatch(value)) and not any(c in value for c in "/?#")


def user_id_local(value):
    return isinstance(value, str) and value.startswith("@") and value.endswith(":" + HOMESERVER)


def no_duplicate_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


class InviteStore:
    """私有有界账本；短锁只包文件读写，永不跨 Synapse 异步操作持有。"""

    def __init__(self, directory):
        self.directory = directory
        if self.directory.resolve(strict=True) != self.directory:
            raise ValueError("Tavern invitation storage must not use symlinks.")
        self.uid = os.geteuid()
        self.gid = os.getegid()
        fd = self._open_directory()
        os.close(fd)

    def _open_directory(self):
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (self.uid, self.gid, 0o700):
            os.close(fd)
            raise PermissionError("Tavern invitation directory is not private.")
        return fd

    def _file_size(self, fd):
        info = os.fstat(fd)
        if not (
            stat.S_ISREG(info.st_mode)
            and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (self.uid, self.gid, 0o600)
            and info.st_nlink == 1
        ):
            raise PermissionError("Tavern invitation file is not private.")
        return info.st_size

    def _read(self, directory):
        try:
            fd = os.open("invites.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, "rb") as stream:
            if self._file_size(fd) > MAX_LEDGER_BYTES:
                raise ValueError("Tavern invitation ledger is too large.")
            records = json.loads(stream.read(MAX_LEDGER_BYTES + 1), object_pairs_hook=no_duplicate_keys)
        if not isinstance(records, dict) or len(records) > MAX_RECORDS:
            raise ValueError("Invalid Tavern invitation ledger.")
        for key, value in records.items():
            if not (
                DIGEST.fullmatch(key)
                and isinstance(value, dict)
                and set(value) == {"inviter", "room_id", "expires_at", "claimant", "status"}
                and user_id_local(value["inviter"])
                and room_id_valid(value["room_id"])
                and type(value["expires_at"]) is int
                and value["status"] in STATES
                and (
                    value["claimant"] is None
                    if value["status"] in {"issued", "revoked"}
                    else user_id_local(value["claimant"])
                )
            ):
                raise ValueError("Invalid Tavern invitation record.")
        return records

    @staticmethod
    def _write(directory, records):
        data = (json.dumps(records, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        if len(data) > MAX_LEDGER_BYTES:
            reject(503, "M_TAVERN_INVITE_BUSY")
        temporary = ".invite-write-" + secrets.token_hex(12)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "invites.json", src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            # 只清理本次用 O_EXCL 建立的临时文件；已经原子换名时无需清理。
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _transaction(self, operation):
        directory = self._open_directory()
        try:
            lock = os.open("invites.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=directory)
            try:
                if self._file_size(lock) != 0:
                    raise ValueError("Invalid Tavern invitation lock.")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    reject(503, "M_TAVERN_INVITE_BUSY")
                records = self._read(directory)
                result, changed = operation(records, int(time.time()))
                if changed:
                    self._write(directory, records)
                return result
            finally:
                os.close(lock)
        finally:
            os.close(directory)

    def create(self, inviter, room_id):
        """保留窗口内配额包含已撤销券，避免通过反复创建、撤销耗尽账本。"""
        token = secrets.token_urlsafe(32)
        key = token_digest(token)

        def apply(records, now):
            expired = [key for key, item in records.items() if item["expires_at"] <= now]
            for old in expired:
                del records[old]
            if len(records) >= MAX_RECORDS or sum(item["inviter"] == inviter for item in records.values()) >= MAX_PER_INVITER:
                reject(429, "M_LIMIT_EXCEEDED")
            if key in records:
                reject(503, "M_TAVERN_INVITE_BUSY")
            expires_at = now + TTL
            records[key] = {"inviter": inviter, "room_id": room_id, "expires_at": expires_at,
                            "claimant": None, "status": "issued"}
            return {"token": token, "expires_at": expires_at}, True

        return self._transaction(apply)

    @staticmethod
    def _usable(records, now, key, claimant):
        item = records.get(key)
        if (
            item is None or item["expires_at"] <= now or item["status"] == "revoked"
            or item["claimant"] not in (None, claimant) or item["inviter"] == claimant
        ):
            unavailable()
        return item

    def inspect(self, key, claimant):
        """读取券归属以先核权限；读取不绑定领取者。"""
        return self._transaction(
            lambda records, now: (self._usable(records, now, key, claimant).copy(), False)
        )

    def claim(self, key, claimant):
        """先原子绑定再发邀请；只有一次可发，后续同账号有限次数只读对账。"""
        def apply(records, now):
            item = self._usable(records, now, key, claimant)
            if item["status"] == "issued":
                item.update(claimant=claimant, status="claimed:0")
                return (item.copy(), True), True
            kind, attempt = item["status"].split(":")
            if int(attempt) >= MAX_RETRIES:
                unavailable()
            item["status"] = f"{kind}:{int(attempt) + 1}"
            return (item.copy(), False), True

        return self._transaction(apply)

    def complete(self, key, claimant):
        """确认看到可用原生成员状态；不恢复领取资格，也不放宽重试次数。"""
        def apply(records, now):
            item = self._usable(records, now, key, claimant)
            if item["claimant"] != claimant:
                unavailable()
            kind, attempt = item["status"].split(":")
            if kind == "complete":
                return None, False
            item["status"] = f"complete:{attempt}"
            return None, True

        return self._transaction(apply)

    def revoke(self, key, inviter):
        """只有原邀请者可撤销未领券；已认领不撤原生邀请或成员资格。"""
        def apply(records, now):
            item = records.get(key)
            if item is None or item["inviter"] != inviter or item["expires_at"] <= now:
                unavailable()
            if item["status"] == "revoked":
                return {}, False
            if item["status"] != "issued":
                reject(409, "M_TAVERN_INVITE_USED")
            item["status"] = "revoked"
            return {}, True

        return self._transaction(apply)


def member_state(events, user):
    event = events.get((EventTypes.Member, user))
    return event.content.get("membership") if event is not None else None


def invite_room(events):
    """原生缺失 join_rules 时默认邀请制；空间/陌生或非邀请制房间不接管。"""
    create = events.get((EventTypes.Create, ""))
    rule = events.get((EventTypes.JoinRules, ""))
    return (
        create is not None
        and create.content.get("type") != "m.space"
        and (rule is None or rule.content.get("join_rule") == JoinRules.INVITE)
    )


def invite_allowed(events, inviter, claimant=None):
    """复用锁定版 Synapse 计算权限，包含新房间版本的创建者规则。"""
    return (
        invite_room(events)
        and member_state(events, inviter) == Membership.JOIN
        and get_user_power_level(inviter, events) >= get_named_level(events, "invite", 0)
        and (claimant is None or member_state(events, claimant) != Membership.BAN)
    )


class TavernInvites:
    """已认证的 Tavern 邀请接口，不接受客户端指定发送人或直接加入他人。"""

    @staticmethod
    def parse_config(config):
        if config != {"storage_dir": str(STORAGE_DIR)}:
            raise ValueError("Tavern invites require the fixed private storage directory.")
        return STORAGE_DIR

    def __init__(self, config, api):
        if (
            synapse_version.split("+", 1)[0] != "1.165.0" or api.server_name != HOMESERVER
            or config != STORAGE_DIR or (os.geteuid(), os.getegid()) != (991, 991)
        ):
            raise ValueError("Tavern invitations require the reviewed Synapse deployment.")
        self.api = api
        self.store = InviteStore(config)
        for action in ("create", "redeem", "revoke"):
            path = f"{ROUTE}/{action}"
            api.register_web_resource(path, InviteResource(self, path, action))
        api.register_third_party_rules_callbacks(check_event_allowed=self.check_feature_invite)

    @staticmethod
    async def check_feature_invite(event, state_events):
        """原生写入时再核房间没有切为公开；仅约束带本功能标志的邀请。"""
        if (
            event.type == EventTypes.Member and event.content.get("membership") == Membership.INVITE
            and event.content.get(EVENT_MARKER) is True
        ):
            return invite_allowed(state_events, event.sender, event.state_key), None
        return True, None

    async def active_local_user(self, user):
        """模块代发会合成 Requester，显式补核不随其继承的账号限制。"""
        if not user_id_local(user) or not self.api.is_mine(user):
            room_forbidden()
        info = await self.api.get_userinfo_by_id(user)
        if (
            info is None or info.is_guest or info.is_deactivated or info.is_shadow_banned
            or info.locked or info.suspended or not info.approved or info.appservice_id is not None
            or info.user_type is not None
        ):
            room_forbidden()

    async def authenticate(self, request):
        requester = await self.api.get_user_by_req(request, allow_guest=False, allow_expired=False)
        if requester.is_guest or requester.shadow_banned or requester.app_service_id is not None:
            room_forbidden()
        user = requester.user.to_string()
        await self.active_local_user(user)
        return user

    async def room_state(self, room_id, inviter, claimant=None):
        filters = [(EventTypes.Create, ""), (EventTypes.PowerLevels, ""), (EventTypes.JoinRules, ""),
                   (EventTypes.Member, inviter)]
        if claimant is not None:
            filters.append((EventTypes.Member, claimant))
        events = await self.api.get_room_state(room_id, filters)
        if not invite_allowed(events, inviter, claimant):
            room_forbidden()
        return member_state(events, claimant) if claimant is not None else None

    async def create(self, inviter, body):
        room_id = body.get("room_id")
        if set(body) != {"room_id"} or not room_id_valid(room_id):
            reject(400, "M_INVALID_PARAM")
        await self.room_state(room_id, inviter)
        return await self.api.defer_to_thread(self.store.create, inviter, room_id)

    async def revoke(self, inviter, body):
        key = self.digest_body(body)
        return await self.api.defer_to_thread(self.store.revoke, key, inviter)

    @staticmethod
    def digest_body(body):
        token = body.get("token")
        if set(body) != {"token"} or not isinstance(token, str) or not TOKEN.fullmatch(token):
            reject(400, "M_INVALID_PARAM")
        return token_digest(token)

    async def redeem(self, claimant, body):
        key = self.digest_body(body)
        item = await self.api.defer_to_thread(self.store.inspect, key, claimant)
        inviter, room_id = item["inviter"], item["room_id"]
        await self.active_local_user(inviter)
        membership = await self.room_state(room_id, inviter, claimant)
        _, send = await self.api.defer_to_thread(self.store.claim, key, claimant)
        if membership not in (Membership.INVITE, Membership.JOIN) and send:
            event = await self.api.update_room_membership(
                sender=inviter, target=claimant, room_id=room_id, new_membership=Membership.INVITE,
                content={EVENT_MARKER: True},
            )
            if (
                event.type != EventTypes.Member or event.room_id != room_id or event.sender != inviter
                or event.state_key != claimant or event.content.get("membership") != Membership.INVITE
            ):
                reject(503, "M_TAVERN_INVITE_PENDING")
        # 客户端以自己的已认证 Matrix 会话加入。存在不确定结果时只读核实，不重发被撤回的邀请。
        if await self.room_state(room_id, inviter, claimant) not in (Membership.INVITE, Membership.JOIN):
            reject(503, "M_TAVERN_INVITE_PENDING")
        await self.api.defer_to_thread(self.store.complete, key, claimant)
        return {"room_id": room_id}


class InviteResource(DirectServeJsonResource):
    """覆盖 Synapse 通用 '*' CORS，只向固定 Tavern 网页允许授权 JSON POST。"""

    isLeaf = True

    def __init__(self, module, path, action):
        super().__init__()
        self.module = module
        self.path = path.encode("ascii")
        self.action = action

    @staticmethod
    def headers(request, key):
        return request.requestHeaders.getRawHeaders(key, [])

    def check_endpoint(self, request):
        if request.uri != self.path:
            reject(400, "M_INVALID_PARAM")
        if self.headers(request, b"origin") != [ORIGIN] or self.headers(request, b"cookie"):
            reject(403, "M_FORBIDDEN")

    def apply_headers(self, request):
        request.setHeader(b"Vary", b"Origin")
        request.setHeader(b"Cache-Control", b"no-store")
        if self.headers(request, b"origin") == [ORIGIN]:
            request.setHeader(b"Access-Control-Allow-Origin", ORIGIN)

    def _send_response(self, request, code, response_object):
        self.apply_headers(request)
        respond_with_json(request, code, response_object, send_cors=False)

    def _send_error_response(self, failure, request):
        error = failure.value
        if isinstance(error, InviteError):
            status, code = error.status, error.errcode
        elif isinstance(error, defer.TimeoutError):
            status, code = 503, "M_TAVERN_INVITE_BUSY"
        elif isinstance(error, ShadowBanError):
            status, code = 403, "M_TAVERN_INVITE_FORBIDDEN"
        elif isinstance(error, SynapseError) and error.code in (401, 403, 429):
            status = error.code
            code = {401: "M_UNKNOWN_TOKEN", 403: "M_TAVERN_INVITE_FORBIDDEN", 429: "M_LIMIT_EXCEEDED"}[status]
        elif isinstance(error, SynapseError) and error.code == 405:
            status, code = 405, "M_UNRECOGNIZED"
        else:
            # 不记录请求、令牌、room ID、异常正文或独立身份信息。
            logger.error("tavern_invite_operation_failed error_type=%s", type(error).__name__)
            status, code = 500, "M_UNKNOWN"
        self._send_response(request, status, {"errcode": code, "error": code})

    async def _async_render_OPTIONS(self, request):
        self.check_endpoint(request)
        methods = self.headers(request, b"access-control-request-method")
        fields = {field.strip().lower() for header in self.headers(request, b"access-control-request-headers")
                  for field in header.split(b",")}
        if methods != [b"POST"] or not fields <= {b"authorization", b"content-type"}:
            reject(403, "M_FORBIDDEN")
        self.apply_headers(request)
        request.setHeader(b"Access-Control-Allow-Methods", b"POST, OPTIONS")
        request.setHeader(b"Access-Control-Allow-Headers", b"Authorization, Content-Type")
        request.setResponseCode(204)
        request.finish()
        return None

    async def _async_render_POST(self, request):
        self.check_endpoint(request)
        bearer = self.headers(request, b"authorization")
        if len(bearer) != 1 or len(bearer[0]) > 2048 or not BEARER.fullmatch(bearer[0]):
            reject(401, "M_MISSING_TOKEN")
        content_type = self.headers(request, b"content-type")
        if len(content_type) != 1 or content_type[0].split(b";", 1)[0].strip().lower() != b"application/json":
            reject(415, "M_BAD_JSON")
        data = request.content.read(MAX_BODY_BYTES + 1)
        if len(data) > MAX_BODY_BYTES:
            reject(413, "M_TOO_LARGE")
        try:
            body = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
        except (ValueError, RecursionError):
            reject(400, "M_BAD_JSON")
        if not isinstance(body, dict):
            reject(400, "M_BAD_JSON")

        async def execute():
            user = await self.module.authenticate(request)
            return await getattr(self.module, self.action)(user, body)

        result = await defer.ensureDeferred(execute()).addTimeout(REQUEST_TIMEOUT, reactor)
        return 200, result
