<script lang="ts">
	import { goto } from '$app/navigation';
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { videoGenerations } from '$lib/apis/videos';
	import Selector from '$lib/components/chat/ModelSelector/Selector.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import VideoCamera from '$lib/components/icons/VideoCamera.svelte';
	import { config, mobile, models, settings, showSidebar, user, WEBUI_NAME } from '$lib/stores';

	type WorkflowMode = 'text' | 'first' | 'first-last';
	type FrameRole = 'first' | 'last';
	type AspectRatio = '16:9' | '9:16' | '1:1';
	type Megapixels = 0.2 | 0.4;
	type Duration = 3 | 5 | 10;
	type FrameAsset = {
		name: string;
		type: string;
		size: number;
		width: number;
		height: number;
		dataUrl: string;
	};
	type GeneratedVideo = {
		url: string;
		prompt: string;
		mode: string;
		duration: number;
		aspect_ratio: string;
		seed: number;
	};

	const PROMPT_SYSTEM = `You are a prompt-enrichment engine for MiniMax H3, which generates video and synchronized stereo audio. Turn the user's creative direction into one detailed, unambiguous production brief. Preserve the user's intent and output only the brief: no preamble, markdown, JSON, or commentary.

Use exactly these three fields in this order:
integrated_multimodal_description:
overall_soundscape:
non_diegetic_music:

In integrated_multimodal_description, describe observable visuals and audio along a timeline. Start [Shot 1] without a timestamp. Later cuts use [Shot N] At MM:SS.mmm with strictly increasing times inside the requested duration. Give each beat one primary change and an observable end state. Put the most important beat before the final beat. Always specify one intentional camera behavior per shot, including amplitude and speed when useful. For a static shot say the frame never moves and explicitly prohibit pan, push-in, and reframing.

Put spoken words verbatim inside <d>[Language] words.</d>. Keep speaker IDs such as (S1) stable. State when lips close after dialogue. Put visible text in double quotes verbatim, or explicitly say no text appears. overall_soundscape is one short paragraph covering ambience and physical sounds without repeating dialogue. non_diegetic_music describes instrumentation, tempo, rhythm, and dynamics, or N/A.

Frame images are handled directly by the FL2VA workflow. You receive only their metadata and cannot see their pixels. Never invent image contents. With a first-frame anchor, begin exactly from the supplied first frame and describe the motion that develops from it using only facts in the user's direction. With first and last anchors, favor one continuous shot and describe a plausible interpolation that begins exactly on the first frame and lands exactly on the last frame at the requested duration. Never use the six-section Ref2VA template; Reference-to-Video is not enabled.

Treat duration and aspect ratio as hard constraints. For 3-10 second clips, keep the action physically achievable. Express exclusions in plain English because MiniMax H3 has no negative-prompt field.`;

	let loaded = false;
	let selectedModelId = '';
	let workflowMode: WorkflowMode = 'text';
	let creativeDirection = '';
	let productionPrompt = '';
	let promptApproved = false;
	let aspectRatio: AspectRatio = '16:9';
	let megapixels: Megapixels = 0.2;
	let duration: Duration = 5;
	let seed: string | number = '';
	let firstFrame: FrameAsset | null = null;
	let lastFrame: FrameAsset | null = null;
	let drafting = false;
	let generating = false;
	let elapsedSeconds = 0;
	let generatedVideos: GeneratedVideo[] = [];
	let firstFileInput: HTMLInputElement;
	let lastFileInput: HTMLInputElement;
	let elapsedTimer: ReturnType<typeof setInterval> | null = null;

	$: availableModels = ($models ?? []).filter((model) => !(model?.info?.meta?.hidden ?? false));
	$: canUseVideo =
		$config?.features?.enable_video_generation &&
		($user?.role === 'admin' || $user?.permissions?.features?.image_generation);
	$: framesReady =
		workflowMode === 'text' ||
		(workflowMode === 'first' && firstFrame !== null) ||
		(workflowMode === 'first-last' && firstFrame !== null && lastFrame !== null);

	const markPromptForReview = () => {
		promptApproved = false;
	};

	const selectMode = (mode: WorkflowMode) => {
		workflowMode = mode;
		if (mode === 'text') {
			firstFrame = null;
			lastFrame = null;
		} else if (mode === 'first') {
			lastFrame = null;
		}
		markPromptForReview();
	};

	const humanFileSize = (bytes: number) =>
		bytes >= 1024 * 1024
			? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
			: `${Math.max(1, Math.round(bytes / 1024))} KB`;

	const readFrame = async (file: File): Promise<FrameAsset> => {
		if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
			throw new Error('Use a PNG, JPEG, or WebP image.');
		}
		if (file.size > 25 * 1024 * 1024) {
			throw new Error('Frame images must be 25 MB or smaller.');
		}
		const dataUrl = await new Promise<string>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(reader.result as string);
			reader.onerror = () => reject(new Error('The image could not be read.'));
			reader.readAsDataURL(file);
		});
		const dimensions = await new Promise<{ width: number; height: number }>((resolve, reject) => {
			const image = new Image();
			image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
			image.onerror = () => reject(new Error('The image could not be decoded.'));
			image.src = dataUrl;
		});
		return {
			name: file.name,
			type: file.type,
			size: file.size,
			width: dimensions.width,
			height: dimensions.height,
			dataUrl
		};
	};

	const setFrame = async (role: FrameRole, file?: File) => {
		if (!file) return;
		try {
			const frame = await readFrame(file);
			if (role === 'first') firstFrame = frame;
			else lastFrame = frame;
			markPromptForReview();
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const handleFileInput = async (event: Event, role: FrameRole) => {
		const input = event.currentTarget as HTMLInputElement;
		await setFrame(role, input.files?.[0]);
		input.value = '';
	};

	const handleDrop = async (event: DragEvent, role: FrameRole) => {
		event.preventDefault();
		await setFrame(role, event.dataTransfer?.files?.[0]);
	};

	const frameMetadata = () => {
		if (workflowMode === 'text') return 'No frame images are attached.';
		const items = [
			firstFrame
				? `First-frame anchor: ${firstFrame.name}, ${firstFrame.width}x${firstFrame.height}, ${firstFrame.type}, ${humanFileSize(firstFrame.size)}.`
				: 'First-frame anchor: expected but not attached.'
		];
		if (workflowMode === 'first-last') {
			items.push(
				lastFrame
					? `Last-frame anchor: ${lastFrame.name}, ${lastFrame.width}x${lastFrame.height}, ${lastFrame.type}, ${humanFileSize(lastFrame.size)}.`
					: 'Last-frame anchor: expected but not attached.'
			);
		}
		return items.join('\n');
	};

	const cleanModelPrompt = (value: string) =>
		value
			.replace(/<think>[\s\S]*?<\/think>/gi, '')
			.replace(/^\s*```(?:text)?\s*/i, '')
			.replace(/\s*```\s*$/i, '')
			.trim();

	const draftPrompt = async () => {
		if (!selectedModelId) {
			toast.error('Select a chat model for prompt drafting.');
			return;
		}
		if (!creativeDirection.trim()) {
			toast.error('Describe the video you want to make.');
			return;
		}
		drafting = true;
		promptApproved = false;
		try {
			const response = await generateOpenAIChatCompletion(localStorage.token, {
				model: selectedModelId,
				stream: false,
				messages: [
					{ role: 'system', content: PROMPT_SYSTEM },
					{
						role: 'user',
						content: `Creative direction:
${creativeDirection.trim()}

Hard constraints:
- Duration: ${duration} seconds
- Aspect ratio: ${aspectRatio}
- Workflow: ${workflowMode === 'text' ? 'text-to-video' : workflowMode === 'first' ? 'first-frame image-to-video' : 'first-and-last-frame interpolation'}

Frame metadata only (the image pixels are intentionally unavailable to you):
${frameMetadata()}

Write the final MiniMax H3 production brief now.`
					}
				]
			});
			const content = response?.choices?.[0]?.message?.content;
			if (!content || typeof content !== 'string') {
				throw new Error('The selected model returned an empty prompt.');
			}
			productionPrompt = cleanModelPrompt(content);
			toast.success('Draft ready. Review and approve it before generating.');
		} catch (error) {
			toast.error(`Prompt drafting failed: ${error}`);
		} finally {
			drafting = false;
		}
	};

	const approvePrompt = () => {
		if (!productionPrompt.trim()) {
			toast.error('Write or draft a production prompt first.');
			return;
		}
		promptApproved = true;
		toast.success('Prompt approved for generation.');
	};

	const startElapsedTimer = () => {
		elapsedSeconds = 0;
		if (elapsedTimer) clearInterval(elapsedTimer);
		elapsedTimer = setInterval(() => (elapsedSeconds += 1), 1000);
	};
	const stopElapsedTimer = () => {
		if (elapsedTimer) {
			clearInterval(elapsedTimer);
			elapsedTimer = null;
		}
	};

	const generateVideo = async () => {
		if (!promptApproved || !productionPrompt.trim()) {
			toast.error('Review and approve the production prompt first.');
			return;
		}
		if (!framesReady) {
			toast.error('Attach the required frame image or images.');
			return;
		}
		const seedText = String(seed).trim();
		const parsedSeed = seedText === '' ? undefined : Number(seedText);
		if (
			parsedSeed !== undefined &&
			(!Number.isSafeInteger(parsedSeed) || parsedSeed < 0 || parsedSeed > Number.MAX_SAFE_INTEGER)
		) {
			toast.error('Seed must be a non-negative whole number.');
			return;
		}
		generating = true;
		startElapsedTimer();
		try {
			const result = await videoGenerations(localStorage.token, productionPrompt.trim(), {
				aspect_ratio: aspectRatio,
				megapixels,
				duration,
				...(parsedSeed !== undefined ? { seed: parsedSeed } : {}),
				...(workflowMode !== 'text' && firstFrame
					? { first_frame_data_url: firstFrame.dataUrl }
					: {}),
				...(workflowMode === 'first-last' && lastFrame
					? { last_frame_data_url: lastFrame.dataUrl }
					: {})
			});
			if (!result?.[0]?.url) throw new Error('No video was returned.');
			generatedVideos = [result[0], ...generatedVideos];
			seed = String(result[0].seed);
			toast.success('MiniMax H3 video completed.');
		} catch (error) {
			toast.error(`Video generation failed: ${error}`);
		} finally {
			generating = false;
			stopElapsedTimer();
		}
	};

	const downloadVideo = async (video: GeneratedVideo) => {
		try {
			const response = await fetch(video.url, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!response.ok) throw new Error();
			const blobUrl = URL.createObjectURL(await response.blob());
			const link = document.createElement('a');
			link.href = blobUrl;
			link.download = `minimax-h3-${video.seed}.mp4`;
			link.click();
			URL.revokeObjectURL(blobUrl);
		} catch {
			toast.error('The video could not be downloaded.');
		}
	};

	onMount(async () => {
		if (!canUseVideo) {
			await goto('/');
			return;
		}
		selectedModelId =
			$settings?.models?.[0] ??
			$config?.default_models?.split(',')?.[0] ??
			availableModels[0]?.id ??
			'';
		loaded = true;
	});
	onDestroy(stopElapsedTimer);
</script>

<svelte:head>
	<title>Video Studio ? {$WEBUI_NAME}</title>
</svelte:head>

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
		<div class="flex items-center gap-2 font-medium">
			<VideoCamera className="size-5" />
			<span>Video Studio</span>
		</div>
		<div class="ml-auto text-xs text-gray-500">MiniMax H3 ? ComfyUI</div>
	</nav>

	{#if loaded}
		<div class="flex-1 overflow-y-auto">
			<div
				class="mx-auto grid w-full max-w-7xl gap-5 p-4 lg:grid-cols-[minmax(0,1fr)_22rem] lg:p-6"
			>
				<main class="flex min-w-0 flex-col gap-5">
					<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
						<div class="mb-4">
							<h1 class="text-xl font-semibold">Create a video</h1>
							<p class="mt-1 text-sm text-gray-500">
								Shape the idea with a chat model, approve the resulting H3 prompt, then send it to
								ComfyUI.
							</p>
						</div>
						<div class="grid grid-cols-3 gap-1 rounded-xl bg-gray-100 p-1 dark:bg-gray-850">
							{#each [{ id: 'text', label: 'Text' }, { id: 'first', label: 'Image' }, { id: 'first-last', label: 'First + last' }] as item}
								<button
									class="rounded-lg px-2 py-2 text-sm transition {workflowMode === item.id
										? 'bg-white font-medium shadow-sm dark:bg-gray-700'
										: 'text-gray-500 hover:text-gray-900 dark:hover:text-white'}"
									on:click={() => selectMode(item.id as WorkflowMode)}
								>
									{item.label}
								</button>
							{/each}
						</div>
						{#if workflowMode !== 'text'}
							<div class="mt-4 grid gap-3 {workflowMode === 'first-last' ? 'sm:grid-cols-2' : ''}">
								{#each workflowMode === 'first-last' ? [{ role: 'first', label: 'First frame', frame: firstFrame }, { role: 'last', label: 'Last frame', frame: lastFrame }] : [{ role: 'first', label: 'Starting image', frame: firstFrame }] as slot}
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-600 dark:text-gray-300">
											{slot.label}
										</div>
										<button
											class="relative flex aspect-video w-full items-center justify-center overflow-hidden rounded-xl border border-dashed border-gray-300 bg-gray-50 text-sm text-gray-500 hover:border-gray-500 dark:border-gray-700 dark:bg-gray-900"
											on:click={() =>
												slot.role === 'first' ? firstFileInput.click() : lastFileInput.click()}
											on:drop={(event) => handleDrop(event, slot.role as FrameRole)}
											on:dragover|preventDefault
										>
											{#if slot.frame}
												<img
													src={slot.frame.dataUrl}
													alt={slot.label}
													class="absolute inset-0 size-full object-contain"
												/>
												<span
													class="absolute bottom-2 left-2 rounded-md bg-black/70 px-2 py-1 text-xs text-white"
												>
													{slot.frame.width}?{slot.frame.height}
												</span>
											{:else}
												<span>Drop an image or click to browse</span>
											{/if}
										</button>
									</div>
								{/each}
							</div>
							<input
								class="hidden"
								type="file"
								accept="image/png,image/jpeg,image/webp"
								bind:this={firstFileInput}
								on:change={(event) => handleFileInput(event, 'first')}
							/>
							<input
								class="hidden"
								type="file"
								accept="image/png,image/jpeg,image/webp"
								bind:this={lastFileInput}
								on:change={(event) => handleFileInput(event, 'last')}
							/>
							<p class="mt-2 text-xs text-gray-500">
								Frame pixels go only to the ComfyUI generation workflow. The prompt model receives
								filename, dimensions, type, size, and frame role.
							</p>
						{/if}
					</section>

					<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
						<div class="mb-3 flex items-center justify-between gap-3">
							<div>
								<h2 class="font-semibold">1. Creative direction</h2>
								<p class="mt-0.5 text-xs text-gray-500">Plain-language ideas are enough.</p>
							</div>
							<div class="w-64 max-w-[50%]">
								<Selector
									placeholder="Select a prompt model"
									items={availableModels.map((model) => ({
										value: model.id,
										label: model.name,
										model
									}))}
									bind:value={selectedModelId}
									className="w-full"
									triggerClassName="text-sm"
								/>
							</div>
						</div>
						<textarea
							class="min-h-32 w-full resize-y rounded-xl border border-gray-200 bg-transparent p-3 text-sm outline-none focus:border-gray-500 dark:border-gray-700"
							placeholder="Example: A rain-soaked detective pauses under a flickering neon sign, hears footsteps behind her, then turns toward camera. Slow handheld push-in, realistic night ambience, no captions."
							bind:value={creativeDirection}
							on:input={markPromptForReview}
						></textarea>
						<div class="mt-3 flex justify-end">
							<button
								class="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black"
								disabled={drafting || !selectedModelId || !creativeDirection.trim()}
								on:click={draftPrompt}
							>
								{drafting
									? 'Drafting prompt?'
									: productionPrompt
										? 'Redraft H3 prompt'
										: 'Draft H3 prompt'}
							</button>
						</div>
					</section>

					<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
						<div class="mb-3 flex items-center justify-between">
							<div>
								<h2 class="font-semibold">2. Review the production prompt</h2>
								<p class="mt-0.5 text-xs text-gray-500">
									Edit anything you want. Edits require approval again.
								</p>
							</div>
							<span
								class="rounded-full px-2.5 py-1 text-xs {promptApproved
									? 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300'
									: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300'}"
							>
								{promptApproved ? 'Approved' : 'Needs approval'}
							</span>
						</div>
						<textarea
							class="min-h-80 w-full resize-y rounded-xl border border-gray-200 bg-transparent p-3 font-mono text-xs leading-5 outline-none focus:border-gray-500 dark:border-gray-700"
							placeholder="The generated MiniMax H3 production brief will appear here. You can also write one directly."
							bind:value={productionPrompt}
							on:input={markPromptForReview}
						></textarea>
						<div class="mt-3 flex justify-end">
							<button
								class="rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-40 dark:border-gray-700 dark:hover:bg-gray-850"
								disabled={!productionPrompt.trim()}
								on:click={approvePrompt}
							>
								{promptApproved ? 'Prompt approved' : 'Approve this prompt'}
							</button>
						</div>
					</section>

					{#if generatedVideos.length > 0}
						<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
							<h2 class="mb-3 font-semibold">Generated videos</h2>
							<div class="grid gap-4 xl:grid-cols-2">
								{#each generatedVideos as video}
									<article
										class="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800"
									>
										<video
											class="aspect-video w-full bg-black object-contain"
											src={video.url}
											controls
										></video>
										<div class="flex items-center justify-between gap-3 p-3 text-xs text-gray-500">
											<span>{video.duration}s ? {video.aspect_ratio} ? seed {video.seed}</span>
											<button
												class="font-medium text-gray-800 hover:underline dark:text-gray-200"
												on:click={() => downloadVideo(video)}>Download</button
											>
										</div>
									</article>
								{/each}
							</div>
						</section>
					{/if}
				</main>

				<aside
					class="h-fit rounded-2xl border border-gray-200 p-4 dark:border-gray-800 lg:sticky lg:top-4"
				>
					<h2 class="font-semibold">3. Generation settings</h2>
					<div class="mt-4 space-y-4">
						<label class="block">
							<span class="mb-1.5 block text-xs font-medium">Aspect ratio</span>
							<select
								class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								bind:value={aspectRatio}
								on:change={markPromptForReview}
							>
								<option value="16:9">16:9 landscape</option>
								<option value="9:16">9:16 portrait</option>
								<option value="1:1">1:1 square</option>
							</select>
						</label>
						<label class="block">
							<span class="mb-1.5 block text-xs font-medium">Resolution budget</span>
							<select
								class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								bind:value={megapixels}
							>
								<option value={0.2}>0.2 MP ? faster</option>
								<option value={0.4}>0.4 MP ? sharper</option>
							</select>
						</label>
						<label class="block">
							<span class="mb-1.5 block text-xs font-medium">Duration</span>
							<select
								class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								bind:value={duration}
								on:change={markPromptForReview}
							>
								<option value={3}>3 seconds</option>
								<option value={5}>5 seconds</option>
								<option value={10}>10 seconds</option>
							</select>
						</label>
						<label class="block">
							<span class="mb-1.5 block text-xs font-medium">Seed</span>
							<input
								class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								type="number"
								min="0"
								step="1"
								placeholder="Random"
								bind:value={seed}
							/>
						</label>
					</div>
					<button
						class="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-black px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black"
						disabled={generating || !promptApproved || !framesReady}
						on:click={generateVideo}
					>
						<VideoCamera className="size-4.5" strokeWidth="2" />
						{generating ? `Generating? ${elapsedSeconds}s` : 'Generate video'}
					</button>
					{#if generating}
						<p class="mt-2 text-center text-xs text-gray-500">
							ComfyUI is rendering. This usually takes one to several minutes.
						</p>
					{:else if !framesReady}
						<p class="mt-2 text-center text-xs text-amber-600">Attach the required frame images.</p>
					{:else if !promptApproved}
						<p class="mt-2 text-center text-xs text-gray-500">
							Approve the production prompt to continue.
						</p>
					{/if}
					<div
						class="mt-5 border-t border-gray-200 pt-4 text-xs leading-5 text-gray-500 dark:border-gray-800"
					>
						<p>Reference-to-Video is intentionally not exposed yet.</p>
						<p class="mt-2">
							The normal chat pipeline remains text-only for video generation; frame workflows live
							only in this studio.
						</p>
					</div>
				</aside>
			</div>
		</div>
	{:else}
		<div class="flex flex-1 items-center justify-center text-sm text-gray-500">
			Loading Video Studio?
		</div>
	{/if}
</div>
