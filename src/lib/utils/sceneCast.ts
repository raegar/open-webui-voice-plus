/** Attached character profiles own their name, appearance and references. */
export const excludeAttachedNpcs = <T extends { name?: string }>(
	npcs: T[],
	characters: { name?: string; kind?: string }[]
): T[] => {
	const nameKey = (name?: string) => (name ?? '').trim().toLowerCase();
	const seen = new Set(
		characters
			.filter((character) => (character.kind ?? 'character') === 'character')
			.map((character) => nameKey(character.name))
	);
	return npcs.filter((npc) => {
		const name = nameKey(npc.name);
		if (!name || seen.has(name)) return false;
		seen.add(name);
		return true;
	});
};
