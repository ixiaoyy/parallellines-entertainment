import React, { type JSX, useEffect, useRef, useState } from "react";
import { type MatrixClient, Method } from "matrix-js-sdk/src/matrix";

import { MatrixClientPeg } from "../../MatrixClientPeg";
import AccessibleButton from "../../components/views/elements/AccessibleButton";
import { getCurrentLanguage } from "../../languageHandler";
import { isTavernClient } from "./RoomInvites";

export const TAVERN_FLUX_MXID = "@bot_flux-the-cat:matrix.pingxingxian.space";
const ENDPOINT = "/_synapse/client/tavern-flux/v1/invite";
const OFFICIAL_CARD = "https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/c4c1d8e8361afe13cbfded374f65fb65e87fcbc9/assets/character/default_FluxTheCat.png";

/** 付费确认仅对已由服务器计费接线的角色生效；其他 Bot 仍走原生邀请。 */
export function isPaidTavernRole(id: string): boolean {
    return id.trim() === TAVERN_FLUX_MXID;
}

/** 不扩展服务器目录；只给 Flux 的固定名称前缀和精确 MXID 提供本站搜索结果。 */
export function matchesTavernFluxSearch(term: string): boolean {
    const query = term.trim().toLowerCase();
    return query === TAVERN_FLUX_MXID || (query.length > 0 && "flux the cat".startsWith(query));
}

function translated(zh: string, en: string): string {
    return /^zh(?:[-_]|$)/i.test(getCurrentLanguage()) ? zh : en;
}

export function inviteOtherTavernMembersLabel(): string {
    return translated("邀请其他成员", "Invite other members");
}

/** 空/零表示不另设房间上限；其余十进制美元值精确转为安全整数微美元。 */
export function parseFluxLimitMicros(input: string): number | null {
    const value = input.trim();
    if (!value) return 0;
    if (value.length > 24 || !/^(?:\d+(?:\.\d{0,6})?|\.\d{1,6})$/.test(value)) return null;
    const [whole, fraction = ""] = value.split(".");
    const micros = Number((whole || "0") + fraction.padEnd(6, "0"));
    return Number.isSafeInteger(micros) ? micros : null;
}

function inviteErrorMessage(error: unknown): string {
    const code = typeof error === "object" && error !== null && "errcode" in error ? error.errcode : undefined;
    switch (code) {
        case "M_TAVERN_FLUX_LIMIT_INVALID":
        case "M_INVALID_PARAM":
            return translated("无法使用这个美元上限。请核对金额后再试。", "This USD limit is not available. Check the amount and try again.");
        case "M_TAVERN_FLUX_WALLET_UNAVAILABLE":
            return translated("没有找到你的主站账号绑定的可用 New API 钱包。未启用 Flux。", "No available New API wallet is linked to your account. Flux was not activated.");
        case "M_TAVERN_FLUX_FORBIDDEN":
        case "M_TAVERN_INVITE_FORBIDDEN":
            return translated("无法在此房间邀请 Flux。请核对账号、房间加密状态和邀请权限。", "Unable to invite Flux to this room. Check your account, room encryption, and invite permissions.");
        case "M_TAVERN_FLUX_STATE_CHANGED":
            return translated("房间的 Flux 授权已变化，或 Flux 已启用。请回房间查看。", "The room’s Flux authorization changed, or Flux is already active. Check the room.");
        case "M_LIMIT_EXCEEDED":
            return translated("邀请过于频繁，请稍后使用同一额度重试。", "Too many invitations. Wait and retry with the same limit.");
        case "M_TAVERN_FLUX_GATEWAY_UNAVAILABLE":
        default:
            return translated("未能确认邀请结果。请稍后使用同一额度重试。", "Could not confirm the invitation. Retry later with the same limit.");
    }
}

/** 复用当前 Matrix 账号的同源鉴权；不把访问凭据发给计费站。 */
export function TavernFluxFunding({ client, roomId, eligible, disabled, requestIds, onBusyChange, onFinished }: {
    client: MatrixClient;
    roomId: string;
    eligible: boolean;
    disabled: boolean;
    requestIds: Map<number, string>;
    onBusyChange: (busy: boolean) => void;
    onFinished: () => void;
}): JSX.Element {
    const inFlight = useRef<AbortController | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    const [amount, setAmount] = useState("");
    const [status, setStatus] = useState("");
    const micros = parseFluxLimitMicros(amount);
    useEffect(() => () => inFlight.current?.abort(), []);

    const invite = async (): Promise<void> => {
        if (!eligible || disabled || micros === null || inFlight.current || MatrixClientPeg.get() !== client || !isTavernClient(client)) return;
        const controller = new AbortController();
        inFlight.current = controller;
        setBusy(true);
        onBusyChange(true);
        setStatus("");
        try {
            // 同一次弹窗内，金额不变的重试始终沿用第一次发出的操作 ID。
            let requestId = requestIds.get(micros);
            if (!requestId) {
                requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
                requestIds.set(micros, requestId);
            }
            const data = await client.http.authedRequest<{ status?: string }>(
                Method.Post, ENDPOINT, undefined, { room_id: roomId, request_id: requestId, limit_usd_micros: micros },
                { prefix: "", localTimeoutMs: 15_000, abortSignal: controller.signal },
            );
            if (controller.signal.aborted || MatrixClientPeg.get() !== client) return;
            if (data.status !== "active") throw new Error("invalid_flux_invite_response");
            onFinished();
        } catch (error) {
            if (!controller.signal.aborted) setStatus(inviteErrorMessage(error));
        } finally {
            inFlight.current = undefined;
            if (!controller.signal.aborted) {
                setBusy(false);
                onBusyChange(false);
            }
        }
    };

    if (!eligible) return <p role="status">{translated("Flux 只能在有邀请权限的私密加密房间启用。", "Flux is only available in private encrypted rooms you can invite to.")}</p>;

    return (
        <section aria-label="Flux the Cat" aria-busy={busy}>
            <p>{translated("邀请本身不扣费。本房间任何成员 @Flux 使用模型时，都只扣你的 New API 钱包余额，不使用订阅额度。留空或填 0 不另设房间上限。",
                           "The invitation itself is free. Model requests from any member mentioning Flux in this room use your New API wallet balance, not subscription quota. Leave blank or enter 0 for no separate room limit.")}</p>
            <details>
                <summary>{translated("设置可选上限（美元）", "Set an optional USD limit")}</summary>
                <label>
                    {translated("本房间上限（USD）", "Room limit (USD)")}{" "}
                    <input type="text" inputMode="decimal" value={amount} maxLength={24} disabled={busy || disabled}
                        aria-invalid={micros === null} onChange={(event) => { setAmount(event.target.value); setStatus(""); }} />
                </label>
                {micros === null && <p role="status">{translated("请输入非负金额，最多保留 6 位小数。", "Enter a non-negative amount with up to 6 decimal places.")}</p>}
            </details>
            <AccessibleButton kind="primary" element="button" type="button" disabled={busy || disabled || micros === null} onClick={invite}>
                {translated("使用我的 New API 钱包额度邀请 Flux", "Invite Flux using my New API wallet balance")}
            </AccessibleButton>
            {status && <p role="status">{status}</p>}
            <p>
                <a href={OFFICIAL_CARD} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
                    {translated("官方下载原卡（PNG）", "Download official original card (PNG)")}
                </a>
            </p>
        </section>
    );
}
