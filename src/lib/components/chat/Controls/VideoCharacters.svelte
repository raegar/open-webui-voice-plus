<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		attachVideoCharacter,
		detachVideoCharacter,
		getChatVideoCharacters,
		getVideoCharacterLibrary,
		setVideoCharacterState,
		type VideoCharacter
	} from '$lib/apis/videos';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { characterStateVersion, pendingChatCharacterIds, settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: any = getContext('i18n');

	export let chatId: string | null = null;

	// Ref2VA accepts at most 9 images in total, addressed as <Picture 1>..<Picture N>.
	const MAX_REFERENCE_IMAGES = 9;
	// Standalone ref_audios are capped at 3, far tighter than the image budget.
	const MAX_REFERENCE_AUDIOS = 3;

	let library: VideoCharacter[] = [];
	let attachedIds: string[] = [];
	let loading = false;
	let busyId: string | null = null;
	let loadedChatId: string | null = null;
	// Per-chat state of dress, keyed by character id. Held separately from the
	// library entry, whose description is the character's default look.
	let states: Record<string, string> = {};
	let savingState: string | null = null;
	let seenStateVersion = 0;
	$: autoTrack = ($settings as any)?.autoTrackCharacterState !== false;

	// Before the first message a chat has no id, so the selection is buffered in a
	// store and flushed by initChatHandler the moment the chat is created.
	$: selectedIds = chatId ? attachedIds : $pendingChatCharacterIds;
	$: attached = library.filter((c) => selectedIds.includes(c.id));
	$: usedImages = attached.reduce((sum, c) => sum + c.image_file_ids.length, 0);
	$: usedVoices = attached.filter((c) => c.voice_file_id).length;
	// Split the image budget so a location is not mistaken for cast headroom.
	$: usedByPeople = attached
		.filter((c) => (c.kind ?? 'character') === 'character')
		.reduce((s, c) => s + c.image_file_ids.length, 0);
	$: usedByRefs = usedImages - usedByPeople;

	const imageUrl = (fileId: string) => `${WEBUI_API_BASE_URL}/files/${fileId}/content`;

	const load = async (id: string | null) => {
		loading = true;
		try {
			const [lib, chatCharacters] = await Promise.all([
				getVideoCharacterLibrary(localStorage.token),
				id ? getChatVideoCharacters(localStorage.token, id) : Promise.resolve([])
			]);
			library = lib;
			attachedIds = chatCharacters.map((c) => c.id);
			states = Object.fromEntries(chatCharacters.map((c) => [c.id, c.state ?? '']));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	// Reload when the pane opens against a different chat, and load the library even
	// when there is no chat yet so characters can be picked before the first message.
	$: if (chatId !== loadedChatId) {
		loadedChatId = chatId;
		void load(chatId);
	}

	// An automatic update bumps the version; reload so the fields show it.
	$: if ($characterStateVersion !== seenStateVersion) {
		seenStateVersion = $characterStateVersion;
		if (chatId) void load(chatId);
	}

	const setAutoTrack = async (enabled: boolean) => {
		try {
			settings.set({ ...($settings as any), autoTrackCharacterState: enabled });
			await updateUserSettings(localStorage.token, { ui: $settings });
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const saveState = async (characterId: string, value: string) => {
		if (!chatId || value === (states[characterId] ?? '')) return;
		savingState = characterId;
		try {
			await setVideoCharacterState(localStorage.token, chatId, characterId, value);
			states = { ...states, [characterId]: value };
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			savingState = null;
		}
	};

	const toggle = async (character: VideoCharacter) => {
		const isAttached = selectedIds.includes(character.id);

		// Refuse an attach that would exceed what Ref2VA can accept.
		if (!isAttached && usedImages + character.image_file_ids.length > MAX_REFERENCE_IMAGES) {
			toast.error($i18n.t('Reference video supports up to 9 images. Detach someone else first.'));
			return;
		}
		if (!isAttached && character.voice_file_id && usedVoices >= MAX_REFERENCE_AUDIOS) {
			toast.error(
				$i18n.t('Only 3 voiced characters fit in one scene. Detach a voiced character first.')
			);
			return;
		}

		// No chat yet: buffer the choice instead of calling an API with no chat id.
		if (!chatId) {
			pendingChatCharacterIds.update((ids) =>
				isAttached ? ids.filter((id) => id !== character.id) : [...ids, character.id]
			);
			return;
		}

		busyId = character.id;
		try {
			if (isAttached) {
				await detachVideoCharacter(localStorage.token, chatId, character.id);
				attachedIds = attachedIds.filter((id) => id !== character.id);
			} else {
				await attachVideoCharacter(localStorage.token, chatId, character.id);
				attachedIds = [...attachedIds, character.id];
			}
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};
</script>

<div class="mt-1.5 flex flex-col gap-2">
	<div class="text-xs text-gray-500">
		{$i18n.t(
			'Attached profiles reinforce the AI personality on every reply and guide reference video scenes.'
		)}
	</div>
	{#if !chatId && $pendingChatCharacterIds.length > 0}
		<div class="text-[11px] text-gray-500">
			{$i18n.t('Applied as soon as you send the first message.')}
		</div>
	{/if}

	{#if loading}
		<div class="flex justify-center py-3"><Spinner className="size-4" /></div>
	{:else if library.length === 0}
		<div class="text-xs text-gray-500">
			{$i18n.t('No characters yet.')}
			<a href="/characters" class="font-medium underline">{$i18n.t('Create one')}</a>
		</div>
	{:else}
		{#each library as character (character.id)}
			{@const isAttached = selectedIds.includes(character.id)}
			<button
				class="flex items-center gap-2 rounded-lg border p-1.5 text-left transition {isAttached
					? 'border-gray-300 bg-gray-50 dark:border-gray-600 dark:bg-gray-850'
					: 'border-gray-100 dark:border-gray-850'}"
				disabled={busyId === character.id}
				on:click={() => toggle(character)}
			>
				{#if character.image_file_ids.length > 0}
					<img
						src={imageUrl(character.image_file_ids[0])}
						alt={character.name}
						class="size-8 shrink-0 rounded-md object-cover"
					/>
				{:else}
					<div class="size-8 shrink-0 rounded-md bg-gray-100 dark:bg-gray-900"></div>
				{/if}
				<div class="min-w-0 flex-1">
					<div class="truncate text-xs font-medium">{character.name}</div>
					<div class="truncate text-[11px] text-gray-500">
						{#if character.image_file_ids.length === 0}
							{$i18n.t('No reference images')}
						{:else}
							{character.image_file_ids.length}
							{$i18n.t('images')}{character.voice_file_id
								? ` · ${$i18n.t('voice')}`
								: ''}{(character.kind ?? 'character') !== 'character' ? ` · ${character.kind}` : ''}
						{/if}
					</div>
				</div>
				{#if busyId === character.id}
					<Spinner className="size-3" />
				{:else}
					<span class="shrink-0 text-xs text-gray-500">
						{isAttached ? (chatId ? $i18n.t('Attached') : $i18n.t('Pending')) : $i18n.t('Add')}
					</span>
				{/if}
			</button>
			{#if isAttached && (character.kind ?? 'character') === 'character'}
				<input
					class="-mt-1 w-full rounded-b-lg border border-t-0 border-gray-100 bg-transparent px-2 py-1 text-[11px] outline-none focus:border-gray-400 dark:border-gray-850"
					placeholder={$i18n.t('Currently wearing… (kept for this chat)')}
					value={states[character.id] ?? ''}
					disabled={savingState === character.id}
					on:blur={(e) => saveState(character.id, e.currentTarget.value)}
				/>
			{/if}
		{/each}

		<div class="flex items-center justify-between text-[11px] text-gray-500">
			<span
				>{usedImages}/{MAX_REFERENCE_IMAGES}
				{$i18n.t('images')}{usedByRefs > 0 ? ` (${usedByPeople} cast, ${usedByRefs} scene)` : ''}
				· {usedVoices}/{MAX_REFERENCE_AUDIOS}
				{$i18n.t('voices')}</span
			>
			<a href="/characters" class="underline">{$i18n.t('Manage')}</a>
		</div>
		<label class="flex items-center gap-1.5 text-[11px] text-gray-500">
			<input
				type="checkbox"
				class="size-3 accent-gray-700"
				checked={autoTrack}
				on:change={(e) => setAutoTrack(e.currentTarget.checked)}
			/>
			{$i18n.t('Track what they are wearing automatically')}
		</label>
	{/if}
</div>
