import React, { type JSX, useCallback, useEffect, useRef, useState } from "react";
import { JoinRule, KnownMembership, type MatrixClient, Method, type Room } from "matrix-js-sdk/src/matrix";

import { MatrixClientPeg } from "../../MatrixClientPeg";
import Modal from "../../Modal";
import BaseDialog from "../../components/views/dialogs/BaseDialog";
import AccessibleButton from "../../components/views/elements/AccessibleButton";
import { getCurrentLanguage } from "../../languageHandler";
import { type PaidTavernRole, TAVERN_PAID_ROLES, useTavernPaidRoles } from "./PaidRoles";
import { isTavernClient } from "./RoomInvites";

export { TAVERN_PAID_ROLES, loadTavernPaidRoles, useTavernPaidRoles } from "./PaidRoles";

const ENDPOINT = "/_synapse/client/tavern-flux/v1";
const PAID_ROLE_INVITED = "tavern-paid-role-invited";

/** 付费确认仅对已由服务器计费接线的角色生效；其他 Bot 仍走原生邀请。 */
export function isPaidTavernRole(id: string): boolean {
    return TAVERN_PAID_ROLES.some((role) => role.mxid === id.trim());
}

/** 不扩展服务器目录；只按本站付费角色名称前缀或精确 MXID 补充结果。 */
export function matchesPaidTavernRoleSearch(term: string, role: PaidTavernRole): boolean {
    const query = term.trim().toLowerCase();
    return query === role.mxid || (query.length > 0 && role.name.toLowerCase().startsWith(query));
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

function errorCode(error: unknown): unknown {
    return typeof error === "object" && error !== null && "errcode" in error ? error.errcode : undefined;
}

function inviteErrorMessage(error: unknown, name: string): string {
    switch (errorCode(error)) {
        case "M_TAVERN_FLUX_LIMIT_INVALID":
        case "M_INVALID_PARAM":
            return translated("无法使用这个美元上限。请核对金额后再试。", "This USD limit is not available. Check the amount and try again.");
        case "M_TAVERN_FLUX_WALLET_UNAVAILABLE":
            return translated(`没有找到你的主站账号绑定的可用 New API 钱包。未启用 ${name}。`, `No available New API wallet is linked to your account. ${name} was not activated.`);
        case "M_TAVERN_FLUX_MODEL_UNAVAILABLE":
            return translated("所选模型暂不可用。请选择其他模型。", "This model is not available right now. Choose another model.");
        case "M_TAVERN_FLUX_FORBIDDEN":
        case "M_TAVERN_INVITE_FORBIDDEN":
            return translated(`无法在此房间邀请 ${name}。请核对账号、房间加密状态和邀请权限。`, `Unable to invite ${name} to this room. Check your account, room encryption, and invite permissions.`);
        case "M_TAVERN_FLUX_STATE_CHANGED":
            return translated(`房间的 ${name} 授权已变化，或 ${name} 已启用。请回房间查看。`, `The room’s ${name} authorization changed, or ${name} is already active. Check the room.`);
        case "M_LIMIT_EXCEEDED":
            return translated("邀请过于频繁，请稍后使用同一模型和上限重试。", "Too many invitations. Wait and retry with the same model and limit.");
        case "M_TAVERN_FLUX_GATEWAY_UNAVAILABLE":
        default:
            return translated("未能确认邀请结果。请稍后使用同一模型和上限重试。", "Could not confirm the invitation. Retry later with the same model and limit.");
    }
}

type ModelOption = { id: string; label: string };
type ModelOptions = { options: ModelOption[]; selected_model_id: string; can_edit: boolean };

function currentClient(client: MatrixClient): boolean {
    return MatrixClientPeg.get() === client && isTavernClient(client);
}

/** 所有付费操作沿用当前 Matrix 会话；Flux 保持原请求，其他角色须显式指定。 */
function fluxRequest<T>(
    client: MatrixClient,
    role: PaidTavernRole,
    action: "invite" | "model-options" | "set-model",
    body: Record<string, string | number>,
    abortSignal: AbortSignal,
): Promise<T> {
    if (!currentClient(client)) return Promise.reject(new Error("flux_wrong_client"));
    return client.http.authedRequest<T>(Method.Post, `${ENDPOINT}/${action}`, undefined,
        role.id === "flux-the-cat" ? body : { ...body, role_id: role.id },
        { prefix: "", localTimeoutMs: 15_000, abortSignal });
}

function isModelOption(value: unknown): value is ModelOption {
    return typeof value === "object" && value !== null && "id" in value && "label" in value &&
        typeof value.id === "string" && value.id.length > 0 && typeof value.label === "string" && value.label.length > 0;
}

function isModelOptions(value: unknown): value is ModelOptions {
    return typeof value === "object" && value !== null && "options" in value && Array.isArray(value.options) &&
        value.options.every(isModelOption) && "selected_model_id" in value && typeof value.selected_model_id === "string" &&
        "can_edit" in value && typeof value.can_edit === "boolean";
}

/** 不沿用上一个角色、房间或账号的结果；禁用时不查询钱包模型。 */
function useFluxModelOptions(client: MatrixClient, role: PaidTavernRole, roomId: string, enabled: boolean, membership?: string): {
    data?: ModelOptions;
    error?: unknown;
    reload: () => void;
} {
    const [version, setVersion] = useState(0);
    const reload = useCallback(() => setVersion((value) => value + 1), []);
    const [result, setResult] = useState<{
        client: MatrixClient; role: PaidTavernRole; roomId: string; version: number; membership?: string; data?: ModelOptions; error?: unknown;
    }>();
    useEffect(() => {
        if (!enabled) return;
        const controller = new AbortController();
        void fluxRequest<unknown>(client, role, "model-options", { room_id: roomId }, controller.signal).then((data) => {
            if (!isModelOptions(data)) throw new Error("flux_invalid_model_options");
            if (!controller.signal.aborted && currentClient(client)) setResult({ client, role, roomId, version, membership, data });
        }).catch((error) => {
            if (!controller.signal.aborted && currentClient(client)) setResult({ client, role, roomId, version, membership, error });
        });
        return () => controller.abort();
    }, [client, enabled, membership, role, roomId, version]);

    const current = enabled && result?.client === client && result.role === role && result.roomId === roomId &&
        result.version === version && result.membership === membership
        ? result : undefined;
    return { data: current?.data, error: current?.error, reload };
}

function availableModel(data: ModelOptions | undefined, choice: string | null): string {
    // 没有可用默认或原选择已下架时留空，不自动改用第一个选项。
    const id = choice ?? data?.selected_model_id;
    return id && data?.options.some((option) => option.id === id) ? id : "";
}

function FluxModelPicker({ data, modelId, disabled, onChange }: {
    data: ModelOptions;
    modelId: string;
    disabled: boolean;
    onChange: (id: string) => void;
}): JSX.Element {
    if (!data.options.length) {
        return <p role="status">{translated("目前没有可用模型。", "No models are currently available.")}</p>;
    }
    return (
        <p>
            <label>
                {translated("模型", "Model")}{" "}
                <select value={modelId} disabled={disabled} style={{ maxWidth: "100%" }} onChange={(event) => onChange(event.target.value)}>
                    <option value="" disabled>{translated("请选择模型", "Choose a model")}</option>
                    {data.options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                </select>
            </label>
        </p>
    );
}

function FluxModelLoadStatus({ error, name, reload }: { error?: unknown; name: string; reload: () => void }): JSX.Element {
    if (errorCode(error) === "M_TAVERN_FLUX_WALLET_UNAVAILABLE") {
        return <p role="status">{inviteErrorMessage(error, name)}</p>;
    }
    return (
        <p role="status">
            {error
                ? translated("没能加载可用模型。", "Unable to load available models.")
                : translated("正在加载可用模型…", "Loading available models…")}
            {Boolean(error) && (
                <AccessibleButton kind="link" element="button" type="button" onClick={reload}>
                    {translated("重新加载", "Reload")}
                </AccessibleButton>
            )}
        </p>
    );
}

/** 复用当前 Matrix 账号的同源鉴权；不把访问凭据发给计费站。 */
export function TavernPaidRoleFunding({ client, role, roomId, eligible, disabled, requestIds, onBusyChange, onFinished }: {
    client: MatrixClient;
    role: PaidTavernRole;
    roomId: string;
    eligible: boolean;
    disabled: boolean;
    requestIds: Map<string, string>;
    onBusyChange: (busy: boolean) => void;
    onFinished: (mxid: string) => void;
}): JSX.Element {
    const inFlight = useRef<AbortController | undefined>(undefined);
    const [busy, setBusy] = useState(false);
    const [amount, setAmount] = useState("");
    const [choice, setChoice] = useState<string | null>(null);
    const [status, setStatus] = useState("");
    const { data: models, error: modelError, reload: reloadModels } = useFluxModelOptions(client, role, roomId, eligible);
    const modelId = availableModel(models, choice);
    const micros = parseFluxLimitMicros(amount);
    useEffect(() => () => inFlight.current?.abort(), []);

    const invite = async (): Promise<void> => {
        if (!eligible || disabled || !modelId || micros === null || inFlight.current || !currentClient(client)) return;
        const controller = new AbortController();
        inFlight.current = controller;
        setBusy(true);
        onBusyChange(true);
        setStatus("");
        try {
            // 同一弹窗内，仅同一角色、模型和金额的重试复用首次操作 ID。
            const requestKey = `${role.id}:${micros}:${modelId}`;
            let requestId = requestIds.get(requestKey);
            if (!requestId) {
                requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
                requestIds.set(requestKey, requestId);
            }
            const data = await fluxRequest<{ status?: string }>(
                client, role, "invite", { room_id: roomId, request_id: requestId, limit_usd_micros: micros, model_id: modelId },
                controller.signal,
            );
            if (controller.signal.aborted || !currentClient(client)) return;
            if (data.status !== "active") throw new Error("invalid_flux_invite_response");
            window.dispatchEvent(new CustomEvent(PAID_ROLE_INVITED, { detail: { roomId, roleId: role.id } }));
            onFinished(role.mxid);
        } catch (error) {
            if (!controller.signal.aborted && currentClient(client)) {
                setStatus(inviteErrorMessage(error, role.shortName));
                if (errorCode(error) === "M_TAVERN_FLUX_MODEL_UNAVAILABLE") {
                    setChoice(modelId);
                    reloadModels();
                }
            }
        } finally {
            inFlight.current = undefined;
            if (!controller.signal.aborted) {
                setBusy(false);
                onBusyChange(false);
            }
        }
    };

    if (!eligible) return <p role="status">{translated(`${role.shortName} 只能在有邀请权限的私密加密房间启用。`, `${role.shortName} is only available in private encrypted rooms you can invite to.`)}</p>;

    return (
        <section aria-label={role.name} aria-busy={busy}>
            <p>{translated(role.inviteNoticeZh, role.inviteNoticeEn)}</p>
            {models
                ? <FluxModelPicker data={models} modelId={modelId} disabled={busy || disabled}
                      onChange={(id) => { setChoice(id); setStatus(""); }} />
                : <FluxModelLoadStatus error={modelError} name={role.shortName} reload={reloadModels} />}
            <details>
                <summary>{translated("设置可选上限（美元）", "Set an optional USD limit")}</summary>
                <label>
                    {translated("本房间上限（USD）", "Room limit (USD)")}{" "}
                    <input type="text" inputMode="decimal" value={amount} maxLength={24} disabled={busy || disabled}
                        aria-invalid={micros === null} onChange={(event) => { setAmount(event.target.value); setStatus(""); }} />
                </label>
                {micros === null && <p role="status">{translated("请输入非负金额，最多保留 6 位小数。", "Enter a non-negative amount with up to 6 decimal places.")}</p>}
            </details>
            <AccessibleButton kind="primary" element="button" type="button" disabled={busy || disabled || !modelId || micros === null} onClick={invite}>
                {translated(`使用我的 New API 钱包额度邀请 ${role.shortName}`, `Invite ${role.shortName} using my New API wallet balance`)}
            </AccessibleButton>
            {status && <p role="status">{status}</p>}
            <p>
                <a href={role.originalCard} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
                    {translated("官方下载原卡（PNG）", "Download official original card (PNG)")}
                </a>
            </p>
        </section>
    );
}

export function TavernPaidRoleRoomModelButtons({ client, room }: { client: MatrixClient; room: Room }): JSX.Element {
    const roles = useTavernPaidRoles(currentClient(client) ? client : null);
    return <>{roles.map((role) =>
        <TavernPaidRoleRoomModelButton key={role.id} client={client} room={room} role={role} />)}</>;
}

/** 房间标题中的入口只在服务端确认原付款人可修改后显示。 */
function TavernPaidRoleRoomModelButton({ client, room, role }: {
    client: MatrixClient;
    room: Room;
    role: PaidTavernRole;
}): JSX.Element | null {
    const membership = room.getMember(role.mxid)?.membership;
    const eligible = currentClient(client) && !room.isSpaceRoom() && room.getMyMembership() === KnownMembership.Join &&
        room.getJoinRule() === JoinRule.Invite && room.hasEncryptionStateEvent() &&
        (membership === KnownMembership.Invite || membership === KnownMembership.Join);
    const { data, reload } = useFluxModelOptions(client, role, room.roomId, eligible, membership);
    useEffect(() => {
        if (!eligible) return;
        // 钱包激活可能晚于成员事件；成功回执只刷新当前房间的对应角色。
        const invited = (event: Event): void => {
            if (event instanceof CustomEvent && event.detail?.roomId === room.roomId && event.detail?.roleId === role.id) reload();
        };
        window.addEventListener(PAID_ROLE_INVITED, invited);
        return () => window.removeEventListener(PAID_ROLE_INVITED, invited);
    }, [eligible, reload, role.id, room.roomId]);
    if (!eligible || !data?.can_edit) return null;

    const open = (): void => {
        // 每次打开重新读取当前房的选择；关闭后再确认入口权限，覆盖并发撤销/过期。
        const dialog = Modal.createDialog(TavernPaidRoleRoomModelDialog, { client, role, roomId: room.roomId });
        void dialog.finished.then(reload);
    };
    return (
        <AccessibleButton kind="secondary" element="button" type="button" onClick={open}>
            {translated(`${role.shortName} 模型`, `${role.shortName} model`)}
        </AccessibleButton>
    );
}

function TavernPaidRoleRoomModelDialog({ client, role, roomId, onFinished }: {
    client: MatrixClient;
    role: PaidTavernRole;
    roomId: string;
    onFinished: () => void;
}): JSX.Element {
    const { data, error, reload } = useFluxModelOptions(client, role, roomId, true);
    const [choice, setChoice] = useState<string | null>(null);
    const [status, setStatus] = useState("");
    const [busy, setBusy] = useState(false);
    const inFlight = useRef<AbortController | undefined>(undefined);
    useEffect(() => () => inFlight.current?.abort(), []);

    const modelId = availableModel(data, choice);
    const canSave = Boolean(data?.can_edit && data.selected_model_id && modelId && modelId !== data.selected_model_id);
    const save = async (): Promise<void> => {
        if (!data || !canSave || inFlight.current || !currentClient(client)) return;
        const controller = new AbortController();
        inFlight.current = controller;
        setBusy(true);
        setStatus("");
        try {
            // 传入刚看到的模型；另一个窗口已改时不得覆盖，须先刷新再另选。
            const result = await fluxRequest<{ model_id?: string }>(client, role, "set-model", {
                room_id: roomId, model_id: modelId, expected_model_id: data.selected_model_id,
            }, controller.signal);
            if (controller.signal.aborted || !currentClient(client)) return;
            if (result.model_id !== modelId) throw new Error("flux_invalid_room_model");
            onFinished();
        } catch (issue) {
            if (controller.signal.aborted || !currentClient(client)) return;
            switch (errorCode(issue)) {
                case "M_TAVERN_FLUX_MODEL_CHANGED":
                case "M_TAVERN_FLUX_STATE_CHANGED":
                    setChoice(null);
                    reload();
                    setStatus(translated("房间的模型或授权已变化。请查看当前状态再选择。", "The room’s model or authorization changed. Review the current settings before choosing."));
                    break;
                case "M_TAVERN_FLUX_MODEL_UNAVAILABLE":
                    reload();
                    setStatus(translated("所选模型暂不可用。请选择其他模型。", "This model is not available right now. Choose another model."));
                    break;
                case "M_TAVERN_INVITE_FORBIDDEN":
                case "M_TAVERN_FLUX_FORBIDDEN":
                case "M_TAVERN_FLUX_WALLET_UNAVAILABLE":
                    setChoice(null);
                    reload();
                    setStatus(translated(`你目前无法更改此房间的 ${role.shortName} 模型。`, `You cannot change ${role.shortName}’s model in this room right now.`));
                    break;
                default:
                    setStatus(translated("未能确认保存结果。请关闭后重新打开查看。", "Could not confirm the update. Reopen this dialog to check it."));
            }
        } finally {
            inFlight.current = undefined;
            if (!controller.signal.aborted) setBusy(false);
        }
    };

    const denied = data ? !data.can_edit : errorCode(error) === "M_TAVERN_FLUX_FORBIDDEN" ||
        errorCode(error) === "M_TAVERN_INVITE_FORBIDDEN" || errorCode(error) === "M_TAVERN_FLUX_WALLET_UNAVAILABLE";
    const savedUnavailable = data?.selected_model_id && !data.options.some(({ id }) => id === data.selected_model_id);
    return (
        <BaseDialog title={translated(`${role.shortName} 模型`, `${role.shortName} model`)} onFinished={onFinished}>
            {denied
                ? <p role="status">{translated(`只有此房间当前的付款人可以更改 ${role.shortName} 模型。`, `Only this room’s current payer can change ${role.shortName}’s model.`)}</p>
                : data
                    ? <>
                        <p>{translated("新请求使用新模型，进行中的请求按原模型结算。本房间的费用上限和付款人不变。",
                                       "New requests use the new model. In-progress requests use the original model. The room’s limit and payer stay the same.")}</p>
                        {savedUnavailable && <p>{translated("当前模型暂不可用：", "The current model is unavailable: ")}{data.selected_model_id}</p>}
                        <FluxModelPicker data={data} modelId={modelId} disabled={busy} onChange={(id) => { setChoice(id); setStatus(""); }} />
                        <AccessibleButton kind="primary" element="button" type="button" disabled={busy || !canSave} onClick={save}>
                            {translated("保存模型", "Save model")}
                        </AccessibleButton>
                    </>
                    : <FluxModelLoadStatus error={error} name={role.shortName} reload={reload} />}
            {status && <p role="status">{status}</p>}
        </BaseDialog>
    );
}
