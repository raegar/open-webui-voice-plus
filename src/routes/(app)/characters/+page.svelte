<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import {
		createVideoCharacter,
		deleteVideoCharacter,
		getVideoCharacterLibrary,
		updateVideoCharacter,
		type VideoCharacter
	} from '$lib/apis/videos';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { config, mobile, showSidebar, user, WEBUI_NAME } from '$lib/stores';

	const MAX_IMAGES_PER_CHARACTER = 3;
	// Mirrors the max_length on VideoCharacterForm; exceeding it is a 422 from the API.
	const MAX_DESCRIPTION = 4000;
	const MAX_NAME = 200;

	let loaded = false;
	let loading = false;
	let characters: VideoCharacter[] = [];
	let newName = '';
	let creating = false;
	let busyId: string | null = null;
	let fileInput: HTMLInputElement;
	let uploadTargetId: string | null = null;
	let descriptionDrafts: Record<string, string> = {};
	let pendingDeletion: VideoCharacter | null = null;
	let showDeleteConfirm = false;

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
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			creating = false;
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
				</section>

				{#if loading}
					<div class="flex justify-center py-10"><Spinner className="size-5" /></div>
				{:else if characters.length === 0}
					<p
						class="rounded-2xl border border-gray-200 py-12 text-center text-sm text-gray-500 dark:border-gray-800"
					>
						No characters yet. Add one above, then give them a description and reference images.
					</p>
				{:else}
					<div class="grid gap-4 lg:grid-cols-2">
						{#each characters as character (character.id)}
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
										class="shrink-0 text-xs text-gray-500 hover:underline"
										on:click={() => confirmDelete(character)}>Delete</button
									>
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
