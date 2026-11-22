"""合成 Synapse/New API 契约测试；不访问网络、生产或真实凭据。"""

from __future__ import annotations

import hashlib
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
LUNA = "gpt-6-luna"
TERRA = "gpt-6.1-terra"


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
        if method == "POST" and url in (flux.MODEL_OPTIONS, flux.SET_MODEL):
            role = body.get("role_id", flux.FLUX_ROLE)
            grant = next((x for x in self.intents.values() if x["room_id"] == body["room_id"] and x["status"] == "active"
                          and x.get("role_id", flux.FLUX_ROLE) == role), None)
            if grant is not None and (grant["inviter_mxid"] != body["inviter_mxid"] or grant["subject"] != body["subject"]):
                flux.forbidden()
            if url == flux.MODEL_OPTIONS:
                return {"options": [{"id": LUNA, "label": "Luna"}, {"id": TERRA, "label": "Terra"}],
                        "selected_model_id": grant.get("model_id", LUNA) if grant else LUNA, "can_edit": grant is not None}
            if grant is None or grant.get("model_id", LUNA) != body["expected_model_id"]:
                flux.reject(409, "M_TAVERN_FLUX_MODEL_CHANGED")
            grant["model_id"] = body["model_id"]
            return {"model_id": grant["model_id"]}
        if method == "POST" and url == flux.INTENTS:
            key = "synthetic-intent-token-" + str(len(self.intents) + 1)
            self.intents[key] = {**body, "intent_id": key, "status": "pending"}
            return {"intent_id": key, "status": "pending"}
        if method == "POST" and url == flux.ONE_CLICK_INTENT:
            # 同房同角色的同次请求保持一个授权，两角色不共用这个键。
            key = hashlib.sha256(json.dumps([body["room_id"], body["inviter_mxid"], body["request_id"],
                                             body.get("role_id", flux.FLUX_ROLE)]).encode()).hexdigest()
            entry = self.intents.setdefault(key, {**body, "intent_id": key, "status": "confirmed"})
            if (entry["subject"] != body["subject"] or entry["limit_usd_micros"] != body["limit_usd_micros"]
                    or entry["status"] not in ("confirmed", "active")):
                flux.state_changed()
            return {"intent_id": key, "status": entry["status"]}
        key = url.removeprefix(flux.INTENTS + "/").removesuffix("/activate")
        value = self.intents[key]
        if method == "GET":
            return value
        if self.drop_activate:
            raise TimeoutError
        assert body["room_id"] == value["room_id"] and body["inviter_mxid"] == value["inviter_mxid"]
        assert body.get("role_id", flux.FLUX_ROLE) == value.get("role_id", flux.FLUX_ROLE)
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

    async def test_two_paid_roles_share_room_not_payer_or_model(self):
        room = self.module.api.rooms[ROOM_A]
        room[(flux.EventTypes.Member, BOB)] = event(flux.EventTypes.Member, ROOM_A, BOB, membership="join")
        room[(flux.EventTypes.PowerLevels, "")] = event(flux.EventTypes.PowerLevels, ROOM_A, invite=50,
                                                       **{ALICE: 50, BOB: 50})
        data_a = {**self.invite_body(ROOM_A), "model_id": LUNA}
        data_b = {**self.invite_body(ROOM_A, limit=20000), "role_id": "sakana", "model_id": TERRA}
        async def options(user, role=None):
            body = {"room_id": ROOM_A}
            if role: body["role_id"] = role
            return await self.module.model_options(user, body)

        self.assertEqual(await self.module.invite(ALICE, data_a), {"status": "active"})
        self.assertFalse((await options(BOB, "sakana"))["can_edit"])
        self.assertEqual(await self.module.invite(BOB, data_b), {"status": "active"})
        self.assertEqual(await self.module.invite(BOB, data_b), {"status": "active"})
        self.assertEqual([(e.state_key, e.sender) for e in self.module.api.sent],
                         [(flux.FLUX, ALICE), (flux.ROLES["sakana"], BOB)])
        first, second = [self.module.gateway.intents[e.content[flux.MARKER]] for e in self.module.api.sent]
        self.assertEqual((first["limit_usd_micros"], second["limit_usd_micros"]), (0, 20000))
        both_same_room = {"room_id": ROOM_A, "role_id": "sakana", "model_id": LUNA, "expected_model_id": TERRA}
        with self.assertRaises(invites.InviteError) as wrong_payer:
            await self.module.set_model(ALICE, both_same_room)
        self.assertEqual(wrong_payer.exception.status, 403)
        self.assertEqual(await self.module.set_model(BOB, both_same_room), {"model_id": LUNA})
        self.assertEqual((await options(ALICE))["selected_model_id"], LUNA)
        second["status"] = "revoked"
        self.assertTrue((await options(ALICE))["can_edit"])
        self.assertFalse((await options(BOB, "sakana"))["can_edit"])
        with self.assertRaises(invites.InviteError):
            await self.module.finish(BOB, {"room_id": ROOM_A, "intent_id": second["intent_id"]})

    async def test_paid_role_proof_is_bound_to_exact_target_and_rejects_invalid_role(self):
        first, second = flux.FLUX, flux.ROLES["sakana"]
        state = self.module.api.rooms[ROOM_A]
        marker, nonce = "a" * 64, "b" * 43
        invite = dict(membership="invite", **{flux.MARKER: marker, flux.ISSUE_NONCE: nonce})
        self.module.issuing_invites.add((ROOM_A, first, ALICE, marker, nonce))
        for target, allowed in ((first, True), (second, False)):
            self.assertEqual(await self.module.check_flux_invite(event(flux.EventTypes.Member, ROOM_A,
                              target, ALICE, **invite), state), (allowed, None))
        f = self.module.claim_room(ROOM_A, first)
        self.assertNotEqual(self.module.claim_room(ROOM_A, second), f)
        with self.assertRaises(invites.InviteError) as busy:
            self.module.claim_room(ROOM_A, first)
        self.assertEqual(busy.exception.status, 429)
        for value in (None, [], "other-paid-role", flux.ROLES["sakana"]):
            with self.assertRaises(invites.InviteError) as invalid:
                await self.module.invite(ALICE, {**self.invite_body(ROOM_A), "role_id": value})
            self.assertEqual(invalid.exception.status, 400)
        self.assertFalse(self.module.gateway.calls)

    async def prepare(self, user=ALICE, room=ROOM_A):
        prepared = await self.module.prepare(user, {"room_id": room})
        key = prepared["intent_id"]
        self.assertEqual(prepared["authorization_url"], flux.AUTHORIZATION_PAGE + key)
        return key

    async def finish(self, intent, user=ALICE, room=ROOM_A):
        return await self.module.finish(user, {"room_id": room, "intent_id": intent})

    @staticmethod
    def invite_body(room=ROOM_A, limit=0, request_id="e" * 32):
        return {"room_id": room, "request_id": request_id, "limit_usd_micros": limit}

    async def test_one_click_passes_zero_and_custom_to_only_the_matching_wallet_issuer(self):
        for user, room, limit, subject in ((ALICE, ROOM_A, 0, "verified-A"), (BOB, ROOM_B, 12_500, "verified-B")):
            self.assertEqual(await self.module.invite(user, self.invite_body(room, limit)), {"status": "active"})
            _, url, signed = self.module.gateway.calls[-2]
            self.assertEqual(url, flux.ONE_CLICK_INTENT)
            self.assertEqual(signed, {**self.invite_body(room, limit), "inviter_mxid": user,
                                      "subject": subject, "room_label": "合成房间"})
        self.assertEqual([(e.room_id, e.sender) for e in self.module.api.sent], [(ROOM_A, ALICE), (ROOM_B, BOB)])
        self.assertEqual(len(self.module.gateway.intents), 2)
        self.assertEqual(self.module.finishing_rooms, set())

    async def test_one_click_retries_same_room_event_and_does_not_replace_the_selection(self):
        call = self.invite_body()
        self.module.gateway.drop_activate = True
        with self.assertRaises(TimeoutError):
            await self.module.invite(ALICE, call)
        original = self.module.api.sent[0]
        self.module.api.rooms[ROOM_A][(flux.EventTypes.Member, flux.FLUX)] = event(
            flux.EventTypes.Member, ROOM_A, flux.FLUX, flux.FLUX, "$joined", original.event_id, membership="join",
        )
        self.module.gateway.drop_activate = False
        self.assertEqual(await self.module.invite(ALICE, call), {"status": "active"})
        self.assertEqual(await self.module.invite(ALICE, call), {"status": "active"})
        for changed in ({**call, "limit_usd_micros": 100_000}, {**call, "request_id": "b" * 32}):
            with self.assertRaises(invites.InviteError) as conflict:
                await self.module.invite(ALICE, changed)
            self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(len(self.module.api.sent), 1)
        self.assertEqual(self.module.gateway.intents[original.content[flux.MARKER]]["invite_event_id"], original.event_id)

    async def test_one_click_rejects_invalid_request_and_room_before_issuer_or_membership(self):
        valid = self.invite_body()
        resource = invites.InviteResource(self.module, f"{flux.ROUTE}/invite", "invite")
        for body in (dict(room_id=ROOM_A, request_id="e" * 32), {**valid, "subject": "fake"},
                     {**valid, "request_id": "x" * 32}, *(self.invite_body(limit=n) for n in (False, None, -1, 0.0, 1 << 53))):
            request = FakeRequest("invite", json.dumps(body).encode())
            request.uri = f"{flux.ROUTE}/invite".encode()
            with self.assertRaises(invites.InviteError) as malformed:
                await resource._async_render_POST(request)
            self.assertEqual(malformed.exception.status, 400)
        for user, room in ((ALICE, ROOM_B), (BOB, ROOM_A)):
            with self.assertRaises(invites.InviteError) as denied:
                await self.module.invite(user, self.invite_body(room))
            self.assertEqual(denied.exception.status, 403)
        request = FakeRequest("invite", json.dumps(valid).encode(), origin=[b"https://other.test"])
        request.uri = f"{flux.ROUTE}/invite".encode()
        with self.assertRaises(invites.InviteError) as cross_origin:
            await resource._async_render_POST(request)
        self.assertEqual(cross_origin.exception.status, 403)
        self.assertEqual(self.module.gateway.calls, [])
        self.assertEqual(self.module.api.sent, [])

    async def test_one_click_requires_a_confirmed_receipt(self):
        for reply in ({"intent_id": "a" * 64, "status": "pending"}, {"intent_id": "short", "status": "confirmed"}):
            async def raw(*_args):
                return reply
            with patch.object(self.module.gateway, "request", raw):
                with self.assertRaises(invites.InviteError) as unavailable:
                    await self.module.invite(ALICE, self.invite_body())
                self.assertEqual(unavailable.exception.status, 503)
        self.assertEqual(self.module.api.sent, [])

    async def test_one_click_gateway_exposes_only_approved_error_codes(self):
        gw = flux.FluxGateway.__new__(flux.FluxGateway)
        for state, received, want_status, code in (
            (400, {"error": "flux_invalid_quota_limit"}, 400, "M_TAVERN_FLUX_LIMIT_INVALID"),
            (403, {"error": "flux_wallet_unavailable", "private": "not exposed"}, 403,
             "M_TAVERN_FLUX_WALLET_UNAVAILABLE"),
            (403, {"error": "issuer credential failure"}, 503, "M_TAVERN_FLUX_GATEWAY_UNAVAILABLE"),
            (409, None, 409, "M_TAVERN_FLUX_STATE_CHANGED"),
        ):
            async def raw(*_args):
                return state, received
            with patch.object(gw, "request_response", raw):
                with self.assertRaises(invites.InviteError) as fail:
                    await gw.request("POST", flux.ONE_CLICK_INTENT, {})
            self.assertEqual((fail.exception.status, fail.exception.errcode), (want_status, code))

    async def test_model_options_do_not_create_authorization_and_invite_forwards_selected_model(self):
        value = await self.module.model_options(ALICE, {"room_id": ROOM_A})
        self.assertEqual((value["selected_model_id"], value["can_edit"]), (LUNA, False))
        self.assertEqual(self.module.gateway.intents, {})
        self.assertEqual(self.module.api.sent, [])
        invitation = {**self.invite_body(), "model_id": TERRA}
        self.assertEqual(await self.module.invite(ALICE, invitation), {"status": "active"})
        _, route, sent = self.module.gateway.calls[-2]
        self.assertEqual((route, sent["model_id"], sent["subject"]), (flux.ONE_CLICK_INTENT, TERRA, "verified-A"))
        await self.module.invite(BOB, self.invite_body(ROOM_B))
        self.assertNotIn("model_id", self.module.gateway.calls[-2][2])
        self.assertEqual((await self.module.model_options(ALICE, {"room_id": ROOM_A}))["selected_model_id"], TERRA)
        self.assertEqual((await self.module.model_options(BOB, {"room_id": ROOM_B}))["selected_model_id"], LUNA)

    async def test_model_edits_only_apply_to_own_existing_room_even_without_invite_power(self):
        await self.module.invite(ALICE, self.invite_body())
        await self.module.invite(BOB, self.invite_body(ROOM_B))
        setting = {"room_id": ROOM_A, "model_id": TERRA, "expected_model_id": LUNA}
        room = self.module.api.rooms[ROOM_A]
        room[(flux.EventTypes.Member, BOB)] = event(flux.EventTypes.Member, ROOM_A, BOB, membership="join")
        room[(flux.EventTypes.PowerLevels, "")] = event(flux.EventTypes.PowerLevels, ROOM_A, invite=50,
                                                       **{ALICE: 0, BOB: 100})
        calls = len(self.module.gateway.calls)
        for action, body in ((self.module.model_options, {"room_id": ROOM_A}), (self.module.set_model, setting)):
            with self.assertRaises(invites.InviteError) as denied:
                await action(BOB, body)
            self.assertEqual(denied.exception.status, 403)
        self.assertEqual(len(self.module.gateway.calls), calls)
        for invalid in ({**setting, "subject": "spoof"}, {**setting, "model_id": "model?evil"},
                        {**setting, "expected_model_id": None}):
            with self.assertRaises(invites.InviteError) as malformed:
                await self.module.set_model(ALICE, invalid)
            self.assertEqual(malformed.exception.status, 400)
        self.assertEqual(await self.module.set_model(ALICE, setting), {"model_id": TERRA})
        _, route, sent = self.module.gateway.calls[-1]
        self.assertEqual((route, sent), (flux.SET_MODEL, {**setting, "inviter_mxid": ALICE, "subject": "verified-A"}))
        with self.assertRaises(invites.InviteError) as stale:
            await self.module.set_model(ALICE, setting)
        self.assertEqual(stale.exception.errcode, "M_TAVERN_FLUX_MODEL_CHANGED")
        self.assertEqual((await self.module.model_options(BOB, {"room_id": ROOM_B}))["selected_model_id"], LUNA)
        room.pop((flux.EventTypes.Member, flux.FLUX))
        with self.assertRaises(invites.InviteError) as left:
            await self.module.set_model(ALICE, {**setting, "expected_model_id": TERRA})
        self.assertEqual(left.exception.status, 409)

    async def test_model_options_rejects_malformed_metadata_instead_of_defaulting(self):
        for response in ({"options": None, "selected_model_id": "", "can_edit": False},
                         {"options": [{"id": LUNA, "label": "Luna"}], "selected_model_id": TERRA, "can_edit": False},
                         {"options": [{"id": LUNA, "label": "Bad\nvalue"}], "selected_model_id": LUNA,
                          "can_edit": True}):
            async def raw(*_args):
                return response
            with patch.object(self.module.gateway, "request", raw):
                with self.assertRaises(invites.InviteError) as rejected:
                    await self.module.model_options(ALICE, {"room_id": ROOM_A})
                self.assertEqual(rejected.exception.status, 503)

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

    async def test_role_catalog_is_config_driven_and_rejects_secrets(self):
        self.assertEqual(
            flux.TavernFlux.parse_config({
                "enabled": True,
                "issuer_key_file": str(flux.KEY_FILE),
                "paid_roles_file": str(flux.PAID_ROLES_FILE),
            }),
            {"paid_roles_file": str(flux.PAID_ROLES_FILE)},
        )
        listed = await self.module.roles_catalog(ALICE, {})
        self.assertEqual([item["id"] for item in listed["roles"]], ["flux-the-cat", "sakana"])
        with self.assertRaises(invites.InviteError) as extra:
            await self.module.roles_catalog(ALICE, {"unexpected": True})
        self.assertEqual(extra.exception.status, 400)

        poisoned = [{**flux.DEFAULT_PAID_ROLES[0], "bearer_token": "should-never-exist-in-public-catalog"}]
        with self.assertRaises(ValueError):
            flux.validate_role_catalog(poisoned)

        custom = flux.validate_role_catalog([
            *flux.DEFAULT_PAID_ROLES,
            {
                "id": "turtle-soup",
                "mxid": "@bot_turtle-soup:matrix.pingxingxian.space",
                "name": "海龟汤裁判",
                "shortName": "海龟汤",
                "originalCard": "https://example.com/turtle-soup.png",
                "profileDescription": "私密加密房内的海龟汤主持机器人。",
                "inviteNoticeZh": "邀请免费，仅在房间内互动时扣邀请人钱包。",
                "inviteNoticeEn": "Free to invite; usage charges only the inviter wallet.",
            },
        ])
        self.assertEqual(len(custom), 3)
        self.assertEqual(custom[2]["id"], "turtle-soup")


if __name__ == "__main__":
    unittest.main()
