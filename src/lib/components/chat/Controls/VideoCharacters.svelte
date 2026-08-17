<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		attachVideoCharacter,
		detachVideoCharacter,
		getChatVideoCharacters,
		getVideoCharacterLibrary,
		type VideoCharacter
	} from '$lib/apis/videos';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
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

	$: attached = library.filter((c) => attachedIds.includes(c.id));
	$: usedImages = attached.reduce((sum, c) => sum + c.image_file_ids.length, 0);
	$: usedVoices = attached.filter((c) => c.voice_file_id).length;

	const imageUrl = (fileId: string) => `${WEBUI_API_BASE_URL}/files/${fileId}/content`;

	const load = async (id: string) => {
		loading = true;
		try {
			const [lib, chatCharacters] = await Promise.all([
				getVideoCharacterLibrary(localStorage.token),
				getChatVideoCharacters(localStorage.token, id)
			]);
			library = lib;
			attachedIds = chatCharacters.map((c) => c.id);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	// Reload when the pane opens against a different chat.
	$: if (chatId && chatId !== loadedChatId) {
		loadedChatId = chatId;
		void load(chatId);
	}

	const toggle = async (character: VideoCharacter) => {
		if (!chatId) return;
		const isAttached = attachedIds.includes(character.id);

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
	{#if !chatId}
		<div class="text-xs text-gray-500">
			{$i18n.t('Send a message first, then attach characters to this chat.')}
		</div>
	{:else}
		<div class="text-xs text-gray-500">
			{$i18n.t(
				'Attached characters make scenes use reference video. With none attached, scenes use text-to-video.'
			)}
		</div>

		{#if loading}
			<div class="flex justify-center py-3"><Spinner className="size-4" /></div>
		{:else if library.length === 0}
			<div class="text-xs text-gray-500">
				{$i18n.t('No characters yet.')}
				<a href="/characters" class="font-medium underline">{$i18n.t('Create one')}</a>
			</div>
		{:else}
			{#each library as character (character.id)}
				{@const isAttached = attachedIds.includes(character.id)}
				<button
					class="flex items-center gap-2 rounded-lg border p-1.5 text-left transition {isAttached
						? 'border-gray-300 bg-gray-50 dark:border-gray-600 dark:bg-gray-850'
						: 'border-gray-100 dark:border-gray-850'}"
					disabled={busyId === character.id || character.image_file_ids.length === 0}
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
								{$i18n.t('images')}{character.voice_file_id ? ` · ${$i18n.t('voice')}` : ''}
							{/if}
						</div>
					</div>
					{#if busyId === character.id}
						<Spinner className="size-3" />
					{:else}
						<span class="shrink-0 text-xs text-gray-500">
							{isAttached ? $i18n.t('Attached') : $i18n.t('Add')}
						</span>
					{/if}
				</button>
			{/each}

			<div class="flex items-center justify-between text-[11px] text-gray-500">
				<span
					>{usedImages}/{MAX_REFERENCE_IMAGES}
					{$i18n.t('images')} · {usedVoices}/{MAX_REFERENCE_AUDIOS}
					{$i18n.t('voices')}</span
				>
				<a href="/characters" class="underline">{$i18n.t('Manage')}</a>
			</div>
		{/if}
	{/if}
</div>
