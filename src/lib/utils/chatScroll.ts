// Check again after rendering: the reader may have scrolled since this was queued.
export const scrollToBottomAfterRender = async (
	getContainer: () => HTMLElement | null | undefined,
	isFollowing: () => boolean,
	afterRender: () => Promise<void>
) => {
	if (!isFollowing()) return;
	const container = getContainer();
	if (!container) return;
	const scrollTop = container.scrollTop;

	await afterRender();
	if (
		!isFollowing() ||
		getContainer() !== container ||
		!container.isConnected ||
		container.scrollTop < scrollTop - 1
	) {
		return;
	}

	// An automatic smooth animation can keep pulling against a reader's scroll.
	container.scrollTo({ top: container.scrollHeight, behavior: 'instant' });
};
