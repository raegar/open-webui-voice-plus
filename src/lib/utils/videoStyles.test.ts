import { describe, expect, it } from 'vitest';

import {
	DEFAULT_VIDEO_STYLE,
	VIDEO_STYLES,
	getVideoStyle,
	isVideoStyleId,
	videoStyleDirection
} from './videoStyles';

describe('video styles', () => {
	it('adds nothing to the draft for the default style', () => {
		expect(videoStyleDirection(DEFAULT_VIDEO_STYLE)).toBe('');
		expect(videoStyleDirection(null)).toBe('');
	});

	it('falls back to the default for an unknown or retired style', () => {
		expect(getVideoStyle('no-such-style').id).toBe('default');
		expect(videoStyleDirection('no-such-style')).toBe('');
		expect(isVideoStyleId('no-such-style')).toBe(false);
		expect(isVideoStyleId(undefined)).toBe(false);
	});

	it('gives every other style a labelled direction', () => {
		for (const style of VIDEO_STYLES.filter((item) => item.id !== 'default')) {
			const direction = videoStyleDirection(style.id);
			expect(direction).toContain(`Filming style: ${style.label}`);
			expect(direction).toContain(style.direction);
		}
	});

	it('keeps the POV subject out of frame only for a POV style', () => {
		const withSubject = videoStyleDirection('smart-glasses', { povSubject: '  Mara ' });
		expect(withSubject).toContain('The glasses are worn by Mara,');
		expect(withSubject).toContain('Mara never appears in frame');

		expect(videoStyleDirection('smart-glasses', { povSubject: '   ' })).toBe(
			videoStyleDirection('smart-glasses')
		);
		expect(videoStyleDirection('smart-glasses')).not.toContain('The glasses are worn by');
		expect(videoStyleDirection('cinematic', { povSubject: 'Mara' })).not.toContain('Mara');
	});

	it('uses unique ids', () => {
		const ids = VIDEO_STYLES.map((style) => style.id);
		expect(new Set(ids).size).toBe(ids.length);
	});
});
