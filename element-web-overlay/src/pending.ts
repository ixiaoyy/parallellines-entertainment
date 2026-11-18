/** 只在当前标签页保留待处理邀请；不可把邀请交给 Element 路由、日志或登录回调。 */
const TAVERN_ORIGIN = "https://tavern.pingxingxian.space";
const ENTRY_FRAGMENT = "#/tavern-invite/";
const RETURN_FRAGMENT = "#/tavern-invite-return/";
const MAIN_HANDOFF = "https://pingxingxian.space/tavern/invite#";
export const TAVERN_INVITE_LINK = TAVERN_ORIGIN + "/" + ENTRY_FRAGMENT;
export const INVITE_TOKEN = /^[A-Za-z0-9_-]{43}$/;

const FLOW = /^[A-Za-z0-9_-]{22}$/;
const SUBJECT = /^[1-9][0-9]{0,31}$/;
const STORAGE_KEY = "pl.tavern.invite.pending.v2";
const MAX_PENDING_MS = 60 * 60 * 1000;
export const PENDING_EVENT = "pl:tavern-invite";

export interface PendingInvite {
    token: string | null;
    flow: string | null;
    subject: string | null;
    receivedAt: number;
    claimant?: string;
}

let pending: PendingInvite | undefined;
let initialized = false;
type Startup = "ready" | "redirecting" | "blocked";

function save(value: PendingInvite | undefined, requireStorage = false): boolean {
    try {
        if (value) {
            const raw = JSON.stringify(value);
            window.sessionStorage.setItem(STORAGE_KEY, raw);
            if (requireStorage && window.sessionStorage.getItem(STORAGE_KEY) !== raw) return false;
        } else {
            window.sessionStorage.removeItem(STORAGE_KEY);
        }
    } catch {
        // 双跳必须确认本域标签存储可用；不把邀请码交给主站或任何其它存储兜底。
        if (requireStorage) return false;
    }
    pending = value;
    return true;
}

function clearFragment(location: URL): boolean {
    try {
        location.hash = "";
        window.history.replaceState(window.history.state, "", location.href);
        return true;
    } catch {
        return false;
    }
}

/** 16 字节不可预测关联值只关联主站回跳，不派生自邀请本身。 */
function newFlow(): string {
    const random = window.crypto.getRandomValues(new Uint8Array(16));
    return window.btoa(String.fromCharCode(...random)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function invalidInvite(): Startup {
    // 已打开的原生应用会显示统一的无效邀请；首次打开则显示安全的独立错误，不启动自动 SSO。
    pending = { token: null, flow: null, subject: null, receivedAt: Date.now() };
    window.dispatchEvent(new Event(PENDING_EVENT));
    return "blocked";
}

/** 先清理邀请 URL；主站只拿随机 FLOW，本标签仍是唯一持有 TOKEN 的客户端入口。 */
function captureTavernInvite(): Startup | undefined {
    const location = new URL(window.location.href);
    const hash = location.hash;
    const entering = hash.startsWith(ENTRY_FRAGMENT);
    if (!entering && !hash.startsWith(RETURN_FRAGMENT)) return undefined;

    // 原生授权回调永远优先。历史替换只清邀请片段，保留原查询参数。
    if (["loginToken", "code", "state", "error"].some((key) => location.searchParams.has(key))) {
        return clearFragment(location) ? "ready" : invalidInvite();
    }
    if (location.origin !== TAVERN_ORIGIN) {
        clearFragment(location);
        return invalidInvite();
    }
    if (entering) {
        const token = hash.slice(ENTRY_FRAGMENT.length);
        let flow: string | undefined;
        try {
            if (INVITE_TOKEN.test(token)) {
                const next = newFlow();
                if (FLOW.test(next) && save({ token, flow: next, subject: null, receivedAt: Date.now() }, true)) {
                    flow = next;
                }
            }
        } catch {
            // 随机源不可用也不能以可预测关联值或真实令牌继续。
        }
        if (!clearFragment(location) || !flow) return invalidInvite();
        try {
            window.location.replace(MAIN_HANDOFF + flow);
            return "redirecting";
        } catch {
            return invalidInvite();
        }
    }

    if (!clearFragment(location)) return invalidInvite();
    const active = freshPending();
    const parts = hash.slice(RETURN_FRAGMENT.length).split("/");
    const [flow, subject] = parts;
    if (!active) return invalidInvite();
    // 已配对或错误的回跳不覆盖同标签的邀请/账号，不消费错误的 FLOW。
    if (parts.length !== 2 || !FLOW.test(flow) || !SUBJECT.test(subject) ||
        !active.token || active.flow !== flow || active.subject !== null) {
        return "ready";
    }
    if (!save({ ...active, subject }, true)) return invalidInvite();
    window.dispatchEvent(new Event(PENDING_EVENT));
    return "ready";
}

/** 在 Element 读取 URL 之前安装，兼容随后在同一标签打开的新邀请。 */
export function setupTavernInviteCapture(): Startup {
    if (initialized) return "ready";
    initialized = true;
    try {
        const raw = window.sessionStorage.getItem(STORAGE_KEY);
        if (raw && raw.length <= 512) {
            const value: PendingInvite = JSON.parse(raw);
            if (
                value &&
                typeof value.token === "string" && INVITE_TOKEN.test(value.token) &&
                typeof value.flow === "string" && FLOW.test(value.flow) &&
                (value.subject === null || (typeof value.subject === "string" && SUBJECT.test(value.subject))) &&
                Number.isSafeInteger(value.receivedAt) &&
                (value.claimant === undefined || typeof value.claimant === "string")
            ) {
                pending = value;
            }
        }
    } catch {
        // 损坏或无法读取的本标签缓存不参与兑换。
    }
    window.addEventListener("hashchange", (event) => {
        if (captureTavernInvite()) event.stopImmediatePropagation();
    }, true);
    return captureTavernInvite() ?? "ready";
}

function freshPending(): PendingInvite | undefined {
    if (pending && (Date.now() - pending.receivedAt > MAX_PENDING_MS || Date.now() < pending.receivedAt)) {
        save(undefined);
    }
    return pending;
}

/** 未收到匹配主站回跳的真实 TOKEN 不进入 Element 的兑换界面。 */
export function getPendingTavernInvite(): PendingInvite | undefined {
    const active = freshPending();
    return active?.token && active.subject === null ? undefined : active;
}

/** 发出第一条兑换请求前绑定当前 Matrix 身份，重试不跨账号。 */
export function bindTavernInvite(value: PendingInvite, claimant: string): boolean {
    const active = getPendingTavernInvite();
    if (
        !value.token ||
        !active ||
        active.token !== value.token ||
        active.flow !== value.flow ||
        active.subject !== value.subject ||
        active.receivedAt !== value.receivedAt ||
        claimant !== expectedTavernAccount(active) ||
        (active.claimant !== undefined && active.claimant !== claimant)
    ) {
        return false;
    }
    save({ ...active, claimant });
    return true;
}

/** 仅用于客户端防止账号误入，不参与服务端授权，也不覆盖现有 Matrix 会话。 */
export function expectedTavernAccount(value: PendingInvite): string | undefined {
    return value.subject ? `@pl_${value.subject}:matrix.pingxingxian.space` : undefined;
}

/** 关闭旧弹窗不影响此后新打开的邀请。 */
export function clearTavernInvite(value?: PendingInvite): void {
    if (
        !value ||
        (pending?.token === value.token && pending.flow === value.flow &&
            pending.subject === value.subject && pending.receivedAt === value.receivedAt)
    ) {
        save(undefined);
    }
}

export function tokenFromTavernInviteLink(input: string): string | undefined {
    const text = input.trim();
    if (!text.startsWith(TAVERN_INVITE_LINK)) return undefined;
    const token = text.slice(TAVERN_INVITE_LINK.length);
    return INVITE_TOKEN.test(token) ? token : undefined;
}
