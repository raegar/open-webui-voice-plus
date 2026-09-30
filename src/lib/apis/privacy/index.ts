import { WEBUI_API_BASE_URL } from '$lib/constants';

const privacyRequest = async (token: string, path: string, init: RequestInit = {}) => {
	let error: string | null = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/privacy${path}`, {
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
			error = err?.detail ?? 'Server connection failed';
			return null;
		});
	if (error) throw error;
	return res;
};

/** Ids of every chat the caller has marked private. */
export const getPrivateChatIds = async (token: string): Promise<string[]> =>
	(await privacyRequest(token, '/chats')) ?? [];

export const setChatPrivate = async (token: string, chatId: string, isPrivate: boolean) =>
	await privacyRequest(token, `/chats/${encodeURIComponent(chatId)}`, {
		method: 'POST',
		body: JSON.stringify({ private: isPrivate })
	});
