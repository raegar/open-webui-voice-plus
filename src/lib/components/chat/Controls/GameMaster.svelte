<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		consultGameMaster,
		getGameMaster,
		updateGameMaster,
		type GameMasterIntensity,
		type GameMasterStatus
	} from '$lib/apis/gamemaster';
	import {
		getChatVideoCharacters,
		getVideoCharacterLibrary,
		type VideoCharacter
	} from '$lib/apis/videos';
	import { gameMasterVersion, pendingChatCharacterIds, pendingGameMaster } from '$lib/stores';

	const i18n = getContext('i18n');

	export let chatId: string | null = null;

	const POLL_MS = 3000;

	let status: GameMasterStatus | null = null;
	let characters: VideoCharacter[] = [];
	let loadedChatId: string | null | undefined = undefined;
	let seenVersion = 0;
	let saving = false;
	let consulting = false;
	let agenda = '';
	// Polling must not overwrite what is being typed.
	let editingAgenda = false;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;

	// Spoilers stay covered until clicked, and cover again when a new pass lands.
	let revealedNoteFor = '';
	let revealedReasoningFor = '';

	$: hasChat = !!chatId && !chatId.startsWith('local:');
	$: pending = $pendingGameMaster;
	$: enabled = hasChat ? !!status?.enabled : !!pending?.enabled;
	$: intensity = (hasChat ? status?.config?.intensity : pending?.intensity) ?? 'firm';
	$: playerId =
		(hasChat ? status?.config?.player_character_id : pending?.player_character_id) ?? '';
	$: current = status?.current ?? null;
	$: people = characters.filter((c) => (c.kind ?? 'character') === 'character');

	const loadCharacters = async () => {
		try {
			if (hasChat && chatId) {
				characters = await getChatVideoCharacters(localStorage.token, chatId);
			} else {
				const library = await getVideoCharacterLibrary(localStorage.token);
				characters = library.filter((c) => $pendingChatCharacterIds.includes(c.id));
			}
		} catch (error) {
			console.error(error);
		}
	};

	const schedulePoll = () => {
		if (pollTimer) clearTimeout(pollTimer);
		pollTimer = null;
		if (status?.running) pollTimer = setTimeout(() => void refresh(), POLL_MS);
	};

	const refresh = async () => {
		if (!hasChat || !chatId) {
			status = null;
			return;
		}
		try {
			status = await getGameMaster(localStorage.token, chatId);
			if (!editingAgenda) agenda = status.config.agenda ?? '';
		} catch (error) {
			console.error(error);
		}
		schedulePoll();
	};

	$: if (chatId !== loadedChatId) {
		loadedChatId = chatId;
		agenda = pending?.agenda ?? '';
		void refresh();
		void loadCharacters();
	}

	$: if ($gameMasterVersion !== seenVersion) {
		seenVersion = $gameMasterVersion;
		void refresh();
	}

	onDestroy(() => {
		if (pollTimer) clearTimeout(pollTimer);
	});

	const change = async (changes: {
		enabled?: boolean;
		intensity?: GameMasterIntensity;
		agenda?: string;
		player_character_id?: string;
	}) => {
		if (!hasChat || !chatId) {
			// Before the first message there is no chat to save to; initChatHandler
			// applies this the moment the chat is created.
			pendingGameMaster.set({
				enabled: pending?.enabled ?? false,
				intensity: pending?.intensity ?? 'firm',
				agenda: pending?.agenda ?? '',
				player_character_id: pending?.player_character_id ?? '',
				...changes
			});
			return;
		}
		saving = true;
		try {
			status = await updateGameMaster(localStorage.token, chatId, changes);
			schedulePoll();
		} catch (error) {
			toast.error(`${error}`);
			await refresh();
		} finally {
			saving = false;
		}
	};

	const saveAgenda = () => {
		editingAgenda = false;
		const saved = hasChat ? (status?.config?.agenda ?? '') : (pending?.agenda ?? '');
		if (agenda !== saved) void change({ agenda });
	};

	const consult = async () => {
		if (!chatId) return;
		consulting = true;
		try {
			status = await consultGameMaster(localStorage.token, chatId);
			schedulePoll();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			consulting = false;
		}
	};

	const ago = (ms: number) => {
		const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
		if (seconds < 60) return $i18n.t('just now');
		const minutes = Math.round(seconds / 60);
		if (minutes < 60) return `${minutes} min ago`;
		const hours = Math.round(minutes / 60);
		if (hours < 48) return `${hours} h ago`;
		return new Date(ms).toLocaleDateString();
	};
</script>

<div class="flex flex-col gap-2 text-xs">
	<label class="flex items-center gap-2 py-0.5">
		<input
			type="checkbox"
			class="size-3.5 accent-gray-700"
			checked={enabled}
			disabled={saving}
			on:change={(e) => change({ enabled: e.currentTarget.checked })}
		/>
		<span>{$i18n.t('Game Master')}</span>
		{#if enabled && status?.running}
			<span class="ml-auto text-gray-500 animate-pulse">{$i18n.t('Planning…')}</span>
		{/if}
	</label>
	<div class="text-gray-500">
		{$i18n.t(
			'A hidden game master plans the story, runs NPCs and briefs the characters after each reply, so they hold to their own goals.'
		)}
	</div>

	{#if enabled}
		<div class="flex items-center gap-2">
			<span class="w-24 shrink-0 text-gray-500">{$i18n.t('Intensity')}</span>
			<select
				class="flex-1 bg-transparent outline-hidden py-0.5"
				value={intensity}
				on:change={(e) => change({ intensity: e.currentTarget.value as GameMasterIntensity })}
			>
				<option value="light">{$i18n.t('Light: consistent characters')}</option>
				<option value="firm">{$i18n.t('Firm: characters hold their ground')}</option>
				<option value="ruthless">{$i18n.t('Ruthless: the world pushes back')}</option>
			</select>
		</div>

		<div class="flex items-center gap-2">
			<span class="w-24 shrink-0 text-gray-500">{$i18n.t('Played by me')}</span>
			<select
				class="flex-1 bg-transparent outline-hidden py-0.5"
				value={playerId}
				on:focus={loadCharacters}
				on:change={(e) => change({ player_character_id: e.currentTarget.value })}
			>
				<option value="">{$i18n.t('Nobody marked (the GM infers it)')}</option>
				{#each people as character (character.id)}
					<option value={character.id}>{character.name}</option>
				{/each}
			</select>
		</div>

		<div>
			<div class="text-gray-500 mb-0.5">{$i18n.t('Agenda for the GM')}</div>
			<textarea
				bind:value={agenda}
				on:focus={() => (editingAgenda = true)}
				on:blur={saveAgenda}
				class="w-full outline-hidden resize-vertical py-1 bg-transparent"
				rows="3"
				placeholder={$i18n.t('e.g. A slow-burn betrayal. Sam should never fully trust Alex.')}
			></textarea>
		</div>

		{#if hasChat}
			<div class="flex items-center gap-2 text-gray-500">
				{#if status?.running}
					<span>{$i18n.t('The GM is planning the next reply…')}</span>
				{:else if current}
					<span>
						{$i18n.t('Direction ready')} · {ago(current.created_at)}
						{#if current.tokens}· {current.tokens.toLocaleString()} {$i18n.t('tokens')}{/if}
						{#if current.duration_ms}· {(current.duration_ms / 1000).toFixed(1)}s{/if}
					</span>
				{:else}
					<span>{$i18n.t('No direction yet. It is written after the next reply.')}</span>
				{/if}
				<button
					class="ml-auto px-2 py-0.5 rounded-lg bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-50"
					disabled={consulting || status?.running}
					on:click={consult}
				>
					{$i18n.t('Consult now')}
				</button>
			</div>

			{#if status?.last_error}
				<div class="text-red-600 dark:text-red-400">
					{$i18n.t('Last GM pass failed')} ({ago(status.last_error.created_at)}): {status.last_error
						.error}
				</div>
			{/if}

			{#if current}
				<div>
					<div class="text-gray-500 mb-0.5">{$i18n.t('Next direction')}</div>
					{#if revealedNoteFor === current.id}
						<button
							class="w-full text-left whitespace-pre-wrap rounded-lg bg-gray-50 dark:bg-gray-850 p-2"
							on:click={() => (revealedNoteFor = '')}
						>
							{current.note || $i18n.t('(no note this turn: hold your ground)')}
						</button>
					{:else}
						<button
							class="w-full rounded-lg bg-gray-50 dark:bg-gray-850 p-2 text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
							on:click={() => (revealedNoteFor = current?.id ?? '')}
						>
							{$i18n.t('Hidden: contains spoilers. Click to reveal.')}
						</button>
					{/if}
				</div>

				<div>
					<div class="text-gray-500 mb-0.5">{$i18n.t("The GM's thinking")}</div>
					{#if revealedReasoningFor === current.id}
						<div class="rounded-lg bg-gray-50 dark:bg-gray-850 p-2 flex flex-col gap-1.5">
							{#if current.observations?.length}
								<ul class="list-disc pl-4">
									{#each current.observations as observation}
										<li>{observation}</li>
									{/each}
								</ul>
							{/if}
							<div class="whitespace-pre-wrap">{current.reasoning}</div>
							{#if current.model_reasoning}
								<details>
									<summary class="cursor-pointer text-gray-500"
										>{$i18n.t("Model's own reasoning")}</summary
									>
									<div class="whitespace-pre-wrap text-gray-500 mt-1">
										{current.model_reasoning}
									</div>
								</details>
							{/if}
							<button
								class="self-start text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
								on:click={() => (revealedReasoningFor = '')}>{$i18n.t('Hide')}</button
							>
						</div>
					{:else}
						<button
							class="w-full rounded-lg bg-gray-50 dark:bg-gray-850 p-2 text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
							on:click={() => (revealedReasoningFor = current?.id ?? '')}
						>
							{$i18n.t('Hidden: contains spoilers. Click to reveal.')}
						</button>
					{/if}
				</div>
			{/if}
		{:else}
			<div class="text-gray-500">
				{$i18n.t('The GM starts planning as soon as the first message is sent.')}
			</div>
		{/if}
	{/if}
</div>
