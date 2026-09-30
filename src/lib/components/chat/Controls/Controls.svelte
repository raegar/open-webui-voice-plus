<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	import XMark from '$lib/components/icons/XMark.svelte';
	import AdvancedParams from '../Settings/Advanced/AdvancedParams.svelte';
	import Valves from '$lib/components/chat/Controls/Valves.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import VideoCharacters from '$lib/components/chat/Controls/VideoCharacters.svelte';

	import { toast } from 'svelte-sonner';
	import { config, privateChatIds, user, settings } from '$lib/stores';
	import { markChatPrivate } from '$lib/utils/privacy';
	export let models = [];
	export let chatFiles = [];
	export let params = {};
	export let embed = false;
	export let chatId: string | null = null;
	// Passed through to the character pane so its refresh can read the conversation.
	export let history: any = undefined;
	export let modelId: string | null = null;

	// Persist collapsible section open/close state
	const getOpen = (key: string, fallback = true): boolean => {
		const v = localStorage.getItem(`chatControls.${key}`);
		return v !== null ? v === 'true' : fallback;
	};
	const setOpen = (key: string) => (open: boolean) => {
		localStorage.setItem(`chatControls.${key}`, String(open));
	};

	let showFiles = getOpen('files');
	let showVideoCharacters = getOpen('videoCharacters', false);
	let showValves = getOpen('valves', false);
	let showChatInstructions = getOpen('chatInstructions');
	let showSystemPrompt = getOpen('systemPrompt');
	let showAdvancedParams = getOpen('advancedParams');

	// Temporary chats have local: ids that are not real chats, so they cannot be marked.
	$: canMarkPrivate = !!chatId && !chatId.startsWith('local:');

	const setPrivate = async (box: HTMLInputElement) => {
		if (!chatId) return;
		try {
			await markChatPrivate(chatId, box.checked);
		} catch (error) {
			box.checked = !box.checked;
			toast.error(`${error}`);
		}
	};
</script>

<div class=" dark:text-white">
	{#if !embed}
		<div class=" flex items-center justify-between dark:text-gray-100 mb-2">
			<div class=" text-md self-center font-primary">{$i18n.t('Controls')}</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close chat controls')}
				on:click={() => {
					dispatch('close');
				}}
			>
				<XMark className="size-3.5" />
			</button>
		</div>
	{/if}

	{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
		<div class=" dark:text-gray-200 text-sm py-0.5 px-0.5">
			{#if canMarkPrivate}
				<label class="flex items-center gap-2 py-1 text-xs">
					<input
						type="checkbox"
						class="size-3.5 accent-gray-700"
						checked={$privateChatIds.has(chatId)}
						on:change={(e) => setPrivate(e.currentTarget)}
					/>
					<span>{$i18n.t('Private chat')}</span>
					<span class="ml-auto text-gray-500">{$i18n.t('Hidden in work mode')}</span>
				</label>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}
			<Collapsible
				title={$i18n.t('Chat Instructions')}
				bind:open={showChatInstructions}
				onChange={setOpen('chatInstructions')}
				buttonClassName="w-full"
			>
				<div class="" slot="content">
					<textarea
						bind:value={params.chat_instructions}
						class="w-full text-xs outline-hidden resize-vertical {$settings.highContrastMode
							? 'border-2 border-gray-300 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800 p-2.5'
							: 'py-1.5 bg-transparent'}"
						rows="4"
						placeholder={$i18n.t('e.g. Always reply in iambic pentameter.')}
					/>
					<div class="text-xs text-gray-500">
						{$i18n.t(
							'Followed in every reply in this chat, ahead of character profiles. New chats start with the default set in Settings.'
						)}
					</div>
				</div>
			</Collapsible>

			<hr class="my-2 border-gray-50 dark:border-gray-700/10" />

			{#if $config?.features?.enable_video_generation && ($user?.role === 'admin' || $user?.permissions?.features?.image_generation)}
				<Collapsible
					title={$i18n.t('Character profiles')}
					bind:open={showVideoCharacters}
					onChange={setOpen('videoCharacters')}
					buttonClassName="w-full"
				>
					<div slot="content">
						<VideoCharacters {chatId} {history} {modelId} />
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if chatFiles.length > 0}
				<Collapsible
					title={$i18n.t('Files')}
					bind:open={showFiles}
					onChange={setOpen('files')}
					buttonClassName="w-full"
				>
					<div class="flex flex-col gap-1 mt-1.5" slot="content">
						{#each chatFiles as file, fileIdx}
							<FileItem
								className="w-full"
								item={file}
								edit={true}
								url={file?.url ? file.url : null}
								name={file.name}
								type={file.type}
								size={file?.size}
								dismissible={true}
								small={true}
								on:dismiss={() => {
									// Remove the file from the chatFiles array

									chatFiles.splice(fileIdx, 1);
									chatFiles = chatFiles;
								}}
								on:click={() => {
									console.log(file);
								}}
							/>
						{/each}
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions.chat?.valves ?? true)}
				<Collapsible
					bind:open={showValves}
					onChange={setOpen('valves')}
					title={$i18n.t('Valves')}
					buttonClassName="w-full"
				>
					<div class="text-sm" slot="content">
						<Valves show={showValves} />
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
				<Collapsible
					title={$i18n.t('System Prompt')}
					bind:open={showSystemPrompt}
					onChange={setOpen('systemPrompt')}
					buttonClassName="w-full"
				>
					<div class="" slot="content">
						<textarea
							bind:value={params.system}
							class="w-full text-xs outline-hidden resize-vertical {$settings.highContrastMode
								? 'border-2 border-gray-300 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800 p-2.5'
								: 'py-1.5 bg-transparent'}"
							rows="4"
							placeholder={$i18n.t('Enter system prompt')}
						/>
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions.chat?.params ?? true)}
				<Collapsible
					title={$i18n.t('Advanced Params')}
					bind:open={showAdvancedParams}
					onChange={setOpen('advancedParams')}
					buttonClassName="w-full"
				>
					<div class="text-sm mt-1.5" slot="content">
						<div>
							<AdvancedParams admin={$user?.role === 'admin'} custom={true} bind:params />
						</div>
					</div>
				</Collapsible>
			{/if}
		</div>
	{/if}
</div>
