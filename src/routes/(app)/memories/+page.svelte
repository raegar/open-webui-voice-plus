<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteMemories,
		getMemorySummary,
		searchMemories,
		type Memory,
		type MemoryFilters,
		type MemorySummary
	} from '$lib/apis/memorybrowser';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import { mobile, settings, showSidebar, user, WEBUI_NAME } from '$lib/stores';

	const PAGE_SIZE = 50;
	// Long memories are clipped until expanded, so a page stays scannable.
	const CLIP = 600;
	const SEARCH_DELAY_MS = 300;

	let summary: MemorySummary | null = null;
	let unavailable = '';
	let items: Memory[] = [];
	let total = 0;
	let loading = false;
	let deleting = false;

	let q = '';
	let role: MemoryFilters['role'] = '';
	let source: MemoryFilters['source'] = '';
	let since = '';
	let until = '';
	let order: 'newest' | 'oldest' = 'newest';
	let conversationId = '';
	let offset = 0;

	let selected = new Set<string>();
	let expanded = new Set<string>();
	let pendingDelete: string[] = [];
	let showConfirm = false;

	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	let requestSerial = 0;

	$: isAdmin = $user?.role === 'admin';
	$: workMode = !!$settings?.hidePrivate;
	$: pageIds = items.map((item) => item.message_id);
	$: allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
	$: filtered = !!(q.trim() || role || source || since || until || conversationId);

	const load = async () => {
		if (!isAdmin || workMode) return;
		const serial = ++requestSerial;
		loading = true;
		try {
			const result = await searchMemories(localStorage.token, {
				q: q.trim(),
				role,
				source,
				since,
				until,
				conversation_id: conversationId,
				order,
				offset,
				limit: PAGE_SIZE
			});
			// A slower earlier search must not overwrite a newer one.
			if (serial !== requestSerial) return;
			items = result.items;
			total = result.total;
			// Selection only spans what is on screen, so nothing hidden gets deleted.
			selected = new Set(
				[...selected].filter((id) => result.items.some((i) => i.message_id === id))
			);
		} catch (error) {
			if (serial === requestSerial) toast.error(`${error}`);
		} finally {
			if (serial === requestSerial) loading = false;
		}
	};

	const refreshSummary = async () => {
		try {
			summary = await getMemorySummary(localStorage.token);
			unavailable = '';
		} catch (error) {
			unavailable = `${error}`;
		}
	};

	// Any filter change starts again from the first page.
	const filtersChanged = () => {
		offset = 0;
		void load();
	};

	const searchTyped = () => {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(filtersChanged, SEARCH_DELAY_MS);
	};

	const clearFilters = () => {
		q = '';
		role = '';
		source = '';
		since = '';
		until = '';
		conversationId = '';
		filtersChanged();
	};

	const showConversation = (id: string) => {
		conversationId = id;
		order = 'oldest';
		filtersChanged();
	};

	const page = (delta: number) => {
		offset = Math.max(0, offset + delta * PAGE_SIZE);
		void load();
	};

	const toggle = (id: string) => {
		selected.has(id) ? selected.delete(id) : selected.add(id);
		selected = selected;
	};

	const togglePage = () => {
		if (allOnPageSelected) pageIds.forEach((id) => selected.delete(id));
		else pageIds.forEach((id) => selected.add(id));
		selected = selected;
	};

	const toggleExpanded = (id: string) => {
		expanded.has(id) ? expanded.delete(id) : expanded.add(id);
		expanded = expanded;
	};

	const askDelete = (ids: string[]) => {
		if (!ids.length) return;
		pendingDelete = ids;
		showConfirm = true;
	};

	const doDelete = async () => {
		const ids = pendingDelete;
		pendingDelete = [];
		deleting = true;
		try {
			const result = await deleteMemories(localStorage.token, ids);
			ids.forEach((id) => selected.delete(id));
			selected = selected;
			toast.success(
				`Deleted ${result.deleted} ${result.deleted === 1 ? 'memory' : 'memories'}` +
					(result.backup ? `. Backed up the store first (${result.backup}).` : '.')
			);
			// Removing the last items of the last page would leave it empty.
			if (offset > 0 && offset >= total - result.deleted) offset = Math.max(0, offset - PAGE_SIZE);
			await Promise.all([load(), refreshSummary()]);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			deleting = false;
		}
	};

	const when = (iso: string) => {
		const parsed = new Date(iso);
		return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
	};

	const count = (n: number) => n.toLocaleString();

	onMount(async () => {
		if (!isAdmin || workMode) return;
		await refreshSummary();
		if (!unavailable) await load();
	});

	onDestroy(() => {
		if (searchTimer) clearTimeout(searchTimer);
	});
</script>

<svelte:head>
	<title>Memories - {$WEBUI_NAME}</title>
</svelte:head>

<ConfirmDialog
	bind:show={showConfirm}
	title={pendingDelete.length === 1 ? 'Delete memory' : `Delete ${pendingDelete.length} memories`}
	message={pendingDelete.length === 1
		? 'This memory will be removed for good, and the AI will no longer recall it. The first deletion each day backs the store up first.'
		: `These ${pendingDelete.length} memories will be removed for good, and the AI will no longer recall them. The first deletion each day backs the store up first.`}
	on:confirm={doDelete}
	on:cancel={() => (pendingDelete = [])}
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
		<div class="font-medium">Memories</div>
		{#if summary}
			<div class="ml-auto text-xs text-gray-500">
				{count(summary.total)} memories · {count(summary.recorded)} recorded from chats · {count(
					summary.imported
				)} imported
			</div>
		{/if}
	</nav>

	<div class="flex-1 overflow-y-auto">
		<div class="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4 lg:p-6">
			{#if !isAdmin}
				<div class="text-sm text-gray-500">
					Only admins can browse memories: the memory store holds every account's messages.
				</div>
			{:else if workMode}
				<div class="text-sm text-gray-500">
					Memories are hidden in work mode, since they hold text from private chats too.
				</div>
			{:else if unavailable}
				<div class="rounded-2xl border border-red-200 dark:border-red-900 p-4 text-sm">
					<div class="font-medium">The memory store could not be opened.</div>
					<div class="mt-1 text-gray-500">{unavailable}</div>
				</div>
			{:else}
				<p class="text-sm text-gray-500">
					Every message the memory pipeline has recorded is a memory the AI can recall in later
					chats. Find the ones you don't want it to remember and delete them. Deleting is permanent;
					the first deletion each day backs the store up first.
				</p>

				<section
					class="flex flex-col gap-2 rounded-2xl border border-gray-200 p-3 text-sm dark:border-gray-800"
				>
					<input
						class="w-full rounded-lg bg-gray-50 px-3 py-2 outline-hidden dark:bg-gray-850"
						placeholder="Search memories (every word must appear)"
						bind:value={q}
						on:input={searchTyped}
					/>
					<div class="flex flex-wrap items-center gap-2">
						<select
							class="rounded-lg bg-gray-50 px-2 py-1.5 outline-hidden dark:bg-gray-850"
							bind:value={role}
							on:change={filtersChanged}
							aria-label="Who wrote it"
						>
							<option value="">Everyone</option>
							<option value="user">Your messages</option>
							<option value="assistant">AI replies</option>
						</select>
						<select
							class="rounded-lg bg-gray-50 px-2 py-1.5 outline-hidden dark:bg-gray-850"
							bind:value={source}
							on:change={filtersChanged}
							aria-label="Source"
						>
							<option value="">Any source</option>
							<option value="recorded">Recorded from chats</option>
							<option value="imported">Imported</option>
						</select>
						<label class="flex items-center gap-1 text-gray-500">
							From
							<input
								type="date"
								class="rounded-lg bg-gray-50 px-2 py-1 outline-hidden dark:bg-gray-850"
								bind:value={since}
								on:change={filtersChanged}
							/>
						</label>
						<label class="flex items-center gap-1 text-gray-500">
							to
							<input
								type="date"
								class="rounded-lg bg-gray-50 px-2 py-1 outline-hidden dark:bg-gray-850"
								bind:value={until}
								on:change={filtersChanged}
							/>
						</label>
						<select
							class="rounded-lg bg-gray-50 px-2 py-1.5 outline-hidden dark:bg-gray-850"
							bind:value={order}
							on:change={filtersChanged}
							aria-label="Order"
						>
							<option value="newest">Newest first</option>
							<option value="oldest">Oldest first</option>
						</select>
						{#if filtered}
							<button
								class="ml-auto text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
								on:click={clearFilters}>Clear filters</button
							>
						{/if}
					</div>
					{#if conversationId}
						<div class="flex items-center gap-2 text-xs">
							<span class="rounded-full bg-gray-100 px-2 py-0.5 dark:bg-gray-850">
								Showing one conversation
							</span>
							<button
								class="text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
								on:click={() => {
									conversationId = '';
									order = 'newest';
									filtersChanged();
								}}>Show all</button
							>
						</div>
					{/if}
				</section>

				<div class="flex flex-wrap items-center gap-3 text-sm">
					<label class="flex items-center gap-2">
						<input
							type="checkbox"
							class="size-3.5 accent-gray-700"
							checked={allOnPageSelected}
							disabled={!items.length}
							on:change={togglePage}
						/>
						<span class="text-gray-500">Select page</span>
					</label>
					<button
						class="rounded-lg bg-red-50 px-3 py-1 text-red-700 hover:bg-red-100 disabled:opacity-40 dark:bg-red-950/40 dark:text-red-300 dark:hover:bg-red-950/70"
						disabled={!selected.size || deleting}
						on:click={() => askDelete([...selected])}
					>
						Delete selected{selected.size ? ` (${selected.size})` : ''}
					</button>
					<div class="ml-auto flex items-center gap-2 text-gray-500">
						{#if loading}<Spinner className="size-4" />{/if}
						{#if total}
							{count(offset + 1)}–{count(Math.min(offset + PAGE_SIZE, total))} of {count(total)}
						{:else if !loading}
							No memories match
						{/if}
						<button
							class="rounded-lg px-2 py-1 hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-850"
							disabled={offset === 0 || loading}
							on:click={() => page(-1)}>Previous</button
						>
						<button
							class="rounded-lg px-2 py-1 hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-gray-850"
							disabled={offset + PAGE_SIZE >= total || loading}
							on:click={() => page(1)}>Next</button
						>
					</div>
				</div>

				<div class="flex flex-col gap-2">
					{#each items as memory (memory.message_id)}
						{@const long = memory.content.length > CLIP}
						{@const open = expanded.has(memory.message_id)}
						<article
							class="flex gap-3 rounded-xl border p-3 text-sm {selected.has(memory.message_id)
								? 'border-red-300 dark:border-red-800'
								: 'border-gray-100 dark:border-gray-850'}"
						>
							<input
								type="checkbox"
								class="mt-1 size-3.5 shrink-0 accent-gray-700"
								checked={selected.has(memory.message_id)}
								on:change={() => toggle(memory.message_id)}
								aria-label="Select this memory"
							/>
							<div class="min-w-0 flex-1">
								<div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
									<span
										class="rounded-full px-2 py-0.5 {memory.role === 'user'
											? 'bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300'
											: 'bg-gray-100 text-gray-700 dark:bg-gray-850 dark:text-gray-300'}"
									>
										{memory.role === 'user'
											? 'You'
											: memory.role === 'assistant'
												? 'AI'
												: memory.role}
									</span>
									<span>{when(memory.timestamp)}</span>
									<span>· {memory.source === 'recorded' ? 'recorded' : 'imported'}</span>
									{#if !conversationId}
										<button
											class="hover:text-gray-800 dark:hover:text-gray-200"
											on:click={() => showConversation(memory.conversation_id)}
											>· show conversation</button
										>
									{/if}
								</div>
								<div class="mt-1 whitespace-pre-wrap break-words">
									{long && !open ? `${memory.content.slice(0, CLIP)}…` : memory.content}
								</div>
								{#if long}
									<button
										class="mt-1 text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
										on:click={() => toggleExpanded(memory.message_id)}
									>
										{open ? 'Show less' : `Show all (${count(memory.content.length)} characters)`}
									</button>
								{/if}
							</div>
							<button
								class="self-start rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-red-50 hover:text-red-700 disabled:opacity-40 dark:hover:bg-red-950/40 dark:hover:text-red-300"
								disabled={deleting}
								on:click={() => askDelete([memory.message_id])}
								aria-label="Delete this memory"
							>
								Delete
							</button>
						</article>
					{/each}
				</div>
			{/if}
		</div>
	</div>
</div>
