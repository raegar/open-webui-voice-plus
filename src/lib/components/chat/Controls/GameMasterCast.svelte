<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import {
		regenerateNpcPortrait,
		uploadNpcPortrait,
		type GameMasterPortrait,
		type GameMasterStatus
	} from '$lib/apis/gamemaster';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	export let chatId: string;
	export let state: Record<string, any> | null = null;
	export let portraits: GameMasterPortrait[] = [];
	export let canDraw = false;
	export let onStatus: (status: GameMasterStatus) => void = () => {};

	const STATUS_LABELS: Record<string, string> = {
		entering: 'Arriving next reply',
		on_stage: 'In the scene',
		off_stage: 'Off stage',
		gone: 'Gone'
	};

	let drawing = '';

	// NPCs still waiting in the wings are the GM's secret, so only the ones the story
	// has already met appear here.
	$: npcs = ((state?.npcs ?? []) as any[]).filter((npc) => npc?.name && npc.status !== 'planned');
	$: byId = new Map(portraits.map((portrait) => [portrait.npc_id, portrait]));

	const portraitOf = (npc: any): GameMasterPortrait | null => {
		const portrait = byId.get(npc.id);
		return portrait && portrait.name === npc.name ? portrait : null;
	};

	// The NPC whose redraw box is open, and what the player wants changed.
	let redrawing = '';
	let direction = '';

	const draw = async (npc: any) => {
		drawing = npc.id;
		try {
			onStatus(await regenerateNpcPortrait(localStorage.token, chatId, npc.id, direction.trim()));
			redrawing = '';
			direction = '';
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			drawing = '';
		}
	};

	const openRedraw = (npc: any) => {
		redrawing = redrawing === npc.id ? '' : npc.id;
		direction = '';
	};

	// The player's own picture in place of the generated one. It sticks: automatic
	// portraits never draw over it, and Video Studio uses it as the NPC's reference.
	const UPLOAD_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
	const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
	let uploading = '';

	const uploadPortrait = async (npc: any, input: HTMLInputElement) => {
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		if (!UPLOAD_TYPES.includes(file.type)) {
			toast.error($i18n.t('Use a PNG, JPEG or WebP image.'));
			return;
		}
		if (file.size > MAX_UPLOAD_BYTES) {
			toast.error($i18n.t('That image is over 25 MB.'));
			return;
		}
		uploading = npc.id;
		try {
			const uploaded = await uploadFile(localStorage.token, file);
			if (!uploaded?.id) throw new Error('Upload failed');
			onStatus(await uploadNpcPortrait(localStorage.token, chatId, npc.id, uploaded.id));
			redrawing = '';
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			uploading = '';
		}
	};
</script>

{#if npcs.length}
	<div class="flex flex-col gap-2">
		{#each npcs as npc (npc.id)}
			{@const portrait = portraitOf(npc)}
			<div class="flex gap-2">
				<div
					class="w-14 h-20 shrink-0 rounded-lg overflow-hidden bg-gray-100 dark:bg-gray-850 flex items-center justify-center text-[10px] text-gray-500 text-center"
				>
					{#if portrait?.status === 'ready' && portrait.file_id}
						<a
							href="{WEBUI_API_BASE_URL}/files/{portrait.file_id}/content"
							target="_blank"
							rel="noopener"
						>
							<img
								src="{WEBUI_API_BASE_URL}/files/{portrait.file_id}/content"
								alt={npc.name}
								class="w-full h-full object-cover"
							/>
						</a>
					{:else if portrait?.status === 'queued' || portrait?.status === 'running'}
						<span class="animate-pulse">{$i18n.t('Drawing…')}</span>
					{:else if portrait?.status === 'failed'}
						<span title={portrait.error}>{$i18n.t('Failed')}</span>
					{:else}
						<span>{$i18n.t('No portrait')}</span>
					{/if}
				</div>
				<div class="flex-1 min-w-0">
					<div class="flex items-baseline gap-1.5">
						<span class="font-medium">{npc.name}</span>
						<span class="text-gray-500">{$i18n.t(STATUS_LABELS[npc.status] ?? npc.status)}</span>
					</div>
					{#if npc.card}<div>{npc.card}</div>{/if}
					{#if npc.look}<div class="text-gray-500">{npc.look}</div>{/if}
					{#if portrait?.source === 'uploaded' && portrait.status === 'ready'}
						<div class="text-gray-500">{$i18n.t('Using your uploaded picture')}</div>
					{/if}
					<label
						class="mt-0.5 mr-2 inline-block cursor-pointer text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 {uploading ===
						npc.id
							? 'pointer-events-none opacity-50'
							: ''}"
					>
						{uploading === npc.id ? $i18n.t('Uploading…') : $i18n.t('Upload image…')}
						<input
							type="file"
							accept={UPLOAD_TYPES.join(',')}
							class="hidden"
							disabled={uploading === npc.id}
							on:change={(e) => uploadPortrait(npc, e.currentTarget)}
						/>
					</label>
					{#if canDraw && npc.look}
						<button
							class="mt-0.5 text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-50"
							disabled={drawing === npc.id ||
								portrait?.status === 'queued' ||
								portrait?.status === 'running'}
							on:click={() => openRedraw(npc)}
						>
							{portrait ? $i18n.t('Regenerate portrait…') : $i18n.t('Draw portrait…')}
						</button>
						{#if redrawing === npc.id}
							<div class="mt-1 flex items-center gap-1.5">
								<input
									class="flex-1 min-w-0 rounded-lg bg-gray-50 dark:bg-gray-850 px-2 py-1 outline-hidden"
									placeholder={$i18n.t('What should change? (optional)')}
									bind:value={direction}
									on:keydown={(e) => e.key === 'Enter' && draw(npc)}
								/>
								<button
									class="px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-50"
									disabled={drawing === npc.id}
									on:click={() => draw(npc)}>{$i18n.t('Draw')}</button
								>
							</div>
						{/if}
					{/if}
					{#if portrait?.prompt && portrait.source !== 'uploaded'}
						<details class="mt-0.5">
							<summary class="cursor-pointer text-gray-500">{$i18n.t('Prompt used')}</summary>
							<div class="text-gray-500 mt-0.5">{portrait.prompt}</div>
						</details>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{:else}
	<div class="text-gray-500">{$i18n.t('No NPCs have appeared yet.')}</div>
{/if}
