import { describe, expect, it } from 'vitest';

import { excludeAttachedNpcs } from './sceneCast';

describe('scene cast identity', () => {
	it('uses the attached person instead of their GM duplicate', () => {
		const characters = [{ name: 'Nell', kind: 'character', imageFileIds: ['library-nell'] }];
		const npcs = [
			{ name: ' nell ', file_id: 'gm-nell' },
			{ name: 'Marek', file_id: 'gm-marek' }
		];
		expect(excludeAttachedNpcs(npcs, characters)).toEqual([npcs[1]]);
		expect(characters[0].imageFileIds).toEqual(['library-nell']);
		expect(npcs).toHaveLength(2);
	});

	it('does not confuse a location or outfit with a person of the same name', () => {
		const npcs = [{ name: 'Nell' }];
		expect(
			excludeAttachedNpcs(npcs, [
				{ name: 'Nell', kind: 'location' },
				{ name: 'Nell', kind: 'outfit' }
			])
		).toEqual(npcs);
	});

	it('keeps distinct whole names and the order of the remaining NPCs', () => {
		const npcs = [{ name: 'Alex (2007)' }, { name: 'Alex' }, { name: 'Alexandra' }];
		expect(excludeAttachedNpcs(npcs, [{ name: 'Alex' }])).toEqual([npcs[0], npcs[2]]);
	});

	it('keeps only the first NPC copy of a person', () => {
		const npcs = [
			{ name: 'Nell', id: 'n1' },
			{ name: ' NELL ', id: 'n2' }
		];
		expect(excludeAttachedNpcs(npcs, [])).toEqual([npcs[0]]);
	});

	it('recognises an attached profile even without pictures and restores the NPC after detaching', () => {
		const npcs = [{ name: 'Nell' }];
		expect(excludeAttachedNpcs(npcs, [{ name: 'Nell' }])).toEqual([]);
		expect(excludeAttachedNpcs(npcs, [])).toEqual(npcs);
	});
});
