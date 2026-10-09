import { GM_API_BASE_URL } from '$lib/constants';

export type GameMasterIntensity = 'light' | 'firm' | 'ruthless';

export type GameMasterConfig = {
	agenda: string;
	intensity: GameMasterIntensity;
	player_character_id: string;
	/** Run a turn pass after every Nth reply. */
	cadence: 1 | 2 | 3;
	auto_portraits: boolean;
};

export type GameMasterChange = { text: string; spoiler: boolean };

export type GameMasterPortrait = {
	npc_id: string;
	name: string;
	look: string;
	status: 'queued' | 'running' | 'ready' | 'failed';
	file_id: string;
	seed: string;
	error: string;
	/** The image prompt it was drawn from, written for this subject. */
	prompt: string;
	/** uploaded: the player's own picture, which automatic portraits never replace. */
	source: 'generated' | 'uploaded';
	updated_at: number;
};

export type GameMasterTalk = { id: string; player: string; gm: string; created_at: number };

export type GameMasterIndexEntry = {
	id: string;
	message_id: string;
	created_at: number;
	error: string;
	has_state: boolean;
	kind: string;
};

export type SceneNpc = {
	npc_id: string;
	name: string;
	card: string;
	look: string;
	file_id: string;
};

export type GameMasterEntry = {
	id: string;
	message_id: string;
	kind: 'setup' | 'turn' | 'consult' | 'reroll' | 'table_talk' | 'talk_plan';
	prior_id: string;
	user_message: string;
	gm_reply: string;
	reasoning: string;
	model_reasoning: string;
	observations: string[];
	patch: { update?: any; remove?: any; changes?: GameMasterChange[] };
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
	portraits: GameMasterPortrait[];
	talk: GameMasterTalk[];
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

export const getGameMasterIndex = async (
	token: string,
	chatId: string
): Promise<{ enabled: boolean; entries: GameMasterIndexEntry[] }> =>
	await gmRequest(token, `${chatPath(chatId)}/index`);

export const talkToGameMaster = async (
	token: string,
	chatId: string,
	message: string
): Promise<{ reply: string; status: GameMasterStatus }> =>
	await gmRequest(token, `${chatPath(chatId)}/talk`, {
		method: 'POST',
		body: JSON.stringify({ message })
	});

export const rerollGameMaster = async (token: string, chatId: string): Promise<GameMasterStatus> =>
	await gmRequest(token, `${chatPath(chatId)}/reroll`, { method: 'POST', body: '{}' });

export const regenerateNpcPortrait = async (
	token: string,
	chatId: string,
	npcId: string,
	direction = ''
): Promise<GameMasterStatus> =>
	await gmRequest(token, `${chatPath(chatId)}/npcs/${encodeURIComponent(npcId)}/portrait`, {
		method: 'POST',
		body: JSON.stringify({ direction })
	});

/** Use an already-uploaded image file as an NPC's portrait in this chat, for good. */
export const uploadNpcPortrait = async (
	token: string,
	chatId: string,
	npcId: string,
	fileId: string
): Promise<GameMasterStatus> =>
	await gmRequest(token, `${chatPath(chatId)}/npcs/${encodeURIComponent(npcId)}/portrait/upload`, {
		method: 'POST',
		body: JSON.stringify({ file_id: fileId })
	});

export const getSceneNpcs = async (
	token: string,
	chatId: string,
	messageId: string
): Promise<SceneNpc[]> =>
	(await gmRequest(
		token,
		`${chatPath(chatId)}/scene-npcs?message_id=${encodeURIComponent(messageId)}`
	)) ?? [];

/**
 * The GM pass whose direction shaped the reply to `messageId`'s parent chain: the
 * newest successful entry on the nearest ancestor that has one. Mirrors the server's
 * find_state_entry_id, so the marker points at the direction that was really in force.
 */
export const findSteeringEntry = (
	entries: GameMasterIndexEntry[],
	messages: Record<string, any>,
	startId: string | null | undefined
): GameMasterIndexEntry | null => {
	const byMessage = new Map<string, GameMasterIndexEntry>();
	for (const entry of entries) {
		if (!entry.has_state) continue;
		const current = byMessage.get(entry.message_id);
		if (!current || entry.created_at >= current.created_at) byMessage.set(entry.message_id, entry);
	}
	const seen = new Set<string>();
	let id = startId ?? null;
	while (id && !seen.has(id)) {
		seen.add(id);
		const found = byMessage.get(id);
		if (found) return found;
		id = messages?.[id]?.parentId ?? null;
	}
	return byMessage.get('') ?? null;
};

/** Have the GM open an empty chat: plans session zero and returns the opening scene
 * card to post as the first message. Waits for the plan. */
export const openGameMasterStory = async (
	token: string,
	chatId: string,
	model?: string
): Promise<{ scene: string; status: GameMasterStatus }> =>
	await gmRequest(token, `${chatPath(chatId)}/opening`, {
		method: 'POST',
		body: JSON.stringify({ model: model || null })
	});
