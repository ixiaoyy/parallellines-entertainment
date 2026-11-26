import React, { type JSX } from "react";
import classNames from "classnames";
import { type MatrixClient } from "matrix-js-sdk/src/matrix";
import { HomeSolidIcon } from "@vector-im/compound-design-tokens/assets/web/icons";
import { IconButton, Text, Tooltip } from "@vector-im/compound-web";

import ContextMenu, { alwaysAboveRightOf, ChevronFace, useContextMenu } from "../../components/structures/ContextMenu";
import { getCurrentLanguage } from "../../languageHandler";
import { isTavernClient } from "./RoomInvites";

export const TAVERN_SITE_LINKS = [
    { id: "main", href: "https://pingxingxian.space/", zh: "返回主站", en: "Main Site" },
    { id: "chat", href: "https://chat.pingxingxian.space/", zh: "AI 聊天", en: "AI Chat" },
    { id: "wallet", href: "https://api.pingxingxian.space/", zh: "额度钱包", en: "API Wallet" },
] as const;

function translated(zh: string, en: string): string {
    return /^zh(?:[-_]|$)/i.test(getCurrentLanguage()) ? zh : en;
}

/** 仅在本站登录视图的左侧栏底部增加轻量跨站导航，保留 Element 原生布局，不传递凭据。 */
export function TavernSiteLinks({
    isPanelCollapsed = false,
    client,
}: {
    isPanelCollapsed: boolean;
    client: MatrixClient;
}): JSX.Element | null {
    const [menuDisplayed, handle, openMenu, closeMenu] = useContextMenu<HTMLButtonElement>();

    if (!isTavernClient(client)) return null;

    const title = translated("平行线", "ParallelLines");
    const menuLabel = translated("平行线站点导航", "ParallelLines navigation");

    let contextMenu: JSX.Element | undefined;
    if (menuDisplayed && handle.current) {
        contextMenu = (
            <ContextMenu
                {...alwaysAboveRightOf(handle.current.getBoundingClientRect(), ChevronFace.None, 16)}
                wrapperClassName="mx_QuickSettingsButton_ContextMenuWrapper"
                data-testid="tavern-site-links-menu"
                onFinished={closeMenu}
                managed={false}
                focusLock={true}
                role="region"
                aria-label={menuLabel}
            >
                <h2>{title}</h2>
                {TAVERN_SITE_LINKS.map((item) => (
                    <a
                        key={item.id}
                        href={item.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        data-testid={`tavern-site-link-${item.id}`}
                        className="mx_AccessibleButton mx_AccessibleButton_hasKind mx_AccessibleButton_kind_primary_outline"
                        style={{ textAlign: "center", textDecoration: "none" }}
                        onClick={closeMenu}
                    >
                        {translated(item.zh, item.en)}
                    </a>
                ))}
            </ContextMenu>
        );
    }

    let button: JSX.Element = (
        <IconButton
            aria-label={menuLabel}
            className={classNames("mx_QuickSettingsButton", { expanded: !isPanelCollapsed })}
            onClick={openMenu}
            ref={handle}
            aria-expanded={menuDisplayed}
            data-testid="tavern-site-links-button"
        >
            <>
                <HomeSolidIcon />
                {!isPanelCollapsed && (
                    <Text className="mx_QuickSettingsButton_label" as="span" size="md" title={title}>
                        {title}
                    </Text>
                )}
            </>
        </IconButton>
    );

    if (isPanelCollapsed) {
        button = (
            <Tooltip label={title} placement="right">
                {button}
            </Tooltip>
        );
    }

    return (
        <>
            {button}
            {contextMenu}
        </>
    );
}
