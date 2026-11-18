#!/usr/bin/env python3
"""发布或回退 Tavern 的认证页背景，沿用现网 SSO 的独立恢复与前后像守卫。"""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import types
from urllib.parse import unquote


ROOT = Path('/data/services/parallellines-entertainment')
PROGRAM = ROOT / 'sso-operations.py'
STATE = ROOT / 'main-login.json'
WORK = ROOT / 'runtime/element-background-v1'
JOURNAL = WORK / 'transition.json'
SOURCE = Path(__file__).resolve().parent.parent / 'config/element.json'
IDENTITY = ('uid', 'gid', 'mode', 'device', 'inode')
MOUNTED = ('element', 'prepared')
OPERATIONS = ('apply', 'rollback')
NEUTRAL_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path fill="#202B38" d="M0 0h1v1H0z"/></svg>'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def update_private(path, data, mode=0o600):
    """保存回执/备份的新版本；已运行的挂载文件不用此方法，避免换 inode。"""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.element-ui-', delete=False) as stream:
        tmp = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_read(path, limit=240 * 1024):
    """只读取本模块固定拥有的私有回执和已经登记的运维程序。"""
    require(path.is_relative_to(ROOT) and not any(p.is_symlink() for p in (path, *path.parents)),
            'unsafe_element_ui_path')
    meta = path.stat()
    require(meta.st_uid == 0 and stat.S_ISREG(meta.st_mode) and
            stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_size <= limit, 'invalid_element_ui_private_file')
    return path.read_bytes()


def load_release_tools():
    """从已登记的独立恢复程序复用原始健康检查、写锁及镜像/文件归属守卫。"""
    value = json.loads(private_read(STATE))
    bundle = private_read(PROGRAM)
    require(digest(bundle) == value.get('program_sha256'), 'registered_sso_program_changed')
    tree = ast.parse(bundle)
    definition = tree.body[0]
    require(isinstance(definition, ast.Assign) and len(definition.targets) == 1 and
            isinstance(definition.targets[0], ast.Name) and definition.targets[0].id == 'BUNDLE',
            'unsupported_sso_recovery_bundle')
    sources = ast.literal_eval(definition.value)
    require(set(sources) == {'revision', 'native', 'sso'} and
            sources['revision'] == value.get('recovery_operation_revision', value['operation_revision']) and
            digest(sources['native'].encode()) == value['native_source_sha256'] and
            digest(sources['sso'].encode()) == value['extension_sha256'], 'registered_sso_sources_changed')
    native = types.ModuleType('entertainment_ops')
    native.__dict__.update(OPS_SOURCE=sources['native'], OPS_REVISION=sources['revision'])
    sys.modules[native.__name__] = native
    exec(compile(sources['native'], '<registered-entertainment-ops>', 'exec'), native.__dict__)
    sso = types.ModuleType('element_appearance_sso')
    sso.__dict__.update(SSO_SOURCE=sources['sso'], SSO_PROGRAM_SOURCE=bundle.decode(),
                        OPS_REVISION=sources['revision'])
    exec(compile(sources['sso'], '<registered-entertainment-sso>', 'exec'), sso.__dict__)
    return native, sso


def begin(native, sso, runner, value, paths):
    """生成只改变背景的一组精确前后像；原始登录回退的 before 备份不动。"""
    status = sso.main_login_status(runner)
    require(status['healthy'] and status['main_login_enabled'] and status.get('public_element_https'),
            'tavern_login_not_healthy')
    sso.assert_targets(value, side='after')
    old = {}
    inode = {}
    for name, file in paths.items():
        old[name], metadata = sso.file_snapshot(file)
        if name in MOUNTED:
            inode[name] = {key: metadata[key] for key in IDENTITY}
    require(old['element'] == sso.backup_content(value, 'element', 'after') and
            old['prepared'] == sso.backup_content(value, 'prepared', 'after'), 'registered_sso_after_images_changed')
    candidate = SOURCE.read_bytes()
    proposed = json.loads(candidate)
    original = json.loads(old['element'])
    branding = proposed.pop('branding', {})
    require(proposed == original and set(branding) == {'welcome_background_url'} and
            re.fullmatch(r'data:image/svg\+xml,[^()\s]+', branding['welcome_background_url']) and
            unquote(branding['welcome_background_url']) == 'data:image/svg+xml,' + NEUTRAL_SVG,
            'only_neutral_element_background_allowed')
    prepared = json.loads(old['prepared'])
    prepared['files']['config/element.json'] = digest(candidate)
    prepared = encode(prepared)
    receipt = json.loads(old['receipt'])
    receipt['after']['element']['sha256'] = digest(candidate)
    receipt['after']['prepared']['sha256'] = digest(prepared)
    new = {'element': candidate, 'prepared': prepared, 'element_after': candidate,
           'prepared_after': prepared, 'receipt': encode(receipt)}
    hashes = {}
    for side, files in (('before', old), ('after', new)):
        hashes[side] = {}
        for name, content in files.items():
            sso.save_private_snapshot(WORK / (name + '.' + side), content)
            hashes[side][name] = digest(content)
    journal = {'status': 'prepared', 'hashes': hashes, 'inode': inode, 'program_sha256': value['program_sha256']}
    update_private(JOURNAL, encode(journal))
    return journal


def images(journal, paths):
    sides = {side: {name: private_read(WORK / (name + '.' + side)) for name in paths}
             for side in ('before', 'after')}
    require({side: {name: digest(data) for name, data in files.items()} for side, files in sides.items()}
            == journal['hashes'], 'element_ui_backup_changed')
    return sides


def inspect_files(sso, paths, sides, journal):
    """只接受本次两个版本，或有写意图的单个已知断点；拒绝后续发布漂移。"""
    result = {}
    for name, path in paths.items():
        content, info = sso.file_snapshot(path)
        original, desired = [sides[side][name] for side in ('before', 'after')]
        if name in MOUNTED:
            require({field: info[field] for field in IDENTITY} == journal['inode'][name], 'element_ui_mount_changed')
        else:
            require(info['uid'] == 0 and info['mode'] == 0o600, 'element_ui_receipt_permissions_changed')
        valid = content in (original, desired)
        intent = journal.get('intent', {})
        if not valid and name == intent.get('name') and name in MOUNTED:
            earlier = intent.get('snapshot', '')
            side = intent.get('side')
            require(re.fullmatch(name + r'\.write-[0-9a-f]{16}', earlier) and side in sides,
                    'invalid_element_ui_write_intent')
            snapshot = private_read(WORK / earlier)
            require(digest(snapshot) == intent.get('sha256'), 'element_ui_write_snapshot_changed')
            valid = sso.intermediate_content_matches(content, snapshot, sides[side][name])
        require(valid, 'unknown_element_ui_preimage_' + name)
        result[name] = content
    return result


def mounted_write(path, current, new, expected_identity):
    """精确核对文件身份和旧字节，再原位同步新状态。"""
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b') as handle:
        info = os.fstat(handle.fileno())
        require((info.st_dev, info.st_ino) == (expected_identity['device'], expected_identity['inode'])
                and handle.read() == current, 'element_ui_mount_raced')
        handle.seek(0)
        handle.write(new)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def switch(native, sso, runner, paths, sides, journal, side):
    """完成指定过渡，核真实 HTTP/独立 SSO 健康以后才结案。"""
    current = inspect_files(sso, paths, sides, journal)
    # 如果上次写入中断，要先收尾该文件，再继续其他已登记的目标。
    interrupted = journal.get('intent', {}).get('name')
    order = ([interrupted] if interrupted else []) + [name for name in paths if name != interrupted]
    for name in order:
        data = sides[side][name]
        if current[name] == data:
            continue
        path = native.owned(paths[name])
        if name in MOUNTED:
            require(len(list(WORK.glob('*.write-*'))) < 64, 'element_ui_write_journal_bound')
            name_snapshot = name + '.write-' + secrets.token_hex(8)
            sso.save_private_snapshot(WORK / name_snapshot, current[name])
            journal['intent'] = {'name': name, 'snapshot': name_snapshot, 'side': side,
                                 'sha256': digest(current[name])}
            update_private(JOURNAL, encode(journal))
            mounted_write(path, current[name], data, journal['inode'][name])
        else:
            require(sso.file_snapshot(path)[0] == current[name], 'element_ui_receipt_raced')
            update_private(path, data)
    require(inspect_files(sso, paths, sides, journal) == sides[side], 'element_ui_write_back_mismatch')
    value = sso.load_record()
    saved, items = native.live_module_record(runner)
    require(saved['container_ids'] == value['container_ids'], 'element_container_changed')
    element = native.select(items, native.PROJECT, 'element')
    other = 'before' if side == 'after' else 'after'
    runner.run('element_background_sync', ['docker', 'exec', element['id'], 'sh', '-c', sso.ELEMENT_SYNC,
               'element-background', journal['hashes'][other]['element'], journal['hashes'][side]['element']])
    config = native.request_status('http://127.0.0.1:13101/config.json', expected_json=True)
    require(config == json.loads(sides[side]['element']), 'served_element_background_mismatch')
    status = sso.main_login_status(runner)
    require(status['main_login_enabled'] and status['healthy'] and
            (side == 'before' or status.get('public_element_https')),
            'tavern_sso_readback_failed')
    journal.pop('intent', None)
    journal['status'] = 'active' if side == 'after' else 'rolled_back'
    update_private(JOURNAL, encode(journal))
    return {'complete': True, 'background': journal['status'], 'main_login_enabled': True,
            'public_element_https': status.get('public_element_https', False), 'service_restarted': False}


def main(operation):
    """只操作已启用主站登录的 Tavern；回退保留账号、权限和加密。"""
    require(os.geteuid() == 0, 'root_required')
    native, sso = load_release_tools()
    with sso.write_operation_lock():
        value = sso.load_record()
        require(value.get('status') == 'enabled' and not value.get('write_intent'), 'tavern_sso_not_stable')
        sso.verify_recovery(value)
        native.verify_renewal_installation()
        runner = native.Runner(150)
        sso.synapse_container(runner, value)
        _, synapse = sso.file_snapshot(sso.TARGETS['synapse'])
        require(synapse['sha256'] == value['after']['synapse']['sha256'] and
                all(synapse[key] == value['before']['synapse'][key] for key in IDENTITY),
                'tavern_synapse_configuration_changed')
        paths = {name: sso.TARGETS[name] for name in MOUNTED}
        paths.update(element_after=sso.BACKUP / 'element.after', prepared_after=sso.BACKUP / 'prepared.after',
                     receipt=STATE)
        native.owned(WORK).mkdir(mode=0o700, exist_ok=True)
        meta = WORK.stat()
        require(meta.st_uid == 0 and stat.S_IMODE(meta.st_mode) == 0o700, 'unsafe_element_ui_backup_directory')
        if native.owned(JOURNAL).exists():
            journal = json.loads(private_read(JOURNAL))
            require(journal['program_sha256'] == value['program_sha256'], 'element_ui_sso_revision_changed')
        else:
            require(operation == 'apply', 'element_ui_release_not_found')
            journal = begin(native, sso, runner, value, paths)
        sides = images(journal, paths)
        if operation == 'apply':
            require(SOURCE.read_bytes() == sides['after']['element'], 'element_ui_candidate_changed')
        return switch(native, sso, runner, paths, sides, journal, 'after' if operation == 'apply' else 'before')


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=OPERATIONS)
    try:
        print(json.dumps(main(parser.parse_args().operation), sort_keys=True))
    except Exception as error:
        message = str(error)
        code = message if re.fullmatch(r'[A-Za-z0-9_:.-]{1,160}', message) else type(error).__name__
        print(json.dumps({'complete': False, 'error': code, 'record': str(JOURNAL)}, sort_keys=True))
        raise SystemExit(1) from None
