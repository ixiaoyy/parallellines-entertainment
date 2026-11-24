#!/usr/bin/env python3
"""切换两站的同功能静态入口；保留原静态文件和容器，不改变路由或登录服务。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
COMMON = runpy.run_path(str(HERE / 'element-appearance.py'))
ROOT = COMMON['ROOT']
WORK = ROOT / 'runtime/tavern-invite-web-flow-v1'
PACKAGE = HERE.parent / 'artifacts'
ROLES = {
    'main': ('parallellines-web-1', '/usr/share/nginx/html', 'assets', 'https://pingxingxian.space/tavern/invite'),
    'element': ('parallellines-entertainment-element-1', '/app', 'bundles', 'https://tavern.pingxingxian.space/'),
}
require, digest, encode, private_read, update_private = (
    COMMON[name] for name in ('require', 'digest', 'encode', 'private_read', 'update_private'))
PATH = re.compile(r'[A-Za-z0-9_@.~-]+(?:/[A-Za-z0-9_@.~-]+)*\Z')
INSTALL_ASSETS = r'''
set -eu
stage=$1
root=$2
kind=$3
[ -d "$root" ] && [ ! -L "$root" ] && [ -d "$root/$kind" ] && [ ! -L "$root/$kind" ] || exit 1
cd "$stage"
while IFS= read -r file; do
    dest=$root/$file
    path=$dest
    while [ "$path" != "$root" ]; do
        [ ! -L "$path" ]
        path=${path%/*}
    done
    if [ -e "$dest" ]; then
        cmp -s "$file" "$dest"
        continue
    fi
    mkdir -p "${dest%/*}"
    part=$(mktemp "${dest%/*}/.tavern-asset.XXXXXXXX")
    trap 'rm -f "$part"' EXIT
    cp "$file" "$part"
    chmod 644 "$part"
    ln "$part" "$dest"
    rm -f "$part"
    trap - EXIT
done
'''
SWITCH_INDEX = r'''
set -eu
root=$1
before=$2
after=$3
[ -d "$root" ] && [ ! -L "$root" ] && [ ! -L "$root/index.html" ] && [ -f "$root/index.html" ] || exit 1
seen=$(sha256sum "$root/index.html"); seen=${seen%% *}
[ "$seen" = "$after" ] && exit 0
[ "$seen" = "$before" ]
tmp=$(mktemp "$root/.tavern-index.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT
cat > "$tmp"
chmod 644 "$tmp"
seen=$(sha256sum "$tmp"); [ "${seen%% *}" = "$after" ]
seen=$(sha256sum "$root/index.html"); [ "${seen%% *}" = "$before" ]
mv -f "$tmp" "$root/index.html"
'''


def cmd(args, label, *, timeout=45, content=None):
    """固定 argv，命令输出只用于本机校验，不把未知容器输出透传出去。"""
    outcome = subprocess.run([str(item) for item in args], input=content, capture_output=True, timeout=timeout)
    require(outcome.returncode == 0, label)
    return outcome.stdout


def validate_assets(role, assets):
    """只接收本次生产构建的入口、它的资产目录和明确核验的摘要。"""
    base = PACKAGE / role
    manifest = json.loads((PACKAGE / (role + '.json')).read_bytes())
    require(set(manifest) == {'index.html', 'files'} and 0 < len(manifest['files']) <= 2048,
            'invalid_tavern_static_manifest')
    require(not base.is_symlink() and {p.name for p in base.iterdir()} == {'index.html', assets},
            'unexpected_tavern_static_entries')
    actual, total = {}, 0
    for path in (base / assets).rglob('*'):
        relative = path.relative_to(base).as_posix()
        require(not path.is_symlink() and PATH.fullmatch(relative) and
                not any(part in ('.', '..') or part.startswith('.') for part in relative.split('/')),
                'unsafe_tavern_static_path')
        if path.is_dir():
            continue
        require(path.is_file() and not path.name.endswith(('.map', '.key', '.pem')) and relative in manifest['files'],
                'unsupported_tavern_static_asset')
        total += path.stat().st_size
        require(total <= 128 * 1024 * 1024 and len(actual) < 2048, 'tavern_static_artifact_too_large')
        with path.open('rb') as handle:
            actual[relative] = hashlib.file_digest(handle, 'sha256').hexdigest()
    index = (base / 'index.html').read_bytes()
    require(actual == manifest['files'] and not (base / 'index.html').is_symlink() and
            0 < len(index) < 65536 and digest(index) == manifest['index.html'], 'tavern_static_artifact_changed')
    return base, index, actual


def get_container(role, native, runner):
    name, root, _, _ = ROLES[role]
    format_ = ('{"id":{{json .Id}},"running":{{.State.Running}},"image":{{json .Image}},'
               '"reference":{{json .Config.Image}},"readonly":{{.HostConfig.ReadonlyRootfs}}}')
    data = json.loads(cmd(['docker', 'inspect', '--format', format_, name], 'tavern_static_container_missing'))
    require(data['running'] and not data['readonly'], 'tavern_static_container_not_writable')
    if role == 'element':
        saved, actual = native.live_module_record(runner)
        require(saved['container_ids']['element'] == data['id'] and
                native.select(actual, native.PROJECT, 'element')['image'] == data['reference'], 'tavern_element_container_changed')
        require(cmd(['docker', 'exec', data['id'], 'readlink', '-f', '/usr/share/nginx/html'],
                    'element_static_root_unavailable').decode().strip() == root, 'element_static_root_changed')
    else:
        revision = cmd(['docker', 'image', 'inspect', '--format',
                        '{{index .Config.Labels "org.opencontainers.image.revision"}}', data['image']],
                       'main_static_image_unavailable').decode().strip()
        require(revision == '9aca29645c09c1285fc30aabbaf7c3447248647b', 'main_static_baseline_changed')
    return {'id': data['id'], 'image': data['image']}


def read_origin(url, expected):
    """以固定域名和证书直查本机源站；公网从发布操作者网络单独验收。"""
    host = urlsplit(url).hostname
    require(host in ('pingxingxian.space', 'tavern.pingxingxian.space'), 'tavern_static_origin_changed')
    body = cmd(['curl', '-fsS', '--noproxy', '*', '--connect-timeout', '4', '--max-time', '14',
                '--resolve', host + ':443:127.0.0.1', '-H', 'Accept-Encoding: identity',
                '-H', 'Cache-Control: no-cache', url], 'tavern_static_origin_unavailable', timeout=16)
    require(body == expected, 'tavern_static_origin_index_mismatch')


def run(role, action):
    """原子切换 index；回退同一容器前像，保留先前和新增的内容寻址资源。"""
    require(os.geteuid() == 0, 'root_required')
    native, sso = COMMON['load_release_tools']()
    _, root, assets, url = ROLES[role]
    with sso.write_operation_lock():
        registered = sso.load_record()
        require(registered.get('status') == 'enabled' and not registered.get('write_intent'), 'tavern_login_not_stable')
        sso.verify_recovery(registered)
        runtime = get_container(role, native, native.Runner(90))
        container = runtime['id']
        current = cmd(['docker', 'exec', container, 'cat', root + '/index.html'], 'tavern_static_index_unavailable')
        require(0 < len(current) < 65536, 'tavern_static_index_bound')
        native.owned(WORK).mkdir(mode=0o700, exist_ok=True)
        work = native.owned(WORK / role)
        work.mkdir(mode=0o700, exist_ok=True)
        require(all(p.stat().st_uid == 0 and stat.S_IMODE(p.stat().st_mode) == 0o700 for p in (WORK, work)),
                'unsafe_static_backup_directory')
        receipt = native.owned(work / 'transition.json')
        if not receipt.exists():
            require(action == 'apply', 'tavern_static_no_release')
            base, after, listing = validate_assets(role, assets)
            read_origin(url, current)
            sso.save_private_snapshot(work / 'index.before', current)
            sso.save_private_snapshot(work / 'index.after', after)
            record = {'runtime': runtime, 'before': digest(current), 'after': digest(after), 'status': 'prepared'}
            update_private(receipt, encode(record))
        else:
            record = json.loads(private_read(receipt))
        before, after = (private_read(work / ('index.' + side)) for side in ('before', 'after'))
        require(record['runtime'] == runtime and digest(before) == record['before'] and
                digest(after) == record['after'] and current in (before, after), 'tavern_static_index_drift')
        if action == 'apply':
            base, candidate, listing = validate_assets(role, assets)
            require(candidate == after, 'tavern_static_candidate_changed')
            if current != after:
                temporary = cmd(['docker', 'exec', '--user', '0:0', container, 'mktemp', '-d',
                                 '/tmp/tavern-invite-static.XXXXXXXX'], 'tavern_static_stage_unavailable').decode().strip()
                require(re.fullmatch(r'/tmp/tavern-invite-static\.[A-Za-z0-9]{8}', temporary), 'unsafe_static_stage_path')
                cmd(['docker', 'cp', str(base / assets), container + ':' + temporary], 'tavern_static_transfer_failed', timeout=180)
                checks = ''.join(hash_ + '  ' + name + '\n' for name, hash_ in sorted(listing.items())).encode()
                cmd(['docker', 'exec', '-i', '--user', '0:0', container, 'sh', '-c', 'cd "$1" && sha256sum -c -', 'tavern-assets',
                     temporary], 'tavern_static_staged_assets_changed', content=checks)
                cmd(['docker', 'exec', '-i', '--user', '0:0', container, 'sh', '-c', INSTALL_ASSETS, 'tavern-assets',
                     temporary, root, assets], 'tavern_static_asset_conflict',
                    content=('\n'.join(sorted(listing)) + '\n').encode(), timeout=180)
            selected, prior = after, before
        else:
            selected, prior = before, after
        cmd(['docker', 'exec', '-i', '--user', '0:0', container, 'sh', '-c', SWITCH_INDEX, 'tavern-index', root,
             digest(prior), digest(selected)], 'tavern_static_index_switch_failed', content=selected)
        read_origin(url, selected)
        record['status'] = 'active' if action == 'apply' else 'rolled_back'
        update_private(receipt, encode(record))
        return {'complete': True, 'site': role, 'status': record['status'], 'restarted': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=ROLES)
    parser.add_argument('operation', choices=('apply', 'rollback'))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(run(args.role, args.operation), sort_keys=True))
    except Exception as error:
        message = str(error)
        code = message if re.fullmatch(r'[A-Za-z0-9_:.-]{1,160}', message) else type(error).__name__
        print(json.dumps({'complete': False, 'error': code, 'rollback_directory': str(WORK)}, sort_keys=True))
        raise SystemExit(1) from None
