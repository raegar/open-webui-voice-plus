import { describe, expect, it } from 'vitest';

import { getMessageContentParts, removeMarkdownCodeBlocks } from './index';

describe('removeMarkdownCodeBlocks', () => {
	it('removes fenced code while preserving surrounding prose', () => {
		const content = [
			'Before the example.',
			'```ts',
			'const greeting = "hello";',
			'```',
			'After the example.'
		].join('\n');

		expect(removeMarkdownCodeBlocks(content)).toBe(
			['Before the example.', 'After the example.'].join('\n')
		);
	});

	it('supports tilde fences and longer closing fences', () => {
		const content = ['Keep this.', '~~~~python', 'print("skip me")', '~~~~~', 'Keep this too.'].join(
			'\n'
		);

		expect(removeMarkdownCodeBlocks(content)).toBe(['Keep this.', 'Keep this too.'].join('\n'));
	});

	it('skips an unfinished fenced block', () => {
		expect(removeMarkdownCodeBlocks('Speak this.\n```\nnot this')).toBe('Speak this.');
	});

	it('removes code before preparing TTS parts', () => {
		const parts = getMessageContentParts(
			'First sentence.\n```js\nconsole.log("not spoken");\n```\nFinal sentence.',
			'none'
		);

		expect(parts).toEqual(['First sentence.\nFinal sentence.']);
	});
});
