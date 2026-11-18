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
        'import { inviteOtherTavernMembersLabel, isPaidTavernRole, matchesTavernFluxSearch,\n'
        '    TAVERN_FLUX_MXID, TavernFluxFunding } from "../../../utils/tavern-invite/FluxFunding";',
        relative,
    )
    data = replace_once(
        data,
        "    private inviteUsers = async (): Promise<void> => {",
        '''    // 同一弹窗内，模型和金额完全匹配的邀请重试沿用原请求 ID。
    private readonly fluxRequestIds = new Map<string, string>();

    private onFluxInviteComplete = (): void => {
        const targets = this.state.targets.filter((member) => !isPaidTavernRole(member.userId));
        const filterText = isPaidTavernRole(this.state.filterText) ? "" : this.state.filterText;
        if (targets.length || filterText) {
            this.setState({ targets, filterText, errorText: undefined });
        } else {
            this.props.onFinished(true);
        }
    };

    private onFluxInviteBusyChange = (busy: boolean): void => this.setBusy(busy);

    private inviteUsers = async (): Promise<void> => {''',
        relative,
    )
    data = replace_once(
        data,
        '''        try {
            const inviter = new MultiInviter(cli, this.props.roomId, {''',
        '''        // 固定付费角色只走专用邀请路线，任何客户端条件失败都不回退原生邀请。
        const hasPaidRole = targetIds.some(isPaidTavernRole);
        const nativeTargets = hasPaidRole ? targetIds.filter((id) => !isPaidTavernRole(id)) : targetIds;
        if (hasPaidRole && !nativeTargets.length) {
            this.setBusy(false);
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
                // 普通成功目标不重发；保留待付费角色和未成功的原生目标。
                this.setState({
                    targets: targets.filter((member) => isPaidTavernRole(member.userId) || states[member.userId] !== "invited"),
                });
            } else {
                this.props.onFinished(true);
            }''',
        relative,
    )
    data = replace_once(
        data,
        '''        const targets = this.convertFilter();
        const unknownIdentityUsers: Member[] = [];''',
        '''        const targets = this.convertFilter();
        // 额度区自己发邀请；只给非付费目标走原有的未知身份提醒。
        const nativeTargets = isRoomInvite(this.props) ? targets.filter((m) => !isPaidTavernRole(m.userId)) : targets;
        if (targets.length && !nativeTargets.length) {
            this.setBusy(false);
            return;
        }
        const unknownIdentityUsers: Member[] = [];''',
        relative,
    )
    data = replace_once(
        data,
        '''            for (const t of targets) {
                const addressType = getAddressType(t.userId);''',
        '''            for (const t of nativeTargets) {
                const addressType = getAddressType(t.userId);''',
        relative,
    )
    data = replace_once(
        data,
        "        const hasMixins = this.state.serverResultsMixin || this.state.threepidResultsMixin;",
        "        let fluxSuggestion: Result | undefined;\n"
        "        const hasMixins = this.state.serverResultsMixin || this.state.threepidResultsMixin;",
        relative,
    )
    data = replace_once(
        data,
        '''            priorityAdditionalMembers = this.state.threepidResultsMixin.filter(notAlreadyExists);
        }
        const hasAdditionalMembers''',
        '''            priorityAdditionalMembers = this.state.threepidResultsMixin.filter(notAlreadyExists);
            if (isRoomInvite(this.props) && matchesTavernFluxSearch(this.state.filterText)) {
                const client = MatrixClientPeg.safeGet();
                const room = client.getRoom(this.props.roomId);
                const fluxMembership = room?.getMember(TAVERN_FLUX_MXID)?.membership;
                const result = {
                    userId: TAVERN_FLUX_MXID,
                    user: new DirectoryMember({ user_id: TAVERN_FLUX_MXID, display_name: "Flux the Cat" }),
                };
                // 目录没返回同一角色时仅补这个固定条目，不改搜索范围或产生重复结果。
                if (room && !room.isSpaceRoom() && isTavernClient(client) &&
                        fluxMembership !== KnownMembership.Join && fluxMembership !== KnownMembership.Invite &&
                        !this.state.targets.some((member) => isPaidTavernRole(member.userId)) && notAlreadyExists(result)) {
                    fluxSuggestion = result;
                }
            }
        }
        const hasAdditionalMembers''',
        relative,
    )
    data = replace_once(
        data,
        "        const hasAdditionalMembers = priorityAdditionalMembers.length > 0 || otherAdditionalMembers.length > 0;",
        "        const hasAdditionalMembers = !!fluxSuggestion || priorityAdditionalMembers.length > 0 || otherAdditionalMembers.length > 0;",
        relative,
    )
    data = replace_once(
        data,
        "        sourceMembers = [...priorityAdditionalMembers, ...sourceMembers, ...otherAdditionalMembers];",
        "        sourceMembers = [...(fluxSuggestion ? [fluxSuggestion] : []), ...priorityAdditionalMembers, ...sourceMembers, ...otherAdditionalMembers];",
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
        "        const onGoButtonPressed = (): void => {\n",
        '''        // 选择 Flux 即呈现明确的付费动作；混合选择另保留只处理其他成员的按钮。
        const paidFilter = isPaidTavernRole(this.state.filterText);
        const paidSelected = isRoomInvite(this.props) && (paidFilter || this.state.targets.some((m) => isPaidTavernRole(m.userId)));
        const nativeSelected = (this.state.filterText.includes("@") && !paidFilter) || this.state.targets.some((m) => !isPaidTavernRole(m.userId));
        const onGoButtonPressed = (): void => {
''',
        relative,
    )
    data = replace_once(
        data,
        '''                    <AccessibleButton
                        kind="primary"
                        onClick={onGoButtonPressed}
                        className="mx_InviteDialog_goButton"
                        disabled={this.state.busy || !this.hasSelection()}
                    >
                        {buttonText}
                    </AccessibleButton>
                </div>
                {this.state.busy ? <InviteProgressBody /> : this.renderSuggestions()}''',
        '''                    {(!paidSelected || nativeSelected) && (
                        <AccessibleButton
                            kind="primary"
                            onClick={onGoButtonPressed}
                            className="mx_InviteDialog_goButton"
                            disabled={this.state.busy || !this.hasSelection()}
                        >
                            {paidSelected ? inviteOtherTavernMembersLabel() : buttonText}
                        </AccessibleButton>
                    )}
                </div>
                {paidSelected && isRoomInvite(this.props) && (
                    <TavernFluxFunding
                        client={cli}
                        roomId={this.props.roomId}
                        eligible={!!tavernInviteRoom && !!cli.getRoom(this.props.roomId)?.hasEncryptionStateEvent()}
                        disabled={this.state.busy}
                        requestIds={this.fluxRequestIds}
                        onBusyChange={this.onFluxInviteBusyChange}
                        onFinished={this.onFluxInviteComplete}
                    />
                )}
                {this.state.busy ? <InviteProgressBody /> : this.renderSuggestions()}''',
        relative,
    )
    data = replace_once(
        data,
        '                <p className="mx_InviteDialog_helpText">{helpText}</p>\n',
        '                <p className="mx_InviteDialog_helpText">{helpText}</p>\n'
        "                {tavernInviteRoom && <TavernInviteControls client={cli} roomId={tavernInviteRoom} />}\n",
        relative,
    )
    return relative, data


def room_header(source):
    """把 Flux 模型入口接到真实房间标题，保留已有按钮和布局。"""
    relative = WEB + "components/views/rooms/RoomHeader/RoomHeader.tsx"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import { SDKContext } from "../../../../contexts/SDKContext.ts";',
        'import { SDKContext } from "../../../../contexts/SDKContext.ts";\n'
        'import { TavernFluxRoomModelButton } from "../../../../utils/tavern-invite/FluxFunding";',
        relative,
    )
    data = replace_once(
        data,
        '            {extraButtons}\n',
        '            {extraButtons}\n'
        '            {sdkContext.client && <TavernFluxRoomModelButton client={sdkContext.client} room={room} />}\n',
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
    replacements = dict([startup(source), ready_view(source), invite_dialog(source), room_header(source)])
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
