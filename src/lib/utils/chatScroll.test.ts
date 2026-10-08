import { describe, expect, it, vi } from 'vitest';

import { scrollToBottomAfterRender } from './chatScroll';

const viewport = () => {
	const element = {
		scrollTop: 600,
		scrollHeight: 1000,
		clientHeight: 400,
		isConnected: true,
		scrollTo: vi.fn(({ top }: ScrollToOptions) => {
			element.scrollTop = Math.min(top!, element.scrollHeight - element.clientHeight);
		})
	};
	return element as unknown as HTMLElement;
};

describe('chat scrolling while rendering new content', () => {
	it('keeps following at the bottom when follow-up prompts increase the content height', async () => {
		const element = viewport();
		await scrollToBottomAfterRender(
			() => element,
			() => true,
			async () => {
				element.scrollHeight += 200;
			}
		);
		expect(element.scrollTop).toBe(800);
		expect(element.scrollTo).toHaveBeenCalledWith({ top: 1200, behavior: 'instant' });
	});

	it('leaves the reading position alone when already away from the bottom', async () => {
		const element = viewport();
		element.scrollTop = 200;
		const render = vi.fn(async () => {});
		await scrollToBottomAfterRender(
			() => element,
			() => false,
			render
		);
		expect(element.scrollTop).toBe(200);
		expect(element.scrollTo).not.toHaveBeenCalled();
		expect(render).not.toHaveBeenCalled();
	});

	it('cancels a pending scroll if the reader stops following during the render', async () => {
		const element = viewport();
		let following = true;
		await scrollToBottomAfterRender(
			() => element,
			() => following,
			async () => {
				following = false;
				element.scrollTop = 300;
				element.scrollHeight += 200;
			}
		);
		expect(element.scrollTop).toBe(300);
		expect(element.scrollTo).not.toHaveBeenCalled();
	});

	it('respects an upward scroll even before the browser delivers its scroll event', async () => {
		const element = viewport();
		await scrollToBottomAfterRender(
			() => element,
			() => true,
			async () => {
				element.scrollTop -= 100;
			}
		);
		expect(element.scrollTop).toBe(500);
		expect(element.scrollTo).not.toHaveBeenCalled();
	});

	it('does not scroll a replacement chat container after navigation', async () => {
		const original = viewport();
		const replacement = viewport();
		let element = original;
		await scrollToBottomAfterRender(
			() => element,
			() => true,
			async () => {
				element = replacement;
			}
		);
		expect(original.scrollTo).not.toHaveBeenCalled();
		expect(replacement.scrollTo).not.toHaveBeenCalled();
	});

	it('ignores missing or detached containers', async () => {
		await scrollToBottomAfterRender(
			() => null,
			() => true,
			async () => {}
		);
		const element = viewport();
		await scrollToBottomAfterRender(
			() => element,
			() => true,
			async () => {
				Object.assign(element, { isConnected: false });
			}
		);
		expect(element.scrollTo).not.toHaveBeenCalled();
	});
});
