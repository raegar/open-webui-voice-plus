<script lang="ts">
	// A spoiler covered until clicked. Off when `spoiler` is false, so callers can use
	// it for every item and let the data decide which ones hide.
	export let spoiler = true;
	let revealed = false;
</script>

{#if spoiler && !revealed}
	<button
		class="blur-sm hover:blur-[3px] select-none text-left"
		title="Spoiler: click to reveal"
		on:click={() => (revealed = true)}><slot /></button
	>
{:else}
	<slot />
{/if}
