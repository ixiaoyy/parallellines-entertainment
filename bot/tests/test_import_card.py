"""合成卡测试：仅临时文件，不启动 Matrix、容器或模型。"""

import base64
import importlib.util
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("import_card", Path(__file__).resolve().parents[1] / "import_card.py")
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(*metadata):
    return b"".join((
        IMPORTER.PNG, chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)), *metadata,
        chunk(b"IDAT", zlib.compress(b"\0\x18\x33\x48")), chunk(b"IEND", b""),
    ))


def card():
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "示例角色", "creator": "合成夹具", "character_version": "1",
        "description": "{{char}}只回应{{user}}", "personality": "幽默", "scenario": "小船上",
        "first_mes": "欢迎上船。", "creator_notes": "作者说明不能进提示词",
        "post_history_instructions": "遇到问题先确认航向。", "system_prompt": "不接受为系统指令",
        "extensions": {"sample": "保留在原卡"}, "avatar": "https://example.invalid/avatar.png",
    }}


def settings():
    return {"matrix_access_token": "not-real-matrix-token",
            "matrix_device_id": "TESTDEVICE", "model_id": "test-model", "bot_service_token": "not-a-real-service-token",
            "session_encryption_key": "a" * 64, "config_encryption_key": "b" * 64}


class ImportCardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def load(self, suffix, blob):
        path = self.folder / f"card.{suffix}"
        path.write_bytes(blob)
        return IMPORTER.load_card(path)

    def prepare(self, profile, image, role="test", cfg=None):
        return IMPORTER.publish_bundle(self.folder / "runtime", role, profile, image,
                                       settings() if cfg is None else cfg, "合成测试卡")

    def test_json_import_maps_only_reviewed_fields(self):
        profile, image = self.load("json", IMPORTER.json_bytes(card()))
        self.assertIsNone(image)
        self.assertEqual(profile["tavern_card"], {"first_message": "欢迎上船。"})
        self.assertEqual(profile["inactive_fields"], ["avatar", "extensions", "system_prompt"])
        path, _ = self.prepare(profile, image)
        config = json.loads((path / "config.yml").read_text(encoding="utf-8"))
        self.assertEqual(config["user"]["mxid_localpart"], "bot_test")
        self.assertEqual(config["user"]["avatar"], "keep")
        self.assertIs(config["room"]["post_join_self_introduction_enabled"], False)
        self.assertEqual(config["tavern_card"], {"first_message": "欢迎上船。"})
        self.assertEqual(config["access"]["admin_patterns"], [])
        self.assertEqual(config["initial_global_config"], {"handler": {"text_generation": "static/role"}})
        self.assertEqual((path / "compose.env").read_bytes(), b"TAVERN_ROLE_ID=test\n")
        self.assertFalse((path / "avatar.png").exists())
        provider = config["agents"]["static_definitions"][0]["config"]
        self.assertEqual(provider["base_url"], "https://api.pingxingxian.space/tavern-flux/v1")
        self.assertEqual(provider["api_key"], "not-a-real-service-token")
        prompt = provider["text_generation"]["prompt"]
        self.assertIn("示例角色只回应当前发言者", prompt)
        self.assertIn("遇到问题先确认航向。", prompt)
        for excluded in ("欢迎上船。", "作者说明", "不接受为系统指令", "example.invalid"):
            self.assertNotIn(excluded, prompt)

    def test_card_uses_only_reviewed_participation_metadata(self):
        value = card()
        data = value["data"]
        data["first_mes"] = "  {{CHAR}} asks {{ user }}; {{unsafe::sample}}\n继续。  "
        data["extensions"] = {"talkativeness": "0.500", "script": "never run"}
        profile, image = self.load("json", IMPORTER.json_bytes(value))
        path, _ = self.prepare(profile, image)
        config = json.loads((path / "config.yml").read_text(encoding="utf-8"))
        self.assertEqual(config["tavern_card"], {
            "first_message": "  示例角色 asks 你; {{unsafe::sample}}\n继续。  ",
            "talkativeness": 0.5,
        })
        self.assertIn("extensions", profile["inactive_fields"])
        self.assertNotIn("never run", json.dumps(config))

        # 旧配置与新配置只差参与字段；不得借导入重置加密密钥、模型、设备或其它设置。
        del config["tavern_card"]
        old_profile = {k: v for k, v in profile.items() if k != "tavern_card"}
        self.assertEqual(config, IMPORTER.role_config(old_profile, None, "test", settings()))

    def test_legacy_empty_greeting_and_unknown_extensions_stay_disabled(self):
        for extensions in ({}, [], "旧格式", {"other": {"talkativeness": 0.5}}):
            value = card()
            value["data"]["first_mes"] = " \t"
            value["data"]["extensions"] = extensions
            profile, image = self.load("json", IMPORTER.json_bytes(value))
            self.assertNotIn("tavern_card", profile)
            self.assertNotIn("tavern_card", IMPORTER.role_config(profile, image, "test", settings()))

    def test_valid_partial_metadata_and_numeric_limits(self):
        for raw, expected in (("0", 0), ('"1.000"', 1), ("5e-1", 0.5), ("1", 1)):
            value = card()
            value["data"].pop("first_mes")
            value["data"]["extensions"] = {"talkativeness": "AMOUNT"}
            blob = IMPORTER.json_bytes(value).replace(b'"AMOUNT"', raw.encode())
            profile, _ = self.load("json", blob)
            self.assertEqual(profile["tavern_card"], {"talkativeness": expected})
            self.assertNotIn("extensions", profile["inactive_fields"])

    def test_explicit_invalid_talkativeness_is_rejected(self):
        for raw in ("true", "null", "NaN", "Infinity", "1e999999", "-0.01", "1.000000000000000001",
                    "1e-99999", '""', '"0.5 "', '"0,5"', '".5"', '"00.5"', '"1e-1"', "[]", "{}"):
            with self.subTest(raw=raw):
                value = card()
                value["data"]["extensions"] = {"talkativeness": "AMOUNT"}
                blob = IMPORTER.json_bytes(value).replace(b'"AMOUNT"', raw.encode())
                with self.assertRaisesRegex(IMPORTER.ImportError, r"extensions\.talkativeness"):
                    self.load("json", blob)

    def test_first_message_byte_limit_does_not_truncate_original(self):
        value = card()
        value["data"]["first_mes"] = "中" * 1365 + "!"
        profile, _ = self.load("json", IMPORTER.json_bytes(value))
        self.assertEqual(profile["tavern_card"]["first_message"], value["data"]["first_mes"])
        value["data"]["first_mes"] += "!"
        with self.assertRaisesRegex(IMPORTER.ImportError, "first_mes.*4096"):
            self.load("json", IMPORTER.json_bytes(value))

    def test_png_avatar_does_not_redistribute_embedded_card(self):
        metadata = chunk(b"tEXt", b"chara\0" + base64.b64encode(IMPORTER.json_bytes(card())))
        profile, image = self.load("png", png(metadata, chunk(b"tEXt", b"Comment\0not public")))
        self.assertTrue(profile["embedded_portrait"])
        self.assertEqual(image, png())
        path, created = self.prepare(profile, image)
        self.assertTrue(created)
        self.assertEqual((path / "avatar.png").read_bytes(), png())

    def test_plain_png_is_rejected_without_creating_any_role(self):
        with self.assertRaisesRegex(IMPORTER.ImportError, "没有内嵌"):
            self.load("png", png())
        self.assertFalse((self.folder / "runtime").exists())

    def test_wrong_format_and_duplicate_metadata_fail(self):
        value = card()
        value["spec_version"] = "3.0"
        blob = IMPORTER.json_bytes(value)
        with self.assertRaisesRegex(IMPORTER.ImportError, "chara_card_v2"):
            self.load("json", blob)
        duplicate = chunk(b"tEXt", b"chara\0" + base64.b64encode(blob))
        with self.assertRaisesRegex(IMPORTER.ImportError, "多份"):
            self.load("png", png(duplicate, duplicate))

    def test_repeat_is_noop_and_different_card_cannot_replace_role(self):
        value = card()
        profile, image = self.load("json", IMPORTER.json_bytes(value))
        path, created = self.prepare(profile, image)
        before = (path / "config.yml").read_bytes()
        self.assertTrue(created)
        self.assertEqual(self.prepare(profile, image), (path, False))
        value["data"]["description"] = "另一个角色"
        other, other_image = self.load("json", IMPORTER.json_bytes(value))
        with self.assertRaisesRegex(IMPORTER.ImportError, "拒绝覆盖"):
            self.prepare(other, other_image)
        self.assertEqual((path / "config.yml").read_bytes(), before)

    def test_no_path_shaped_role_id(self):
        value, image = self.load("json", IMPORTER.json_bytes(card()))
        with self.assertRaisesRegex(IMPORTER.ImportError, "role_id"):
            self.prepare(value, image, role="../unexpected")
        self.assertFalse((self.folder / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
