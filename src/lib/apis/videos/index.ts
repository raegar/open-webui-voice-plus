import { VIDEOS_API_BASE_URL } from '$lib/constants';

export type VideoGenerationOptions = {
	mode: 'text' | 'image';
	aspect_ratio: '16:9' | '9:16' | '1:1';
	megapixels: 0.2 | 0.4;
	duration: 3 | 5;
	source_image_url?: string;
};

export const videoGenerations = async (
	token: string,
	prompt: string,
	options: VideoGenerationOptions,
	chatId?: string,
	messageId?: string
) => {
	let error: string | null = null;
	const res = await fetch(`${VIDEOS_API_BASE_URL}/generations`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
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
			if (Array.isArray(err?.detail)) {
				error = err.detail
					.map((item: { msg?: string }) => item.msg ?? JSON.stringify(item))
					.join(', ');
			} else {
				error = err?.detail ?? 'Server connection failed';
			}
			return null;
		});

	if (error) throw error;
	return res;
};
