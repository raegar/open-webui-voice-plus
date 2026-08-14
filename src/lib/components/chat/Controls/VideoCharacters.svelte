<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import {
		createVideoCharacter,
		deleteVideoCharacter,
		getVideoCharacters,
		updateVideoCharacter,
		type VideoCharacter
	} from '$lib/apis/videos';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: any = getContext('i18n');

	export let chatId: string | null = null;

	// Ref2VA accepts at most 9 images in total, addressed as <Picture 1>..<Picture N>.
	const MAX_IMAGES_PER_CHARACTER = 3;
	const MAX_REFERENCE_IMAGES = 9;

	let characters: VideoCharacter[] = [];
	let loading = false;
	let busyId: string | null = null;
	let addingName = '';
	let fileInput: HTMLInputElement;
	let uploadTargetId: string | null = null;

	$: totalImages = characters.reduce((sum, c) => sum + c.image_file_ids.length, 0);

	const imageUrl = (fileId: string) => `${WEBUI_API_BASE_URL}/files/${fileId}/content`;

	const load = async () => {
		if (!chatId) {
			characters = [];
			return;
		}
		loading = true;
		try {
			characters = await getVideoCharacters(localStorage.token, chatId);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	// Reload whenever the pane is opened against a different chat.
	$: if (chatId) void load();

	const addCharacter = async () => {
		const name = addingName.trim();
		if (!name || !chatId) return;
		busyId = 'new';
		try {
			const created = await createVideoCharacter(localStorage.token, {
				chat_id: chatId,
				name,
				description: '',
				image_file_ids: []
			});
			characters = [...characters, created];
			addingName = '';
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busyId = null;
		}
	};

	const saveDescription = async (character: VideoCharacter, description: string) => {
		if (description === character.description) return;
		try {
			const updated = await updateVideoCharacter(localStorage.token, character.id, {
				description
			});
			characters = characters.map((c) => (c.id === updated.id ? updated : c));
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const removeCharacter = async (character: VideoCharacter) => {
		busyId = character.id;
		try {
			await deleteVideoCharacter(localStorage.token, character.id);
			characters = characters.filter((c) => c.id !== character.id);
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
			toast.error($i18n.t('Use a PNG, JPEG, or WebP image.'));
			return;
		}
		if (character.image_file_ids.length >= MAX_IMAGES_PER_CHARACTER) {
			toast.error($i18n.t('Each character can have up to 3 reference images.'));
			return;
		}
		if (totalImages >= MAX_REFERENCE_IMAGES) {
			toast.error($i18n.t('Reference video supports up to 9 images across all characters.'));
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

	onMount(load);
</script>

<div class="flex flex-col gap-2 mt-1.5">
	{#if !chatId}
		<div class="text-xs text-gray-500">
			{$i18n.t('Send a message first, then add characters to this chat.')}
		</div>
	{:else}
		<div class="text-xs text-gray-500">
			{$i18n.t(
				'Characters give reference video consistent faces. With none set, scenes use text-to-video.'
			)}
		</div>

		{#if loading}
			<div class="flex justify-center py-3"><Spinner className="size-4" /></div>
		{/if}

		{#each characters as character, index (character.id)}
			<div class="rounded-lg border border-gray-100 dark:border-gray-850 p-2">
				<div class="flex items-center gap-2">
					<div class="text-sm font-medium truncate">{character.name}</div>
					{#if busyId === character.id}
						<Spinner className="size-3" />
					{/if}
					<button
						class="ml-auto text-xs text-gray-500 hover:underline shrink-0"
						on:click={() => removeCharacter(character)}
					>
						{$i18n.t('Remove')}
					</button>
				</div>

				<textarea
					class="w-full mt-1.5 text-xs bg-transparent rounded-lg border border-gray-100 dark:border-gray-850 p-2 outline-none resize-y min-h-14"
					placeholder={$i18n.t('Appearance: age, build, hair, clothing, distinguishing features')}
					value={character.description}
					on:blur={(e) => saveDescription(character, e.currentTarget.value)}
				></textarea>

				<div class="flex flex-wrap gap-1.5 mt-1.5">
					{#each character.image_file_ids as fileId (fileId)}
						<div class="relative size-12 rounded-lg overflow-hidden bg-gray-50 dark:bg-gray-900">
							<img src={imageUrl(fileId)} alt={character.name} class="size-full object-cover" />
							<button
								class="absolute top-0 right-0 bg-black/60 text-white text-[10px] leading-none px-1 py-0.5"
								aria-label={$i18n.t('Remove image')}
								on:click={() => removeImage(character, fileId)}>&times;</button
							>
						</div>
					{/each}
					{#if character.image_file_ids.length < MAX_IMAGES_PER_CHARACTER && totalImages < MAX_REFERENCE_IMAGES}
						<button
							class="size-12 rounded-lg border border-dashed border-gray-300 dark:border-gray-700 text-lg text-gray-400 hover:border-gray-500"
							aria-label={$i18n.t('Add reference image')}
							on:click={() => pickImage(character.id)}>+</button
						>
					{/if}
				</div>
			</div>
		{/each}

		<div class="flex gap-1.5">
			<input
				class="flex-1 text-xs bg-transparent rounded-lg border border-gray-100 dark:border-gray-850 px-2 py-1.5 outline-none"
				placeholder={$i18n.t('Character name')}
				bind:value={addingName}
				on:keydown={(e) => e.key === 'Enter' && addCharacter()}
			/>
			<button
				class="text-xs px-2.5 py-1.5 rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-40"
				disabled={!addingName.trim() || busyId === 'new'}
				on:click={addCharacter}>{$i18n.t('Add')}</button
			>
		</div>

		{#if totalImages >= MAX_REFERENCE_IMAGES}
			<div class="text-xs text-amber-600">
				{$i18n.t('Image limit reached (9 across all characters).')}
			</div>
		{/if}
	{/if}
</div>

<input
	class="hidden"
	type="file"
	accept="image/png,image/jpeg,image/webp"
	bind:this={fileInput}
	on:change={handleImage}
/>
