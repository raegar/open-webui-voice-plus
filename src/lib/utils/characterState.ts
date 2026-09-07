import { get } from 'svelte/store';

import { generateOpenAIChatCompletion } from '$lib/apis/openai';
import { getChatVideoCharacters, setVideoCharacterState } from '$lib/apis/videos';
import { characterStateVersion, models, settings } from '$lib/stores';

// The automatic pass runs every turn, so it stays cheap and only looks at the latest
// exchange. A manual refresh is deliberate and infrequent, so it can afford a much
// wider window — which is what catches changes the narrow automatic pass missed.
const AUTO_TURNS = 4;
const MANUAL_TURNS = 24;
// Matches the max_length on the API's state field.
const MAX_STATE = 2000;

const SYSTEM = `You maintain a continuity record of what each character is currently wearing in a roleplay.

You receive each character's default appearance, the state currently recorded for them, and the most recent messages. Decide whether the recent messages show that character's clothing or state of dress changing.

Reply with a JSON object only. No prose, no code fences. Keys are character names exactly as given. Values are the character's new state as a short phrase.

Rules:
- Include a character ONLY if the recent messages actually show their clothing changing: putting something on, taking something off, changing outfit, becoming dishevelled, getting wet, and so on.
- If nothing about a character's clothing changed, omit them entirely. An empty object {} is the correct and common answer.
- Never restate the default appearance as a change.
- Never infer a change from mood, location, or dialogue alone.
- Describe only clothing and state of dress. No personality, no actions, no plot.
- Write the complete current state, not the delta: "barefoot in a red silk gown" rather than "took off shoes".
- Keep each value under 200 characters.`;

/**
 * A workspace model carries its own system prompt, and the backend prepends it to
 * every request made against that model. For a roleplay model that means this
 * tracker would run *in character* - a persona narrating the scene in first person
 * biases which characters it bothers to report on, so the character the persona
 * plays keeps getting updated and the other one silently does not. Resolving to the
 * underlying base model lets the tracker's own instructions stand on their own.
 */
const trackerModel = (modelId: string): string => {
	const model = (get(models) as any[])?.find((m) => m?.id === modelId);
	return model?.info?.base_model_id || modelId;
};

/**
 * Names are matched ignoring case and surrounding whitespace. Matching stays
 * whole-name: profiles like "Alex", "Alex (A.I.)" and "Alex (2007)" coexist in
 * the library, so anything looser would put one character's clothes on another.
 */
const nameKey = (name: string): string => name.trim().toLowerCase();

type TrackedCharacter = { id: string; name: string; description: string; state: string };

const asText = (content: unknown): string => {
	if (typeof content === 'string') return content;
	if (Array.isArray(content)) {
		return content
			.filter((part: any) => part?.type === 'text' && typeof part?.text === 'string')
			.map((part: any) => part.text)
			.join(' ');
	}
	return '';
};

/** Strip a ```json fence if the model added one despite being told not to. */
const parseStates = (raw: string): Record<string, string> => {
	const cleaned = raw
		.replace(/^\s*```(?:json)?\s*/i, '')
		.replace(/\s*```\s*$/i, '')
		.trim();
	const start = cleaned.indexOf('{');
	const end = cleaned.lastIndexOf('}');
	if (start === -1 || end <= start) return {};
	const parsed = JSON.parse(cleaned.slice(start, end + 1));
	return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
};

/**
 * Update the recorded state of dress for characters attached to this chat, from the
 * latest exchange. Fire-and-forget: it runs after the reply is already on screen and
 * must never surface an error or delay the conversation.
 */
export const syncCharacterState = async (
	chatId: string,
	modelId: string,
	messages: { role: string; content: unknown }[],
	options: { manual?: boolean } = {}
): Promise<{ updated: string[] } | null> => {
	const manual = options.manual === true;
	try {
		if (!chatId || chatId.startsWith('local:') || !modelId) return null;
		// A manual refresh is an explicit request, so it ignores the automatic setting.
		if (!manual && (get(settings) as any)?.autoTrackCharacterState === false) return null;

		const attached = await getChatVideoCharacters(localStorage.token, chatId);
		const tracked: TrackedCharacter[] = attached
			.filter((c) => (c.kind ?? 'character') === 'character')
			.map((c) => ({
				id: c.id,
				name: c.name,
				description: c.description ?? '',
				state: c.state ?? ''
			}));
		if (tracked.length === 0) return { updated: [] };

		const recent = messages
			.slice(-(manual ? MANUAL_TURNS : AUTO_TURNS))
			.map((m) => `${m.role === 'user' ? 'User' : 'Assistant'}: ${asText(m.content).trim()}`)
			.filter((line) => line.length > 12)
			.join('\n\n');
		if (!recent) return { updated: [] };

		const roster = tracked
			.map(
				(c) =>
					`- ${c.name}\n  default: ${c.description.slice(0, 400) || '(none given)'}\n  recorded now: ${c.state || '(nothing recorded)'}`
			)
			.join('\n');

		const response = await generateOpenAIChatCompletion(localStorage.token, {
			model: trackerModel(modelId),
			stream: false,
			messages: [
				{ role: 'system', content: SYSTEM },
				{
					role: 'user',
					content: `Characters:\n${roster}\n\nRecent messages:\n---\n${recent}\n---\n\nJSON only.`
				}
			]
		});

		const content = response?.choices?.[0]?.message?.content;
		if (typeof content !== 'string' || !content.trim()) return { updated: [] };

		const updates = parseStates(content);
		// A reply that differs only in case or padding still has to land; a lookup
		// miss here is invisible and looks exactly like a character never updating.
		const byName = new Map<string, unknown>();
		for (const [key, value] of Object.entries(updates)) {
			byName.set(nameKey(key), value);
		}
		const updated: string[] = [];
		for (const character of tracked) {
			const next = Object.prototype.hasOwnProperty.call(updates, character.name)
				? updates[character.name]
				: byName.get(nameKey(character.name));
			// Only a non-empty string that actually differs counts as a change, so a
			// malformed reply or an unchanged character never clears what is recorded.
			if (typeof next !== 'string') continue;
			const value = next.trim().slice(0, MAX_STATE);
			if (!value || value === character.state) continue;
			await setVideoCharacterState(localStorage.token, chatId, character.id, value);
			updated.push(character.name);
		}
		// Let an open Controls pane pick the new values up rather than showing stale text.
		if (updated.length) characterStateVersion.update((n) => n + 1);
		return { updated };
	} catch (error) {
		console.error('Character state sync failed', error);
		// A manual run reports failure; the automatic one stays silent by design.
		if (manual) throw error;
		return null;
	}
};
