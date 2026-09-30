import { GM_API_BASE_URL } from '$lib/constants';

export type GameMasterIntensity = 'light' | 'firm' | 'ruthless';

export type GameMasterConfig = {
	agenda: string;
	intensity: GameMasterIntensity;
	player_character_id: string;
};

export type GameMasterEntry = {
	id: string;
	message_id: string;
	kind: 'setup' | 'turn' | 'consult';
	reasoning: string;
	model_reasoning: string;
	observations: string[];
	patch: Record<string, any>;
	state: Record<string, any> | null;
	note: string;
	model: string;
	tokens: number;
	duration_ms: number;
	error: string;
	created_at: number;
};

export type GameMasterStatus = {
	enabled: boolean;
	config: GameMasterConfig;
	running: boolean;
	/** The pass whose direction applies to the next reply on this branch. */
	current: GameMasterEntry | null;
	/** A failed pass newer than `current`, if any. */
	last_error: { error: string; created_at: number } | null;
	entries: number;
};

const gmRequest = async (token: string, path: string, init: RequestInit = {}) => {
	let error: string | null = null;
	const res = await fetch(`${GM_API_BASE_URL}${path}`, {
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
			error = err?.detail ?? `${err}`;
			return null;
		});
	if (error) throw error;
	return res;
};

const chatPath = (chatId: string) => `/chats/${encodeURIComponent(chatId)}`;

export const getGameMaster = async (token: string, chatId: string): Promise<GameMasterStatus> =>
	await gmRequest(token, chatPath(chatId));

export const updateGameMaster = async (
	token: string,
	chatId: string,
	changes: Partial<GameMasterConfig> & { enabled?: boolean }
): Promise<GameMasterStatus> =>
	await gmRequest(token, chatPath(chatId), { method: 'POST', body: JSON.stringify(changes) });

export const consultGameMaster = async (token: string, chatId: string): Promise<GameMasterStatus> =>
	await gmRequest(token, `${chatPath(chatId)}/consult`, { method: 'POST', body: '{}' });

export const getGameMasterJournal = async (
	token: string,
	chatId: string,
	limit = 100
): Promise<GameMasterEntry[]> =>
	(await gmRequest(token, `${chatPath(chatId)}/journal?limit=${limit}`)) ?? [];
