import React, { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { type MatrixClient, Method } from "matrix-js-sdk/src/matrix";
import { copyPlainTextToClipboard } from "@element-hq/element-web-shared-utils";

import { MatrixClientPeg } from "../../MatrixClientPeg";
import dis from "../../dispatcher/dispatcher";
import { Action } from "../../dispatcher/actions";
import { type ViewRoomPayload } from "../../dispatcher/payloads/ViewRoomPayload";
import Modal from "../../Modal";
import { _t, getCurrentLanguage } from "../../languageHandler";
import BaseDialog from "../../components/views/dialogs/BaseDialog";
import AccessibleButton from "../../components/views/elements/AccessibleButton";
import DialogButtons from "../../components/views/elements/DialogButtons";
import {
    INVITE_TOKEN,
    PENDING_EVENT,
    TAVERN_INVITE_LINK,
    type PendingInvite,
    bindTavernInvite,
    clearTavernInvite,
    expectedTavernAccount,
    getPendingTavernInvite,
    tokenFromTavernInviteLink,
} from "./pending";

const HOMESERVER = "https://matrix.pingxingxian.space";
const API = "/_synapse/client/tavern-invites/v1";
const REQUEST_TIMEOUT_MS = 15_000;
const JOIN_WAIT_MS = 25_000;

type Operation = "create" | "redeem" | "revoke";

function translated(zh: string, en: string): string {
    return /^zh(?:[-_]|$)/i.test(getCurrentLanguage()) ? zh : en;
}

export function isTavernClient(client: MatrixClient): boolean {
    return client.getHomeserverUrl().replace(/\/$/, "") === HOMESERVER && !client.isGuest() && !!client.getUserId();
}

/** 仅替换私密房间邀请说明中的旧永久分享短语，保留输入用途和示例用户 ID。 */
export function tavernInviteHelp(identityServersEnabled: boolean, example: JSX.Element): JSX.Element {
    return (
        <>
            {identityServersEnabled
                ? translated("可用姓名、邮件地址或 Matrix ID（例如 ", "Invite by name, email address, or Matrix ID (for example, ")
                : translated("可用姓名或 Matrix ID（例如 ", "Invite by name or Matrix ID (for example, ")}
            {example}
            {translated("）邀请成员。", ").")}
        </>
    );
}

function current(client: MatrixClient): boolean {
    return MatrixClientPeg.get() === client && isTavernClient(client);
}

/** 复用当前 MatrixClient 的鉴权、刷新处理和 homeserver；邀请令牌只放请求体。 */
async function request(
    client: MatrixClient,
    action: Operation,
    body: Record<string, string>,
    abortSignal: AbortSignal,
): Promise<unknown> {
    if (!current(client)) throw new Error("tavern_invite_wrong_client");
    return client.http.authedRequest(Method.Post, `${API}/${action}`, undefined, body, {
        prefix: "",
        localTimeoutMs: REQUEST_TIMEOUT_MS,
        abortSignal,
    });
}

function asObject(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("tavern_invite_bad_response");
    return value as Record<string, unknown>;
}

interface Issued {
    token: string;
    expiresAt: number;
}

function issuedInvite(value: unknown): Issued {
    const { token, expires_at } = asObject(value);
    if (
        typeof token !== "string" ||
        !INVITE_TOKEN.test(token) ||
        typeof expires_at !== "number" ||
        !Number.isSafeInteger(expires_at) ||
        expires_at <= Date.now() / 1000 ||
        expires_at > 8_640_000_000_000
    ) {
        throw new Error("tavern_invite_bad_create_response");
    }
    return { token, expiresAt: expires_at };
}

function redeemedRoom(value: unknown): string {
    const id = asObject(value).room_id;
    if (typeof id !== "string" || id.length > 255 || !/^![^\s\x00-\x1f\x7f]+$/.test(id)) {
        throw new Error("tavern_invite_bad_redeem_response");
    }
    return id;
}

/** 仅在合格的普通私密房间邀请窗中增加一次性链接，不改变原生直接邀请。 */
export function TavernInviteControls({ client, roomId }: { client: MatrixClient; roomId: string }): JSX.Element {
    const [issued, setIssued] = useState<Issued>();
    const [active, setActive] = useState(false);
    const [message, setMessage] = useState("");
    const [pasted, setPasted] = useState("");
    const [notice, setNotice] = useState("");
    const inFlight = useRef<AbortController | undefined>(undefined);

    useEffect(() => () => inFlight.current?.abort(), []);

    const run = async (fn: (signal: AbortSignal) => Promise<void>): Promise<void> => {
        if (inFlight.current || !current(client)) return;
        const controller = new AbortController();
        inFlight.current = controller;
        setActive(true);
        setMessage("");
        setNotice("");
        try {
            await fn(controller.signal);
        } catch {
            // 不报告服务器原始响应，避免把房间、邀请或凭据写进日志。
            if (!controller.signal.aborted) {
                setMessage(translated("无法完成操作，请稍后重试。", "Could not complete the action. Please try again."));
            }
        } finally {
            if (inFlight.current === controller) inFlight.current = undefined;
            if (!controller.signal.aborted) setActive(false);
        }
    };

    const copy = (): Promise<void> => run(async (signal) => {
        // 同一弹窗重复复制尚未过期的链接时，不重新创建一张邀请。
        const value = issued && issued.expiresAt > Date.now() / 1000
            ? issued
            : issuedInvite(await request(client, "create", { room_id: roomId }, signal));
        if (signal.aborted || !current(client)) return;
        setIssued(value);
        const copied = await copyPlainTextToClipboard(`${TAVERN_INVITE_LINK}${value.token}`);
        if (signal.aborted) return;
        setNotice(copied
            ? translated("已复制邀请链接。", "Invite link copied.")
            : translated("无法自动复制，请手动复制下方链接。", "Copy failed. Copy the link below manually."));
    });

    const revoke = (token?: string): Promise<void> => run(async (signal) => {
        if (!token) {
            setMessage(translated("请粘贴完整的 Tavern 邀请链接。", "Paste the full Tavern invite link."));
            return;
        }
        await request(client, "revoke", { token }, signal);
        if (signal.aborted || !current(client)) return;
        if (issued?.token === token) setIssued(undefined);
        setPasted("");
        setNotice(translated("邀请链接已撤销。", "Invite link revoked."));
    });

    const label = translated("一次性邀请链接", "One-time invite link");
    return (
        <section aria-label={label} aria-busy={active}>
            <AccessibleButton kind="secondary" element="button" type="button" disabled={active} onClick={copy}>
                {translated("复制邀请链接", "Copy invite link")}
            </AccessibleButton>
            {issued && (
                <>
                    <p>
                        {translated("仅可由一人使用，有效至 ", "One person can use this link. Expires at ")}
                        <time dateTime={new Date(issued.expiresAt * 1000).toISOString()}>
                            {new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
                                new Date(issued.expiresAt * 1000),
                            )}
                        </time>
                    </p>
                    <input
                        aria-label={label}
                        readOnly
                        value={`${TAVERN_INVITE_LINK}${issued.token}`}
                        onFocus={(event) => event.currentTarget.select()}
                    />
                    <AccessibleButton
                        kind="link"
                        element="button"
                        type="button"
                        disabled={active}
                        onClick={() => revoke(issued.token)}
                    >
                        {translated("撤销此链接", "Revoke this link")}
                    </AccessibleButton>
                </>
            )}
            <details>
                <summary>{translated("撤销已有链接", "Revoke an existing link")}</summary>
                <label>
                    {translated("完整邀请链接", "Full invite link")}
                    <input
                        type="password"
                        autoComplete="off"
                        spellCheck={false}
                        maxLength={160}
                        value={pasted}
                        onChange={(event) => setPasted(event.target.value)}
                    />
                </label>
                <AccessibleButton
                    kind="secondary"
                    element="button"
                    type="button"
                    disabled={active || !pasted.trim()}
                    onClick={() => revoke(tokenFromTavernInviteLink(pasted))}
                >
                    {translated("撤销链接", "Revoke link")}
                </AccessibleButton>
            </details>
            {message && <p role="alert">{message}</p>}
            {notice && <p role="status">{notice}</p>}
        </section>
    );
}

/** 等待原生加入有界；超时后同一弹窗复用原请求，不发送并行加入。 */
async function waitForJoin<T>(request: Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
        return await Promise.race([
            request,
            new Promise<never>((_resolve, reject) => {
                timer = setTimeout(() => reject(new Error("tavern_invite_join_pending")), JOIN_WAIT_MS);
            }),
        ]);
    } finally {
        clearTimeout(timer);
    }
}

function RedeemDialog({
    client,
    pending,
    onFinished,
}: {
    client: MatrixClient;
    pending: PendingInvite;
    onFinished: () => void;
}): JSX.Element {
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const running = useRef<AbortController | undefined>(undefined);
    const autoStarted = useRef(false);
    const room = useRef<string | undefined>(undefined);
    const join = useRef<ReturnType<MatrixClient["joinRoom"]> | undefined>(undefined);
    const wrongAccount = Boolean(pending.token && expectedTavernAccount(pending) !== client.getUserId());

    useEffect(() => () => running.current?.abort(), []);

    const dismiss = (): void => {
        if (running.current) return;
        if (!wrongAccount) clearTavernInvite(pending);
        onFinished();
    };

    const redeem = useCallback(async (): Promise<void> => {
        if (!pending.token || wrongAccount || running.current) return;
        const claimant = client.getUserId();
        if (!claimant || !current(client) || !client.getCrypto() || !bindTavernInvite(pending, claimant)) {
            setMessage(translated(
                "当前会话无法领取此邀请，请返回检查登录和安全设置。",
                "This session cannot redeem the invite. Check your sign-in and security setup.",
            ));
            return;
        }
        const controller = new AbortController();
        running.current = controller;
        setBusy(true);
        setMessage("");
        try {
            if (!room.current) {
                const reply = await request(client, "redeem", { token: pending.token }, controller.signal);
                room.current = redeemedRoom(reply);
            }
            if (controller.signal.aborted || !current(client)) return;
            const roomId = room.current;
            if (!join.current) {
                const attempt = client.joinRoom(roomId);
                join.current = attempt;
                void attempt.catch(() => {
                    if (join.current === attempt) join.current = undefined;
                });
            }
            await waitForJoin(join.current);
            if (controller.signal.aborted || !current(client)) return;
            clearTavernInvite(pending);
            onFinished();
            dis.dispatch<ViewRoomPayload>({ action: Action.ViewRoom, room_id: roomId, metricsTrigger: undefined });
        } catch {
            if (!controller.signal.aborted) {
                setMessage(room.current
                    ? translated("暂未加入房间。请重试；不会重新领取邀请。", "Joining did not complete. Retry without redeeming again.")
                    : translated("暂时无法领取邀请。链接可能已过期、撤销或使用，请重试或返回。", "Could not redeem the invite. It may be expired, revoked, or used. Retry or go back."));
            }
        } finally {
            running.current = undefined;
            if (!controller.signal.aborted) setBusy(false);
        }
    }, [client, onFinished, pending, wrongAccount]);

    useEffect(() => {
        if (!pending.token || wrongAccount || autoStarted.current) return;
        // 等本次挂载定案再发送；StrictMode 的清理/重放会先撤掉未发生的第一次排程。
        const timer = setTimeout(() => {
            if (autoStarted.current) return;
            autoStarted.current = true;
            void redeem();
        }, 0);
        return () => clearTimeout(timer);
    }, [pending.token, redeem, wrongAccount]);

    return (
        <BaseDialog title={translated("加入私密房间", "Join a private room")} onFinished={dismiss}>
            <div className="mx_Dialog_content" aria-busy={busy}>
                <p>{wrongAccount
                    ? translated(
                        "当前 Tavern 账号与打开邀请时的平行线账号不同。关闭后，请退出当前 Tavern 账号，再重新打开原链接；邀请尚未领取。",
                        "This Tavern account differs from the account that opened the link. Close this dialog, sign out of Tavern, and open the link again. The invite has not been redeemed.",
                    )
                    : pending.token
                    ? translated("将使用当前平行线账号加入邀请所指房间。", "The invited room will open with your current account.")
                    : translated("这条邀请链接无效。", "This invite link is invalid.")}</p>
                {pending.token && !wrongAccount && !message && <p role="status">
                    {translated("正在确认邀请并加入房间…", "Checking the invite and joining the room…")}
                </p>}
                {message && <p role="alert">{message}</p>}
            </div>
            {!pending.token || wrongAccount ? (
                <DialogButtons primaryButton={_t("action|back")} hasCancel={false} onPrimaryButtonClick={dismiss} />
            ) : message ? (
                <DialogButtons
                    primaryButton={_t("action|retry")}
                    disabled={busy}
                    onPrimaryButtonClick={redeem}
                    cancelButton={_t("action|back")}
                    onCancel={dismiss}
                />
            ) : null}
        </BaseDialog>
    );
}

/** 仅挂载在完成 Element 登录、安全验证和初始同步的原生视图，随后处理同账号邀请。 */
export function TavernInviteGate({ client }: { client: MatrixClient }): null {
    useEffect(() => {
        let close: (() => void) | undefined;
        const offer = (): void => {
            const pending = getPendingTavernInvite();
            if (!pending || !current(client)) return;
            close?.();
            close = Modal.createDialog(RedeemDialog, { client, pending }).close;
        };
        offer();
        window.addEventListener(PENDING_EVENT, offer);
        return () => {
            window.removeEventListener(PENDING_EVENT, offer);
            close?.();
        };
    }, [client]);
    return null;
}
