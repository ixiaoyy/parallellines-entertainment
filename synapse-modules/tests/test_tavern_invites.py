"""隔离契约测试；不启动服务，不访问真实 Synapse 数据或会话。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace as Attr
import unittest
from unittest.mock import patch


EVENT_TYPES = Attr(Create="m.room.create", JoinRules="m.room.join_rules",
                   PowerLevels="m.room.power_levels", Member="m.room.member")
MEMBERSHIP = Attr(JOIN="join", INVITE="invite", BAN="ban")
ORIGIN = b"https://tavern.pingxingxian.space"
ROOM = "!test:matrix.pingxingxian.space"
INVITER = "@inviter:matrix.pingxingxian.space"
RECIPIENT = "@recipient:matrix.pingxingxian.space"
OTHER = "@other:matrix.pingxingxian.space"


class FakeDeferred:
    def __init__(self, coroutine):
        self.coroutine = coroutine

    def addTimeout(self, seconds, reactor):
        assert 0 < seconds <= 15
        return self

    def __await__(self):
        return self.coroutine.__await__()


def load_with_module_api_doubles():
    """仅替换 Synapse/Twisted ABI；邀请码状态机执行生产源码。"""
    def make(name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        return module

    mocks = {
        "synapse": make("synapse", __version__="1.165.0"),
        "synapse.api": make("synapse.api"),
        "synapse.api.constants": make("synapse.api.constants", EventTypes=EVENT_TYPES,
                                     JoinRules=Attr(INVITE="invite"), Membership=MEMBERSHIP),
        "synapse.api.errors": make("synapse.api.errors", ShadowBanError=type("ShadowBanError", (Exception,), {}),
                                   SynapseError=type("SynapseError", (Exception,), {})),
        "synapse.event_auth": make("synapse.event_auth", get_user_power_level=lambda user, events: events[(EVENT_TYPES.PowerLevels, "")].content.get(user, 0),
                                  get_named_level=lambda events, name, fallback: events[(EVENT_TYPES.PowerLevels, "")].content.get(name, fallback)),
        "synapse.http": make("synapse.http"),
        "synapse.http.server": make("synapse.http.server",
                                    respond_with_json=lambda request, status, body, send_cors: setattr(request, "response", (status, body, send_cors))),
        "synapse.module_api": make("synapse.module_api", DirectServeJsonResource=object),
        "twisted": make("twisted"),
        "twisted.internet": make("twisted.internet", defer=Attr(TimeoutError=TimeoutError, ensureDeferred=FakeDeferred), reactor=Attr()),
    }
    if sys.platform == "win32":
        mocks["fcntl"] = make("fcntl")  # Windows 不提供 Linux 文件锁；该层用内存状态测试。
    spec = importlib.util.spec_from_file_location(
        "isolated_tavern_invites", Path(__file__).resolve().parents[1] / "tavern_invites.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, mocks):
        spec.loader.exec_module(module)
    return module


invites = load_with_module_api_doubles()


class InMemoryStore(invites.InviteStore):
    def __init__(self):
        self.records = {}
        self.now = 1000

    def _transaction(self, operation):
        return operation(self.records, self.now)[0]


def event(event_type, membership=None, **content):
    if membership is not None:
        content["membership"] = membership
    return Attr(type=event_type, room_id=ROOM, content=content)


class FakeApi:
    def __init__(self):
        self.user_info = Attr(
            is_guest=False, is_deactivated=False, is_shadow_banned=False, locked=False,
            suspended=False, approved=True, appservice_id=None, user_type=None,
        )
        self.request_user = INVITER
        self.did_authenticate = False
        self.disabled = set()
        self.invite_calls = []
        self.failure = None
        self.events = {
            (EVENT_TYPES.Create, ""): event(EVENT_TYPES.Create),
            (EVENT_TYPES.JoinRules, ""): event(EVENT_TYPES.JoinRules, join_rule="invite"),
            (EVENT_TYPES.PowerLevels, ""): event(EVENT_TYPES.PowerLevels, invite=50, **{INVITER: 50}),
            (EVENT_TYPES.Member, INVITER): event(EVENT_TYPES.Member, "join"),
        }

    @staticmethod
    def is_mine(user):
        return user.endswith(":matrix.pingxingxian.space")

    async def get_user_by_req(self, request, allow_guest=False, allow_expired=False):
        assert not allow_guest and not allow_expired
        self.did_authenticate = True
        return Attr(user=Attr(to_string=lambda: self.request_user), is_guest=False,
                    shadow_banned=False, app_service_id=getattr(self, "request_app_service_id", None))

    async def get_userinfo_by_id(self, user):
        return None if user in self.disabled else self.user_info

    async def get_room_state(self, room, filters):
        return {k: v for k, v in self.events.items() if room == ROOM and k in filters}

    @staticmethod
    async def defer_to_thread(call, *args):
        return call(*args)

    async def update_room_membership(self, *, sender, target, room_id, new_membership, content):
        self.invite_calls.append((sender, target, room_id, new_membership))
        assert new_membership == "invite"
        if self.failure == "before":
            raise TimeoutError
        result = event(EVENT_TYPES.Member, new_membership, **content)
        result.sender = sender
        result.state_key = target
        allowed, _ = await invites.TavernInvites.check_feature_invite(result, self.events)
        assert allowed
        self.events[(EVENT_TYPES.Member, target)] = result
        if self.failure == "after":
            raise TimeoutError
        return result


class FakeRequest:
    def __init__(self, action, body=b"{}", **headers):
        self.uri = f"{invites.ROUTE}/{action}".encode()
        self.content = io.BytesIO(body)
        values = {b"origin": [ORIGIN], b"authorization": [b"Bearer synthetic-test"],
                  b"content-type": [b"application/json"]}
        values.update({key.encode().replace(b"_", b"-"): value for key, value in headers.items()})
        self.requestHeaders = Attr(getRawHeaders=values.get)
        self.headers = {}

    def setHeader(self, key, value):
        self.headers[key] = value

    def setResponseCode(self, code):
        self.status = code

    def finish(self):
        self.finished = True


class InviteContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = invites.TavernInvites.__new__(invites.TavernInvites)
        self.module.api = FakeApi()
        self.module.store = InMemoryStore()

    async def create(self):
        return (await self.module.create(INVITER, {"room_id": ROOM}))["token"]

    async def test_single_invite_and_bounded_same_account_retry(self):
        token = await self.create()
        self.assertEqual(len(token), 43)
        self.assertNotIn(token, json.dumps(self.module.store.records))
        self.assertEqual(next(iter(self.module.store.records.values()))["expires_at"], 1000 + 86400)
        self.assertEqual(await self.module.redeem(RECIPIENT, {"token": token}), {"room_id": ROOM})
        self.assertEqual(self.module.api.invite_calls, [(INVITER, RECIPIENT, ROOM, "invite")])
        with self.assertRaises(invites.InviteError) as captured:
            await self.module.redeem(OTHER, {"token": token})
        self.assertEqual(captured.exception.status, 404)
        for attempt in range(invites.MAX_RETRIES):
            self.assertEqual(await self.module.redeem(RECIPIENT, {"token": token}), {"room_id": ROOM})
        with self.assertRaises(invites.InviteError) as captured:
            await self.module.redeem(RECIPIENT, {"token": token})
        self.assertEqual(captured.exception.status, 404)
        self.assertEqual(len(self.module.api.invite_calls), 1)

    async def test_ambiguous_invite_only_reconciled_never_reissued(self):
        token = await self.create()
        self.module.api.failure = "after"
        with self.assertRaises(TimeoutError):
            await self.module.redeem(RECIPIENT, {"token": token})
        with self.assertRaises(invites.InviteError):
            await self.module.redeem(OTHER, {"token": token})
        self.assertEqual(await self.module.redeem(RECIPIENT, {"token": token}), {"room_id": ROOM})
        self.module.api.events[(EVENT_TYPES.Member, RECIPIENT)] = event(EVENT_TYPES.Member, "leave")
        with self.assertRaises(invites.InviteError) as captured:
            await self.module.redeem(RECIPIENT, {"token": token})
        self.assertEqual(captured.exception.errcode, "M_TAVERN_INVITE_PENDING")
        self.assertEqual(len(self.module.api.invite_calls), 1)

    async def test_revocation_and_expiry_never_change_membership(self):
        token = await self.create()
        with self.assertRaises(invites.InviteError):
            await self.module.revoke(OTHER, {"token": token})
        self.assertEqual(await self.module.revoke(INVITER, {"token": token}), {})
        self.assertEqual(await self.module.revoke(INVITER, {"token": token}), {})
        with self.assertRaises(invites.InviteError):
            await self.module.redeem(RECIPIENT, {"token": token})
        next_token = await self.create()
        self.module.store.now += invites.TTL
        with self.assertRaises(invites.InviteError):
            await self.module.redeem(RECIPIENT, {"token": next_token})
        self.assertEqual(self.module.api.invite_calls, [])

    async def test_permission_is_rechecked_before_claim(self):
        token = await self.create()
        api = self.module.api
        alternatives = [
            ((EVENT_TYPES.Member, INVITER), event(EVENT_TYPES.Member, "leave")),
            ((EVENT_TYPES.PowerLevels, ""), event(EVENT_TYPES.PowerLevels, invite=51, **{INVITER: 50})),
            ((EVENT_TYPES.Member, RECIPIENT), event(EVENT_TYPES.Member, "ban")),
            ((EVENT_TYPES.JoinRules, ""), event(EVENT_TYPES.JoinRules, join_rule="public")),
            ((EVENT_TYPES.Create, ""), event(EVENT_TYPES.Create, type="m.space")),
        ]
        for key, unusable in alternatives:
            prev = api.events.get(key)
            api.events[key] = unusable
            with self.assertRaises(invites.InviteError) as captured:
                await self.module.redeem(RECIPIENT, {"token": token})
            self.assertEqual(captured.exception.status, 403)
            if prev is None:
                del api.events[key]
            else:
                api.events[key] = prev
        api.disabled.add(INVITER)
        with self.assertRaises(invites.InviteError):
            await self.module.redeem(RECIPIENT, {"token": token})
        self.assertIsNone(next(iter(self.module.store.records.values()))["claimant"])
        self.assertEqual(api.invite_calls, [])

    async def test_app_service_requester_cannot_create(self):
        """真实 Rust Requester 以 app_service_id 标记应用服务，不允许签发邀请。"""
        self.module.api.request_app_service_id = "synthetic-application-service"
        resource = invites.InviteResource(self.module, f"{invites.ROUTE}/create", "create")
        request = FakeRequest("create", json.dumps({"room_id": ROOM}).encode())
        with self.assertRaises(invites.InviteError) as captured:
            await resource._async_render_POST(request)
        self.assertEqual(captured.exception.errcode, "M_TAVERN_INVITE_FORBIDDEN")
        self.assertEqual(self.module.store.records, {})

    async def test_http_origin_headers_and_input(self):
        resource = invites.InviteResource(self.module, f"{invites.ROUTE}/create", "create")
        data = json.dumps({"room_id": ROOM}).encode()
        request = FakeRequest("create", data, origin=[b"https://other.pingxingxian.space"])
        with self.assertRaises(invites.InviteError) as captured:
            await resource._async_render_POST(request)
        resource._send_error_response(Attr(value=captured.exception), request)
        self.assertEqual(request.response[0], 403)
        self.assertFalse(request.response[2])
        self.assertFalse(self.module.api.did_authenticate)
        self.assertNotIn(b"Access-Control-Allow-Origin", request.headers)
        request = FakeRequest("create", data, cookie=[b"session=synthetic"])
        with self.assertRaises(invites.InviteError):
            await resource._async_render_POST(request)
        request = FakeRequest("create", data)
        request.uri += b"?access_token=synthetic"
        with self.assertRaises(invites.InviteError):
            await resource._async_render_POST(request)
        request = FakeRequest("create", b" " * 513)
        with self.assertRaises(invites.InviteError) as captured:
            await resource._async_render_POST(request)
        self.assertEqual(captured.exception.status, 413)
        request = FakeRequest("create", b'{"room_id":"!one","room_id":"!two"}')
        with self.assertRaises(invites.InviteError) as captured:
            await resource._async_render_POST(request)
        self.assertEqual(captured.exception.errcode, "M_BAD_JSON")
        request = FakeRequest("create", data)
        status, body = await resource._async_render_POST(request)
        resource._send_response(request, status, body)
        self.assertEqual(status, 200)
        self.assertEqual(request.headers[b"Access-Control-Allow-Origin"], ORIGIN)
        self.assertFalse(request.response[2])

    async def test_cors_options_allows_only_known_methods_and_headers(self):
        resource = invites.InviteResource(self.module, f"{invites.ROUTE}/redeem", "redeem")
        request = FakeRequest("redeem", access_control_request_method=[b"POST"],
                              access_control_request_headers=[b"authorization,content-type"])
        self.assertIsNone(await resource._async_render_OPTIONS(request))
        self.assertEqual(request.status, 204)
        self.assertEqual(request.headers[b"Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(request.headers[b"Cache-Control"], b"no-store")
        self.assertNotIn(b"Access-Control-Allow-Credentials", request.headers)
        self.assertFalse(self.module.api.did_authenticate)
        request = FakeRequest("redeem", access_control_request_method=[b"POST"],
                              access_control_request_headers=[b"authorization,x-private-data"])
        with self.assertRaises(invites.InviteError):
            await resource._async_render_OPTIONS(request)

    async def test_native_guard_leaves_unrelated_events_alone(self):
        native = event(EVENT_TYPES.Member, "invite")
        native.sender = INVITER
        native.state_key = RECIPIENT
        self.assertEqual(await self.module.check_feature_invite(native, {}), (True, None))
        native.content[invites.EVENT_MARKER] = True
        self.assertEqual(await self.module.check_feature_invite(native, {}), (False, None))


@unittest.skipUnless(sys.platform.startswith("linux"), "real flock and dir_fd require Linux")
class LinuxStoreTest(unittest.TestCase):
    def test_concurrent_claim_and_private_atomic_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first, second = invites.InviteStore(path), invites.InviteStore(path)
            token = first.create(INVITER, ROOM)["token"]
            key = invites.token_digest(token)

            def attempt(store, user):
                try:
                    return store.claim(key, user)[1]
                except invites.InviteError as error:
                    self.assertIn(error.status, (404, 503))
                    return False

            with ThreadPoolExecutor(2) as executor:
                tasks = (executor.submit(attempt, first, RECIPIENT), executor.submit(attempt, second, OTHER))
                self.assertEqual(sum(task.result() for task in tasks), 1)
            data = (path / "invites.json").read_text()
            self.assertNotIn(token, data)
            self.assertIn(key, json.loads(data))
            self.assertEqual(stat.S_IMODE(os.stat(path / "invites.json").st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
