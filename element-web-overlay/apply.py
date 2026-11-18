#!/usr/bin/env python3
"""Check/apply the narrowly scoped Tavern overlay to a disposable official Element checkout."""

import argparse
from pathlib import Path
import subprocess


REVISION = "7001d81efc9ff634e29494d714a986cd514aff74"
OVERLAY = Path(__file__).resolve().parent
WEB = "apps/web/src/"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE, timeout=20)


def replace_once(text, old, new, target):
    if text.count(old) != 1:
        raise ValueError(f"upstream context changed: {target}")
    return text.replace(old, new, 1)


def source_file(root, target):
    """Only modify the expected Git blob; accept Windows checkout line endings, not other edits."""
    path = root / target
    if not path.is_file() or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError(f"unsafe upstream file: {target}")
    data = path.read_bytes().replace(b"\r\n", b"\n")
    if data != git(root, "show", f"{REVISION}:{target}"):
        raise ValueError(f"upstream file is not pristine: {target}")
    return data.decode()


def startup(source):
    relative = WEB + "vector/index.ts"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import { parseAppUrl } from "./url_utils";',
        'import { parseAppUrl } from "./url_utils";\n'
        'import { getPendingTavernInvite, setupTavernInviteCapture } from "../utils/tavern-invite/pending";',
        relative,
    )
    data = replace_once(
        data,
        'require("./localstorage-fix");',
        'require("./localstorage-fix");\n\n'
        "// 先处理双跳邀请，再由 Element 读取 URL，原生 SSO 查询参数不变。\n"
        "const tavernInviteStartup = setupTavernInviteCapture();",
        relative,
    )
    data = replace_once(
        data,
        "        const preventRedirect = !!parsedUrl.params.threepid || parsedUrl.location.length > 0;",
        "        const preventRedirect =\n"
        "            !!getPendingTavernInvite() || !!parsedUrl.params.threepid || parsedUrl.location.length > 0;",
        relative,
    )
    data = replace_once(
        data,
        "async function start(): Promise<void> {\n",
        '''async function start(): Promise<void> {
    if (tavernInviteStartup !== "ready") {
        if (tavernInviteStartup === "blocked") {
            // 禁止存储或回跳无效时不触发 Element 的自动登录；普通入口仍由用户主动打开。
            const message = document.createElement("main");
            message.textContent = "邀请未继续。请在允许会话存储的窗口中重新打开有效邀请。 / Invite not continued. Reopen a valid invite with session storage enabled. ";
            const back = document.createElement("a");
            back.href = "/";
            back.textContent = "返回酒馆 / Back to Tavern";
            message.append(back);
            document.getElementById("matrixchat")?.replaceChildren(message);
        }
        return;
    }
''',
        relative,
    )
    return relative, data


def ready_view(source):
    relative = WEB + "components/structures/MatrixChat.tsx"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import { MatrixClientPeg } from "../../MatrixClientPeg";',
        'import { MatrixClientPeg } from "../../MatrixClientPeg";\n'
        'import { TavernInviteGate } from "../../utils/tavern-invite/RoomInvites";',
        relative,
    )
    data = replace_once(
        data,
        '''                    return (
                        <LoggedInView
                            {...this.props}
                            {...this.state}
                            ref={this.loggedInView}
                            matrixClient={MatrixClientPeg.safeGet()}
                            onRegistered={this.onRegistered}
                            currentRoomId={this.state.currentRoomId}
                        />
                    );''',
        '''                    const client = MatrixClientPeg.safeGet();
                    return (
                        <>
                            <LoggedInView
                                {...this.props}
                                {...this.state}
                                ref={this.loggedInView}
                                matrixClient={client}
                                onRegistered={this.onRegistered}
                                currentRoomId={this.state.currentRoomId}
                            />
                            <TavernInviteGate client={client} />
                        </>
                    );''',
        relative,
    )
    return relative, data


def invite_dialog(source):
    relative = WEB + "components/views/dialogs/InviteDialog.tsx"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import { EventType, RoomMember } from "matrix-js-sdk/src/matrix";',
        'import { EventType, JoinRule, RoomMember } from "matrix-js-sdk/src/matrix";',
        relative,
    )
    data = replace_once(
        data,
        'import CopyableText from "../elements/CopyableText";',
        'import CopyableText from "../elements/CopyableText";\n'
        'import { isTavernClient, TavernInviteControls, tavernInviteHelp } from "../../../utils/tavern-invite/RoomInvites";\n'
        'import { isPaidTavernRole, TavernFluxFunding } from "../../../utils/tavern-invite/FluxFunding";',
        relative,
    )
    data = replace_once(
        data,
        "    errorText?: string;\n}",
        "    errorText?: string;\n    showFluxFunding?: boolean;\n}",
        relative,
    )
    data = replace_once(
        data,
        '''        try {
            const inviter = new MultiInviter(cli, this.props.roomId, {''',
        '''        // 仅已接入付费的角色走确认额度；其他成员和机器人仍由 Matrix 原生邀请。
        const hasPaidRole = isTavernClient(cli) && targetIds.some(isPaidTavernRole);
        const nativeTargets = hasPaidRole ? targetIds.filter((id) => !isPaidTavernRole(id)) : targetIds;
        this.setState({ showFluxFunding: hasPaidRole, ...(hasPaidRole ? { errorText: undefined } : {}) });
        if (hasPaidRole && !nativeTargets.length) {
            this.setState({ busy: false });
            return;
        }

        try {
            const inviter = new MultiInviter(cli, this.props.roomId, {''',
        relative,
    )
    data = replace_once(
        data,
        '''            const states = await inviter.invite(targetIds);
            this.setState({ busy: false });
            const userMap = new Map<string, Member>(this.state.targets.map((member) => [member.userId, member]));
            this.props.onFinished(true);''',
        '''            const states = await inviter.invite(nativeTargets);
            this.setState({ busy: false });
            const userMap = new Map<string, Member>(this.state.targets.map((member) => [member.userId, member]));
            if (hasPaidRole) {
                // 同一次选择里的普通邀请已按原流程发出，保留弹窗给付费角色确认。
                this.setState({ targets: targets.filter((member) => isPaidTavernRole(member.userId)) });
            } else {
                this.props.onFinished(true);
            }''',
        relative,
    )
    data = replace_once(
        data,
        "    private renderMainTab(): JSX.Element {\n        let helpText;\n        let buttonText;",
        "    private renderMainTab(): JSX.Element {\n        let helpText;\n        let buttonText;\n"
        "        let tavernInviteRoom: string | undefined;",
        relative,
    )
    data = replace_once(
        data,
        '''            helpText = _t(
                helpTextUntranslated,
                {},
                {
                    userId: () => (
                        <a
                            className="mx_InviteDialog_helpText_userId"
                            href={makeUserPermalink(userId)}
                            rel="noreferrer noopener"
                            target="_blank"
                        >
                            {userId}
                        </a>
                    ),
                    a: (sub) => (
                        <a href={makeRoomPermalink(cli, roomId)} rel="noreferrer noopener" target="_blank">
                            {sub}
                        </a>
                    ),
                },
            );''',
        '''            const renderUserId = (): JSX.Element => (
                <a
                    className="mx_InviteDialog_helpText_userId"
                    href={makeUserPermalink(userId)}
                    rel="noreferrer noopener"
                    target="_blank"
                >
                    {userId}
                </a>
            );
            // 只替换合格私密房间的邀请窗分享入口；真正的邀请权限仍由服务端复核。
            if (
                isTavernClient(cli) &&
                room &&
                !isSpace &&
                room.getJoinRule() === JoinRule.Invite &&
                room.getMyMembership() === KnownMembership.Join &&
                room.canInvite(userId)
            ) {
                tavernInviteRoom = roomId;
                helpText = tavernInviteHelp(identityServersEnabled, renderUserId());
            } else {
                helpText = _t(
                    helpTextUntranslated,
                    {},
                    {
                        userId: renderUserId,
                        a: (sub) => (
                            <a href={makeRoomPermalink(cli, roomId)} rel="noreferrer noopener" target="_blank">
                                {sub}
                            </a>
                        ),
                    },
                );
            }''',
        relative,
    )
    data = replace_once(
        data,
        '                <p className="mx_InviteDialog_helpText">{helpText}</p>\n',
        '                <p className="mx_InviteDialog_helpText">{helpText}</p>\n'
        "                {tavernInviteRoom && <TavernInviteControls client={cli} roomId={tavernInviteRoom} />}\n"
        "                {isRoomInvite(this.props) && this.state.showFluxFunding && (\n"
        "                    <TavernFluxFunding client={cli} roomId={this.props.roomId}\n"
        "                        eligible={!!tavernInviteRoom && !!cli.getRoom(this.props.roomId)?.hasEncryptionStateEvent()}\n"
        "                        onFinished={() => this.props.onFinished(true)} />\n"
        "                )}\n",
        relative,
    )
    return relative, data


def plan(source):
    if git(source, "rev-parse", "HEAD").decode().strip() != REVISION:
        raise ValueError("Element checkout must match the locked revision")
    if git(source, "rev-parse", "--show-toplevel").decode().strip().replace("\\", "/") != source.as_posix():
        raise ValueError("pass the isolated upstream repository root")
    source_file(source, "pnpm-lock.yaml")
    directory = source / WEB / "utils/tavern-invite"
    if directory.exists() or directory.is_symlink():
        raise ValueError("Tavern overlay target already exists; use a fresh checkout")
    replacements = dict([startup(source), ready_view(source), invite_dialog(source)])
    additions = {name: (OVERLAY / "src" / name).read_text(encoding="utf-8")
                 for name in ("pending.ts", "RoomInvites.tsx", "FluxFunding.tsx")}
    return directory, replacements, additions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="isolated checkout at the pinned upstream commit")
    parser.add_argument("--apply", action="store_true", help="write the overlay to the disposable checkout")
    args = parser.parse_args()
    source = args.source.resolve()
    directory, replacements, additions = plan(source)
    if args.apply:
        directory.mkdir()
        for relative, data in replacements.items():
            (source / relative).write_bytes(data.encode())
        for name, data in additions.items():
            (directory / name).write_bytes(data.encode())
    print(f"{'Applied' if args.apply else 'Checked'} {REVISION}: {len(replacements)} patched, {len(additions)} added")


if __name__ == "__main__":
    main()
