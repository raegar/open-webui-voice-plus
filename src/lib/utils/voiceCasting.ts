// MiniMax H3 voice references are positional: <Audio N> is the Nth recording sent,
// and every recording sent is available to every speaker. A character whose voice
// is attached but who says nothing in the clip lends that voice to whoever does
// speak, typically a Game Master NPC with no recording of their own. These helpers
// work out which recordings a drafted brief actually uses, so the rest can be left
// out of the render.

const SUBJECT_TAG = /<Subject\s*(\d+)>/gi;
const AUDIO_TAG = /<Audio\s*(\d+)>/gi;

const tagNumbers = (text: string, pattern: RegExp) =>
	[...text.matchAll(pattern)].map((match) => Number(match[1]));

const subjectDefinitions = (brief: string) => {
	const start = brief.search(/subject_definitions\s*:/i);
	if (start < 0) return '';
	const end = brief.slice(start).search(/^\s*summary\s*:/im);
	return end < 0 ? brief.slice(start) : brief.slice(start, start + end);
};

/** For each <Audio N> cited in subject_definitions, the one <Subject M> it is given to. */
export const audioOwners = (brief: string): Map<number, number> => {
	const owners = new Map<number, number>();
	const ambiguous = new Set<number>();
	let subject: number | null = null;
	for (const line of subjectDefinitions(brief).split('\n')) {
		const subjects = tagNumbers(line, SUBJECT_TAG);
		if (subjects.length) subject = subjects[0];
		for (const audio of tagNumbers(line, AUDIO_TAG)) {
			if (subject === null || (owners.has(audio) && owners.get(audio) !== subject)) {
				ambiguous.add(audio);
			} else {
				owners.set(audio, subject);
			}
		}
	}
	for (const audio of ambiguous) owners.delete(audio);
	return owners;
};

/**
 * The subjects who speak, judged by the last <Subject M> tag in the sentence that
 * opens each <d> line. Null when any line has no tagged speaker, since a guess
 * could then strip the voice of someone who does speak.
 */
export const dialogueSpeakers = (brief: string): Set<number> | null => {
	const speakers = new Set<number>();
	for (const match of brief.matchAll(/<d>/gi)) {
		const before = brief.slice(0, match.index);
		// The sentence holding the line: after the last full stop, line break, closing
		// </d> or shot marker. Timestamps such as 00:06.000 have no space after the dot.
		const boundary = Math.max(
			...[...before.matchAll(/[.!?](?=\s)|\n|<\/d>|\[Shot\s*\d+\]/gi)].map(
				(found) => (found.index ?? 0) + found[0].length
			),
			0
		);
		const tagged = tagNumbers(before.slice(boundary), SUBJECT_TAG);
		if (!tagged.length) return null;
		speakers.add(tagged[tagged.length - 1]);
	}
	return speakers;
};

/**
 * Drops the voice recordings no speaker in the brief uses, and rewrites the brief
 * to match: sentences about a dropped recording go, the remaining <Audio N> tags
 * are renumbered to their new positions, and each silenced subject is stated as
 * not speaking. A recording is dropped when its tag is missing from the brief, or
 * when every line has a tagged speaker and its owner is not one of them.
 */
export const pruneUnspokenVoices = (
	brief: string,
	voiceIds: string[]
): { brief: string; voiceIds: string[]; dropped: number } => {
	const cited = new Set(tagNumbers(brief, AUDIO_TAG));
	const owners = audioOwners(brief);
	const speakers = dialogueSpeakers(brief);

	const keep = voiceIds.map((_, index) => {
		const audio = index + 1;
		if (!cited.has(audio)) return false;
		const owner = owners.get(audio);
		return !(speakers && owner !== undefined && !speakers.has(owner));
	});
	if (keep.every(Boolean)) return { brief, voiceIds, dropped: 0 };

	const renumber = new Map<number, number>();
	keep.forEach((kept, index) => {
		if (kept) renumber.set(index + 1, renumber.size + 1);
	});
	const droppedTag = (sentence: string) =>
		tagNumbers(sentence, AUDIO_TAG).some((audio) => !renumber.has(audio));
	const silenced = keep
		.map((kept, index) => (kept ? undefined : owners.get(index + 1)))
		.filter((subject): subject is number => subject !== undefined);

	const rewritten = brief
		.split('\n')
		.map((line) =>
			(line.match(/[^.!?]*(?:[.!?]+(?=\s|$)|$)/g) ?? [line])
				.filter((sentence) => !droppedTag(sentence))
				.join('')
				.replace(AUDIO_TAG, (tag, number) =>
					renumber.has(Number(number)) ? `<Audio ${renumber.get(Number(number))}>` : tag
				)
		)
		.join('\n')
		.trimEnd();
	const note = silenced.length
		? `\n\n${silenced.map((subject) => `<Subject ${subject}>`).join(' and ')} ${silenced.length > 1 ? 'do' : 'does'} not speak in this clip. Every line of dialogue belongs to the speaker named beside it.`
		: '';

	return {
		brief: rewritten + note,
		voiceIds: voiceIds.filter((_, index) => keep[index]),
		dropped: keep.filter((kept) => !kept).length
	};
};
