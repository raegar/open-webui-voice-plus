<script lang="ts">
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { onMount, tick, getContext } from 'svelte';

	const i18n = getContext('i18n');

	export let followUps: string[] = [];
	export let onClick: (followUp: string) => void = () => {};

	// A truncated follow-up expands on the first click and is sent on the second,
	// so the full prompt can be read before it goes out.
	let expandedIdx: number | null = null;
	let textElements: HTMLElement[] = [];

	$: followUps, (expandedIdx = null);

	const isTruncated = (el: HTMLElement | undefined) =>
		!!el && el.scrollHeight > el.clientHeight + 1;

	const clickHandler = (idx: number, followUp: string) => {
		if (expandedIdx !== idx && isTruncated(textElements[idx])) {
			expandedIdx = idx;
			return;
		}

		expandedIdx = null;
		onClick(followUp);
	};
</script>

<div class="mt-4">
	<div class="text-sm font-medium">
		{$i18n.t('Follow up')}
	</div>

	<div class="flex flex-col text-left gap-1 mt-1.5">
		{#each followUps as followUp, idx (idx)}
			<Tooltip
				content={expandedIdx === idx ? '' : followUp}
				placement="top-start"
				className={expandedIdx === idx ? '' : 'line-clamp-1'}
				touch={false}
			>
				<button
					class=" py-1.5 bg-transparent text-left text-sm flex flex-col items-start gap-0.5 {expandedIdx ===
					idx
						? 'text-black dark:text-white'
						: 'text-gray-500 dark:text-gray-400'} hover:text-black dark:hover:text-white transition cursor-pointer w-full"
					on:click={() => clickHandler(idx, followUp)}
					aria-label={$i18n.t('Follow up: {{question}}', { question: followUp })}
				>
					<div
						bind:this={textElements[idx]}
						class={expandedIdx === idx ? 'whitespace-pre-wrap break-words' : 'line-clamp-1'}
					>
						{followUp}
					</div>

					{#if expandedIdx === idx}
						<div class="text-xs text-gray-500 dark:text-gray-400">
							{$i18n.t('Click again to send')}
						</div>
					{/if}
				</button>
			</Tooltip>

			{#if idx < followUps.length - 1}
				<hr class="border-gray-50 dark:border-gray-850/30" />
			{/if}
		{/each}
	</div>
</div>
