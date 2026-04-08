<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getScheduledJobs,
		createScheduledJob,
		updateScheduledJob,
		deleteScheduledJob,
		runScheduledJob,
		type ScheduledJob,
		type ScheduledJobForm
	} from '$lib/apis/scheduled-jobs';
	import ScheduledJobEditor from './ScheduledJobEditor.svelte';

	const i18n = getContext('i18n');

	let jobs: ScheduledJob[] = [];
	let loaded = false;
	let showEditor = false;
	let editingJob: (ScheduledJobForm & { id?: string }) | null = null;
	let deletingId: string | null = null;

	onMount(async () => {
		await loadJobs();
		loaded = true;
	});

	async function loadJobs() {
		try {
			jobs = await getScheduledJobs(localStorage.token);
		} catch (e) {
			toast.error(`${e}`);
		}
	}

	function formatSchedule(job: ScheduledJob): string {
		const time = `${job.hour.toString().padStart(2, '0')}:${job.minute.toString().padStart(2, '0')}`;
		const s = job.schedule;
		if (s === 'daily') return `Daily at ${time}`;
		if (s === 'weekdays') return `Weekdays at ${time}`;
		if (s === 'weekends') return `Weekends at ${time}`;
		const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
		const days = s.split(',').map((d) => DAY_NAMES[parseInt(d)] ?? d).join(', ');
		return `${days} at ${time}`;
	}

	function formatLastRun(ts: number | null): string {
		if (!ts) return '—';
		return new Date(ts * 1000).toLocaleString();
	}

	function openNew() {
		editingJob = null;
		showEditor = true;
	}

	function openEdit(job: ScheduledJob) {
		editingJob = { ...job };
		showEditor = true;
	}

	async function handleSave(data: ScheduledJobForm) {
		try {
			if (editingJob?.id) {
				await updateScheduledJob(localStorage.token, editingJob.id, data);
				toast.success($i18n.t('Job updated'));
			} else {
				await createScheduledJob(localStorage.token, data);
				toast.success($i18n.t('Job created'));
			}
			showEditor = false;
			await loadJobs();
		} catch (e) {
			toast.error(`${e}`);
		}
	}

	async function handleDelete(id: string) {
		deletingId = id;
		try {
			await deleteScheduledJob(localStorage.token, id);
			toast.success($i18n.t('Job deleted'));
			await loadJobs();
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			deletingId = null;
		}
	}

	async function handleRun(job: ScheduledJob) {
		try {
			await runScheduledJob(localStorage.token, job.id);
			toast.success($i18n.t('Job triggered: ') + job.title);
		} catch (e) {
			toast.error(`${e}`);
		}
	}

	async function handleToggle(job: ScheduledJob) {
		try {
			await updateScheduledJob(localStorage.token, job.id, {
				title: job.title,
				chat_id: job.chat_id,
				model_id: job.model_id,
				prompt: job.prompt,
				schedule: job.schedule,
				hour: job.hour,
				minute: job.minute,
				enabled: !job.enabled
			});
			await loadJobs();
		} catch (e) {
			toast.error(`${e}`);
		}
	}
</script>

<div class="flex flex-col gap-4">
	{#if showEditor}
		<div class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
			<ScheduledJobEditor
				job={editingJob}
				onSave={handleSave}
				onCancel={() => (showEditor = false)}
			/>
		</div>
	{:else}
		<div class="flex items-center justify-between">
			<div>
				<h2 class="text-xl font-semibold">{$i18n.t('Scheduled Jobs')}</h2>
				<p class="text-sm text-gray-500 mt-0.5">
					{$i18n.t('Automatically send messages to chats on a schedule.')}
				</p>
			</div>
			<button
				class="px-4 py-2 rounded-lg text-sm bg-black dark:bg-white text-white dark:text-black hover:opacity-80 transition"
				on:click={openNew}
			>
				+ {$i18n.t('New Job')}
			</button>
		</div>

		{#if !loaded}
			<div class="text-sm text-gray-400">{$i18n.t('Loading...')}</div>
		{:else if jobs.length === 0}
			<div class="text-sm text-gray-400 py-8 text-center">
				{$i18n.t('No scheduled jobs yet. Click "New Job" to create one.')}
			</div>
		{:else}
			<div class="flex flex-col gap-2">
				{#each jobs as job (job.id)}
					<div
						class="flex items-start gap-3 p-4 rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-700"
					>
						<!-- Enabled toggle -->
						<button
							class="mt-0.5 w-9 h-5 rounded-full transition-colors flex-shrink-0 {job.enabled
								? 'bg-green-500'
								: 'bg-gray-300 dark:bg-gray-600'}"
							title={job.enabled ? $i18n.t('Disable') : $i18n.t('Enable')}
							on:click={() => handleToggle(job)}
						>
							<span
								class="block w-4 h-4 rounded-full bg-white shadow transition-transform mx-0.5 {job.enabled
									? 'translate-x-4'
									: 'translate-x-0'}"
							/>
						</button>

						<!-- Info -->
						<div class="flex-1 min-w-0">
							<div class="font-medium text-sm truncate">{job.title}</div>
							<div class="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">
								<span>🕐 {formatSchedule(job)}</span>
								<span>🤖 {job.model_id}</span>
								<span>💬 {job.chat_id.slice(0, 8)}…</span>
								<span>{$i18n.t('Last run')}: {formatLastRun(job.last_run_at)}</span>
							</div>
							<div class="text-xs text-gray-400 mt-1 italic truncate">"{job.prompt}"</div>
						</div>

						<!-- Actions -->
						<div class="flex items-center gap-1 flex-shrink-0">
							<button
								class="px-2 py-1 rounded text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 hover:opacity-80 transition"
								title={$i18n.t('Run now')}
								on:click={() => handleRun(job)}
							>
								▶ {$i18n.t('Run')}
							</button>
							<button
								class="px-2 py-1 rounded text-xs bg-gray-100 dark:bg-gray-700 hover:opacity-80 transition"
								on:click={() => openEdit(job)}
							>
								{$i18n.t('Edit')}
							</button>
							<button
								class="px-2 py-1 rounded text-xs bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 hover:opacity-80 transition disabled:opacity-40"
								disabled={deletingId === job.id}
								on:click={() => handleDelete(job.id)}
							>
								{deletingId === job.id ? '…' : $i18n.t('Delete')}
							</button>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	{/if}
</div>
