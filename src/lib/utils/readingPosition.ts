type ReadingPosition = {
	container: HTMLElement;
	paragraph: HTMLElement;
	words: string[];
	wordIndex: number;
	top: number;
};

const wordsIn = (text: string) => [...text.matchAll(/[\p{L}\p{N}]+/gu)];

const textNodesIn = (element: HTMLElement) =>
	element.ownerDocument.createTreeWalker(element, NodeFilter.SHOW_TEXT);

// Measure the first character at a text offset, regardless of inline formatting.
const wordTop = (element: HTMLElement, offset: number) => {
	const walker = textNodesIn(element);
	let node: Node | null;
	while ((node = walker.nextNode())) {
		const length = node.textContent?.length ?? 0;
		if (offset < length) {
			const range = element.ownerDocument.createRange();
			range.setStart(node, offset);
			range.setEnd(node, offset + 1);
			return range.getBoundingClientRect().top;
		}
		offset -= length;
	}
	return null;
};

export const captureReadingPosition = (
	content: HTMLElement | null | undefined,
	container: HTMLElement | null
): ReadingPosition | null => {
	if (!content || !container) return null;
	// Readers at the bottom should continue following the reply normally.
	if (container.scrollHeight - container.scrollTop <= container.clientHeight + 5) return null;
	const viewport = container.getBoundingClientRect();
	const bounds = content.getBoundingClientRect();
	if (bounds.bottom <= viewport.top || bounds.top >= viewport.bottom) return null;

	const walker = textNodesIn(content);
	let node: Node | null;
	while ((node = walker.nextNode())) {
		const range = content.ownerDocument.createRange();
		range.selectNodeContents(node);
		const rect = range.getBoundingClientRect();
		if (rect.bottom <= viewport.top + 2 || rect.top >= viewport.bottom || !rect.width) continue;

		for (const word of wordsIn(node.textContent ?? '')) {
			range.setStart(node, word.index!);
			range.setEnd(node, word.index! + 1);
			const wordRect = range.getBoundingClientRect();
			if (wordRect.top < viewport.top + 2 || wordRect.top >= viewport.bottom) continue;

			const paragraph =
				node.parentElement?.closest<HTMLElement>(
					'p, li, blockquote, pre, h1, h2, h3, h4, h5, h6'
				) ?? content;
			if (!content.contains(paragraph)) continue;
			const prefix = content.ownerDocument.createRange();
			prefix.selectNodeContents(paragraph);
			prefix.setEnd(node, word.index!);
			const wordIndex = wordsIn(prefix.toString()).length;
			const words = wordsIn(paragraph.textContent ?? '')
				.slice(wordIndex, wordIndex + 8)
				.map((match) => match[0]);
			return { container, paragraph, words, wordIndex, top: wordRect.top };
		}
	}
	return null;
};

export const restoreReadingPosition = (position: ReadingPosition | null) => {
	if (!position) return;
	const { container, paragraph, words, wordIndex, top } = position;
	if (!container.isConnected || !paragraph.isConnected || !words.length) return;
	const current = wordsIn(paragraph.textContent ?? '');
	let bestIndex = -1;
	for (let index = 0; index <= current.length - words.length; index++) {
		if (
			words.every((word, offset) => current[index + offset][0] === word) &&
			(bestIndex === -1 || Math.abs(index - wordIndex) < Math.abs(bestIndex - wordIndex))
		) {
			bestIndex = index;
		}
	}
	if (bestIndex === -1) return;
	const currentTop = wordTop(paragraph, current[bestIndex].index!);
	if (currentTop !== null && Math.abs(currentTop - top) > 0.5) {
		container.scrollTop += currentTop - top;
	}
};
