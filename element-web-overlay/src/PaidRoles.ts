import { useEffect, useState } from "react";
import { type MatrixClient, Method } from "matrix-js-sdk/src/matrix";

export type PaidTavernRole = Readonly<{
    id: string;
    mxid: string;
    name: string;
    shortName: string;
    originalCard: string;
    profileDescription: string;
    inviteNoticeZh: string;
    inviteNoticeEn: string;
}>;

const ROLES_ENDPOINT = "/_synapse/client/tavern-flux/v1/roles";
const ROLE_ID_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

/** 服务端尚未提供动态目录时的默认回退清单；运行时优先以 Synapse 返回的公开角色目录为准。 */
export const DEFAULT_TAVERN_PAID_ROLES: readonly PaidTavernRole[] = [
    {
        id: "flux-the-cat",
        mxid: "@bot_flux-the-cat:matrix.pingxingxian.space",
        name: "Flux the Cat",
        shortName: "Flux",
        originalCard: "https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/c4c1d8e8361afe13cbfded374f65fb65e87fcbc9/assets/character/default_FluxTheCat.png",
        profileDescription: "不会说人话的猫，只会喵叫、打呼噜，用动作表达。",
        inviteNoticeZh: "邀请和开场白免费。Flux 会主动接话；模型用量只扣你的 New API 钱包，不用订阅额度。留空或填 0 不另设房间上限。",
        inviteNoticeEn: "Inviting Flux and its opening message are free. Flux can join in without a mention. Model usage is charged only to your New API wallet, not your subscription. Leave blank or enter 0 for no separate room limit.",
    },
    {
        id: "sakana",
        mxid: "@bot_sakana:matrix.pingxingxian.space",
        name: "Sakana",
        shortName: "Sakana",
        originalCard: "https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/e3ca170989689c6eae8b5cba8b6c49390bdbebbf/assets/character/contest_Assistant_Sakana.png",
        profileDescription: "爱吐槽的办公助理，默认用中文，被 @ 时才回复。",
        inviteNoticeZh: "邀请免费。只有 @Sakana 才调用模型；模型用量只扣你的 New API 钱包，不用订阅额度。留空或填 0 不另设 Sakana 的房间上限。",
        inviteNoticeEn: "Inviting Sakana is free. Sakana calls the model only when mentioned. Usage is charged only to your New API wallet, not your subscription. Leave blank or enter 0 for no separate Sakana room limit.",
    },
];

/** 兼容既有同步引用点：服务端目录加载成功后就地刷新，邀请与资料侧栏共享同一份运行时登记。 */
export const TAVERN_PAID_ROLES: PaidTavernRole[] = [...DEFAULT_TAVERN_PAID_ROLES];

const listeners = new Set<() => void>();
const loadedClients = new WeakSet<MatrixClient>();

function isValidPaidRole(value: unknown): value is PaidTavernRole {
    if (typeof value !== "object" || value === null) return false;
    const item = value as Record<string, unknown>;
    return (
        typeof item.id === "string" &&
        ROLE_ID_RE.test(item.id) &&
        typeof item.mxid === "string" &&
        item.mxid.startsWith("@bot_") &&
        typeof item.name === "string" &&
        item.name.length > 0 &&
        typeof item.shortName === "string" &&
        item.shortName.length > 0 &&
        typeof item.originalCard === "string" &&
        item.originalCard.startsWith("https://") &&
        typeof item.profileDescription === "string" &&
        item.profileDescription.length > 0 &&
        typeof item.inviteNoticeZh === "string" &&
        item.inviteNoticeZh.length > 0 &&
        typeof item.inviteNoticeEn === "string" &&
        item.inviteNoticeEn.length > 0
    );
}

function notifyRoleListeners(): void {
    for (const listener of listeners) {
        try {
            listener();
        } catch {
            // 单个视图监听异常不影响其他组件刷新。
        }
    }
}

/** 仅从 Synapse 公开目录读取已启用付费角色，不触碰任何 Bot 密钥。 */
export async function loadTavernPaidRoles(client: MatrixClient): Promise<readonly PaidTavernRole[]> {
    if (loadedClients.has(client)) return TAVERN_PAID_ROLES;
    loadedClients.add(client);
    try {
        const response = await client.http.authedRequest<{ roles?: unknown }>(
            Method.Post,
            ROLES_ENDPOINT,
            undefined,
            {},
            { prefix: "", localTimeoutMs: 10_000 },
        );
        if (response && Array.isArray(response.roles) && response.roles.length > 0 && response.roles.every(isValidPaidRole)) {
            TAVERN_PAID_ROLES.splice(0, TAVERN_PAID_ROLES.length, ...response.roles);
            notifyRoleListeners();
        }
    } catch {
        // 旧版 Synapse 未开放 /roles 时静默回退默认清单，不影响既有邀请体验。
        loadedClients.delete(client);
    }
    return TAVERN_PAID_ROLES;
}

export function useTavernPaidRoles(client?: MatrixClient | null): readonly PaidTavernRole[] {
    const [roles, setRoles] = useState<readonly PaidTavernRole[]>(() => [...TAVERN_PAID_ROLES]);
    useEffect(() => {
        const sync = (): void => setRoles([...TAVERN_PAID_ROLES]);
        listeners.add(sync);
        if (client) void loadTavernPaidRoles(client);
        return () => {
            listeners.delete(sync);
        };
    }, [client]);
    return roles;
}
