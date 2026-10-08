<script lang="ts">
	import { fade } from 'svelte/transition';

	export let token;
	export let done = true;

	// Keep the same word elements when a streamed reply finishes, so the browser
	// does not lose its reading/scroll anchor. Loaded replies can stay plain text.
	const startedStreaming = !done;
	let texts = [];
	$: texts = (token?.raw ?? '').match(/\S+\s*|\s+/g) ?? [];
</script>

{#if startedStreaming}
	{#each texts as text}
		<span in:fade={{ duration: done ? 0 : 100 }}>{text}</span>
	{/each}
{:else}
	{token?.raw}
{/if}
