import { describe, expect, it } from 'vitest';

import { audioOwners, dialogueSpeakers, pruneUnspokenVoices } from './voiceCasting';

const brief = (definitions: string, description: string) => `subject_definitions:
${definitions}

summary:
[reference generation] A tense meeting. Clear intelligible speech takes priority.

retention_analysis:
- <Subject 1>: fully_preserved.

detailed_description:
${description}

overall_soundscape:
Quiet room tone.

non_diegetic_music:
N/A`;

const definitions = `- <Subject 1> (Ada): A woman in her thirties. <Picture 1>. Voice: <Audio 1> (timbre and delivery only).
- <Subject 2> (Ben): A tall man in his forties. <Picture 2>, <Picture 3>. His voice is <Audio 2>, providing timbre only.
- <Subject 3> (Cole): A man in his early thirties with dark hair. <Picture 4>. No voice recording; a low, gravelly baritone.`;

describe('voice casting', () => {
	it('maps each recording to the subject it is defined for', () => {
		expect(audioOwners(brief(definitions, ''))).toEqual(
			new Map([
				[1, 1],
				[2, 2]
			])
		);
	});

	it('finds each line’s speaker within its own sentence', () => {
		const text = brief(
			definitions,
			`[Shot 1] A two-shot of <Subject 1> and <Subject 2>. <Subject 1> whispers <d>[English] Maybe we shouldn't.</d> <Subject 2> leans closer.
[Shot 2] At 00:06.000 The door swings open. <Subject 3> shouts <d>[English] Ada!</d>`
		);
		expect(dialogueSpeakers(text)).toEqual(new Set([1, 3]));
	});

	it('gives up when a line has no tagged speaker in its sentence', () => {
		const text = brief(
			definitions,
			`[Shot 1] <Subject 3> strides in. He shouts <d>[English] Ada!</d>`
		);
		expect(dialogueSpeakers(text)).toBeNull();
	});

	it('drops a silent character’s voice so the NPC cannot borrow it', () => {
		const text = brief(
			definitions,
			`[Shot 1] <Subject 1> whispers <d>[English] Maybe we shouldn't.</d>
[Shot 2] At 00:06.000 <Subject 3> shouts <d>[English] Ada!</d> <Subject 2> steps back.`
		);
		const result = pruneUnspokenVoices(text, ['voice-ada', 'voice-ben']);
		expect(result.voiceIds).toEqual(['voice-ada']);
		expect(result.dropped).toBe(1);
		expect(result.brief).not.toContain('<Audio 2>');
		expect(result.brief).toContain('Voice: <Audio 1> (timbre and delivery only).');
		expect(result.brief).toContain('<Picture 2>, <Picture 3>.');
		expect(result.brief).toContain('<Subject 2> does not speak in this clip.');
	});

	it('renumbers the recordings that remain', () => {
		const text = brief(
			definitions,
			`[Shot 1] <Subject 2> says <d>[English] Not now.</d> <Subject 3> answers <d>[English] Now.</d>`
		);
		const result = pruneUnspokenVoices(text, ['voice-ada', 'voice-ben']);
		expect(result.voiceIds).toEqual(['voice-ben']);
		expect(result.brief).toContain('His voice is <Audio 1>, providing timbre only.');
		expect(result.brief).not.toContain('<Audio 2>');
		expect(result.brief).toContain('<Subject 1> does not speak in this clip.');
	});

	it('drops a recording the brief never cites, even without tagged speakers', () => {
		const text = brief(
			`- <Subject 1>: A woman. <Picture 1>. Voice: <Audio 1>.
- <Subject 2>: A man. <Picture 2>. Silent in this clip.`,
			`[Shot 1] She says <d>[English] Hello.</d>`
		);
		const result = pruneUnspokenVoices(text, ['voice-ada', 'voice-ben']);
		expect(result.voiceIds).toEqual(['voice-ada']);
		expect(result.brief).toContain('Voice: <Audio 1>.');
	});

	it('keeps every voice when the speakers cannot be told apart', () => {
		const text = brief(
			definitions,
			`[Shot 1] <Subject 3> strides in. He shouts <d>[English] Ada!</d>`
		);
		const result = pruneUnspokenVoices(text, ['voice-ada', 'voice-ben']);
		expect(result).toEqual({ brief: text, voiceIds: ['voice-ada', 'voice-ben'], dropped: 0 });
	});
});
