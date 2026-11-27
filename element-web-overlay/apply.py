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
            back.textContent = "返回多人聊天 / Back to Group Chat";
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
        'import { inviteOtherTavernMembersLabel, isPaidTavernRole, matchesPaidTavernRoleSearch,\n'
        '    TAVERN_PAID_ROLES, TavernPaidRoleFunding } from "../../../utils/tavern-invite/FluxFunding";',
        relative,
    )
    data = replace_once(
        data,
        "    private inviteUsers = async (): Promise<void> => {",
        '''    // 同一弹窗内，角色、模型和金额都一致的邀请重试沿用原请求 ID。
    private readonly paidRoleRequestIds = new Map<string, string>();

    private get selectedPaidRoles() {
        if (!isRoomInvite(this.props)) return [];
        return TAVERN_PAID_ROLES.filter((role) =>
            role.mxid === this.state.filterText.trim() || this.state.targets.some((member) => member.userId.trim() === role.mxid)
        );
    }

    private onPaidRoleInviteComplete = (mxid: string): void => {
        // 一个角色启用只清除自身；同一次选择的其他角色、普通目标继续保留。
        const targets = this.state.targets.filter((member) => member.userId.trim() !== mxid);
        const filterText = this.state.filterText.trim() === mxid ? "" : this.state.filterText;
        if (targets.length || filterText) {
            this.setState({ targets, filterText, errorText: undefined });
        } else {
            this.props.onFinished(true);
        }
    };

    private onPaidRoleInviteBusyChange = (busy: boolean): void => this.setBusy(busy);

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
        "        const paidRoleSuggestions: Result[] = [];\n"
        "        const hasMixins = this.state.serverResultsMixin || this.state.threepidResultsMixin;",
        relative,
    )
    data = replace_once(
        data,
        '''            priorityAdditionalMembers = this.state.threepidResultsMixin.filter(notAlreadyExists);
        }
        const hasAdditionalMembers''',
        '''            priorityAdditionalMembers = this.state.threepidResultsMixin.filter(notAlreadyExists);
            if (isRoomInvite(this.props)) {
                const client = MatrixClientPeg.safeGet();
                const room = client.getRoom(this.props.roomId);
                if (room && !room.isSpaceRoom() && isTavernClient(client)) {
                    // 目录没返回时仅补命中的固定角色；已在房或已选的同一角色不重复补。
                    for (const role of TAVERN_PAID_ROLES) {
                        const membership = room.getMember(role.mxid)?.membership;
                        if (!matchesPaidTavernRoleSearch(this.state.filterText, role) ||
                                membership === KnownMembership.Join || membership === KnownMembership.Invite ||
                                this.state.targets.some((member) => member.userId === role.mxid) ||
                                !notAlreadyExists({ userId: role.mxid })) continue;
                        paidRoleSuggestions.push({
                            userId: role.mxid,
                            user: new DirectoryMember({ user_id: role.mxid, display_name: role.name }),
                        });
                    }
                }
            }
        }
        const hasAdditionalMembers''',
        relative,
    )
    data = replace_once(
        data,
        "        const hasAdditionalMembers = priorityAdditionalMembers.length > 0 || otherAdditionalMembers.length > 0;",
        "        const hasAdditionalMembers = paidRoleSuggestions.length > 0 || priorityAdditionalMembers.length > 0 || otherAdditionalMembers.length > 0;",
        relative,
    )
    data = replace_once(
        data,
        "        sourceMembers = [...priorityAdditionalMembers, ...sourceMembers, ...otherAdditionalMembers];",
        "        sourceMembers = [...paidRoleSuggestions, ...priorityAdditionalMembers, ...sourceMembers, ...otherAdditionalMembers];",
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
        '''        // 每个付费角色有独立的确认动作；普通成员仍用原生邀请按钮。
        const paidRoomId = isRoomInvite(this.props) ? this.props.roomId : undefined;
        const paidFilter = isPaidTavernRole(this.state.filterText);
        const paidRoles = this.selectedPaidRoles;
        const paidSelected = paidRoles.length > 0;
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
                {paidRoomId && paidRoles.map((role) => (
                    <TavernPaidRoleFunding
                        key={role.id}
                        role={role}
                        client={cli}
                        roomId={paidRoomId}
                        eligible={!!tavernInviteRoom && !!cli.getRoom(paidRoomId)?.hasEncryptionStateEvent()}
                        disabled={this.state.busy}
                        requestIds={this.paidRoleRequestIds}
                        onBusyChange={this.onPaidRoleInviteBusyChange}
                        onFinished={this.onPaidRoleInviteComplete}
                    />
                ))}
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
    data = replace_once(
        data,
        '                <div className="mx_InviteDialog_content">{this.renderMainTab()}</div>',
        '''                {/* 原生内容区会截断；只在并列付费表单时允许纵向滚动。 */}
                <div className="mx_InviteDialog_content" style={this.selectedPaidRoles.length > 1 ? { overflowY: "auto" } : undefined}>
                    {this.renderMainTab()}
                </div>''',
        relative,
    )
    return relative, data


def room_header(source):
    """把独立的付费角色模型入口接到房间标题，保留已有按钮和布局。"""
    relative = WEB + "components/views/rooms/RoomHeader/RoomHeader.tsx"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import { SDKContext } from "../../../../contexts/SDKContext.ts";',
        'import { SDKContext } from "../../../../contexts/SDKContext.ts";\n'
        'import { TavernPaidRoleRoomModelButtons } from "../../../../utils/tavern-invite/FluxFunding";',
        relative,
    )
    data = replace_once(
        data,
        '            {extraButtons}\n',
        '            {extraButtons}\n'
        '            {sdkContext.client && <TavernPaidRoleRoomModelButtons client={sdkContext.client} room={room} />}\n',
        relative,
    )
    return relative, data


def role_profile_header(source):
    """只为 Tavern 的两个精确角色身份在普通资料页名称下加简介。"""
    relative = WEB + "components/views/right_panel/user_info/UserInfoHeaderView.tsx"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import { useUserStatus } from "../../../../hooks/useUserStatus";',
        'import { useUserStatus } from "../../../../hooks/useUserStatus";\n'
        'import { useMatrixClientContext } from "../../../../contexts/MatrixClientContext";\n'
        'import { TAVERN_PAID_ROLES } from "../../../../utils/tavern-invite/PaidRoles";\n'
        'import { isTavernClient } from "../../../../utils/tavern-invite/RoomInvites";',
        relative,
    )
    data = replace_once(
        data,
        '    const userStatus = useUserStatus(member.userId);',
        '''    const userStatus = useUserStatus(member.userId);
    const client = useMatrixClientContext();
    // 精确匹配本站角色的 Matrix ID；不凭显示名判断，也不改设备验证面板。
    const role = TAVERN_PAID_ROLES.find((candidate) => candidate.mxid === member.userId);
    const roleDescription = role && !hideVerificationSection && isTavernClient(client)
        ? role.profileDescription
        : undefined;''',
        relative,
    )
    data = replace_once(
        data,
        '                    {userStatus && <StatusTextView status={userStatus} />}',
        '''                    {roleDescription && (
                        <Text as="p" size="sm" weight="regular" lang="zh-CN" style={{ margin: 0, textAlign: "center" }}>
                            {roleDescription}
                        </Text>
                    )}
                    {userStatus && <StatusTextView status={userStatus} />}''',
        relative,
    )
    return relative, data


def space_panel(source):
    """在左侧 SpacePanel 底部挂载轻量跨站导航入口，保留 Element 原生布局与快捷键。"""
    relative = WEB + "components/views/spaces/SpacePanel.tsx"
    data = source_file(source, relative)
    data = replace_once(
        data,
        'import QuickSettingsButton from "./QuickSettingsButton";',
        'import QuickSettingsButton from "./QuickSettingsButton";\n'
        'import { TavernSiteLinks } from "../../../utils/tavern-invite/SiteLinks";',
        relative,
    )
    data = replace_once(
        data,
        '                        <QuickSettingsButton isPanelCollapsed={isPanelCollapsed} />',
        '                        <TavernSiteLinks isPanelCollapsed={isPanelCollapsed} client={client} />\n'
        '                        <QuickSettingsButton isPanelCollapsed={isPanelCollapsed} />',
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
    replacements = dict([startup(source), ready_view(source), invite_dialog(source), room_header(source),
                         role_profile_header(source), space_panel(source)])
    additions = {name: (OVERLAY / "src" / name).read_text(encoding="utf-8")
                 for name in ("pending.ts", "RoomInvites.tsx", "FluxFunding.tsx", "PaidRoles.ts", "SiteLinks.tsx")}
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
