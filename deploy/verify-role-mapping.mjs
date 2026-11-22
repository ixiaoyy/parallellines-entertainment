// 发布前核对 Synapse 公开角色目录、New API 私有凭据映射与 Bot 列表的三边 role_id 一致性。
// 严格守卫：公开目录禁止出现任何密钥字段；校验输出绝不打印任何凭据或令牌原文。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const HOMESERVER = 'matrix.pingxingxian.space';
export const ROLE_ID_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;
export const FORBIDDEN_PUBLIC_KEYS = new Set([
	'bearer',
	'bearer_token',
	'token',
	'secret',
	'api_key',
	'key',
	'password',
	'credential'
]);

const REQUIRED_PUBLIC_FIELDS = [
	'id',
	'mxid',
	'name',
	'shortName',
	'originalCard',
	'profileDescription',
	'inviteNoticeZh',
	'inviteNoticeEn'
];

export function validateSynapsePublicCatalog(payload) {
	if (!payload || typeof payload !== 'object' || !Array.isArray(payload.roles) || payload.roles.length === 0) {
		throw new Error('synapse_catalog_empty_or_invalid');
	}
	const roleIds = new Set();
	const mxids = new Set();

	for (const item of payload.roles) {
		if (!item || typeof item !== 'object') throw new Error('synapse_role_entry_invalid');
		for (const key of Object.keys(item)) {
			if (FORBIDDEN_PUBLIC_KEYS.has(key.toLowerCase())) {
				throw new Error(`secret_field_forbidden_in_public_catalog:${key}`);
			}
		}
		for (const field of REQUIRED_PUBLIC_FIELDS) {
			if (typeof item[field] !== 'string' || !item[field].trim()) {
				throw new Error(`synapse_role_missing_field:${field}`);
			}
		}
		if (!ROLE_ID_RE.test(item.id)) throw new Error(`synapse_role_invalid_id:${item.id}`);
		if (roleIds.has(item.id)) throw new Error(`synapse_role_duplicate_id:${item.id}`);
		if (!item.mxid.startsWith('@bot_') || !item.mxid.endsWith(`:${HOMESERVER}`)) {
			throw new Error(`synapse_role_invalid_mxid:${item.id}`);
		}
		if (mxids.has(item.mxid)) throw new Error(`synapse_role_duplicate_mxid:${item.id}`);
		if (!item.originalCard.startsWith('https://')) {
			throw new Error(`synapse_role_invalid_card_url:${item.id}`);
		}
		roleIds.add(item.id);
		mxids.add(item.mxid);
	}
	return [...roleIds].sort();
}

export function validateNewApiRoleCredentials(payload) {
	if (!payload || typeof payload !== 'object' || !Array.isArray(payload.roles) || payload.roles.length === 0) {
		throw new Error('newapi_credentials_empty_or_invalid');
	}
	const enabledRoleIds = new Set();
	const allRoleIds = new Set();
	const seenTokens = new Set();

	for (const item of payload.roles) {
		if (!item || typeof item !== 'object') throw new Error('newapi_role_entry_invalid');
		if (typeof item.role_id !== 'string' || !ROLE_ID_RE.test(item.role_id)) {
			throw new Error(`newapi_role_invalid_id:${String(item.role_id)}`);
		}
		if (allRoleIds.has(item.role_id)) {
			throw new Error(`newapi_role_duplicate_id:${item.role_id}`);
		}
		allRoleIds.add(item.role_id);

		const enabled = item.enabled !== false;
		if (enabled) {
			if (typeof item.bearer_token !== 'string' || item.bearer_token.trim().length < 32) {
				throw new Error(`newapi_role_invalid_token_length:${item.role_id}`);
			}
			if (seenTokens.has(item.bearer_token)) {
				throw new Error(`newapi_role_duplicate_token:${item.role_id}`);
			}
			seenTokens.add(item.bearer_token);
			enabledRoleIds.add(item.role_id);
		}
	}
	return [...enabledRoleIds].sort();
}

export function verifyThreeWayRoleMapping({ synapseCatalog, newApiCredentials, botRoleIds = null }) {
	const synapseIds = validateSynapsePublicCatalog(synapseCatalog);
	const newApiIds = validateNewApiRoleCredentials(newApiCredentials);

	const newApiSet = new Set(newApiIds);
	const synapseSet = new Set(synapseIds);

	const missingInNewApi = synapseIds.filter((id) => !newApiSet.has(id));
	if (missingInNewApi.length > 0) {
		throw new Error(`roles_missing_in_newapi:${missingInNewApi.join(',')}`);
	}

	const missingInSynapse = newApiIds.filter((id) => !synapseSet.has(id));
	if (missingInSynapse.length > 0) {
		throw new Error(`roles_missing_in_synapse:${missingInSynapse.join(',')}`);
	}

	if (Array.isArray(botRoleIds)) {
		const normalizedBots = [...new Set(botRoleIds.map((s) => s.trim()).filter(Boolean))].sort();
		for (const id of normalizedBots) {
			if (!ROLE_ID_RE.test(id)) throw new Error(`bot_role_invalid_id:${id}`);
		}
		if (JSON.stringify(normalizedBots) !== JSON.stringify(synapseIds)) {
			throw new Error(`bot_roles_mismatch:expected=${synapseIds.join(',')}:got=${normalizedBots.join(',')}`);
		}
	}

	// 只输出脱敏汇总，严禁输出任何凭据原文。
	return {
		ok: true,
		role_ids: synapseIds,
		count: synapseIds.length
	};
}

function parseArgs(argv) {
	let synapsePath = null;
	let newApiPath = null;
	let botRoleIds = null;
	for (let i = 0; i < argv.length; i += 1) {
		const arg = argv[i];
		if (arg === '--synapse-roles') synapsePath = argv[++i];
		else if (arg === '--newapi-roles') newApiPath = argv[++i];
		else if (arg === '--bot-roles') botRoleIds = (argv[++i] || '').split(',');
		else throw new Error(`unknown_arg:${arg}`);
	}
	if (!synapsePath || !newApiPath) {
		throw new Error('usage: node deploy/verify-role-mapping.mjs --synapse-roles <public.json> --newapi-roles <private.json> [--bot-roles a,b]');
	}
	return { synapsePath, newApiPath, botRoleIds };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	const { synapsePath, newApiPath, botRoleIds } = parseArgs(process.argv.slice(2));
	const synapseCatalog = JSON.parse(fs.readFileSync(path.resolve(synapsePath), 'utf8'));
	const newApiCredentials = JSON.parse(fs.readFileSync(path.resolve(newApiPath), 'utf8'));
	const summary = verifyThreeWayRoleMapping({ synapseCatalog, newApiCredentials, botRoleIds });
	console.log(JSON.stringify(summary));
}
