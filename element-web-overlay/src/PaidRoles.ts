export type PaidTavernRole = Readonly<{
    id: "flux-the-cat" | "sakana";
    mxid: string;
    name: string;
    shortName: string;
    originalCard: string;
    profileDescription: string;
}>;

/** 两个已接通独立付费授权的固定身份；邀请和资料侧栏使用同一份登记。 */
export const TAVERN_PAID_ROLES: readonly PaidTavernRole[] = [
    {
        id: "flux-the-cat",
        mxid: "@bot_flux-the-cat:matrix.pingxingxian.space",
        name: "Flux the Cat",
        shortName: "Flux",
        originalCard: "https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/c4c1d8e8361afe13cbfded374f65fb65e87fcbc9/assets/character/default_FluxTheCat.png",
        profileDescription: "不会说人话的猫，只会喵叫、打呼噜，用动作表达。",
    },
    {
        id: "sakana",
        mxid: "@bot_sakana:matrix.pingxingxian.space",
        name: "Sakana",
        shortName: "Sakana",
        originalCard: "https://raw.githubusercontent.com/SillyTavern/SillyTavern-Content/e3ca170989689c6eae8b5cba8b6c49390bdbebbf/assets/character/contest_Assistant_Sakana.png",
        profileDescription: "爱吐槽的办公助理，默认用中文，被 @ 时才回复。",
    },
];
