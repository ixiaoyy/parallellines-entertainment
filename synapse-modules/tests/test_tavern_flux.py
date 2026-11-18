"""合成 Synapse/New API 契约测试；不访问网络、生产或真实凭据。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as Attr
import unittest
from unittest.mock import patch

from test_tavern_invites import FakeDeferred, FakeRequest, invites


def load_flux():
    """只替换框架/网络接口；房间、映射和状态校验执行实际模块。"""
    def fake(name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        return module

    def errors(*names):
        return {name: type(name, (Exception,), {}) for name in names}

    types = Attr(**vars(invites.EventTypes), RoomEncryption="m.room.encryption", Name="m.room.name")
    mocks = {
        "isolated_flux": fake("isolated_flux"),
        "isolated_flux.tavern_invites": invites,
        "synapse": fake("synapse", __version__="1.165.0"),
        "synapse.api": fake("synapse.api"),
        "synapse.api.constants": fake("synapse.api.constants", EventTypes=types,
                                      JoinRules=invites.JoinRules, Membership=invites.Membership),
        "synapse.api.errors": fake("synapse.api.errors", SynapseError=invites.SynapseError),
        "synapse.http": fake("synapse.http"),
        "synapse.http.client": fake("synapse.http.client", SimpleHttpClient=object,
                                    read_body_with_max_size=None, **errors("BodyExceededMaxSize")),
        "twisted": fake("twisted"),
        "twisted.internet": fake("twisted.internet", defer=Attr(TimeoutError=TimeoutError,
                                  ensureDeferred=FakeDeferred), reactor=Attr()),
        "twisted.internet.error": fake("twisted.internet.error", **errors("ConnectError", "ConnectionLost")),
        "twisted.web": fake("twisted.web"),
        "twisted.web.client": fake("twisted.web.client", **errors("RequestTransmissionFailed", "ResponseFailed",
                                   "ResponseNeverReceived")),
        "twisted.web.http_headers": fake("twisted.web.http_headers", Headers=object),
    }
    spec = importlib.util.spec_from_file_location("isolated_flux.tavern_flux",
                                                Path(__file__).resolve().parents[1] / "tavern_flux.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, mocks):
        spec.loader.exec_module(module)
    return module


flux = load_flux()
ROOM_A = "!roomA:matrix.pingxingxian.space"
ROOM_B = "!roomB:matrix.pingxingxian.space"
ALICE = "@alice:matrix.pingxingxian.space"
BOB = "@bob:matrix.pingxingxian.space"


def event(event_type, room, key="", sender=ALICE, event_id="$test", replaces=None, **content):
    return Attr(type=event_type, room_id=room, state_key=key, sender=sender, event_id=event_id, content=content,
                unsigned={} if replaces is None else {"replaces_state": replaces})


class FakeApi:
    def __init__(self):
        self._store = self
        self.user = ALICE
        self.links = {ALICE: [(flux.OIDC_PROVIDER, "verified-A")], BOB: [(flux.OIDC_PROVIDER, "verified-B")]}
        self.rooms = {}
        self.sent = []
        self.history = {}
        for room, owner in [(ROOM_A, ALICE), (ROOM_B, BOB)]:
            self.rooms[room] = {
                (flux.EventTypes.Create, ""): event(flux.EventTypes.Create, room),
                (flux.EventTypes.JoinRules, ""): event(flux.EventTypes.JoinRules, room, join_rule="invite"),
                (flux.EventTypes.RoomEncryption, ""): event(flux.EventTypes.RoomEncryption, room,
                                                             algorithm="m.megolm.v1.aes-sha2"),
                (flux.EventTypes.Name, ""): event(flux.EventTypes.Name, room, name="合成房间\u202e"),
                (flux.EventTypes.PowerLevels, ""): event(flux.EventTypes.PowerLevels, room, invite=50,
                                                        **{owner: 50}),
                (flux.EventTypes.Member, owner): event(flux.EventTypes.Member, room, owner, membership="join"),
            }

    @staticmethod
    def is_mine(user):
        return user.endswith(":" + flux.HOMESERVER)

    async def get_userinfo_by_id(self, user):
        return Attr(is_guest=False, is_deactivated=False, is_shadow_banned=False, locked=False, suspended=False,
                    approved=True, appservice_id=None, user_type=None)

    async def get_user_by_req(self, request, allow_guest=False, allow_expired=False):
        assert not allow_guest and not allow_expired
        return Attr(user=Attr(to_string=lambda: self.user), is_guest=False, shadow_banned=False, app_service_id=None)

    async def get_external_ids_by_user(self, user):
        return self.links.get(user, [])

    async def get_room_state(self, room, filters):
        return {key: value for key, value in self.rooms.get(room, {}).items() if key in filters}

    async def get_event(self, event_id, allow_none, check_room_id):
        value = self.history.get(event_id)
        return value if value is not None and value.room_id == check_room_id else None

    async def update_room_membership(self, *, sender, target, room_id, new_membership, content):
        result = event(flux.EventTypes.Member, room_id, target, sender, f"$invite{len(self.sent)}",
                       membership=new_membership, **content)
        allowed, _ = await self.check_flux_invite(result, self.rooms[room_id])
        assert allowed
        self.sent.append(result)
        self.history[result.event_id] = result
        self.rooms[room_id][(flux.EventTypes.Member, target)] = result
        return result


class FakeGateway:
    def __init__(self):
        self.intents = {}
        self.calls = []
        self.drop_activate = False

    async def request(self, method, url, body=None):
        self.calls.append((method, url, body))
        if method == "POST" and url == flux.INTENTS:
            key = "synthetic-intent-token-" + str(len(self.intents) + 1)
            self.intents[key] = {**body, "intent_id": key, "status": "pending"}
            return {"intent_id": key, "status": "pending"}
        key = url.removeprefix(flux.INTENTS + "/").removesuffix("/activate")
        value = self.intents[key]
        if method == "GET":
            return value
        if self.drop_activate:
            raise TimeoutError
        assert body["room_id"] == value["room_id"] and body["inviter_mxid"] == value["inviter_mxid"]
        value["status"] = "active"
        value["invite_event_id"] = body["invite_event_id"]
        return {"status": "active"}


class FluxContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = flux.TavernFlux.__new__(flux.TavernFlux)
        self.module.api = FakeApi()
        self.module.api.check_flux_invite = self.module.check_flux_invite
        self.module.gateway = FakeGateway()
        self.module.finishing_rooms = set()
        self.module.issuing_invites = set()

    async def prepare(self, user=ALICE, room=ROOM_A):
        prepared = await self.module.prepare(user, {"room_id": room})
        key = prepared["intent_id"]
        self.assertEqual(prepared["authorization_url"], flux.AUTHORIZATION_PAGE + key)
        return key

    async def finish(self, intent, user=ALICE, room=ROOM_A):
        return await self.module.finish(user, {"room_id": room, "intent_id": intent})

    async def test_pending_has_no_invite_confirmed_activates_native_event_once(self):
        key = await self.prepare()
        stored = self.module.gateway.intents[key]
        self.assertEqual(stored["subject"], "verified-A")
        self.assertEqual(stored["room_label"], "合成房间")
        self.assertEqual(await self.finish(key), {"status": "pending"})
        self.assertEqual(self.module.api.sent, [])
        stored["status"] = "confirmed"
        self.assertEqual(await self.finish(key), {"status": "active"})
        self.assertEqual(await self.finish(key), {"status": "active"})
        self.assertEqual(len(self.module.api.sent), 1)
        self.assertEqual(stored["invite_event_id"], self.module.api.sent[0].event_id)

    async def test_two_native_invites_preserve_room_and_verified_payer_boundaries(self):
        first = await self.prepare()
        second = await self.prepare(BOB, ROOM_B)
        with self.assertRaises(invites.InviteError) as mismatch:
            await self.finish(first, BOB, ROOM_B)
        self.assertEqual(mismatch.exception.status, 403)
        for key, user, room in [(first, ALICE, ROOM_A), (second, BOB, ROOM_B)]:
            self.module.gateway.intents[key]["status"] = "confirmed"
            self.assertEqual(await self.finish(key, user, room), {"status": "active"})
        self.assertEqual([(e.room_id, e.sender) for e in self.module.api.sent], [(ROOM_A, ALICE), (ROOM_B, BOB)])
        self.module.gateway.intents[first]["status"] = "revoked"
        with self.assertRaises(invites.InviteError) as revoked:
            await self.finish(first)
        self.assertEqual(revoked.exception.status, 409)
        self.assertEqual(await self.finish(second, BOB, ROOM_B), {"status": "active"})
        self.assertEqual(len(self.module.api.sent), 2)

    async def test_changed_or_missing_oidc_mapping_is_not_accepted(self):
        key = await self.prepare()
        self.module.gateway.intents[key]["status"] = "confirmed"
        for links in [[], [(flux.OIDC_PROVIDER, "different-subject")],
                      [(flux.OIDC_PROVIDER, "verified-A"), (flux.OIDC_PROVIDER, "second-subject")]]:
            self.module.api.links[ALICE] = links
            with self.assertRaises(invites.InviteError) as denied:
                await self.finish(key)
            self.assertEqual(denied.exception.status, 403)
        self.assertEqual(self.module.api.sent, [])

    async def test_privacy_and_invite_power_rechecked_before_finishing(self):
        key = await self.prepare()
        self.module.gateway.intents[key]["status"] = "confirmed"
        room = self.module.api.rooms[ROOM_A]
        for state_key, content in [((flux.EventTypes.JoinRules, ""), {"join_rule": "public"}),
                                   ((flux.EventTypes.RoomEncryption, ""), {"algorithm": "unknown"}),
                                   ((flux.EventTypes.PowerLevels, ""), {"invite": 100, ALICE: 50})]:
            old = room[state_key]
            room[state_key] = event(state_key[0], ROOM_A, **content)
            with self.assertRaises(invites.InviteError) as failure:
                await self.finish(key)
            self.assertEqual(failure.exception.status, 403)
            room[state_key] = old
        self.assertEqual(self.module.api.sent, [])

    async def test_ambiguous_activation_reuses_invite_event_even_after_bot_join(self):
        key = await self.prepare()
        self.module.gateway.intents[key]["status"] = "confirmed"
        self.module.gateway.drop_activate = True
        with self.assertRaises(TimeoutError):
            await self.finish(key)
        original = self.module.api.sent[0]
        self.module.api.rooms[ROOM_A][(flux.EventTypes.Member, flux.FLUX)] = event(
            flux.EventTypes.Member, ROOM_A, flux.FLUX, flux.FLUX, "$joined", original.event_id, membership="join",
        )
        self.module.gateway.drop_activate = False
        self.assertEqual(await self.finish(key), {"status": "active"})
        self.assertEqual(self.module.gateway.intents[key]["invite_event_id"], original.event_id)
        self.assertEqual(len(self.module.api.sent), 1)

    async def test_revoked_native_invite_is_not_reissued_and_unmarked_invite_not_activated(self):
        key = await self.prepare()
        self.module.gateway.intents[key]["status"] = "confirmed"
        self.module.gateway.drop_activate = True
        with self.assertRaises(TimeoutError):
            await self.finish(key)
        original = self.module.api.sent[0]
        room = self.module.api.rooms[ROOM_A]
        member_key = (flux.EventTypes.Member, flux.FLUX)
        room[member_key] = event(flux.EventTypes.Member, ROOM_A, flux.FLUX, ALICE, "$left", original.event_id,
                                membership="leave")
        with self.assertRaises(invites.InviteError) as withdrawn:
            await self.finish(key)
        self.assertEqual(withdrawn.exception.status, 409)
        room[member_key] = event(flux.EventTypes.Member, ROOM_A, flux.FLUX, membership="invite")
        with self.assertRaises(invites.InviteError) as unmarked:
            await self.finish(key)
        self.assertEqual(unmarked.exception.status, 409)
        self.assertEqual(len(self.module.api.sent), 1)

    async def test_http_requires_fixed_origin_and_never_accepts_client_payer(self):
        resource = invites.InviteResource(self.module, f"{flux.ROUTE}/prepare", "prepare")
        request = FakeRequest("prepare", json.dumps({"room_id": ROOM_A, "subject": "chosen"}).encode())
        request.uri = f"{flux.ROUTE}/prepare".encode()
        with self.assertRaises(invites.InviteError) as supplied_payer:
            await resource._async_render_POST(request)
        self.assertEqual(supplied_payer.exception.status, 400)
        request = FakeRequest("prepare", json.dumps({"room_id": ROOM_A}).encode(), origin=[b"https://other.test"])
        request.uri = f"{flux.ROUTE}/prepare".encode()
        with self.assertRaises(invites.InviteError) as origin:
            await resource._async_render_POST(request)
        self.assertEqual(origin.exception.status, 403)
        self.assertEqual(self.module.gateway.calls, [])

    async def test_rule_blocks_native_flux_invites_without_live_authorized_issue_only(self):
        flux.TavernFlux(False, None)
        self.assertIs(flux.TavernFlux.parse_config({"enabled": False}), False)
        state = self.module.api.rooms[ROOM_A]
        native_flux = event(flux.EventTypes.Member, ROOM_A, flux.FLUX, ALICE, membership="invite")
        self.assertEqual(await self.module.check_flux_invite(native_flux, state), (False, None))
        human = event(flux.EventTypes.Member, ROOM_A, BOB, ALICE, membership="invite", **{flux.MARKER: "fake"})
        free = event(flux.EventTypes.Member, ROOM_A, "@free:matrix.pingxingxian.space", ALICE,
                     membership="invite")
        self.assertEqual(await self.module.check_flux_invite(human, {}), (True, None))
        self.assertEqual(await self.module.check_flux_invite(free, {}), (True, None))
        key = await self.prepare()
        native_flux.content.update({flux.MARKER: key, flux.ISSUE_NONCE: "a" * 43})
        self.assertEqual(await self.module.check_flux_invite(native_flux, state), (False, None))
        self.module.gateway.intents[key]["status"] = "confirmed"
        self.assertEqual(await self.finish(key), {"status": "active"})
        self.assertEqual(self.module.issuing_invites, set())
        # 去掉 Flux 再以旧票据或直接邀请也不能让角色回房使用原付款人。
        state.pop((flux.EventTypes.Member, flux.FLUX))
        replay = self.module.api.sent[0]
        self.assertEqual(await self.module.check_flux_invite(replay, state), (False, None))
        with self.assertRaises(invites.InviteError) as removed:
            await self.finish(key)
        self.assertEqual(removed.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
