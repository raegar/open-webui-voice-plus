import { MEMORY_BROWSER_API_BASE_URL } from '$lib/constants';

export type MemorySummary = {
	path: string;
	total: number;
	recorded: number;
	imported: number;
	first: string | null;
	last: string | null;
};

export type Memory = {
	message_id: string;
	conversation_id: string;
	timestamp: string;
	role: 'user' | 'assistant' | string;
	content: string;
	source: 'recorded' | 'imported';
};

export type MemoryFilters = {
	q?: string;
	role?: '' | 'user' | 'assistant';
	source?: '' | 'recorded' | 'imported';
	since?: string;
	until?: string;
	conversation_id?: string;
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
		if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
	}
	return await request(token, `/messages?${params.toString()}`);
};

export const deleteMemories = async (
	token: string,
	ids: string[]
): Promise<{ deleted: number; conversations_removed: number; backup: string | null }> =>
	await request(token, '/delete', { method: 'POST', body: JSON.stringify({ ids }) });
