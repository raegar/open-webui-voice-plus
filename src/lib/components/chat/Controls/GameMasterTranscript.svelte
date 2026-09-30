<script lang="ts">
	import { getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { getGameMasterJournal, type GameMasterEntry } from '$lib/apis/gamemaster';
	import { gameMasterTranscript } from '$lib/stores';

	const i18n = getContext('i18n');

	export let chatId: string | null = null;
	export let history: any = undefined;

	const KIND_LABELS: Record<string, string> = {
		setup: 'Session zero',
		turn: 'After a reply',
		consult: 'Consulted',
		reroll: 'Rerolled',
		table_talk: 'Table talk'
	};

	let entries: GameMasterEntry[] = [];
	let loading = false;
	let loadedFor = '';
	// Everything that could spoil the story stays covered until clicked.
	let revealed = new Set<string>();

	// Modal closes itself (click outside, Escape) by clearing its bound flag, so the
	// flag follows the store and a self-close clears the store.
	let show = false;
	$: show = $gameMasterTranscript !== null;
	$: if (!show && $gameMasterTranscript !== null) gameMasterTranscript.set(null);
	$: target = $gameMasterTranscript?.entryId ?? '';
	$: if (show && chatId && loadedFor !== `${chatId}:${target}`) {
		loadedFor = `${chatId}:${target}`;
		void load(chatId, target);
	}
	$: if (!show) loadedFor = '';

	const load = async (id: string, scrollTo: string) => {
		loading = true;
		try {
			// The API returns newest first; the transcript reads as a story, oldest first.
			entries = (await getGameMasterJournal(localStorage.token, id, 300)).reverse();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
		if (scrollTo) {
			await tick();
			document.getElementById(`gm-entry-${scrollTo}`)?.scrollIntoView({ block: 'start' });
		}
	};

	const close = () => gameMasterTranscript.set(null);
	const reveal = (key: string) => {
		revealed.add(key);
		revealed = revealed;
	};
	const hide = (key: string) => {
		revealed.delete(key);
		revealed = revealed;
	};

	const snippet = (messageId: string): string => {
		const content = history?.messages?.[messageId]?.content;
		if (typeof content !== 'string') return '';
		const text = content
			.replace(/<details[\s\S]*?<\/details>/gi, '')
			.replace(/<think>[\s\S]*?<\/think>/gi, '')
			.replace(/\s+/g, ' ')
			.trim();
		return text.length > 110 ? `${text.slice(0, 110)}…` : text;
	};

	const when = (ms: number) => new Date(ms).toLocaleString();
</script>

<Modal size="2xl" bind:show>
	<div class="px-5 pt-4 pb-5 dark:text-gray-200 text-sm">
		<div class="flex items-center justify-between mb-1">
			<div class="text-lg font-medium">{$i18n.t('Game Master transcript')}</div>
			<button aria-label={$i18n.t('Close')} on:click={close}>
				<XMark className="size-5" />
			</button>
		</div>
		<div class="text-xs text-gray-500 mb-3">
			{$i18n.t(
				'Every pass the GM has made in this chat, oldest first. Anything that could spoil the story is covered until you click it.'
			)}
		</div>

		{#if loading}
			<div class="text-gray-500 py-6 text-center">{$i18n.t('Loading…')}</div>
		{:else if entries.length === 0}
			<div class="text-gray-500 py-6 text-center">{$i18n.t('The GM has not made a pass yet.')}</div>
		{:else}
			<div class="flex flex-col gap-3 max-h-[70vh] overflow-y-auto pr-1">
				{#each entries as entry, index (entry.id)}
					<div
						id="gm-entry-{entry.id}"
						class="rounded-xl border p-3 {entry.id === target
							? 'border-blue-400 dark:border-blue-500'
							: 'border-gray-100 dark:border-gray-800'}"
					>
						<div class="flex flex-wrap items-baseline gap-x-2 text-xs text-gray-500">
							<span class="font-medium text-gray-800 dark:text-gray-100">
								#{index + 1} · {$i18n.t(KIND_LABELS[entry.kind] ?? entry.kind)}
							</span>
							<span>{when(entry.created_at)}</span>
							{#if entry.tokens}<span>· {entry.tokens.toLocaleString()} tokens</span>{/if}
							{#if entry.duration_ms}<span>· {(entry.duration_ms / 1000).toFixed(1)}s</span>{/if}
						</div>
						{#if entry.kind !== 'table_talk' && snippet(entry.message_id)}
							<div class="text-xs text-gray-500 mt-0.5 italic">
								{$i18n.t('After')}: “{snippet(entry.message_id)}”
							</div>
						{/if}

						{#if entry.kind === 'table_talk'}
							<div class="mt-2 flex flex-col gap-1.5">
								<div><span class="text-gray-500">{$i18n.t('You')}:</span> {entry.user_message}</div>
								{#if entry.gm_reply}
									<div class="whitespace-pre-wrap">
										<span class="text-gray-500">{$i18n.t('GM')}:</span>
										{entry.gm_reply}
									</div>
								{/if}
							</div>
						{/if}

						{#if entry.error}
							<div class="mt-2 text-red-600 dark:text-red-400 text-xs">{entry.error}</div>
						{/if}

						{#if entry.reasoning || entry.note || entry.patch?.changes?.length || entry.observations?.length}
							{#if revealed.has(entry.id)}
								<div class="mt-2 flex flex-col gap-2">
									{#if entry.observations?.length}
										<div>
											<div class="text-xs text-gray-500">{$i18n.t('Noticed')}</div>
											<ul class="list-disc pl-5">
												{#each entry.observations as observation}
													<li>{observation}</li>
												{/each}
											</ul>
										</div>
									{/if}
									{#if entry.patch?.changes?.length}
										<div>
											<div class="text-xs text-gray-500">{$i18n.t('Changed')}</div>
											<ul class="list-disc pl-5">
												{#each entry.patch.changes as change, i}
													{@const key = `${entry.id}:${i}`}
													<li>
														{#if change.spoiler && !revealed.has(key)}
															<button
																class="blur-sm hover:blur-[3px] select-none text-left"
																title={$i18n.t('Spoiler: click to reveal')}
																on:click={() => reveal(key)}>{change.text}</button
															>
														{:else}
															{change.text}
														{/if}
													</li>
												{/each}
											</ul>
										</div>
									{/if}
									{#if entry.note}
										<div>
											<div class="text-xs text-gray-500">{$i18n.t('Direction issued')}</div>
											<div class="whitespace-pre-wrap rounded-lg bg-gray-50 dark:bg-gray-850 p-2">
												{entry.note}
											</div>
										</div>
									{/if}
									{#if entry.reasoning}
										<div>
											<div class="text-xs text-gray-500">{$i18n.t("The GM's thinking")}</div>
											<div class="whitespace-pre-wrap">{entry.reasoning}</div>
										</div>
									{/if}
									{#if entry.model_reasoning}
										<details>
											<summary class="cursor-pointer text-xs text-gray-500"
												>{$i18n.t("Model's own reasoning")}</summary
											>
											<div class="whitespace-pre-wrap text-gray-500 mt-1">
												{entry.model_reasoning}
											</div>
										</details>
									{/if}
									<button
										class="self-start text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
										on:click={() => hide(entry.id)}>{$i18n.t('Cover again')}</button
									>
								</div>
							{:else}
								<button
									class="mt-2 w-full rounded-lg bg-gray-50 dark:bg-gray-850 p-2 text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
									on:click={() => reveal(entry.id)}
								>
									{entry.observations?.length
										? $i18n.t(
												'{{count}} findings, the plan and the direction. Click to reveal spoilers.',
												{
													count: entry.observations.length
												}
											)
										: $i18n.t('The plan and the direction. Click to reveal spoilers.')}
								</button>
							{/if}
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
</Modal>
