import { MEMORY_BROWSER_API_BASE_URL } from '$lib/constants';

export type MemorySummary = {
	path: string;
	total: number;
	recorded: number;
	imported: number;
	first: string | null;
	last: string | null;
	/** Characters memories are linked to, most memories first. */
	characters: { name: string; count: number; inferred: number }[];
	/** Memories not yet linked to characters. */
	unindexed: number;
	/** How many memories this request linked. */
	linked?: number;
};

/** chat: the memory matched a message in a chat with this character. named: an
 * imported conversation that names them often enough (inferred). */
export type MemoryCharacter = { name: string; how: 'chat' | 'named' };

export type Memory = {
	message_id: string;
	conversation_id: string;
	timestamp: string;
	role: 'user' | 'assistant' | string;
	content: string;
	source: 'recorded' | 'imported';
	characters: MemoryCharacter[];
};

export type MemoryFilters = {
	q?: string;
	role?: '' | 'user' | 'assistant';
	source?: '' | 'recorded' | 'imported';
	since?: string;
	until?: string;
	conversation_id?: string;
	include?: string[];
	exclude?: string[];
	order?: 'newest' | 'oldest';
	offset?: number;
	limit?: number;
};

const request = async (token: string, path: string, init: RequestInit = {}) => {
	let error: string | null = null;
	const res = await fetch(`${MEMORY_BROWSER_API_BASE_URL}${path}`, {
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		...init
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			console.error(err);
			error = typeof err?.detail === 'string' ? err.detail : `${err?.detail ?? err}`;
			return null;
		});
	if (error) throw error;
	return res;
};

export const getMemorySummary = async (token: string): Promise<MemorySummary> =>
	await request(token, '/summary');

export const searchMemories = async (
	token: string,
	filters: MemoryFilters
): Promise<{ total: number; items: Memory[] }> => {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(filters)) {
		if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
		else if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
	}
	return await request(token, `/messages?${params.toString()}`);
};

export const deleteMemories = async (
	token: string,
	ids: string[]
): Promise<{ deleted: number; conversations_removed: number; backup: string | null }> =>
	await request(token, '/delete', { method: 'POST', body: JSON.stringify({ ids }) });

export const relinkMemoryCharacters = async (token: string): Promise<MemorySummary> =>
	await request(token, '/characters/relink', { method: 'POST', body: '{}' });

/** Delete every memory in a date range that matches the other filters. expected is
 * the count shown when confirming; the server refuses if the matches have changed. */
export const deleteMemoryRange = async (
	token: string,
	filters: Omit<MemoryFilters, 'order' | 'offset' | 'limit'>,
	expected: number
): Promise<{ deleted: number; conversations_removed: number; backup: string | null }> =>
	await request(token, '/delete-range', {
		method: 'POST',
		body: JSON.stringify({ ...filters, expected })
	});
