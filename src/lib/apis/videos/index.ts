import { VIDEOS_API_BASE_URL } from '$lib/constants';

export type VideoGenerationOptions = {
	mode?: 'text' | 'reference';
	aspect_ratio: '16:9' | '9:16' | '1:1';
	megapixels: 0.2 | 0.4;
	duration: 3 | 5 | 10;
	seed?: number | string;
	first_frame_data_url?: string;
	last_frame_data_url?: string;
	reference_image_data_urls?: string[];
};

export type VideoGenerationJob = {
	job_id: string;
	status: 'queued' | 'running' | 'completed' | 'failed';
	created_at: number;
	updated_at: number;
	result: Array<Record<string, any>> | null;
	error: string | null;
};

const videoApiError = (err: any) =>
	Array.isArray(err?.detail)
		? err.detail.map((item: { msg?: string }) => item.msg ?? JSON.stringify(item)).join(', ')
		: (err?.detail ?? 'Server connection failed');

export const getVideoHistory = async (token: string, limit: number = 50) => {
	let error: string | null = null;
	const searchParams = new URLSearchParams({ limit: String(limit) });
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
