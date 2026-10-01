<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		regenerateNpcPortrait,
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

	const draw = async (npc: any) => {
		drawing = npc.id;
		try {
			onStatus(await regenerateNpcPortrait(localStorage.token, chatId, npc.id));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			drawing = '';
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
					{#if canDraw && npc.look}
						<button
							class="mt-0.5 text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 disabled:opacity-50"
							disabled={drawing === npc.id ||
								portrait?.status === 'queued' ||
								portrait?.status === 'running'}
							on:click={() => draw(npc)}
						>
							{portrait ? $i18n.t('Regenerate portrait') : $i18n.t('Draw portrait')}
						</button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{:else}
	<div class="text-gray-500">{$i18n.t('No NPCs have appeared yet.')}</div>
{/if}
