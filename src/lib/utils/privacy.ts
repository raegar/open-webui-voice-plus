import { getPrivateChatIds, setChatPrivate } from '$lib/apis/privacy';
import { privateChatIds } from '$lib/stores';

export const loadPrivateChatIds = async () => {
	privateChatIds.set(new Set(await getPrivateChatIds(localStorage.token)));
};

/** Mark a chat private or not, and update every list that filters on it. */
export const markChatPrivate = async (chatId: string, isPrivate: boolean) => {
	await setChatPrivate(localStorage.token, chatId, isPrivate);
	privateChatIds.update((ids) => {
		const next = new Set(ids);
		if (isPrivate) next.add(chatId);
		else next.delete(chatId);
		return next;
	});
};

/** Leave hidden chats out of a list, keeping null (still loading) as null. */
export const withoutHidden = <T extends { id: string }>(
	items: T[] | null,
	hidden: Set<string>
): T[] | null => (items && hidden.size ? items.filter((item) => !hidden.has(item.id)) : items);
