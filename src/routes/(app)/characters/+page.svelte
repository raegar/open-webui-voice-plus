<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { updateUserSettings } from '$lib/apis/users';
	import {
		createVideoCharacter,
		deleteVideoCharacter,
		getVideoCharacterLibrary,
		updateVideoCharacter,
		type VideoCharacter
	} from '$lib/apis/videos';
	import Selector from '$lib/components/chat/ModelSelector/Selector.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { config, mobile, models, settings, showSidebar, user, WEBUI_NAME } from '$lib/stores';

	const MAX_IMAGES_PER_CHARACTER = 3;
	// Mirrors the max_length on VideoCharacterForm; exceeding it is a 422 from the API.
	const MAX_DESCRIPTION = 4000;
	const MAX_NAME = 200;
	const KINDS = [
		{ value: 'character', label: 'Character' },
		{ value: 'location', label: 'Location' },
		{ value: 'outfit', label: 'Outfit' }
	] as const;
	// The Ref2VA node accepts at most 3 standalone ref_audios across the whole
	// generation, so only three attached characters can be voiced in one scene.
	const AUDIO_TYPES = [
		'audio/wav',
		'audio/x-wav',
		'audio/mpeg',
		'audio/mp3',
		'audio/mp4',
		'audio/m4a',
		'audio/x-m4a',
		'audio/ogg',
		'audio/flac',
		'audio/webm'
	];

	let loaded = false;
	let loading = false;
	let characters: VideoCharacter[] = [];
	let newName = '';
	let creating = false;
	let busyId: string | null = null;
	let fileInput: HTMLInputElement;
	let uploadTargetId: string | null = null;
	let voiceInput: HTMLInputElement;
	let voiceTargetId: string | null = null;
	let descriptionDrafts: Record<string, string> = {};
	let pendingDeletion: VideoCharacter | null = null;
	let showDeleteConfirm = false;
	let descriptionModelId = '';
	let lastPersistedModel = '';
	let visionModelId = '';
	let lastPersistedVisionModel = '';
	let savingModelPreference = false;
	// Which card is being described from its reference images.
	let visionFor: string | null = null;
	// The short brief each card expands from, and which card is mid-generation.
	let seedPrompts: Record<string, string> = {};
	let generatingFor: string | null = null;
	// What a description was before the last generation, so it can be put back.
	let previousDescriptions: Record<string, string> = {};
	// Which control made that change, so Undo shows beside it rather than twice.
	let undoSource: Record<string, 'brief' | 'images'> = {};

	$: availableModels = ($models ?? []).filter((model) => !(model?.info?.meta?.hidden ?? false));
	// Open WebUI treats a model with no vision flag as vision-capable, so only an
	// explicit false rules one out.
	$: visionModels = availableModels.filter(
		(model) => model?.info?.meta?.capabilities?.vision ?? true
	);

	// Work mode leaves private characters off the page entirely.
	$: visibleCharacters = $settings?.hidePrivate ? characters.filter((c) => !c.private) : characters;
	// Archived entries get a view of their own rather than mixing in with the rest.
	let showArchived = false;
	$: archivedCount = visibleCharacters.filter((c) => c.archived).length;
	$: viewCharacters = visibleCharacters.filter((c) => !!c.archived === showArchived);
	$: if (showArchived && archivedCount === 0) showArchived = false;

	type KindFilter = 'all' | (typeof KINDS)[number]['value'];
	type SortOrder = 'name' | 'name-desc' | 'newest' | 'oldest';
	const SORTS: { value: SortOrder; label: string }[] = [
		{ value: 'name', label: 'Name A–Z' },
		{ value: 'name-desc', label: 'Name Z–A' },
		{ value: 'newest', label: 'Newest first' },
		{ value: 'oldest', label: 'Oldest first' }
	];
	// Type and sort are remembered in this browser; the search box starts empty.
	const VIEW_KEY = 'characters-page-view';
	let search = '';
	let kindFilter: KindFilter = 'all';
	let sortOrder: SortOrder = 'name';
	try {
		const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? '{}');
		if (['all', ...KINDS.map((k) => k.value)].includes(saved.kind)) kindFilter = saved.kind;
		if (SORTS.some((s) => s.value === saved.sort)) sortOrder = saved.sort;
	} catch {
		// Blocked or malformed storage just means the defaults.
	}
	const saveView = (kind: KindFilter, sort: SortOrder) => {
		try {
			localStorage.setItem(VIEW_KEY, JSON.stringify({ kind, sort }));
		} catch {
			// Not remembering the view is harmless.
		}
	};
	$: saveView(kindFilter, sortOrder);

	$: kindCounts = viewCharacters.reduce(
		(counts, c) => {
			const kind = c.kind ?? 'character';
			counts[kind] = (counts[kind] ?? 0) + 1;
			return counts;
		},
		{} as Record<string, number>
	);
	// Filters on saved values, not drafts, so a card does not vanish mid-edit.
	$: query = search.trim().toLowerCase();
	$: shownCharacters = viewCharacters
		.filter((c) => kindFilter === 'all' || (c.kind ?? 'character') === kindFilter)
		.filter(
			(c) =>
				!query ||
				c.name.toLowerCase().includes(query) ||
				c.description.toLowerCase().includes(query)
		)
		.sort((a, b) => {
			if (sortOrder === 'newest') return (b.created_at ?? 0) - (a.created_at ?? 0);
			if (sortOrder === 'oldest') return (a.created_at ?? 0) - (b.created_at ?? 0);
			const byName = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
			return sortOrder === 'name-desc' ? -byName : byName;
		});
	$: filtered = kindFilter !== 'all' || query !== '';

	const clearFilters = () => {
		search = '';
		kindFilter = 'all';
	};

	const addedOn = (seconds: number) =>
		seconds
			? new Date(seconds * 1000).toLocaleDateString(undefined, {
					day: 'numeric',
					month: 'short',
					year: 'numeric'
				})
			: '';

	$: canUseVideo =
		$config?.features?.enable_video_generation &&
		($user?.role === 'admin' || $user?.permissions?.features?.image_generation);

	const imageUrl = (fileId: string) => `${WEBUI_API_BASE_URL}/files/${fileId}/content`;

	const load = async () => {
		loading = true;
		try {
			characters = await getVideoCharacterLibrary(localStorage.token);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	const addCharacter = async () => {
		const name = newName.trim();
		if (!name) return;
		creating = true;
		try {
			const created = await createVideoCharacter(localStorage.token, {
				name,
				description: '',
				image_file_ids: []
			});
			characters = [...characters, created].sort((a, b) => a.name.localeCompare(b.name));
			newName = '';
			// A new entry is always a character; make sure the filters are not hiding it.
			search = '';
			showArchived = false;
			if (kindFilter !== 'character') kindFilter = 'all';
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			creating = false;
		}
	};

	// "Alex" -> "Alex (copy)" -> "Alex (copy 2)". The base is trimmed so the
	// suffix still fits inside MAX_NAME, which the API enforces as a 422.
	const copyName = (base: string) => {
		const taken = new Set(characters.map((c) => c.name));
		const withSuffix = (suffix: string) =>
			`${base.slice(0, Math.max(1, MAX_NAME - suffix.length))}${suffix}`;
		let candidate = withSuffix(' (copy)');
		let n = 2;
		while (taken.has(candidate)) {
			candidate = withSuffix(` (copy ${n})`);
			n += 1;
		}
		return candidate;
	};

	// The copy points at the same uploaded files as the original. Deleting either
	// character leaves those files in place, so the two never invalidate each other.
	const duplicateCharacter = async (character: VideoCharacter) => {
		busyId = character.id;
		try {
			const created = await createVideoCharacter(localStorage.token, {
				name: copyName(character.name),
				description: character.description,
				image_file_ids: [...character.image_file_ids],
				kind: character.kind ?? 'character',
				...(character.voice_file_id && { voice_file_id: character.voice_file_id }),
				...(character.private && { private: true })
			});
			characters = [...characters, created].sort((a, b) => a.name.localeCompare(b.name));
			toast.success(`${created.name} added to your library.`);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	// Kind and privacy save immediately; they are pickers, not free text.
	const saveMeta = async (
		character: VideoCharacter,
		patch: {
			kind?: 'character' | 'location' | 'outfit';
			private?: boolean;
		}
	) => {
		busyId = character.id;
		try {
			const updated = await updateVideoCharacter(localStorage.token, character.id, patch);
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const setArchived = async (character: VideoCharacter, archived: boolean) => {
		busyId = character.id;
		try {
			const updated = await updateVideoCharacter(localStorage.token, character.id, { archived });
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
			toast.success(
				archived
					? `${character.name} archived. Chats they are already in are unaffected.`
					: `${character.name} restored to your library.`
			);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const saveField = async (
		character: VideoCharacter,
		patch: { name?: string; description?: string }
	) => {
		const key = Object.keys(patch)[0] as 'name' | 'description';
		if ((patch[key] ?? '') === character[key]) return;
		if (key === 'name' && !patch.name?.trim()) {
			toast.error('A character needs a name.');
			characters = [...characters];
			return;
		}
		// Refuse over-length edits here rather than letting the API answer with a 422.
		// The draft is kept so the text stays on screen to be trimmed.
		if (key === 'name' && (patch.name ?? '').length > MAX_NAME) {
			toast.error(
				`Name is ${(patch.name ?? '').length - MAX_NAME} characters over the ${MAX_NAME} limit.`
			);
			return;
		}
		if (key === 'description' && (patch.description ?? '').length > MAX_DESCRIPTION) {
			toast.error(
				`Description is ${(patch.description ?? '').length - MAX_DESCRIPTION} characters over the ${MAX_DESCRIPTION} limit. Trim it to save.`
			);
			return;
		}
		try {
			const updated = await updateVideoCharacter(localStorage.token, character.id, patch);
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
			if (key === 'description') {
				const { [character.id]: _saved, ...rest } = descriptionDrafts;
				descriptionDrafts = rest;
			}
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	// A description feeds two very different consumers: the chat, where it becomes the
	// assistant's characterization, and the video reference sheet, where it must tell a
	// model that has never seen the person what they look like. Both halves have to be
	// present, which is why the shape is prescribed rather than left to the model.
	const DESCRIPTION_SYSTEM: Record<string, string> = {
		character: `You write reference profiles for a roleplay and video generation system. Expand the user's short brief into one description.

Write it in two parts, in this order, each under its own heading exactly as given:

Background and personality
Where they come from, what they do, what shaped them, how they carry themselves with other people, how they speak, and their habits and mannerisms.

Appearance
Their usual look, concretely and visually: apparent age, build and height, skin tone, hair colour, length and style, eye colour, face shape, distinguishing features, and the clothes they normally wear.

Rules:
- Anything the brief states is fixed and must survive unchanged. Fill every gap yourself with a specific, definite choice; never write "unspecified", "varies", "perhaps", or offer alternatives.
- The appearance part is read by a video model that has never seen this person and cannot see any picture. Describe what is visible rather than naming a style, a subculture, or a resemblance to someone else.
- Third person, present tense, plain prose. No markdown, no bullet points, no headings other than the two above.
- Aim for 200 to 350 words, and never exceed 3500 characters.`,
		location: `You write place references for a video generation system. Expand the user's short brief into one description of a location.

Write it in two parts, in this order, each under its own heading exactly as given:

Character and use
What the place is, who uses it and what for, and the atmosphere it has.

Appearance
Its visual detail: size and layout, surfaces and materials, colours, light sources and how the light falls, furniture, and the objects that fill it.

Rules:
- Anything the brief states is fixed. Fill every gap yourself with a specific, definite choice; never hedge or offer alternatives.
- This is an environment, never a subject. Do not describe people in it, and never give it actions, speech, or intent.
- It is read by a video model that has never seen the place. Describe what is visible rather than naming a style or a period alone.
- Third person, present tense, plain prose. No markdown, no bullet points, no headings other than the two above.
- Aim for 150 to 300 words, and never exceed 3500 characters.`,
		outfit: `You write wardrobe references for a video generation system. Expand the user's short brief into one description of a set of clothing.

Write plain prose covering every garment in turn: each piece, its cut and fit, fabric and texture, colour and pattern, condition and wear, and how it sits on the body. Then footwear, then accessories and jewellery.

Rules:
- Anything the brief states is fixed. Fill every gap yourself with a specific, definite choice; never hedge or offer alternatives.
- Describe the clothing only. Never describe the face, body, hair, age, or identity of anyone wearing it: these garments are placed on a character defined elsewhere.
- It is read by a video model that has never seen the clothes. Describe what is visible rather than naming a brand, a designer, or a style alone.
- Third person, present tense, plain prose. No markdown, no bullet points, no headings.
- Aim for 120 to 250 words, and never exceed 3500 characters.`
	};

	const seedPlaceholder = (kind: string) =>
		kind === 'location'
			? 'A line is enough: cramped 1980s arcade above a chip shop'
			: kind === 'outfit'
				? 'A line is enough: battered leather jacket over a faded band tee'
				: 'A line is enough: sardonic goth barista, late 20s, ex-art student';

	// Models reach for fences and bold headings however plainly they are told not to.
	// Some hosts of reasoning models also inline the reasoning as a <think> block.
	const cleanDescription = (text: string) =>
		text
			.replace(/^[\s\S]*<\/think>/i, '')
			.replace(/^\s*```[a-z]*\s*/i, '')
			.replace(/\s*```\s*$/i, '')
			.replace(/\*\*/g, '')
			.replace(/^#+\s*/gm, '')
			.trim();

	const describeCharacter = async (character: VideoCharacter) => {
		const seed = (seedPrompts[character.id] ?? '').trim();
		if (!seed) {
			toast.error('Write a short brief first.');
			return;
		}
		if (!descriptionModelId) {
			toast.error('Choose a description model first.');
			return;
		}
		generatingFor = character.id;
		try {
			const kind = character.kind ?? 'character';
			const response = await generateOpenAIChatCompletion(localStorage.token, {
				model: descriptionModelId,
				stream: false,
				messages: [
					{ role: 'system', content: DESCRIPTION_SYSTEM[kind] ?? DESCRIPTION_SYSTEM.character },
					{
						role: 'user',
						content: `Name: ${character.name}\n\nBrief:\n${seed}\n\nWrite the description now.`
					}
				]
			});
			const content = response?.choices?.[0]?.message?.content;
			if (typeof content !== 'string' || !content.trim()) {
				throw new Error('The model returned an empty description.');
			}
			const text = cleanDescription(content).slice(0, MAX_DESCRIPTION);
			// Capture what was there before saving over it, so Undo has something to put
			// back whether or not that text had ever been saved.
			undoSource = { ...undoSource, [character.id]: 'brief' };
			previousDescriptions = {
				...previousDescriptions,
				[character.id]: descriptionDrafts[character.id] ?? character.description
			};
			descriptionDrafts = { ...descriptionDrafts, [character.id]: text };
			await saveField(character, { description: text });
			toast.success(`Description written for ${character.name}.`);
		} catch (error) {
			toast.error(`The description could not be generated: ${error}`);
		} finally {
			generatingFor = null;
		}
	};

	const undoDescription = async (character: VideoCharacter) => {
		const previous = previousDescriptions[character.id];
		if (previous === undefined) return;
		descriptionDrafts = { ...descriptionDrafts, [character.id]: previous };
		await saveField(character, { description: previous });
		const { [character.id]: _dropped, ...rest } = previousDescriptions;
		previousDescriptions = rest;
	};

	// Reference photos describe only what can be seen, so each kind gets a prompt for
	// its visual part alone. For a character or location that part replaces the
	// Appearance section and leaves the rest of the description alone; an outfit's
	// description is entirely visual, so it is replaced whole.
	const VISION_RULES = `Rules:
- Describe only what the images show. Where a detail is hard to make out, commit to the most plausible definite reading rather than hedging; never write "unclear", "appears to be possibly", or offer alternatives.
- Never identify, name, or guess who anyone in the images is, and never compare them to a real person.
- Do not describe the photograph itself: no camera angle, framing, pose, image quality, or filters.
- Third person, present tense, plain prose. No markdown, no bullet points, no headings.`;

	const VISION_SYSTEM: Record<string, string> = {
		character: `You write the appearance section of a character reference for a video generation system, working from reference photos of that character. The text is read by a video model that has never seen this person, so it must let that model draw them.

Cover, concretely: apparent age, build and height, skin tone, hair colour, length and style, eye colour, face shape, distinguishing features such as freckles, scars, tattoos, piercings, or glasses, and the clothes they are wearing.

Every image shows the same person. Describe the features that stay consistent across them. Where the images disagree on clothing or hairstyle, describe what the first image shows. Leave out the background and anyone else in the frame.

${VISION_RULES}`,
		location: `You write the appearance section of a location reference for a video generation system, working from reference photos of the place. The text is read by a video model that has never seen it.

Cover: size and layout, surfaces and materials, colours, light sources and how the light falls, furniture, and the objects that fill it.

Every image shows the same place. This is an environment, never a subject: leave out any people in the images, and never give the place actions or intent.

${VISION_RULES}`,
		outfit: `You write wardrobe references for a video generation system, working from reference photos of a set of clothing. The text is read by a video model that has never seen the clothes.

Cover every garment in turn: each piece, its cut and fit, fabric and texture, colour and pattern, condition and wear, and how it sits on the body. Then footwear, then accessories and jewellery. Describe what is visible rather than naming a brand, a designer, or a style alone.

Every image shows the same outfit. Describe the clothing only. Never describe the face, body, hair, age, or identity of anyone wearing it: these garments are placed on a character defined elsewhere.

${VISION_RULES}`
	};

	// Headings the text generator writes; the appearance section runs from its
	// heading to the next of these, or to the end.
	const SECTION_HEADINGS = /^(background and personality|character and use|appearance)\s*:?\s*$/i;
	const APPEARANCE_HEADING = /^appearance\s*:?\s*$/i;

	// Returns the description with its appearance section cut out, and where to put
	// a new one back.
	const splitAppearance = (description: string) => {
		const lines = description.split('\n');
		const start = lines.findIndex((line) => APPEARANCE_HEADING.test(line.trim()));
		if (start === -1) return { before: description.trimEnd(), after: '' };
		let end = lines.length;
		for (let i = start + 1; i < lines.length; i++) {
			if (SECTION_HEADINGS.test(lines[i].trim())) {
				end = i;
				break;
			}
		}
		return {
			before: lines.slice(0, start).join('\n').trimEnd(),
			after: lines.slice(end).join('\n').trim()
		};
	};

	const joinAppearance = (before: string, appearance: string, after: string) =>
		[before, `Appearance\n${appearance}`, after].filter(Boolean).join('\n\n');

	// Phone photos run to several megabytes; a long edge of 1024px keeps the request
	// small and is as much as vision models take in anyway.
	const MAX_IMAGE_EDGE = 1024;

	const loadImageDataUrl = async (fileId: string) => {
		const res = await fetch(imageUrl(fileId), {
			headers: { Authorization: `Bearer ${localStorage.token}` },
			credentials: 'include'
		});
		if (!res.ok) throw new Error(`a reference image could not be loaded (${res.status})`);
		const bitmap = await createImageBitmap(await res.blob());
		try {
			const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
			const canvas = document.createElement('canvas');
			canvas.width = Math.round(bitmap.width * scale);
			canvas.height = Math.round(bitmap.height * scale);
			const context = canvas.getContext('2d');
			if (!context) throw new Error('the image could not be prepared');
			context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
			return canvas.toDataURL('image/jpeg', 0.9);
		} finally {
			bitmap.close();
		}
	};

	const describeFromImages = async (character: VideoCharacter) => {
		if (character.image_file_ids.length === 0) {
			toast.error('Add a reference image first.');
			return;
		}
		if (!visionModelId) {
			toast.error('Choose a vision model first.');
			return;
		}
		const kind = character.kind ?? 'character';
		const current = descriptionDrafts[character.id] ?? character.description;
		const { before, after } =
			kind === 'outfit' ? { before: '', after: '' } : splitAppearance(current);
		// Whatever the rest of the description leaves of the limit, less the heading
		// and the blank lines around it.
		const budget =
			MAX_DESCRIPTION -
			(kind === 'outfit' ? 0 : before.length + after.length + '\n\nAppearance\n\n\n'.length);
		if (budget < 400) {
			toast.error(
				'The rest of the description leaves too little room for an appearance section. Shorten it first.'
			);
			return;
		}

		visionFor = character.id;
		try {
			const images = await Promise.all(character.image_file_ids.map(loadImageDataUrl));
			const response = await generateOpenAIChatCompletion(localStorage.token, {
				model: visionModelId,
				stream: false,
				messages: [
					{ role: 'system', content: VISION_SYSTEM[kind] ?? VISION_SYSTEM.character },
					{
						role: 'user',
						content: [
							{
								type: 'text',
								text: `Name: ${character.name}\n\nWrite the description from ${
									images.length === 1 ? 'this image' : `these ${images.length} images`
								} now, in 120 to 250 words and never more than ${Math.min(budget, 2500)} characters. Refer to them as ${character.name}.`
							},
							...images.map((url) => ({ type: 'image_url', image_url: { url } }))
						]
					}
				]
			});
			const content = response?.choices?.[0]?.message?.content;
			if (typeof content !== 'string' || !content.trim()) {
				throw new Error('The model returned an empty description.');
			}
			// A model that repeats the heading would otherwise leave it doubled.
			const visual = cleanDescription(content)
				.replace(/^appearance\s*:?\s*\n+/i, '')
				.slice(0, budget);
			const text = kind === 'outfit' ? visual : joinAppearance(before, visual, after);
			undoSource = { ...undoSource, [character.id]: 'images' };
			previousDescriptions = { ...previousDescriptions, [character.id]: current };
			descriptionDrafts = { ...descriptionDrafts, [character.id]: text };
			await saveField(character, { description: text });
			toast.success(`Appearance described from ${character.name}'s images.`);
		} catch (error) {
			toast.error(`The images could not be described: ${error}`);
		} finally {
			visionFor = null;
		}
	};

	const persistModelPreference = async (
		patch: { characterDescriptionModel: string } | { characterVisionModel: string }
	) => {
		savingModelPreference = true;
		try {
			settings.set({ ...$settings, ...patch });
			await updateUserSettings(localStorage.token, { ui: $settings });
		} catch (error) {
			toast.error(`The model preference could not be saved: ${error}`);
		} finally {
			savingModelPreference = false;
		}
	};

	// Selector does not dispatch a change event, so watch the value instead.
	$: if (loaded && descriptionModelId && descriptionModelId !== lastPersistedModel) {
		lastPersistedModel = descriptionModelId;
		void persistModelPreference({ characterDescriptionModel: descriptionModelId });
	}

	$: if (loaded && visionModelId && visionModelId !== lastPersistedVisionModel) {
		lastPersistedVisionModel = visionModelId;
		void persistModelPreference({ characterVisionModel: visionModelId });
	}

	const confirmDelete = (character: VideoCharacter) => {
		pendingDeletion = character;
		showDeleteConfirm = true;
	};

	const doDelete = async () => {
		const character = pendingDeletion;
		pendingDeletion = null;
		if (!character) return;
		busyId = character.id;
		try {
			await deleteVideoCharacter(localStorage.token, character.id);
			characters = characters.filter((c) => c.id !== character.id);
			toast.success(`${character.name} removed from your library.`);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const pickImage = (characterId: string) => {
		uploadTargetId = characterId;
		fileInput.click();
	};

	const handleImage = async (event: Event) => {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		const character = characters.find((c) => c.id === uploadTargetId);
		uploadTargetId = null;
		if (!file || !character) return;

		if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
			toast.error('Use a PNG, JPEG, or WebP image.');
			return;
		}
		if (character.image_file_ids.length >= MAX_IMAGES_PER_CHARACTER) {
			toast.error('Each character can have up to 3 reference images.');
			return;
		}

		busyId = character.id;
		try {
			const uploaded = await uploadFile(localStorage.token, file);
			if (!uploaded?.id) throw new Error('Upload failed');
			const updated = await updateVideoCharacter(localStorage.token, character.id, {
				image_file_ids: [...character.image_file_ids, uploaded.id]
			});
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const pickVoice = (characterId: string) => {
		voiceTargetId = characterId;
		voiceInput.click();
	};

	const handleVoice = async (event: Event) => {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		const character = characters.find((c) => c.id === voiceTargetId);
		voiceTargetId = null;
		if (!file || !character) return;

		// Browsers report m4a inconsistently, so fall back to the extension.
		const isAudio =
			AUDIO_TYPES.includes(file.type) || /\.(wav|mp3|m4a|ogg|flac|webm)$/i.test(file.name);
		if (!isAudio) {
			toast.error('Use a WAV, MP3, M4A, OGG, FLAC, or WebM audio file.');
			return;
		}
		if (file.size > 25 * 1024 * 1024) {
			toast.error('Voice reference must be under 25 MB.');
			return;
		}

		busyId = character.id;
		try {
			const uploaded = await uploadFile(localStorage.token, file);
			if (!uploaded?.id) throw new Error('Upload failed');
			const updated = await updateVideoCharacter(localStorage.token, character.id, {
				voice_file_id: uploaded.id
			});
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const removeVoice = async (character: VideoCharacter) => {
		busyId = character.id;
		try {
			const updated = await updateVideoCharacter(localStorage.token, character.id, {
				voice_file_id: ''
			});
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const removeImage = async (character: VideoCharacter, fileId: string) => {
		busyId = character.id;
		try {
			const updated = await updateVideoCharacter(localStorage.token, character.id, {
				image_file_ids: character.image_file_ids.filter((id) => id !== fileId)
			});
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	onMount(async () => {
		if (!canUseVideo) {
			await goto('/');
			return;
		}
		// A saved default wins, but only while that model still exists. Falling back to
		// the video prompt model means one choice to configure rather than two.
		const known = (id?: string) =>
			id && availableModels.some((model) => model.id === id) ? id : undefined;
		descriptionModelId =
			known($settings?.characterDescriptionModel) ??
			known($settings?.videoPromptModel) ??
			$settings?.models?.[0] ??
			$config?.default_models?.split(',')?.[0] ??
			availableModels[0]?.id ??
			'';
		lastPersistedModel = descriptionModelId;
		const knownVision = (id?: string) =>
			id && visionModels.some((model) => model.id === id) ? id : undefined;
		visionModelId =
			knownVision($settings?.characterVisionModel) ??
			knownVision(descriptionModelId) ??
			visionModels[0]?.id ??
			'';
		lastPersistedVisionModel = visionModelId;
		loaded = true;
		await load();
	});
</script>

<svelte:head>
	<title>Characters - {$WEBUI_NAME}</title>
</svelte:head>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title="Delete character"
	message={pendingDeletion
		? `Delete ${pendingDeletion.name}? This removes them from every chat they are attached to. The uploaded images stay in your files.`
		: ''}
	on:confirm={doDelete}
	on:cancel={() => (pendingDeletion = null)}
/>

<div
	class="flex h-screen max-h-[100dvh] w-full flex-col overflow-hidden transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''}"
>
	<nav
		class="flex h-12 shrink-0 items-center gap-2 border-b border-gray-100 px-3 dark:border-gray-850"
	>
		{#if $mobile}
			<Tooltip content={$showSidebar ? 'Close Sidebar' : 'Open Sidebar'}>
				<button
					class="rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-850"
					on:click={() => showSidebar.set(!$showSidebar)}
					aria-label="Toggle sidebar"
				>
					<Sidebar />
				</button>
			</Tooltip>
		{/if}
		<div class="flex items-center gap-2 font-medium">
			<span>Characters</span>
		</div>
		<div class="ml-auto text-xs text-gray-500">Reference video cast</div>
	</nav>

	{#if loaded}
		<div class="flex-1 overflow-y-auto">
			<div class="mx-auto flex w-full max-w-5xl flex-col gap-5 p-4 lg:p-6">
				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<h1 class="text-xl font-semibold">Your characters</h1>
					<p class="mt-1 text-sm text-gray-500">
						Reference images keep a character's face consistent across generated scenes. Attach them
						to a chat from that chat's Controls panel, then use "Generate video of this scene".
					</p>
					<div class="mt-4 flex gap-2">
						<input
							class="flex-1 rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none focus:border-gray-500 dark:border-gray-700"
							placeholder="New character name"
							bind:value={newName}
							on:keydown={(e) => e.key === 'Enter' && addCharacter()}
						/>
						<button
							class="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
							disabled={!newName.trim() || creating}
							on:click={addCharacter}
						>
							{creating ? 'Adding...' : 'Add character'}
						</button>
					</div>
					<div class="mt-3 flex flex-wrap items-center justify-end gap-2">
						<span class="text-xs text-gray-500">Description model</span>
						<div class="w-64 max-w-full">
							<Selector
								placeholder="Select a model"
								items={availableModels.map((model) => ({
									value: model.id,
									label: model.name,
									model
								}))}
								bind:value={descriptionModelId}
								className="w-full"
								triggerClassName="text-sm"
							/>
						</div>
						<span class="text-xs text-gray-500">Vision model</span>
						<div class="w-64 max-w-full">
							<Selector
								placeholder="Select a model"
								items={visionModels.map((model) => ({
									value: model.id,
									label: model.name,
									model
								}))}
								bind:value={visionModelId}
								className="w-full"
								triggerClassName="text-sm"
							/>
						</div>
						{#if savingModelPreference}
							<span class="text-xs text-gray-400">saving...</span>
						{/if}
					</div>
				</section>

				{#if !loading && visibleCharacters.length > 0}
					<div class="flex flex-wrap items-center gap-2">
						<input
							type="search"
							class="min-w-0 flex-1 basis-48 rounded-xl border border-gray-200 bg-transparent px-3 py-1.5 text-sm outline-none focus:border-gray-500 dark:border-gray-700"
							placeholder="Search names and descriptions"
							aria-label="Search characters"
							bind:value={search}
						/>
						<div
							class="flex shrink-0 rounded-xl border border-gray-200 p-0.5 text-xs dark:border-gray-700"
							role="group"
							aria-label="Filter by type"
						>
							{#each [{ value: 'all', label: 'All' }, ...KINDS] as k}
								{@const count =
									k.value === 'all' ? viewCharacters.length : (kindCounts[k.value] ?? 0)}
								<button
									class="rounded-lg px-2.5 py-1 {kindFilter === k.value
										? 'bg-gray-100 font-medium dark:bg-gray-800'
										: 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100'}"
									aria-pressed={kindFilter === k.value}
									on:click={() => (kindFilter = k.value)}
								>
									{k.value === 'all' ? k.label : `${k.label}s`}
									<span class="text-gray-400">{count}</span>
								</button>
							{/each}
						</div>
						<select
							class="shrink-0 rounded-xl border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-700"
							aria-label="Sort"
							bind:value={sortOrder}
						>
							{#each SORTS as s}
								<option value={s.value}>{s.label}</option>
							{/each}
						</select>
						{#if archivedCount > 0}
							<button
								class="shrink-0 rounded-xl border px-2.5 py-1.5 text-xs {showArchived
									? 'border-gray-500 bg-gray-100 font-medium dark:bg-gray-800'
									: 'border-gray-200 text-gray-500 hover:text-gray-900 dark:border-gray-700 dark:hover:text-gray-100'}"
								aria-pressed={showArchived}
								on:click={() => (showArchived = !showArchived)}
							>
								Archived <span class="text-gray-400">{archivedCount}</span>
							</button>
						{/if}
					</div>
					{#if showArchived}
						<p class="-mt-2 text-xs text-gray-500">
							Archived entries are left out of the chat character pickers. Any chat they are
							already in keeps them.
							<button class="underline" on:click={() => (showArchived = false)}
								>Back to library</button
							>
						</p>
					{/if}
				{/if}

				{#if loading}
					<div class="flex justify-center py-10"><Spinner className="size-5" /></div>
				{:else if shownCharacters.length === 0}
					<p
						class="rounded-2xl border border-gray-200 py-12 text-center text-sm text-gray-500 dark:border-gray-800"
					>
						{#if characters.length === 0}
							No characters yet. Add one above, then give them a description and reference images.
						{:else if filtered && viewCharacters.length > 0}
							Nothing matches.
							<button class="font-medium underline" on:click={clearFilters}>Clear filters</button>
						{:else if !showArchived && archivedCount > 0}
							Everything here is archived.
							<button class="font-medium underline" on:click={() => (showArchived = true)}
								>Show archived</button
							>
						{:else}
							No characters to show.
						{/if}
					</p>
				{:else}
					{#if filtered}
						<p class="-mt-2 text-xs text-gray-500">
							Showing {shownCharacters.length} of {viewCharacters.length}.
							<button class="underline" on:click={clearFilters}>Clear</button>
						</p>
					{/if}
					<div class="grid gap-4 lg:grid-cols-2">
						{#each shownCharacters as character (character.id)}
							{@const draft = descriptionDrafts[character.id] ?? character.description}
							{@const over = draft.length - MAX_DESCRIPTION}
							<article class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
								<div class="flex items-center gap-2">
									<input
										class="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-1 py-0.5 font-semibold outline-none hover:border-gray-200 focus:border-gray-500 dark:hover:border-gray-700"
										value={character.name}
										on:blur={(e) => saveField(character, { name: e.currentTarget.value })}
									/>
									{#if busyId === character.id}
										<Spinner className="size-4" />
									{/if}
									<button
										class="shrink-0 text-xs text-gray-500 hover:underline disabled:opacity-40 disabled:no-underline"
										disabled={busyId === character.id}
										on:click={() => duplicateCharacter(character)}>Duplicate</button
									>
									<button
										class="shrink-0 text-xs text-gray-500 hover:underline disabled:opacity-40 disabled:no-underline"
										disabled={busyId === character.id}
										title={character.archived
											? 'Put back in the library and the chat pickers'
											: 'Hide from the library and the chat pickers; chats already using it are unaffected'}
										on:click={() => setArchived(character, !character.archived)}
										>{character.archived ? 'Restore' : 'Archive'}</button
									>
									<button
										class="shrink-0 text-xs text-gray-500 hover:underline"
										on:click={() => confirmDelete(character)}>Delete</button
									>
								</div>

								<div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
									<select
										class="rounded-lg border border-gray-200 bg-transparent px-2 py-1 text-xs dark:border-gray-700"
										value={character.kind ?? 'character'}
										on:change={(e) => saveMeta(character, { kind: e.currentTarget.value })}
									>
										{#each KINDS as k}
											<option value={k.value}>{k.label}</option>
										{/each}
									</select>
									<label
										class="flex items-center gap-1 text-gray-500"
										title="Hidden while work mode is on in Settings"
									>
										<input
											type="checkbox"
											class="size-3.5 accent-gray-700"
											checked={character.private}
											disabled={busyId === character.id}
											on:change={(e) => saveMeta(character, { private: e.currentTarget.checked })}
										/>
										Private
									</label>
									{#if (character.kind ?? 'character') === 'outfit'}
										<span class="text-gray-400"
											>Choose it for any character in a chat's character panel.</span
										>
									{:else if (character.kind ?? 'character') === 'location'}
										<span class="text-gray-400">Sets the scene in chats it is attached to.</span>
									{/if}
									{#if character.created_at}
										<span class="ml-auto text-gray-400">Added {addedOn(character.created_at)}</span>
									{/if}
								</div>

								<textarea
									class="mt-2 min-h-20 w-full resize-y rounded-xl border bg-transparent p-2.5 text-sm outline-none {over >
									0
										? 'border-red-400 focus:border-red-500 dark:border-red-500'
										: 'border-gray-200 focus:border-gray-500 dark:border-gray-700'}"
									placeholder="Appearance: apparent age, build, skin tone, hair, clothing, distinguishing features."
									value={draft}
									on:input={(e) => (descriptionDrafts[character.id] = e.currentTarget.value)}
									on:blur={(e) => saveField(character, { description: e.currentTarget.value })}
								></textarea>
								<div
									class="mt-1 text-right text-xs tabular-nums {over > 0
										? 'font-medium text-red-500'
										: over > -200
											? 'text-amber-600'
											: 'text-gray-500'}"
								>
									{draft.length}/{MAX_DESCRIPTION}{over > 0 ? ` — ${over} over` : ''}
								</div>

								<div class="mt-2 rounded-xl border border-gray-200 p-2.5 dark:border-gray-700">
									<div class="flex items-center gap-2">
										<input
											class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-xs outline-none focus:border-gray-500 dark:border-gray-700"
											placeholder={seedPlaceholder(character.kind ?? 'character')}
											value={seedPrompts[character.id] ?? ''}
											on:input={(e) => (seedPrompts[character.id] = e.currentTarget.value)}
											on:keydown={(e) => e.key === 'Enter' && describeCharacter(character)}
										/>
										<button
											class="shrink-0 rounded-lg bg-black px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-black"
											disabled={generatingFor === character.id ||
												visionFor === character.id ||
												!(seedPrompts[character.id] ?? '').trim() ||
												!descriptionModelId}
											on:click={() => describeCharacter(character)}
										>
											{generatingFor === character.id ? 'Writing...' : 'Generate'}
										</button>
									</div>
									<div class="mt-1 flex items-center gap-2 text-[11px] text-gray-500">
										<span class="min-w-0 flex-1"
											>Expands a short brief into a full description, replacing what is above.</span
										>
										{#if previousDescriptions[character.id] !== undefined && undoSource[character.id] === 'brief'}
											<button
												class="shrink-0 hover:underline"
												on:click={() => undoDescription(character)}>Undo</button
											>
										{/if}
									</div>
								</div>

								<div class="mt-2 flex flex-wrap gap-2">
									{#each character.image_file_ids as fileId (fileId)}
										<div
											class="relative size-20 overflow-hidden rounded-xl bg-gray-50 dark:bg-gray-900"
										>
											<img
												src={imageUrl(fileId)}
												alt={character.name}
												class="size-full object-cover"
											/>
											<button
												class="absolute right-0 top-0 bg-black/60 px-1.5 py-0.5 text-xs leading-none text-white"
												aria-label="Remove image"
												on:click={() => removeImage(character, fileId)}>&times;</button
											>
										</div>
									{/each}
									{#if character.image_file_ids.length < MAX_IMAGES_PER_CHARACTER}
										<button
											class="size-20 rounded-xl border border-dashed border-gray-300 text-2xl text-gray-400 hover:border-gray-500 dark:border-gray-700"
											aria-label="Add reference image"
											on:click={() => pickImage(character.id)}>+</button
										>
									{/if}
								</div>
								{#if character.image_file_ids.length > 0}
									<div class="mt-2 flex flex-wrap items-center gap-2">
										<button
											class="shrink-0 rounded-lg border border-gray-200 px-2.5 py-1 text-xs font-medium hover:border-gray-500 disabled:opacity-40 dark:border-gray-700"
											disabled={visionFor === character.id ||
												generatingFor === character.id ||
												busyId === character.id ||
												!visionModelId}
											on:click={() => describeFromImages(character)}
										>
											{visionFor === character.id ? 'Looking...' : 'Describe from images'}
										</button>
										<span class="min-w-0 flex-1 text-[11px] text-gray-500">
											{(character.kind ?? 'character') === 'outfit'
												? 'Writes the description from these images, replacing what is above.'
												: 'Writes the Appearance part from these images and keeps the rest.'}
										</span>
										{#if previousDescriptions[character.id] !== undefined && undoSource[character.id] === 'images'}
											<button
												class="shrink-0 text-[11px] text-gray-500 hover:underline"
												on:click={() => undoDescription(character)}>Undo</button
											>
										{/if}
									</div>
								{/if}
								<!-- Outfits never speak; the section stays only to remove a voice set before the kind changed. -->
								{#if (character.kind ?? 'character') !== 'outfit' || character.voice_file_id}
									<div
										class="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3 dark:border-gray-850"
									>
										<span class="text-xs font-medium">Voice</span>
										{#if character.voice_file_id}
											<!-- svelte-ignore a11y-media-has-caption -->
											<audio
												class="h-8 max-w-[15rem] flex-1"
												controls
												src={imageUrl(character.voice_file_id)}
											></audio>
											<button
												class="text-xs text-gray-500 hover:underline"
												on:click={() => removeVoice(character)}>Remove</button
											>
										{:else}
											<button
												class="rounded-lg border border-dashed border-gray-300 px-2.5 py-1 text-xs text-gray-500 hover:border-gray-500 dark:border-gray-700"
												on:click={() => pickVoice(character.id)}>Add voice reference</button
											>
											<span class="text-xs text-gray-400"
												>Optional. A few seconds of clean speech.</span
											>
										{/if}
									</div>
								{/if}
								<p class="mt-2 text-xs text-gray-500">
									{character.image_file_ids.length}/{MAX_IMAGES_PER_CHARACTER} reference images.
									{#if character.image_file_ids.length === 0}
										Without images this character is ignored by reference video.
									{/if}
								</p>
							</article>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{:else}
		<div class="flex flex-1 items-center justify-center text-sm text-gray-500">
			Loading characters...
		</div>
	{/if}
</div>

<input
	class="hidden"
	type="file"
	accept="image/png,image/jpeg,image/webp"
	bind:this={fileInput}
	on:change={handleImage}
/>

<input
	class="hidden"
	type="file"
	accept="audio/*,.wav,.mp3,.m4a,.ogg,.flac,.webm"
	bind:this={voiceInput}
	on:change={handleVoice}
/>
