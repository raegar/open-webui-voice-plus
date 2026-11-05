<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { getGameMaster, planGameMasterArc, type GameMasterStatus } from '$lib/apis/gamemaster';
	import { gameMasterPlanOpen, gameMasterVersion } from '$lib/stores';
	import Spoiler from './GameMasterSpoiler.svelte';

	const i18n = getContext('i18n');

	export let chatId: string | null = null;

	const POLL_MS = 3000;
	const MILESTONE_MARKS: Record<string, string> = {
		reached: '✓',
		current: '▶',
		ahead: '·',
		dropped: '✕'
	};

	let status: GameMasterStatus | null = null;
	let loading = false;
	let planning = false;
	// The whole plan is one big spoiler: covered until the player chooses to look.
	let revealed = false;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let seenVersion = 0;

	// Modal closes itself by clearing its bound flag; mirror that into the store.
	let show = false;
	$: show = $gameMasterPlanOpen;
	$: if (!show && $gameMasterPlanOpen) gameMasterPlanOpen.set(false);
	$: if (!show) {
		revealed = false;
		if (pollTimer) clearTimeout(pollTimer);
	}
	$: if (show && chatId) void load();
	$: if (show && $gameMasterVersion !== seenVersion) {
		seenVersion = $gameMasterVersion;
		void load();
	}

	$: plan = status?.current?.state ?? null;
	$: arc = plan?.arc ?? null;
	$: characters = Object.entries((plan?.characters ?? {}) as Record<string, any>);
	$: npcs = ((plan?.npcs ?? []) as any[]).filter((npc) => npc?.name);
	$: requests = (plan?.player_requests ?? []) as any[];
	$: directives = (plan?.player_directives ?? []) as string[];

	const load = async () => {
		if (!chatId || chatId.startsWith('local:')) return;
		loading = !status;
		try {
			status = await getGameMaster(localStorage.token, chatId);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
		if (pollTimer) clearTimeout(pollTimer);
		if (show && status?.running) pollTimer = setTimeout(() => void load(), POLL_MS);
	};

	const planArc = async () => {
		if (!chatId) return;
		planning = true;
		try {
			status = await planGameMasterArc(localStorage.token, chatId);
			if (pollTimer) clearTimeout(pollTimer);
			pollTimer = setTimeout(() => void load(), POLL_MS);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			planning = false;
		}
	};

	const close = () => gameMasterPlanOpen.set(false);
	const when = (ms: number) => new Date(ms).toLocaleString();
	const label = (value: string) => (value ?? '').replace(/_/g, ' ');
</script>

<Modal size="2xl" bind:show>
	<div class="px-5 pt-4 pb-5 dark:text-gray-200 text-sm">
		<div class="flex items-center justify-between mb-1">
			<div class="text-lg font-medium">{$i18n.t("The GM's plan")}</div>
			<button aria-label={$i18n.t('Close')} on:click={close}>
				<XMark className="size-5" />
			</button>
		</div>
		<div class="text-xs text-gray-500 mb-3">
			{$i18n.t(
				'Where the Game Master thinks the story is going, as of its latest pass. The biggest spoilers stay blurred even after you reveal the plan; click one to see it.'
			)}
			{#if status?.current}· {when(status.current.created_at)}{/if}
		</div>

		{#if loading}
			<div class="text-gray-500 py-6 text-center">{$i18n.t('Loading…')}</div>
		{:else if !plan}
			<div class="text-gray-500 py-6 text-center">
				{status?.running
					? $i18n.t('The GM is working on its first plan…')
					: $i18n.t('The GM has no plan for this chat yet.')}
			</div>
		{:else}
			{#if !arc?.destination}
				<div
					class="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 p-3 dark:border-gray-800"
				>
					<div class="flex-1 min-w-48">
						<div class="font-medium">{$i18n.t('No story arc yet')}</div>
						<div class="text-xs text-gray-500">
							{$i18n.t(
								'This plan only looks a beat ahead. The GM can work out where the story is heading from what has happened so far, and fill in a thin plan while it is at it.'
							)}
						</div>
					</div>
					<button
						class="rounded-lg bg-gray-900 px-3 py-1.5 text-white hover:bg-gray-800 disabled:opacity-60 dark:bg-white dark:text-gray-900"
						disabled={planning || status?.running}
						on:click={planArc}
					>
						{status?.running ? $i18n.t('The GM is planning…') : $i18n.t('Plan the story arc')}
					</button>
				</div>
			{/if}

			{#if !revealed}
				<button
					class="w-full rounded-xl bg-gray-50 p-6 text-gray-500 hover:text-gray-800 dark:bg-gray-850 dark:hover:text-gray-200"
					on:click={() => (revealed = true)}
				>
					{$i18n.t('The whole plan is a spoiler. Click to reveal it.')}
				</button>
			{:else}
				<div class="flex flex-col gap-4 max-h-[65vh] overflow-y-auto pr-1">
					{#if plan.premise}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('What it is about')}
							</div>
							<div><Spoiler>{plan.premise}</Spoiler></div>
						</section>
					{/if}

					{#if arc?.destination}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('Where it is heading')}
							</div>
							<div class="font-medium"><Spoiler>{arc.destination}</Spoiler></div>
							{#if arc.milestones?.length}
								<ol class="mt-2 flex flex-col gap-1">
									{#each arc.milestones as milestone (milestone.id)}
										<li
											class="flex gap-2 {milestone.status === 'dropped'
												? 'line-through opacity-60'
												: ''}"
										>
											<span
												class="w-4 shrink-0 text-center {milestone.status === 'current'
													? 'text-blue-600 dark:text-blue-400'
													: 'text-gray-500'}">{MILESTONE_MARKS[milestone.status] ?? '·'}</span
											>
											<span class="flex-1">
												<Spoiler spoiler={milestone.status === 'ahead'}>{milestone.beat}</Spoiler>
												{#if milestone.status === 'current'}
													<span class="text-xs text-blue-600 dark:text-blue-400">
														· {$i18n.t('working toward this now')}</span
													>
												{/if}
											</span>
										</li>
									{/each}
								</ol>
							{/if}
							{#if arc.twists?.length}
								<div class="mt-2 flex flex-col gap-1">
									{#each arc.twists as twist (twist.id)}
										<div class="text-gray-600 dark:text-gray-300">
											<span class="text-xs text-gray-500"
												>{$i18n.t('Twist')} ({label(twist.status)}):</span
											>
											<Spoiler spoiler={twist.status !== 'revealed'}>
												{twist.reveal}{#if twist.seeding}
													<span class="text-gray-500">
														— {$i18n.t('seeded by')} {twist.seeding}</span
													>{/if}
											</Spoiler>
										</div>
									{/each}
								</div>
							{/if}
						</section>
					{/if}

					{#if plan.threads?.length}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('Threads')}
							</div>
							<div class="flex flex-col gap-1.5">
								{#each plan.threads as thread (thread.id)}
									<div>
										<Spoiler spoiler={!!thread.gm_only}>
											<span class="font-medium">{thread.title}</span>
											<span class="text-xs text-gray-500">· {label(thread.status)}</span>
											{#if thread.next_beat}
												<div class="text-gray-600 dark:text-gray-300">
													{$i18n.t('Next')}: {thread.next_beat}
												</div>
											{/if}
										</Spoiler>
									</div>
								{/each}
							</div>
						</section>
					{/if}

					{#if plan.clocks?.length}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('Pressure')}
							</div>
							<div class="flex flex-col gap-1.5">
								{#each plan.clocks as clock (clock.id)}
									<div>
										<div class="flex items-center gap-2">
											<span>{clock.label}</span>
											<span class="flex gap-0.5">
												{#each Array(clock.size ?? 6) as _, i}
													<span
														class="inline-block size-2.5 rounded-sm {i < (clock.filled ?? 0)
															? 'bg-red-500'
															: 'bg-gray-200 dark:bg-gray-700'}"
													></span>
												{/each}
											</span>
											<span class="text-xs text-gray-500"
												>{clock.filled ?? 0}/{clock.size ?? 6}</span
											>
										</div>
										{#if clock.on_full}
											<div class="text-xs text-gray-500">
												{$i18n.t('When full')}: <Spoiler>{clock.on_full}</Spoiler>
											</div>
										{/if}
									</div>
								{/each}
							</div>
						</section>
					{/if}

					{#if characters.length}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('What drives them')}
							</div>
							<div class="flex flex-col gap-2">
								{#each characters as [name, character] (name)}
									<div>
										<div class="font-medium">{name}</div>
										{#each [['Wants', 'want'], ['Needs', 'need'], ['Fears', 'fear'], ["Won't", 'line']] as [title, key]}
											{#if character?.[key]}
												<div>
													<span class="text-gray-500">{$i18n.t(title)}:</span>
													{character[key]}
												</div>
											{/if}
										{/each}
										{#each Object.entries(character?.stance ?? {}) as [issue, stance]}
											<div>
												<span class="text-gray-500">{$i18n.t('On')} {issue}:</span>
												{stance?.position ?? ''}{#if stance?.price}<span class="text-gray-500">
														({$i18n.t('moves only if')} {stance.price})</span
													>{/if}
											</div>
										{/each}
									</div>
								{/each}
							</div>
						</section>
					{/if}

					{#if npcs.length}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('NPCs')}
							</div>
							<div class="flex flex-col gap-1">
								{#each npcs as npc (npc.id)}
									<div>
										<Spoiler spoiler={npc.status === 'planned'}>
											<span class="font-medium">{npc.name}</span>
											<span class="text-xs text-gray-500"
												>· {npc.status === 'planned'
													? $i18n.t('waiting in the wings')
													: label(npc.status)}</span
											>
											{#if npc.card}<span class="text-gray-600 dark:text-gray-300">
													— {npc.card}</span
												>{/if}
											{#if npc.want}<div class="text-gray-500">
													{$i18n.t('Wants')}: {npc.want}
												</div>{/if}
										</Spoiler>
									</div>
								{/each}
							</div>
						</section>
					{/if}

					{#if plan.secrets?.length}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('Secrets')}
							</div>
							<div class="flex flex-col gap-1">
								{#each plan.secrets as secret (secret.id)}
									<div>
										<Spoiler>{secret.text}</Spoiler>
										<span class="text-xs text-gray-500">
											· {$i18n.t('known by')}
											{(secret.known_by ?? []).join(', ') || $i18n.t('nobody yet')}</span
										>
									</div>
								{/each}
							</div>
						</section>
					{/if}

					{#if requests.length || directives.length}
						<section>
							<div class="text-xs uppercase tracking-wide text-gray-500 mb-1">
								{$i18n.t('From you')}
							</div>
							{#each requests as item (item.id)}
								<div>
									{item.text} <span class="text-xs text-gray-500">· {label(item.status)}</span>
									{#if item.plan}<div class="text-gray-500">{item.plan}</div>{/if}
								</div>
							{/each}
							{#each directives as directive}
								<div>{$i18n.t('Rule')}: {directive}</div>
							{/each}
						</section>
					{/if}

					{#if plan.tension}
						<div class="text-xs text-gray-500">
							{$i18n.t('Tension')}: {plan.tension.current ?? '?'} / 10 · {$i18n.t('aiming for')}
							{plan.tension.target ?? '?'}
						</div>
					{/if}
				</div>
			{/if}
		{/if}
	</div>
</Modal>
