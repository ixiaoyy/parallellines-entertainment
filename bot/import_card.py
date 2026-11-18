#!/usr/bin/env python3
"""预览 SillyTavern V2 原卡，或为一个已授权的 Matrix 机器人准备私有配置。"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import zlib
from pathlib import Path


SERVER = "matrix.pingxingxian.space"
RUNTIME = Path(__file__).resolve().parents[1] / "runtime" / "bot"
PNG = b"\x89PNG\r\n\x1a\n"
MAX_FILE = 8 * 1024 * 1024
MAX_CARD = 256 * 1024
MAX_PIXELS = 4096 * 4096
ROLE_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}")
PROMPT_FIELDS = {
    "description": "角色描述", "personality": "性格", "scenario": "场景",
    "mes_example": "对话示例", "post_history_instructions": "角色补充说明",
}
PREVIEW_FIELDS = ("first_mes", "creator_notes", "creator", "character_version")


class ImportError(ValueError):
    """可安全报告的输入错误，不包含卡片正文或密钥。"""


def read_file(path, limit):
    """最多读取 limit + 1 字节；拒绝超限输入。"""
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ImportError("文件超过允许大小")
    return raw


def parse_object(raw):
    """解析 UTF-8 JSON 对象；同一个字段不能以不同值出现两次。"""
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ImportError("JSON 存在重复字段")
        return result

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
    except ImportError:
        raise
    except (ValueError, RecursionError):
        raise ImportError("文件不是有效的 UTF-8 JSON 对象") from None
    if not isinstance(value, dict):
        raise ImportError("文件不是 JSON 对象")
    return value


def read_png(raw):
    """按 SillyTavern 的 tEXt/chara 约定读取 V2 卡，返回卡 JSON 和不带文本元数据的头像。"""
    offset, encoded, image, has_pixels = len(PNG), None, bytearray(PNG), False
    while len(raw) - offset >= 12:
        size, kind = struct.unpack_from(">I4s", raw, offset)
        end = offset + size + 12
        if end > len(raw):
            break
        payload = raw[offset + 8:end - 4]
        if zlib.crc32(raw[offset + 4:end - 4]) != struct.unpack_from(">I", raw, end - 4)[0]:
            break
        if offset == len(PNG):
            if kind != b"IHDR" or size != 13:
                break
            width, height = struct.unpack_from(">II", payload)
            if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= MAX_PIXELS):
                raise ImportError("PNG 头像尺寸不在允许范围内")
        elif kind == b"IHDR":
            break

        # 头像只保留绘制所需的数据；不能把藏有人设、说明等信息的原始 PNG 当公开头像。
        if kind in (b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"):
            image.extend(raw[offset:end])
        if kind == b"IDAT" and size:
            has_pixels = True
        if kind == b"tEXt":
            key, _, value = payload.partition(b"\0")
            if key.lower() == b"chara":
                if encoded is not None:
                    raise ImportError("PNG 含多份 V2 角色数据，无法确定原卡")
                if len(value) > ((MAX_CARD + 2) // 3) * 4:
                    raise ImportError("内嵌角色数据超过允许大小")
                encoded = value
        if kind == b"IEND":
            if size or end != len(raw) or not has_pixels:
                break
            if encoded is None:
                raise ImportError("这张 PNG 没有内嵌的 SillyTavern V2 角色数据")
            try:
                return base64.b64decode(encoded, validate=True), bytes(image)
            except binascii.Error:
                raise ImportError("PNG 的 V2 角色数据不是有效 Base64") from None
        offset = end
    raise ImportError("PNG 结构或校验不正确")


def card_text(data, field):
    """保留原文；拒绝不支持的控制符、类型和无法编码的字符。"""
    value = data.get(field, "")
    if not isinstance(value, str) or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ImportError(f"角色字段 {field} 必须是有效文本")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ImportError(f"角色字段 {field} 包含无效字符") from None
    return value


def load_card(path):
    """读取一个完整 V2 原卡，不猜测 V1/V3 或普通图片的结构。"""
    if path.suffix.lower() not in (".png", ".json"):
        raise ImportError("仅支持 SillyTavern V2 PNG 或 JSON 原卡")
    original = read_file(path, MAX_FILE if path.suffix.lower() == ".png" else MAX_CARD)
    if path.suffix.lower() == ".png":
        if not original.startswith(PNG):
            raise ImportError("文件不是 PNG 图片")
        payload, avatar = read_png(original)
    else:
        payload, avatar = original, None
    if len(payload) > MAX_CARD:
        raise ImportError("角色数据超过允许大小")
    card = parse_object(payload)
    data = card.get("data")
    if card.get("spec") != "chara_card_v2" or card.get("spec_version") != "2.0" or not isinstance(data, dict):
        raise ImportError("只接受格式为 chara_card_v2 2.0 的角色原卡")
    name = card_text(data, "name")
    fields = {field: card_text(data, field) for field in (*PROMPT_FIELDS, *PREVIEW_FIELDS)}
    if not name.strip() or len(name.encode("utf-8")) > 255 or any(c in name for c in "\n\r\t"):
        raise ImportError("角色名为空、过长或含换行")
    if not any(fields[key].strip() for key in ("description", "personality", "scenario")):
        raise ImportError("原卡没有角色描述、性格或场景")
    # 不把额外指令、世界书、脚本、扩展或图片链接默默带到现有聊天服务。
    return {
        "name": name,
        "format": "chara_card_v2 2.0",
        "version": fields["character_version"],
        "creator": fields["creator"],
        "notes": fields["creator_notes"],
        "first_message_preview_only": fields["first_mes"],
        "prompt_fields": {key: fields[key] for key in PROMPT_FIELDS if fields[key].strip()},
        "inactive_fields": sorted(key for key, value in data.items() if value and key not in {
            "name", *PREVIEW_FIELDS, *PROMPT_FIELDS,
        }),
        "embedded_portrait": avatar is not None,
        "source_sha256": hashlib.sha256(original).hexdigest(),
    }, avatar


def role_config(profile, avatar, role, settings):
    """从预览字段和已分配的机器人设备生成普通 Baibot 静态配置。"""
    def match(key, pattern):
        value = settings.get(key)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ImportError(f"私有配置项 {key} 缺失或格式不正确")
        return value

    model = match("model_id", MODEL_ID)
    access_token = match("matrix_access_token", re.compile(r"[!-~]{1,512}"))
    device_id = match("matrix_device_id", re.compile(r"[A-Za-z0-9_-]{1,128}"))
    bot_token = match("bot_service_token", re.compile(r"[!-~]{1,512}"))
    hex_key = re.compile(r"[a-fA-F0-9]{64}")
    session_key = match("session_encryption_key", hex_key)
    config_key = match("config_encryption_key", hex_key)
    if session_key.lower() == config_key.lower():
        raise ImportError("会话和配置加密密钥必须独立")

    name = profile["name"]
    if any(ch in name for ch in "{}"):
        raise ImportError("角色名称不能包含模板字符")
    text = [f"你在群聊中扮演角色 {name}，只写该角色对当前单条消息的回应，不替其他成员发言。"]
    for field, value in profile["prompt_fields"].items():
        # 仅解析原卡最常用的两个称呼；不执行任意宏，也不把作者说明当作提示词。
        value = re.sub(r"\{\{\s*(char|user)\s*\}\}",
                       lambda m: name if m[1].lower() == "char" else "当前发言者", value, flags=re.I)
        text.append(f"【{PROMPT_FIELDS[field]}】\n{value}")

    config = {
        "homeserver": {"server_name": SERVER, "url": f"https://{SERVER}"},
        "user": {
            "mxid_localpart": f"bot_{role}", "access_token": access_token, "device_id": device_id,
            "name": name, "avatar": "/role/avatar.png" if avatar else "keep",
            "encryption": {"recovery_passphrase": None, "recovery_reset_allowed": False},
        },
        "room": {"post_join_self_introduction_enabled": False, "catch_up_tolerance_seconds": 0},
        "access": {"admin_patterns": []},
        "persistence": {"data_dir_path": "/data", "session_encryption_key": session_key,
                        "config_encryption_key": config_key},
        "agents": {"static_definitions": [{"id": "role", "provider": "openai-compatible", "config": {
            "base_url": "https://api.pingxingxian.space/tavern-flux/v1", "api_key": bot_token,
            "text_generation": {"model_id": model, "prompt": "\n\n".join(text),
                                "max_response_tokens": 384, "max_context_tokens": 8192},
        }}]},
        # 保留 Baibot 必填的原生配置对象；房间和用户资格均由 Matrix 成员关系确定。
        "initial_global_config": {"handler": {"text_generation": "static/role"}},
        "logging": "warn",
    }
    return config


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def publish_bundle(root, role, profile, avatar, settings, authorization):
    """原子写入私有角色目录；相同导入可重跑，不覆盖既有角色、密钥或设备。"""
    if not ROLE_ID.fullmatch(role):
        raise ImportError("role_id 须为 1 至 32 个小写字母、数字、横线或下划线，并以字母开头")
    if not authorization.strip() or len(authorization) > 1000 or any(c in authorization for c in "\n\r"):
        raise ImportError("须记录卡片设定和头像的使用许可来源")
    config = role_config(profile, avatar, role, settings)
    files = {
        "config.yml": json_bytes(config),  # JSON 是 YAML 兼容子集，由 Baibot 的 YAML 配置入口读取。
        # 房间成员关系由 Element 邀请建立，部署材料不再记录房间或用户名单。
        "compose.env": f"TAVERN_ROLE_ID={role}\n".encode(),
        "source.json": json_bytes({"name": profile["name"], "creator": profile["creator"],
                                    "version": profile["version"], "format": profile["format"],
                                    "sha256": profile["source_sha256"], "authorization": authorization}),
    }
    if avatar is not None:
        files["avatar.png"] = avatar
    if root.absolute() != root.resolve():
        raise ImportError("私有角色目录不能经过符号链接或重解析到其他路径")
    target = root / role

    def existing():
        if target.is_symlink() or not target.is_dir() or {p.name for p in target.iterdir()} != files.keys():
            raise ImportError("角色目录已存在，拒绝覆盖")
        for key, value in files.items():
            path = target / key
            if path.is_symlink() or not path.is_file() or path.stat().st_size != len(value):
                raise ImportError("同名角色配置有差异，拒绝覆盖")
            with path.open("rb") as stream:
                if stream.read(len(value) + 1) != value:
                    raise ImportError("同名角色配置有差异，拒绝覆盖")
        return target, False

    if os.path.lexists(target):
        return existing()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{role}-", dir=root) as tmp:
        staging = Path(tmp) / role
        staging.mkdir(mode=0o700)
        for name, value in files.items():
            with os.fdopen(os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
                out.write(value)
        try:
            staging.rename(target)
        except OSError:
            if os.path.lexists(target):
                return existing()
            raise
    return target, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preview", help="只读查看原卡字段及生效范围").add_argument("card", type=Path)
    prepare = commands.add_parser("prepare", help="准备私有配置，不创建账号、不启动机器人")
    prepare.add_argument("card", type=Path)
    prepare.add_argument("--role-id", required=True)
    prepare.add_argument("--settings", type=Path, required=True, help="已授权的私有设备及模型配置 JSON")
    prepare.add_argument("--authorization-reference", required=True, help="卡片及头像的许可记录或链接")
    args = parser.parse_args(argv)
    try:
        profile, avatar = load_card(args.card)
        if args.command == "preview":
            print(json.dumps(profile, ensure_ascii=True, indent=2))
        else:
            settings = parse_object(read_file(args.settings, MAX_CARD))
            target, created = publish_bundle(RUNTIME, args.role_id, profile, avatar, settings,
                                             args.authorization_reference)
            print(f"{'已准备' if created else '配置未变化'}：{target}")
        return 0
    except ImportError as err:
        print(f"角色卡导入失败：{err}", file=sys.stderr)
    except OSError:
        print("角色卡导入失败：文件不可读，或私有配置目录不可写", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
