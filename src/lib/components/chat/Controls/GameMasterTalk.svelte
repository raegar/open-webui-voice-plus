<script lang="ts">
	import { getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		talkToGameMaster,
		type GameMasterStatus,
		type GameMasterTalk
	} from '$lib/apis/gamemaster';
	import { gameMasterTalkRequest } from '$lib/stores';

	const i18n = getContext('i18n');

	export let chatId: string;
	export let talk: GameMasterTalk[] = [];
	export let onStatus: (status: GameMasterStatus) => void = () => {};

	let draft = '';
	let sending: string | null = null;
	let thread: HTMLDivElement;

	const scrollDown = async () => {
		await tick();
		if (thread) thread.scrollTop = thread.scrollHeight;
	};

	$: if (talk) void scrollDown();

	const send = async (text: string) => {
		const message = text.trim();
		if (!message || sending) return;
		sending = message;
		draft = '';
		await scrollDown();
		try {
			const result = await talkToGameMaster(localStorage.token, chatId, message);
			onStatus(result.status);
		} catch (error) {
			toast.error(`${error}`);
			// Give the words back rather than losing them.
			if (!draft) draft = message;
		} finally {
			sending = null;
			await scrollDown();
		}
	};

	// A "/gm ..." typed in the chat box lands here.
	$: if ($gameMasterTalkRequest !== null && chatId) {
		const request = $gameMasterTalkRequest;
		gameMasterTalkRequest.set(null);
		void send(request);
	}

	const keydown = (event: KeyboardEvent) => {
		if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
			event.preventDefault();
			void send(draft);
		}
	};
</script>

<div class="flex flex-col gap-1.5">
	<div class="text-gray-500">
		{$i18n.t(
			'Talk to the GM out of character: ask where things are going, suggest ideas, or correct it. None of this enters the story. You can also type /gm in the chat box.'
		)}
	</div>

	{#if talk.length || sending}
		<div bind:this={thread} class="flex flex-col gap-2 max-h-72 overflow-y-auto pr-1">
			{#each talk as exchange (exchange.id)}
				<div
					class="self-end max-w-[85%] rounded-lg bg-gray-100 dark:bg-gray-800 px-2 py-1 whitespace-pre-wrap"
				>
					{exchange.player}
				</div>
				<div class="self-start max-w-[92%] whitespace-pre-wrap">
					<span class="text-gray-500">{$i18n.t('GM')}:</span>
					{exchange.gm}
				</div>
			{/each}
			{#if sending}
				<div
					class="self-end max-w-[85%] rounded-lg bg-gray-100 dark:bg-gray-800 px-2 py-1 whitespace-pre-wrap"
				>
					{sending}
				</div>
				<div class="self-start text-gray-500 animate-pulse">{$i18n.t('The GM is thinking…')}</div>
			{/if}
		</div>
	{/if}

	<div class="flex items-end gap-1.5">
		<textarea
			bind:value={draft}
			on:keydown={keydown}
			class="flex-1 outline-hidden resize-none py-1 px-2 rounded-lg bg-gray-50 dark:bg-gray-850"
			rows="2"
			placeholder={$i18n.t('e.g. Could a rival for the ledger turn up soon?')}
			disabled={!!sending}
		></textarea>
		<button
			class="px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-50"
			disabled={!!sending || !draft.trim()}
			on:click={() => send(draft)}
		>
			{$i18n.t('Send')}
		</button>
	</div>
</div>
