import React, { type JSX, useEffect, useRef, useState } from "react";
import { type MatrixClient, Method } from "matrix-js-sdk/src/matrix";

import { MatrixClientPeg } from "../../MatrixClientPeg";
import AccessibleButton from "../../components/views/elements/AccessibleButton";
import { getCurrentLanguage } from "../../languageHandler";
import { isTavernClient } from "./RoomInvites";

const PAID_ROLE_IDS = new Set(["@bot_flux-the-cat:matrix.pingxingxian.space"]);
const ENDPOINT = "/_synapse/client/tavern-flux/v1";
const AUTHORIZATION = "https://api.pingxingxian.space/tavern-flux/authorize";

/** 付费确认仅对已由服务器计费接线的角色生效；其他 Bot 仍走原生邀请。 */
export function isPaidTavernRole(id: string): boolean {
    return PAID_ROLE_IDS.has(id);
}

function translated(zh: string, en: string): string {
    return /^zh(?:[-_]|$)/i.test(getCurrentLanguage()) ? zh : en;
}

/** 复用当前 Matrix 账号的同源鉴权；不把访问凭据发给计费站。 */
export function TavernFluxFunding({ client, roomId, eligible, onFinished }: {
    client: MatrixClient;
    roomId: string;
    eligible: boolean;
    onFinished: () => void;
}): JSX.Element {
    const inFlight = useRef<AbortController | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    const [intent, setIntent] = useState<string>();
    const [status, setStatus] = useState("");
    useEffect(() => () => inFlight.current?.abort(), []);

    const run = async (action: "prepare" | "finish"): Promise<void> => {
        if (inFlight.current || MatrixClientPeg.get() !== client || !isTavernClient(client)) return;
        const controller = new AbortController();
        inFlight.current = controller;
        setBusy(true);
        setStatus("");
        try {
            const data = await client.http.authedRequest<{ intent_id?: string; status?: string }>(
                Method.Post, `${ENDPOINT}/${action}`, undefined,
                action === "prepare" ? { room_id: roomId } : { room_id: roomId, intent_id: intent },
                { prefix: "", localTimeoutMs: 15_000, abortSignal: controller.signal },
            );
            if (controller.signal.aborted || MatrixClientPeg.get() !== client) return;
            if (action === "prepare" && typeof data.intent_id === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(data.intent_id)) {
                setIntent(data.intent_id);
            } else if (action === "finish" && data.status === "active") {
                onFinished();
            } else if (action === "finish" && data.status === "pending") {
                setStatus(translated("还没确认额度。请在 New API 完成确认后再回来。", "Confirm the limit in New API first."));
            } else {
                throw new Error("invalid_flux_funding_response");
            }
        } catch {
            if (!controller.signal.aborted) {
                setStatus(translated("未能确认 Flux 的启用状态。请核对房间权限或重试检查。",
                    "Could not verify whether Flux was activated. Check room permissions or try again."));
            }
        } finally {
            inFlight.current = undefined;
            if (!controller.signal.aborted) setBusy(false);
        }
    };

    if (!eligible) return <p role="status">{translated("Flux 只能在有邀请权限的私密加密房间启用。", "Flux is only available in private encrypted rooms you can invite to.")}</p>;

    return (
        <section aria-label="Flux the Cat" aria-busy={busy}>
            <p>{translated("本房间任何成员 @Flux，都会从你的 New API 钱包额度扣除。确认上限前不会启用，也不会使用订阅额度。",
                           "Any member mentioning Flux in this room uses your New API wallet balance, not subscription quota. Confirm a limit before activating it.")}</p>
            {!intent ? (
                <AccessibleButton kind="secondary" element="button" type="button" disabled={busy} onClick={() => run("prepare")}>
                    {translated("设置 Flux 的额度上限", "Set Flux’s quota limit")}
                </AccessibleButton>
            ) : (
                <>
                    <p><a href={`${AUTHORIZATION}?intent_id=${encodeURIComponent(intent)}`} target="_blank" rel="noopener noreferrer">
                        {translated("在 New API 设置并确认上限", "Confirm a limit in New API")}
                    </a></p>
                    <AccessibleButton kind="secondary" element="button" type="button" disabled={busy} onClick={() => run("finish")}>
                        {translated("我已确认，启用 Flux", "I’ve confirmed. Activate Flux")}
                    </AccessibleButton>
                </>
            )}
            {status && <p role="status">{status}</p>}
        </section>
    );
}
