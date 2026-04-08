<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { models } from '$lib/stores';
	import { getChatList } from '$lib/apis/chats';
	import type { ScheduledJobForm } from '$lib/apis/scheduled-jobs';

	const i18n = getContext('i18n');

	export let job: (ScheduledJobForm & { id?: string }) | null = null;
	export let onSave: (data: ScheduledJobForm) => Promise<void>;
	export let onCancel: () => void;

	const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

	let title = '';
	let chat_id = '';
	let model_id = '';
	let prompt = '';
	let scheduleType = 'daily'; // daily | weekdays | weekends | custom
	let customDays: boolean[] = [false, false, false, false, false, false, false]; // Mon=0..Sun=6
	let hour = 6;
	let minute = 0;
	let enabled = true;

	let chats: { id: string; title: string }[] = [];
	let chatQuery = '';
	let saving = false;

	$: filteredChats = chatQuery
		? chats.filter((c) => c.title.toLowerCase().includes(chatQuery.toLowerCase()))
		: chats;

	$: selectedChatTitle = chats.find((c) => c.id === chat_id)?.title ?? '';

	function buildScheduleString(): string {
		if (scheduleType === 'daily') return 'daily';
		if (scheduleType === 'weekdays') return 'weekdays';
		if (scheduleType === 'weekends') return 'weekends';
		const days = customDays
			.map((on, i) => (on ? i.toString() : null))
			.filter(Boolean)
			.join(',');
		return days || 'daily';
	}

	function parseSchedule(schedule: string) {
		if (schedule === 'daily' || !schedule) { scheduleType = 'daily'; return; }
		if (schedule === 'weekdays') { scheduleType = 'weekdays'; return; }
		if (schedule === 'weekends') { scheduleType = 'weekends'; return; }
		scheduleType = 'custom';
		customDays = [false, false, false, false, false, false, false];
		schedule.split(',').forEach((d) => {
			const idx = parseInt(d.trim());
			if (idx >= 0 && idx <= 6) customDays[idx] = true;
		});
	}

	onMount(async () => {
		chats = (await getChatList(localStorage.token)) ?? [];

		if (job) {
			title = job.title;
			chat_id = job.chat_id;
			model_id = job.model_id;
			prompt = job.prompt;
			hour = job.hour;
			minute = job.minute;
			enabled = job.enabled;
			parseSchedule(job.schedule);
		} else {
			// Default model to first available
			if ($models?.length) model_id = $models[0].id;
		}
	});

	async function handleSave() {
		if (!title.trim() || !chat_id || !model_id || !prompt.trim()) return;
		saving = true;
		try {
			await onSave({
				title: title.trim(),
				chat_id,
				model_id,
				prompt: prompt.trim(),
				schedule: buildScheduleString(),
				hour,
				minute,
				enabled
			});
		} finally {
			saving = false;
		}
	}

	function padTwo(n: number) {
		return n.toString().padStart(2, '0');
	}
</script>

<div class="flex flex-col gap-4 p-1">
	<div class="text-lg font-semibold">
		{job?.id ? $i18n.t('Edit Scheduled Job') : $i18n.t('New Scheduled Job')}
	</div>

	<!-- Title -->
	<div>
		<label class="block text-sm font-medium mb-1">{$i18n.t('Title')}</label>
		<input
			class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none"
			type="text"
			bind:value={title}
			placeholder={$i18n.t('e.g. Morning greeting')}
		/>
	</div>

	<!-- Chat picker -->
	<div>
		<label class="block text-sm font-medium mb-1">{$i18n.t('Target Chat')}</label>
		<input
			class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none mb-1"
			type="text"
			bind:value={chatQuery}
			placeholder={$i18n.t('Search chats...')}
		/>
		<select
			class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none"
			bind:value={chat_id}
			size="4"
		>
			{#each filteredChats as chat}
				<option value={chat.id}>{chat.title}</option>
			{/each}
		</select>
		{#if selectedChatTitle}
			<div class="text-xs text-gray-500 mt-1">{$i18n.t('Selected')}: {selectedChatTitle}</div>
		{/if}
	</div>

	<!-- Model picker -->
	<div>
		<label class="block text-sm font-medium mb-1">{$i18n.t('Model')}</label>
		<select
			class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none"
			bind:value={model_id}
		>
			{#each $models ?? [] as model}
				<option value={model.id}>{model.name ?? model.id}</option>
			{/each}
		</select>
	</div>

	<!-- Prompt -->
	<div>
		<label class="block text-sm font-medium mb-1">{$i18n.t('Prompt')}</label>
		<textarea
			class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none resize-none"
			rows="4"
			bind:value={prompt}
			placeholder={$i18n.t('e.g. Good morning! Give me a brief uplifting greeting for today.')}
		/>
	</div>

	<!-- Schedule -->
	<div>
		<label class="block text-sm font-medium mb-1">{$i18n.t('Schedule')}</label>
		<select
			class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none"
			bind:value={scheduleType}
		>
			<option value="daily">{$i18n.t('Daily')}</option>
			<option value="weekdays">{$i18n.t('Weekdays (Mon–Fri)')}</option>
			<option value="weekends">{$i18n.t('Weekends (Sat–Sun)')}</option>
			<option value="custom">{$i18n.t('Custom days')}</option>
		</select>

		{#if scheduleType === 'custom'}
			<div class="flex gap-2 mt-2 flex-wrap">
				{#each DAY_NAMES as day, i}
					<label class="flex items-center gap-1 text-sm cursor-pointer">
						<input type="checkbox" bind:checked={customDays[i]} class="rounded" />
						{day}
					</label>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Time -->
	<div class="flex gap-3">
		<div class="flex-1">
			<label class="block text-sm font-medium mb-1">{$i18n.t('Hour')} (0–23)</label>
			<input
				class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none"
				type="number"
				min="0"
				max="23"
				bind:value={hour}
			/>
		</div>
		<div class="flex-1">
			<label class="block text-sm font-medium mb-1">{$i18n.t('Minute')} (0–59)</label>
			<input
				class="w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 text-sm focus:outline-none"
				type="number"
				min="0"
				max="59"
				bind:value={minute}
			/>
		</div>
	</div>

	<!-- Enabled -->
	<label class="flex items-center gap-3 cursor-pointer">
		<input type="checkbox" bind:checked={enabled} class="rounded" />
		<span class="text-sm font-medium">{$i18n.t('Enabled')}</span>
	</label>

	<!-- Buttons -->
	<div class="flex gap-2 justify-end pt-1">
		<button
			class="px-4 py-2 rounded-lg text-sm bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
			on:click={onCancel}
		>
			{$i18n.t('Cancel')}
		</button>
		<button
			class="px-4 py-2 rounded-lg text-sm bg-black dark:bg-white text-white dark:text-black hover:opacity-80 transition disabled:opacity-50"
			disabled={saving || !title.trim() || !chat_id || !model_id || !prompt.trim()}
			on:click={handleSave}
		>
			{saving ? $i18n.t('Saving...') : $i18n.t('Save')}
		</button>
	</div>
</div>
