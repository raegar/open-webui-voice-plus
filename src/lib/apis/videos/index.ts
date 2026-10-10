import { VIDEOS_API_BASE_URL } from '$lib/constants';

export type VideoGenerationOptions = {
	mode?: 'text' | 'reference';
	aspect_ratio: '16:9' | '9:16' | '1:1';
	megapixels: 0.2 | 0.4;
	duration: 3 | 5 | 10 | 15;
	seed?: number | string;
	// Filming style id the brief was drafted with; recorded on the video.
	style?: string;
	// Whose eyes a point-of-view style films from.
	pov_subject?: string;
	first_frame_data_url?: string;
	last_frame_data_url?: string;
	reference_image_data_urls?: string[];
	// Ordered like <Picture N>. A file id reuses a picture the server already holds,
	// so only new or swapped pictures are uploaded.
	reference_images?: Array<{ file_id: string } | { data_url: string }>;
	reference_audio_data_urls?: string[];
	reference_audio_file_ids?: string[];
	motion_lora?: boolean;
	motion_lora_variant?: 'hmmotion' | 'm3_unlocked';
	// Look LoRA in its own slot; combines with motion and turbo.
	style_lora?: 'flat_anime' | 'astro_realism';
	turbo_lora?: boolean;
};

export type VideoGenerationJob = {
	job_id: string;
	status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
	created_at: number;
	updated_at: number;
	result: Array<Record<string, any>> | null;
	error: string | null;
	// Generations in front of this one. 0 is running or next up, null once finished.
	position?: number | null;
	// When it left the queue and began rendering, so elapsed time excludes the wait.
	started_at?: number | null;
	// Opening of the brief, so one waiting item can be told from another.
	label?: string | null;
};

const videoApiError = (err: any) =>
	Array.isArray(err?.detail)
		? err.detail.map((item: { msg?: string }) => item.msg ?? JSON.stringify(item)).join(', ')
		: (err?.detail ?? 'Server connection failed');

export const getVideoHistory = async (token: string, limit: number = 50, chatId?: string) => {
	let error: string | null = null;
	const searchParams = new URLSearchParams({ limit: String(limit) });
	if (chatId) searchParams.set('chat_id', chatId);
	const res = await fetch(`${VIDEOS_API_BASE_URL}/history?${searchParams.toString()}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? 'Server connection failed';
			return [];
		});

	if (error) throw error;
	return res;
};

export const startVideoGenerationJob = async (
	token: string,
	jobId: string,
	prompt: string,
	options: VideoGenerationOptions,
	chatId?: string,
	messageId?: string
): Promise<VideoGenerationJob> => {
	let error: string | null = null;
	const res = await fetch(`${VIDEOS_API_BASE_URL}/generations/jobs`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			job_id: jobId,
			prompt,
			...options,
			...(chatId ? { chat_id: chatId } : {}),
			...(messageId ? { message_id: messageId } : {})
		})
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			console.error(err);
			error = videoApiError(err);
			return null;
		});
	if (error || !res) throw error ?? 'Server connection failed';
	return res;
};

export const getVideoGenerationJob = async (
	token: string,
	jobId: string
): Promise<VideoGenerationJob> => {
	let error: string | null = null;
	const res = await fetch(`${VIDEOS_API_BASE_URL}/generations/jobs/${jobId}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			console.error(err);
			error = videoApiError(err);
			return null;
		});
	if (error || !res) throw error ?? 'Server connection failed';
	return res;
};

export const listVideoGenerationJobs = async (token: string): Promise<VideoGenerationJob[]> => {
	let error: string | null = null;
	const res = await fetch(`${VIDEOS_API_BASE_URL}/generations/jobs`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			console.error(err);
			error = videoApiError(err);
			return null;
		});
	if (error || !res) throw error ?? 'Server connection failed';
	return res;
};

export const cancelVideoGenerationJob = async (
	token: string,
	jobId: string
): Promise<VideoGenerationJob> => {
	let error: string | null = null;
	const res = await fetch(`${VIDEOS_API_BASE_URL}/generations/jobs/${jobId}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
		})
		.catch((err) => {
			console.error(err);
			error = videoApiError(err);
			return null;
		});
	if (error || !res) throw error ?? 'Server connection failed';
	return res;
};

export const videoGenerations = async (
	token: string,
	prompt: string,
	options: VideoGenerationOptions,
	chatId?: string,
	messageId?: string
) => {
	const jobId = crypto.randomUUID();
	try {
		await startVideoGenerationJob(token, jobId, prompt, options, chatId, messageId);
	} catch (error) {
		if (!String(error).includes('Server connection failed')) throw error;
		// The request may have reached the server before the browser connection dropped.
		// Retrying is safe because job IDs are idempotent.
		await new Promise((resolve) => setTimeout(resolve, 1500));
		await startVideoGenerationJob(token, jobId, prompt, options, chatId, messageId).catch(
			(retryError) => {
				if (!String(retryError).includes('Server connection failed')) throw retryError;
			}
		);
	}
	while (true) {
		try {
			const job = await getVideoGenerationJob(token, jobId);
			if (job.status === 'completed') return job.result;
			if (job.status === 'failed') throw job.error || 'Video generation failed';
		} catch (error) {
			if (!String(error).includes('Server connection failed')) throw error;
		}
		await new Promise((resolve) => setTimeout(resolve, 3000));
	}
};

export type ReferenceKind = 'character' | 'location' | 'outfit';

export type VideoCharacter = {
	id: string;
	name: string;
	description: string;
	image_file_ids: string[];
	voice_file_id: string;
	kind: ReferenceKind;
	applies_to_id: string;
	/** Hidden while work mode (the hidePrivate setting) is on. */
	private: boolean;
	/** Out of the library and pickers; still works where already attached. */
	archived: boolean;
	/** Unix seconds. */
	created_at: number;
	updated_at: number;
	/** Per-chat state of dress; only present from getChatVideoCharacters. */
	state?: string;
	/** Library outfit worn in this chat; only present from getChatVideoCharacters. */
	outfit?: VideoOutfit | null;
};

export type VideoOutfit = {
	id: string;
	name: string;
	description: string;
	image_file_ids: string[];
};

const videoCharacterRequest = async (token: string, path: string, init: RequestInit = {}) => {
	let error: string | null = null;
	const res = await fetch(`${VIDEOS_API_BASE_URL}${path}`, {
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
			error = videoApiError(err);
			return null;
		});
	if (error) throw error;
	return res;
};

/** The caller's whole character library. */
export const getVideoCharacterLibrary = async (token: string): Promise<VideoCharacter[]> =>
	(await videoCharacterRequest(token, '/characters')) ?? [];

/** Only the characters attached to one chat, in attachment order. */
export const getChatVideoCharacters = async (
	token: string,
	chatId: string
): Promise<VideoCharacter[]> =>
	(await videoCharacterRequest(token, `/characters/chat/${encodeURIComponent(chatId)}`)) ?? [];

export const createVideoCharacter = async (
	token: string,
	character: {
		name: string;
		description: string;
		image_file_ids: string[];
		voice_file_id?: string;
		kind?: ReferenceKind;
		applies_to_id?: string;
		private?: boolean;
	}
): Promise<VideoCharacter> =>
	await videoCharacterRequest(token, '/characters', {
		method: 'POST',
		body: JSON.stringify(character)
	});

export const updateVideoCharacter = async (
	token: string,
	id: string,
	patch: {
		name?: string;
		description?: string;
		image_file_ids?: string[];
		voice_file_id?: string;
		kind?: ReferenceKind;
		applies_to_id?: string;
		private?: boolean;
		archived?: boolean;
	}
): Promise<VideoCharacter> =>
	await videoCharacterRequest(token, `/characters/${id}`, {
		method: 'POST',
		body: JSON.stringify(patch)
	});

export const deleteVideoCharacter = async (token: string, id: string) =>
	await videoCharacterRequest(token, `/characters/${id}`, { method: 'DELETE' });

export const attachVideoCharacter = async (token: string, chatId: string, characterId: string) =>
	await videoCharacterRequest(
		token,
		`/characters/chat/${encodeURIComponent(chatId)}/attach/${characterId}`,
		{ method: 'POST' }
	);

export const setVideoCharacterState = async (
	token: string,
	chatId: string,
	characterId: string,
	state: string
) =>
	await videoCharacterRequest(
		token,
		`/characters/chat/${encodeURIComponent(chatId)}/state/${characterId}`,
		{ method: 'POST', body: JSON.stringify({ state }) }
	);

/** Dress an attached character in a library outfit for this chat; '' clears it. */
export const setVideoCharacterOutfit = async (
	token: string,
	chatId: string,
	characterId: string,
	outfitId: string
) =>
	await videoCharacterRequest(
		token,
		`/characters/chat/${encodeURIComponent(chatId)}/outfit/${characterId}`,
		{ method: 'POST', body: JSON.stringify({ outfit_id: outfitId }) }
	);

export const detachVideoCharacter = async (token: string, chatId: string, characterId: string) =>
	await videoCharacterRequest(
		token,
		`/characters/chat/${encodeURIComponent(chatId)}/attach/${characterId}`,
		{ method: 'DELETE' }
	);
