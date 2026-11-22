import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	validateSynapsePublicCatalog,
	validateNewApiRoleCredentials,
	verifyThreeWayRoleMapping
} from './verify-role-mapping.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXAMPLE_CATALOG = JSON.parse(
	fs.readFileSync(path.join(REPO_ROOT, 'config', 'paid-roles.example.json'), 'utf8')
);

test('validates real config/paid-roles.example.json cleanly', () => {
	const ids = validateSynapsePublicCatalog(EXAMPLE_CATALOG);
	assert.deepEqual(ids, ['flux-the-cat', 'sakana']);
});

test('rejects any secret-like field placed in public Synapse catalog', () => {
	const poisoned = {
		roles: [
			{
				...EXAMPLE_CATALOG.roles[0],
				bearer_token: 'super-secret-token-should-never-be-here'
			}
		]
	};
	assert.throws(() => validateSynapsePublicCatalog(poisoned), /secret_field_forbidden_in_public_catalog/);
});

test('rejects duplicate or too-short bearer tokens in New API private credentials', () => {
	const token = 'a'.repeat(40);
	assert.throws(
		() =>
			validateNewApiRoleCredentials({
				roles: [
					{ role_id: 'flux-the-cat', bearer_token: token, enabled: true },
					{ role_id: 'sakana', bearer_token: token, enabled: true }
				]
			}),
		/newapi_role_duplicate_token/
	);

	assert.throws(
		() =>
			validateNewApiRoleCredentials({
				roles: [{ role_id: 'flux-the-cat', bearer_token: 'too-short', enabled: true }]
			}),
		/newapi_role_invalid_token_length/
	);
});

test('verifies three-way role_id mapping across Synapse, New API, and Bots without leaking tokens', () => {
	const newApiCredentials = {
		roles: [
			{ role_id: 'flux-the-cat', bearer_token: 'f'.repeat(48), enabled: true },
			{ role_id: 'sakana', bearer_token: 's'.repeat(48), enabled: true }
		]
	};

	const summary = verifyThreeWayRoleMapping({
		synapseCatalog: EXAMPLE_CATALOG,
		newApiCredentials,
		botRoleIds: ['sakana', 'flux-the-cat']
	});

	assert.equal(summary.ok, true);
	assert.deepEqual(summary.role_ids, ['flux-the-cat', 'sakana']);
	assert.equal(JSON.stringify(summary).includes('ffff'), false);
	assert.equal(JSON.stringify(summary).includes('ssss'), false);
});

test('fails loudly when Synapse and New API role_id sets diverge', () => {
	assert.throws(
		() =>
			verifyThreeWayRoleMapping({
				synapseCatalog: EXAMPLE_CATALOG,
				newApiCredentials: {
					roles: [{ role_id: 'flux-the-cat', bearer_token: 'f'.repeat(48), enabled: true }]
				}
			}),
		/roles_missing_in_newapi:sakana/
	);
});
