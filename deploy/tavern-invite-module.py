#!/usr/bin/env python3
"""在既有 Synapse 注册一次性邀请模块；仅替换已登记的配置后像和重启本实例。"""

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import stat
import subprocess

import yaml


HERE = Path(__file__).resolve().parent
COMMON = runpy.run_path(str(HERE / 'element-appearance.py'))
ROOT, STATE = (COMMON[name] for name in ('ROOT', 'STATE'))
require, digest, encode, private_read, update_private, mounted_write = (
    COMMON[name] for name in ('require', 'digest', 'encode', 'private_read', 'update_private', 'mounted_write'))
WORK = ROOT / 'runtime/tavern-invite-module-v1'
JOURNAL = WORK / 'transition.json'
DATA = ROOT / 'data/synapse'
MODULE = DATA / 'tavern_invites.py'
LEDGER = DATA / 'tavern-invites'
EXPECTED_MODULE = {'modules': [{'module': 'data.tavern_invites.TavernInvites',
                              'config': {'storage_dir': '/data/tavern-invites'}}]}
IDENTITY = ('uid', 'gid', 'mode', 'device', 'inode')
MOUNTED = ('synapse', 'prepared')
SOURCE = HERE.parent / 'synapse-modules/tavern_invites.py'
OVERLAY = HERE.parent / 'config/synapse-tavern-invites.yaml'


def sources():
    """固定模块内容及路径，不加载任意外部 Python 或 YAML。"""
    module, overlay = SOURCE.read_bytes(), OVERLAY.read_bytes()
    require(0 < len(module) <= 128 * 1024 and 0 < len(overlay) <= 4096 and
            yaml.safe_load(overlay) == EXPECTED_MODULE, 'unexpected_invite_module_input')
    compile(module, str(SOURCE), 'exec')
    return module, overlay


def begin(native, sso, runner, value, paths, module, overlay):
    """只追加邀请模块；保留所有既有认证、数据库、旧备份和房间设置。"""
    sso.assert_targets(value, side='after')
    status = sso.main_login_status(runner)
    require(status['healthy'] and status['main_login_enabled'] and status.get('public_matrix_https'),
            'tavern_sso_not_ready')
    old, identity = {}, {}
    for name, path in paths.items():
        old[name], meta = sso.file_snapshot(path)
        if name in MOUNTED:
            identity[name] = {key: meta[key] for key in IDENTITY}
    for name in MOUNTED:
        require(old[name] == sso.backup_content(value, name, 'after'), 'unregistered_invite_baseline')
    current = yaml.safe_load(old['synapse'])
    require(isinstance(current, dict) and 'modules' not in current and
            not (current.get('account_validity') or {}).get('enabled'), 'unsupported_existing_synapse_policy')
    updated = old['synapse'].rstrip(b'\n') + b'\n\n' + overlay
    require(yaml.safe_load(updated) == {**current, **EXPECTED_MODULE}, 'unrelated_synapse_settings_changed')
    prepared = json.loads(old['prepared'])
    prepared['private_files']['data/synapse/homeserver.yaml'] = digest(updated)
    prepared = encode(prepared)
    receipt = json.loads(old['receipt'])
    receipt['after']['synapse']['sha256'] = digest(updated)
    receipt['after']['prepared']['sha256'] = digest(prepared)
    new = {'synapse': updated, 'prepared': prepared, 'synapse_after': updated,
           'prepared_after': prepared, 'receipt': encode(receipt)}
    hashes = {}
    for side, files in (('before', old), ('after', new)):
        hashes[side] = {}
        for name, content in files.items():
            sso.save_private_snapshot(WORK / (name + '.' + side), content)
            hashes[side][name] = digest(content)
    record = {'status': 'prepared', 'hashes': hashes, 'inode': identity,
              'program_sha256': value['program_sha256'], 'module_sha256': digest(module)}
    update_private(JOURNAL, encode(record))
    return record


def install_module(native, runner, synapse, module, candidate):
    """先核私有目录以及真实版本的配置加载；候选通过前不改运行配置。"""
    ledger = native.owned(LEDGER)
    if not ledger.exists():
        ledger.mkdir(mode=0o700)
        os.chown(ledger, 991, 991)
    meta = ledger.stat()
    require(stat.S_ISDIR(meta.st_mode) and (meta.st_uid, meta.st_gid, stat.S_IMODE(meta.st_mode)) == (991, 991, 0o700),
            'invite_ledger_permissions_changed')
    target = native.owned(MODULE)
    if target.exists():
        meta = target.stat()
        require(meta.st_uid == 0 and stat.S_IMODE(meta.st_mode) == 0o644 and target.read_bytes() == module,
                'unfamiliar_invite_module')
    else:
        update_private(target, module, mode=0o644)
    runner.run('tavern_invite_module_import', ['docker', 'exec', '--user', '991:991', synapse, 'python', '-B', '-c',
               'from data.tavern_invites import TavernInvites; assert TavernInvites.__name__ == "TavernInvites"'], timeout=35)
    file = DATA / ('.tavern-invite-check-' + secrets.token_hex(12) + '.yaml')
    created = False
    try:
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(fd, 'wb') as handle:
            os.fchown(handle.fileno(), 991, 991)
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        runner.run('tavern_invite_candidate_config', ['docker', 'exec', '--user', '991:991', synapse,
                   'python', '-B', '-m', 'synapse.config', '-c', '/data/' + file.name], timeout=45)
    finally:
        if created:
            file.unlink(missing_ok=True)


def verify_endpoint():
    """核 TLS 源站浏览器接线、实际 POST 来源和鉴权，不生成邀请或账号。"""
    url = 'https://matrix.pingxingxian.space/_synapse/client/tavern-invites/v1/redeem'
    origin = 'https://tavern.pingxingxian.space'
    argv = ['curl', '-sS', '--noproxy', '*', '--connect-timeout', '4', '--max-time', '12', '-i',
            '--resolve', 'matrix.pingxingxian.space:443:127.0.0.1']
    for caller, code, options in (
        (origin, 204, ['-X', 'OPTIONS', '-H', 'Access-Control-Request-Method: POST',
                       '-H', 'Access-Control-Request-Headers: authorization,content-type']),
        (origin, 401, ['-H', 'Content-Type: application/json', '--data', '{}']),
        ('https://foreign-origin.invalid', 403, ['-H', 'Content-Type: application/json', '--data', '{}']),
    ):
        response = subprocess.run([*argv, '-H', 'Origin: ' + caller, *options, url], capture_output=True, timeout=15)
        require(response.returncode == 0 and 0 < len(response.stdout) < 65536, 'tavern_invite_origin_unavailable')
        header, _, body = response.stdout.partition(b'\r\n\r\n')
        first, *lines = header.split(b'\r\n')
        def headers(key):
            return [line.split(b':', 1)[1].strip().decode() for line in lines
                    if line.lower().startswith(key + b':')]
        cors = headers(b'access-control-allow-origin')
        require(first.split()[1:2] == [str(code).encode()], 'tavern_invite_origin_status_mismatch')
        if code == 204:
            # 锁定版 Synapse 在根资源应答 OPTIONS，模块对真实 POST 继续独立限制来源与鉴权。
            methods = {m.strip().upper() for line in headers(b'access-control-allow-methods') for m in line.split(',')}
            fields = {h.strip().lower() for line in headers(b'access-control-allow-headers') for h in line.split(',')}
            require(cors in ([origin], ['*']) and 'POST' in methods and {'authorization', 'content-type'} <= fields,
                    'tavern_invite_preflight_prevents_browser_access')
        elif code == 401:
            require(cors == [origin] and json.loads(body).get('errcode') == 'M_MISSING_TOKEN',
                    'tavern_invite_unauthenticated_not_rejected')
        else:
            require(not cors and json.loads(body).get('errcode') == 'M_FORBIDDEN',
                    'tavern_invite_foreign_origin_not_rejected')


def read_transition(sso, paths, record):
    """记录只接受前后两套或明确登记的原位写中断，拒绝同期其他发布。"""
    snapshots, current = {}, {}
    for side in ('before', 'after'):
        snapshots[side] = {name: private_read(WORK / (name + '.' + side)) for name in paths}
        require({name: digest(value) for name, value in snapshots[side].items()} == record['hashes'][side],
                'tavern_invite_snapshot_changed')
    intent = record.get('intent', {})
    for name, path in paths.items():
        current[name], meta = sso.file_snapshot(path)
        if name in MOUNTED:
            require({key: meta[key] for key in IDENTITY} == record['inode'][name], 'tavern_invite_mount_changed')
        else:
            require(meta['uid'] == 0 and meta['mode'] == 0o600, 'tavern_invite_record_permissions_changed')
        valid = current[name] in (snapshots['before'][name], snapshots['after'][name])
        if not valid and name in MOUNTED and intent.get('name') == name:
            filename, side = intent.get('snapshot', ''), intent.get('side')
            require(re.fullmatch(name + r'\.write-[0-9a-f]{16}', filename) and side in snapshots,
                    'tavern_invite_write_intent_changed')
            earlier = private_read(WORK / filename)
            require(digest(earlier) == intent['sha256'], 'tavern_invite_write_preimage_changed')
            valid = sso.intermediate_content_matches(current[name], earlier, snapshots[side][name])
        require(valid, 'tavern_invite_unknown_preimage_' + name)
    return snapshots, current


def replace_config(native, sso, paths, snapshots, current, record, side):
    """同步现有 SSO 的后像登记，同时保留原始登录前像及配置挂载 inode。"""
    pending = record.get('intent', {}).get('name')
    order = ([pending] if pending else []) + [name for name in paths if name != pending]
    for name in order:
        data = snapshots[side][name]
        if current[name] == data:
            continue
        path = native.owned(paths[name])
        if name in MOUNTED:
            require(len(list(WORK.glob('*.write-*'))) < 64, 'tavern_invite_write_journal_bound')
            filename = name + '.write-' + secrets.token_hex(8)
            sso.save_private_snapshot(WORK / filename, current[name])
            record['intent'] = {'name': name, 'snapshot': filename, 'sha256': digest(current[name]), 'side': side}
            update_private(JOURNAL, encode(record))
            mounted_write(path, current[name], data, record['inode'][name])
        else:
            require(private_read(path) == current[name], 'tavern_invite_record_raced')
            update_private(path, data)
    require(read_transition(sso, paths, record)[1] == snapshots[side], 'tavern_invite_readback_mismatch')


def run(operation):
    """独占现有登录写锁，apply/rollback 只重启当前已登记的 Synapse。"""
    require(os.geteuid() == 0, 'root_required')
    native, sso = COMMON['load_release_tools']()
    module, overlay = sources() if operation == 'apply' else (None, None)
    with sso.write_operation_lock():
        value = sso.load_record()
        require(value.get('status') == 'enabled' and not value.get('write_intent'), 'tavern_sso_not_stable')
        sso.verify_recovery(value)
        native.verify_renewal_installation()
        runner = native.Runner(360)
        synapse = sso.synapse_container(runner, value)
        _, element = sso.file_snapshot(sso.TARGETS['element'])
        require(element['sha256'] == value['after']['element']['sha256'], 'element_configuration_changed')
        paths = {name: sso.TARGETS[name] for name in MOUNTED}
        paths.update(synapse_after=sso.BACKUP / 'synapse.after', prepared_after=sso.BACKUP / 'prepared.after',
                     receipt=STATE)
        native.owned(WORK).mkdir(mode=0o700, exist_ok=True)
        meta = WORK.stat()
        require(meta.st_uid == 0 and stat.S_IMODE(meta.st_mode) == 0o700, 'unsafe_invite_release_storage')
        if native.owned(JOURNAL).exists():
            record = json.loads(private_read(JOURNAL))
            require(record['program_sha256'] == value['program_sha256'] and
                    (module is None or record['module_sha256'] == digest(module)),
                    'tavern_invite_release_revision_changed')
        else:
            require(operation == 'apply', 'tavern_invite_no_record')
            record = begin(native, sso, runner, value, paths, module, overlay)
        snapshots, current = read_transition(sso, paths, record)
        side = 'after' if operation == 'apply' else 'before'
        desired_status = 'active' if side == 'after' else 'rolled_back'
        resume = side == 'after' and current == snapshots[side] and record['status'] == 'switching'
        if resume:
            # 配置已切换时复核已装模块及真实接口，不重复重启 Synapse。
            target = native.owned(MODULE)
            metadata = target.stat()
            require(metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o644 and
                    target.read_bytes() == module, 'installed_tavern_invite_module_changed')
        restart = not resume and (current != snapshots[side] or record['status'] not in ('active', 'rolled_back'))
        if restart:
            if side == 'after':
                install_module(native, runner, synapse['id'], module, snapshots[side]['synapse'])
            record['status'] = 'switching'
            update_private(JOURNAL, encode(record))
            replace_config(native, sso, paths, snapshots, current, record, side)
            sso.restart_synapse(runner, sso.load_record())
        result = sso.main_login_status(runner)
        require(result['healthy'] and result['main_login_enabled'] and
                (side == 'before' or result.get('public_matrix_https')), 'tavern_invite_sso_health_failed')
        if side == 'after':
            verify_endpoint()
        record.pop('intent', None)
        record['status'] = desired_status
        update_private(JOURNAL, encode(record))
        return {'complete': True, 'module': desired_status, 'synapse_restarted': restart,
                'main_login_enabled': True, 'existing_membership_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('apply', 'rollback'))
    os.umask(0o077)
    try:
        print(json.dumps(run(parser.parse_args().operation), sort_keys=True))
    except Exception as error:
        message = str(error)
        code = message if re.fullmatch(r'[A-Za-z0-9_:.-]{1,160}', message) else type(error).__name__
        print(json.dumps({'complete': False, 'error': code, 'record': str(JOURNAL)}, sort_keys=True))
        raise SystemExit(1) from None
