<script lang="ts">
	import { goto } from '$app/navigation';
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { deleteFileById } from '$lib/apis/files';
	import {
		cancelVideoGenerationJob,
		getVideoGenerationJob,
		getVideoHistory,
		listVideoGenerationJobs,
		startVideoGenerationJob,
		type VideoGenerationJob,
		type VideoGenerationOptions
	} from '$lib/apis/videos';
	import Selector from '$lib/components/chat/ModelSelector/Selector.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import VideoCamera from '$lib/components/icons/VideoCamera.svelte';
	import { config, mobile, models, settings, showSidebar, user, WEBUI_NAME } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { updateUserSettings } from '$lib/apis/users';

	type WorkflowMode = 'text' | 'first' | 'first-last' | 'reference';
	type FrameRole = 'first' | 'last';
	type AspectRatio = '16:9' | '9:16' | '1:1';
	type Megapixels = 0.2 | 0.4;
	type Duration = 3 | 5 | 10 | 15;
	// MiniMaxH3ReferenceToVideo caps standalone ref_audios at 3.
	const MAX_REFERENCE_AUDIOS = 3;
	type FrameAsset = {
		name: string;
		type: string;
		size: number;
		width: number;
		height: number;
		dataUrl: string;
		// Which roster entry this slot stands for, when it came from one. Kept when the
		// picture is swapped, because the slot still means that character in the brief.
		origin?: string;
	};
	type GeneratedVideo = {
		url: string;
		id?: string;
		filename?: string;
		prompt: string;
		mode: string;
		duration: number;
		aspect_ratio: string;
		seed: string | number;
		megapixels?: number;
		has_first_frame?: boolean;
		has_last_frame?: boolean;
		reference_image_count?: number;
		created_at?: number;
	};

	const TIMELINE_PROMPT_SYSTEM = `You are a professional prompt engineer for MiniMax H3 image/text-to-video with synchronized audio. Turn the user's scenario into a concise, vivid production prompt and output ONLY that prompt.

Use contiguous [Xs-Ys] segments covering the full requested duration with no gaps. The first starts at 0s and the last ends at the exact duration. Use 2-3 segments for about 5 seconds, 4-5 for about 10 seconds, and 6-7 for about 15 seconds.

Each segment is 1-3 present-tense sentences describing observable motion rather than a static frame. Give every person on screen something to be doing in every segment: a listener still has posture, gaze, and a changing expression, and one left undescribed renders as a blank stare into the middle distance. Include setting, subject appearance and position, action and state change, lighting and atmosphere, plus an intentional camera angle or movement. Make events flow naturally and remain physically achievable. Imply synchronized ambience, speech, and physical sounds through the scene description; do not add separate audio sections. Preserve visible text verbatim, and dialogue verbatim up to the spoken-word budget in the constraints - beyond it, keep the strongest line and drop the rest rather than compressing the whole speech, because speech that overruns the clip length comes out garbled. Never say "show me", "create", or "generate".

Frame pixels are unavailable to you. Never invent their contents. A first-frame workflow must begin exactly at the supplied frame and develop forward. A first-and-last workflow must describe a plausible continuous path that starts exactly at the first frame and lands exactly at the last frame. Treat duration and aspect ratio as hard constraints.

Never describe lipstick as smudged or smeared, and never describe skin, cheeks, or a face as flushed: the video model renders both badly. This holds even when the reference sheet or the conversation mentions them - describe the lipstick simply as worn, and leave any change in skin colour out.`;

	const REFERENCE_PROMPT_SYSTEM = `You are an expert prompt engineer for MiniMax H3 Ref2VA. Turn the user's scenario and reference-image descriptions into a structurally compatible prompt that drives both video and audio. Output ONLY the formatted prompt.

The supplied images are connected in order as <Picture 1>, <Picture 2>, and so on. You receive metadata, not pixels: never invent image contents. Use only the user's description to define what each reference contributes.

Use exactly these six sections in order:
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:

In subject_definitions, define reusable <Subject N> identities, scenes, or styles and cite the contributing <Picture N>. In summary, begin with [reference generation] and state the target and relationships in one paragraph. In retention_analysis, give one line per subject or picture with a fixed marker: fully_preserved, partially_preserved, attribute_transfer, or weak_reference, and say where it appears.

In detailed_description, establish the overall style and describe every shot concretely: composition, appearance, position, action, environment, lighting, camera, state changes, and where referenced content appears. [Shot 1] has no timestamp. Later cuts use [Shot N] At 00:SS.mmm with strictly increasing times inside the duration. Camera movement includes type, amplitude, and speed. Keep stable speaker IDs such as (S1); put exact dialogue inside <d>[Language] words.</d>. Put visible text in double quotes verbatim.

overall_soundscape contains ambience and physical sounds without dialogue or music. non_diegetic_music specifies instrumentation, tempo, rhythm, and dynamics, or N/A. Treat duration and aspect ratio as hard constraints. Prefer a focused, achievable sequence over unnecessary shot changes.

Speech obeys real time and cannot be sped up. Keep the total spoken words across every <d> tag inside the budget given in the constraints, and keep dialogue to the stated maximum number of shots. When the scenario contains more speech than fits, stage only the strongest line or two and let the rest go unsaid; a brief that overruns the budget produces garbled, unintelligible audio, which is a worse failure than leaving a line out. Write dialogue as short spoken sentences, not paragraphs. End the summary paragraph by stating that clear intelligible speech takes priority over additional action or shot changes. Keep the soundscape quiet and uncluttered while anyone is speaking, and never ask for muttering, background chatter, or invented vocalisations.

Every person visible in a shot needs described behaviour for the whole of that shot. Someone who is not speaking is never idle: state where they are looking, what their face does as they listen, how they are holding themselves, and at least one small physical response - a shift of weight, a breath, a smile starting, a hand tightening. Reactions run at the same time as the speaker rather than after them, so they cost no runtime and never justify an extra shot or an extra line of dialogue. A character left undescribed renders as a blank stare into the middle distance.

Never describe lipstick as smudged or smeared, and never describe skin, cheeks, or a face as flushed: the video model renders both badly. This holds even when the reference sheet or the conversation mentions them - describe the lipstick simply as worn, and leave any change in skin colour out.`;

	let loaded = false;
	let selectedModelId = '';
	// When true the studio adopts the model of the chat a scene came from (the
	// original behaviour). When false the saved default below is always used.
	let useChatModel = true;
	let savingModelPreference = false;
	// Guards the persist watcher: set whenever the model is changed for us (initial
	// load, or a scene handoff) so those do not overwrite the saved default.
	let lastPersistedModel = '';
	let workflowMode: WorkflowMode = 'text';
	let creativeDirection = '';
	let productionPrompt = '';
	let promptApproved = false;
	let aspectRatio: AspectRatio = '9:16';
	let megapixels: Megapixels = 0.2;
	// LoRA path. With both off the graph is exactly the known-good 20-step
	// res_multistep one, so returning to it needs no rebuild. Turbo defaults on: it
	// is the speed win and has tested clean. Sampler settings are the same for every
	// combination, so toggling one changes only which weights load.
	let motionLora = false;
	let turboLora = true;
	let duration: Duration = 10;
	let seed: string | number | null = '';
	let firstFrame: FrameAsset | null = null;
	let lastFrame: FrameAsset | null = null;
	let referenceImages: FrameAsset[] = [];
	let drafting = false;
	let generating = false;
	let submitting = false;
	// How many times the currently approved prompt has been sent, and when last. Any edit
	// drops approval, which clears this, so it always describes the prompt on screen.
	let approvedSubmissions = 0;
	let lastSubmittedAt: number | null = null;
	$: if (!promptApproved) {
		approvedSubmissions = 0;
		lastSubmittedAt = null;
	}
	$: seedIsSet = seed !== null && seed !== undefined && String(seed).trim() !== '';
	// Job ids this browser is following, and the server's view of the ones still
	// waiting or rendering. The server is authoritative on order and position, so a
	// phone and a desktop looking at the same queue agree.
	let trackedJobIds: string[] = [];
	let queueJobs: VideoGenerationJob[] = [];
	let queuePolling = false;
	let destroyed = false;
	let elapsedSeconds = 0;
	let currentVideo: GeneratedVideo | null = null;
	let videoHistory: GeneratedVideo[] = [];
	let continuationSource: GeneratedVideo | null = null;
	let sceneContext: string | null = null;
	let sceneChatId: string | null = null;
	let sceneCharacters: string | null = null;
	// Voice reference data URLs, in the same order as the <Audio N> labels.
	let sceneVoices: string[] = [];
	let capturingFrame = false;
	let scrollContainer: HTMLDivElement;
	let firstFileInput: HTMLInputElement;
	let historyLoading = false;
	let showDeleteVideoConfirm = false;
	let videoPendingDeletion: GeneratedVideo | null = null;
	let deletingVideoId: string | null = null;
	let lastFileInput: HTMLInputElement;
	let referenceFileInput: HTMLInputElement;
	let replaceFileInput: HTMLInputElement;
	// Which slot a pending swap will land in; null when no swap is in flight.
	let replaceIndex: number | null = null;
	let elapsedTimer: ReturnType<typeof setInterval> | null = null;

	$: availableModels = ($models ?? []).filter((model) => !(model?.info?.meta?.hidden ?? false));
	$: canUseVideo =
		$config?.features?.enable_video_generation &&
		($user?.role === 'admin' || $user?.permissions?.features?.image_generation);
	$: framesReady =
		(workflowMode === 'reference' && referenceImages.length > 0) ||
		workflowMode === 'text' ||
		(workflowMode === 'first' && firstFrame !== null) ||
		(workflowMode === 'first-last' && firstFrame !== null && lastFrame !== null);

	// A voice reference conditions the model to produce speech whether or not the
	// brief gives it words: the node appends an audio ref item unconditionally. With
	// no <d> lines it invents phonemes, which comes out as garbled speech.
	const promptHasDialogue = (text: string) => /<d>[\s\S]*?<\/d>/i.test(text ?? '');

	// Speech is the one thing in a clip that cannot be compressed: a line takes as long
	// to say as it takes to say. Overrunning the runtime is the main cause of garbled
	// output - the model tries to fit the words in and the phonemes collapse into mush.
	// Calibrated against two real drafts of the same 10s scene: a 137-word one garbled
	// (it needs about 46 seconds to speak), and a 30-word rewrite of it came out clean.
	// 3 words per second is brisk but articulate, and puts that working rewrite exactly
	// at the limit, so the budget is permissive enough not to cry wolf on good drafts.
	const SPEECH_WORDS_PER_SECOND = 3;
	const speechBudget = (seconds: number) =>
		Math.max(8, Math.round(seconds * SPEECH_WORDS_PER_SECOND));
	// Every cut spends runtime establishing a new frame, and that time competes with the
	// spoken line rather than adding to it.
	const dialogueShotBudget = (seconds: number) => (seconds <= 5 ? 1 : seconds <= 10 ? 2 : 3);

	/** Words actually spoken: the contents of every <d> tag, minus the [Language] marker. */
	const countSpokenWords = (text: string): number =>
		[...(text ?? '').matchAll(/<d>([\s\S]*?)<\/d>/gi)]
			.map(([, inner]) => inner.replace(/\[[^\]]*\]/g, ' '))
			.join(' ')
			.split(/\s+/)
			.filter(Boolean).length;

	// Looks the video model renders badly: "smudged lipstick" comes out as paint smeared
	// across the face, and "flushed" as sunburn. Detection only - cutting the phrases out
	// with a regex leaves sentences like "Her cheeks are." behind, so a hit is repaired
	// by the drafting model instead. The lookahead stops "lips curl, eyeliner smudged"
	// from reading as smudged lipstick.
	const AVOIDED_LOOKS: RegExp[] = [
		/\b(?:smudg|smear)\w*(?:(?!eye|mascara|kohl|brow)[^.!?\n]){0,40}?\blip(?:s|stick|\s?gloss)?\b/gi,
		/\blip(?:s|stick|\s?gloss)?\b(?:(?!eye|mascara|kohl|brow)[^.!?\n]){0,40}?\b(?:smudg|smear)\w*/gi,
		/\bflush(?:ed|ing)\b/gi,
		/\b(?:a|the|faint|deep|soft|slight|pink|red|rosy|warm|hot|crimson)\s+flush\b/gi,
		/\bflush\s+(?:of|across|over|on|in|up|spreads?|spreading|creeps?|creeping|rises?|rising|blooms?|blooming|colou?rs?)\b/gi
	];

	const findAvoidedLooks = (text: string): string[] => [
		...new Set(
			AVOIDED_LOOKS.flatMap((pattern) =>
				[...(text ?? '').matchAll(pattern)].map((match) => match[0].trim().toLowerCase())
			)
		)
	];

	const describeLooks = (looks: string[]) =>
		looks
			.slice(0, 3)
			.map((look) => `"${look}"`)
			.join(', ');

	const AVOIDED_LOOKS_REPAIR_SYSTEM = `You edit MiniMax H3 video production briefs. Return the brief exactly as given with one change: remove every mention of smudged or smeared lipstick, and every mention of flushed skin, cheeks, or faces. Where removing a phrase would leave a sentence broken, reword only that sentence, as little as possible. Change nothing else: keep every section, tag, timestamp, and speaker label as it is, and keep dialogue word for word unless a line itself contains one of those mentions. Output only the brief.`;

	const formatClock = (timestamp: number | null) =>
		timestamp
			? new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
			: '';

	// Dropping the audio is not enough on its own. H3 always generates an audio track,
	// and the drafted brief still asserts "Their speaking voice is <Audio N>" because
	// that phrasing comes from the cast block we inject. Left in, the model invents
	// speech to satisfy a claim nothing else fulfils. So strip the claims and say
	// plainly that nobody speaks.
	const silenceProductionPrompt = (text: string) =>
		`${text
			.split('\n')
			.map((line) =>
				line
					.split('.')
					.filter((sentence) => !/<Audio\s*\d+>/i.test(sentence))
					.join('.')
			)
			.join('\n')
			.trim()}\n\nNo character speaks in this shot. There is no dialogue and no voice-over: all voices are silent, and the audio contains only ambience and physical sounds.`;

	$: voicesWithoutDialogue =
		sceneVoices.length > 0 &&
		productionPrompt.trim() !== '' &&
		!promptHasDialogue(productionPrompt);

	const persistModelPreference = async () => {
		savingModelPreference = true;
		try {
			settings.set({
				...$settings,
				videoPromptUseChatModel: useChatModel,
				videoPromptModel: selectedModelId
			});
			await updateUserSettings(localStorage.token, { ui: $settings });
		} catch (error) {
			toast.error(`The prompt model preference could not be saved: ${error}`);
		} finally {
			savingModelPreference = false;
		}
	};

	// Selector does not dispatch a change event, so watch the value instead.
	$: if (loaded && selectedModelId && selectedModelId !== lastPersistedModel) {
		lastPersistedModel = selectedModelId;
		void persistModelPreference();
	}

	const markPromptForReview = () => {
		promptApproved = false;
	};

	const selectMode = (mode: WorkflowMode) => {
		workflowMode = mode;
		if (mode === 'text') {
			firstFrame = null;
			lastFrame = null;
			referenceImages = [];
		} else if (mode === 'first') {
			lastFrame = null;
			referenceImages = [];
		} else if (mode === 'first-last') {
			referenceImages = [];
		} else {
			firstFrame = null;
			lastFrame = null;
		}
		// Any manual mode change means the starting image is no longer a continuation.
		continuationSource = null;
		if (mode !== 'reference') sceneVoices = [];
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
			if (role === 'first') {
				firstFrame = frame;
				// A hand-picked first frame replaces any captured continuation frame.
				continuationSource = null;
			} else lastFrame = frame;
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

	const addReferenceFiles = async (files: File[]) => {
		if (!files.length) return;
		if (referenceImages.length + files.length > 9) {
			toast.error('MiniMax H3 Ref2VA supports up to 9 reference images.');
			return;
		}
		try {
			const additions = await Promise.all(files.map(readFrame));
			referenceImages = [...referenceImages, ...additions];
			markPromptForReview();
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const handleReferenceInput = async (event: Event) => {
		const input = event.currentTarget as HTMLInputElement;
		await addReferenceFiles(Array.from(input.files ?? []));
		input.value = '';
	};

	const handleReferenceDrop = async (event: DragEvent) => {
		event.preventDefault();
		await addReferenceFiles(Array.from(event.dataTransfer?.files ?? []));
	};

	const removeReferenceImage = (index: number) => {
		referenceImages = referenceImages.filter((_, itemIndex) => itemIndex !== index);
		markPromptForReview();
	};

	// Swapping a picture is scoped to this generation. The studio never writes to the
	// character library, so the stored reference is untouched either way. Replacing in
	// place rather than removing and re-adding keeps the <Picture N> numbering stable,
	// which matters because both the drafted brief and the injected reference sheet
	// cite pictures by number.
	const replaceReferenceImage = (index: number) => {
		replaceIndex = index;
		replaceFileInput?.click();
	};

	const handleReplaceInput = async (event: Event) => {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		const index = replaceIndex;
		// Clear first: a failed read should not leave the picker armed on a stale slot,
		// and reselecting the same file has to fire change again.
		input.value = '';
		replaceIndex = null;
		if (!file || index === null || index >= referenceImages.length) return;
		try {
			const frame = await readFrame(file);
			const origin = referenceImages[index]?.origin;
			referenceImages = referenceImages.map((item, itemIndex) =>
				itemIndex === index ? { ...frame, origin } : item
			);
			markPromptForReview();
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	// Grab the last rendered frame of an existing clip so it can anchor the next one.
	const captureFinalFrame = async (source: GeneratedVideo): Promise<FrameAsset> => {
		const response = await fetch(source.url, {
			headers: { Authorization: `Bearer ${localStorage.token}` }
		});
		if (!response.ok) throw new Error('The source video could not be read.');
		// Read through a blob URL so the canvas stays untainted and auth is not re-negotiated.
		const objectUrl = URL.createObjectURL(await response.blob());
		const element = document.createElement('video');
		try {
			element.muted = true;
			element.playsInline = true;
			element.preload = 'auto';
			element.src = objectUrl;

			await new Promise<void>((resolve, reject) => {
				element.onloadeddata = () => resolve();
				element.onerror = () => reject(new Error('The source video could not be decoded.'));
			});

			// Seeking exactly to duration usually lands past the last frame and paints nothing.
			await new Promise<void>((resolve, reject) => {
				element.onseeked = () => resolve();
				element.onerror = () => reject(new Error('The final frame could not be reached.'));
				element.currentTime = Math.max(0, (element.duration || 0) - 0.05);
			});

			const canvas = document.createElement('canvas');
			canvas.width = element.videoWidth;
			canvas.height = element.videoHeight;
			const context = canvas.getContext('2d');
			if (!context || !canvas.width || !canvas.height) {
				throw new Error('The final frame could not be captured.');
			}
			context.drawImage(element, 0, 0, canvas.width, canvas.height);
			const dataUrl = canvas.toDataURL('image/png');
			return {
				name: `continuation-${source.seed || 'clip'}.png`,
				type: 'image/png',
				size: Math.round((dataUrl.length - dataUrl.indexOf(',') - 1) * 0.75),
				width: canvas.width,
				height: canvas.height,
				dataUrl
			};
		} finally {
			element.src = '';
			URL.revokeObjectURL(objectUrl);
		}
	};

	const continueFromVideo = async (source: GeneratedVideo) => {
		capturingFrame = true;
		try {
			const frame = await captureFinalFrame(source);
			workflowMode = 'first';
			firstFrame = frame;
			lastFrame = null;
			continuationSource = source;
			promptApproved = false;
			// A continuation is a new shot, so let the backend pick a fresh seed.
			seed = '';
			if (['16:9', '9:16', '1:1'].includes(source.aspect_ratio)) {
				aspectRatio = source.aspect_ratio as AspectRatio;
			}
			if (source.megapixels === 0.2 || source.megapixels === 0.4) {
				megapixels = source.megapixels;
			}
			// The video cards sit below the settings panel, so bring the editor back into view.
			scrollContainer?.scrollTo({ top: 0, behavior: 'smooth' });
			toast.success('Final frame captured. Describe what happens next.');
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			capturingFrame = false;
		}
	};

	const clearContinuation = () => {
		continuationSource = null;
		firstFrame = null;
		workflowMode = 'text';
		markPromptForReview();
	};

	const frameMetadata = () => {
		if (workflowMode === 'text') return 'No frame images are attached.';
		if (workflowMode === 'reference') {
			return referenceImages
				.map(
					(image, index) =>
						`<Picture ${index + 1}>: ${image.name}, ${image.width}x${image.height}, ${image.type}, ${humanFileSize(image.size)}.`
				)
				.join('\n');
		}
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

	const workflowLabel = () =>
		workflowMode === 'text'
			? 'text-to-video'
			: workflowMode === 'first'
				? 'first-frame image-to-video'
				: workflowMode === 'first-last'
					? 'first-and-last-frame interpolation'
					: 'reference-to-video using the ordered Picture labels';

	// Turn a handed-over roster into reference frames. The <Picture N> numbering is
	// positional, so the summary must record which indices belong to which character
	// or the model will attribute the wrong face to the wrong name.
	// Turn a handed-over roster into reference frames. <Picture N> numbering is
	// positional, so every entry records the indices it owns, or the model attributes
	// the wrong picture to the wrong subject. Characters are emitted first so their
	// indices stay stable when scene references are added or removed.
	const loadCharacterReferences = async (
		roster: {
			name?: string;
			description?: string;
			imageFileIds?: string[];
			voiceFileId?: string;
			kind?: string;
			appliesTo?: string;
			state?: string;
		}[]
	): Promise<{ images: FrameAsset[]; voices: string[]; summary: string | null }> => {
		const images: FrameAsset[] = [];
		const voices: string[] = [];
		// Built with an explicit constant rather than an inline escape: this file has
		// been bitten twice by escaping in edit tooling.
		const NL = '\n';
		const cast: string[] = [];
		const settingRefs: string[] = [];
		const wardrobe: string[] = [];

		const isCharacter = (entry: { kind?: string }) => (entry?.kind ?? 'character') === 'character';
		// Characters first, then references, preserving order within each group.
		const ordered = [...roster.filter(isCharacter), ...roster.filter((e) => !isCharacter(e))];

		for (const entry of ordered) {
			const fileIds = Array.isArray(entry?.imageFileIds) ? entry.imageFileIds : [];
			const indices: number[] = [];
			for (const fileId of fileIds) {
				if (images.length >= 9) break;
				try {
					const frame = await loadFrameFromFile(fileId, entry?.name ?? 'reference');
					images.push({ ...frame, origin: entry?.name });
					indices.push(images.length);
				} catch (error) {
					console.error(error);
				}
			}
			if (indices.length === 0) continue;

			const pictures = indices.map((index) => `<Picture ${index}>`).join(', ');
			const name = entry?.name ?? 'Unnamed';
			const description =
				entry?.description?.trim() || 'No written description provided; rely on the images.';
			const kind = entry?.kind ?? 'character';

			if (kind === 'location') {
				settingRefs.push(
					`${name} — ${pictures}. Mark fully_preserved. This is the environment the scene takes place in, not a person: it never speaks, moves of its own accord, or is treated as a subject. ${description}`
				);
			} else if (kind === 'outfit') {
				const target = entry?.appliesTo || 'whichever person wears it';
				wardrobe.push(
					`${name} — ${pictures}. Mark attribute_transfer onto ${target}: take the garments only, never the face or body of whoever is pictured wearing them. ${description}`
				);
			} else {
				// A voice only earns an <Audio N> label if the character is actually shown.
				let audioLabel = '';
				if (entry?.voiceFileId && voices.length < MAX_REFERENCE_AUDIOS) {
					try {
						const voice = await loadFrameFromFile(entry.voiceFileId, 'voice');
						voices.push(voice.dataUrl);
						audioLabel = ` Their speaking voice is <Audio ${voices.length}>.`;
					} catch (error) {
						console.error(error);
					}
				}
				// Present state overrides the library description, which is only their
				// default look; a scene should show what they are wearing right now.
				const state = entry?.state?.trim();
				const stateLine = state
					? ` Right now they are: ${state} — this overrides any clothing in the description.`
					: '';
				cast.push(`${name} — ${pictures}.${audioLabel} ${description}${stateLine}`);
			}
		}

		const sections: string[] = [];
		if (cast.length) sections.push('CAST' + NL + cast.join(NL));
		if (settingRefs.length) sections.push('SETTING' + NL + settingRefs.join(NL));
		if (wardrobe.length) sections.push('WARDROBE' + NL + wardrobe.join(NL));
		return {
			images,
			voices,
			summary: sections.length ? sections.join(NL + NL) : null
		};
	};

	const loadFrameFromFile = async (fileId: string, label: string): Promise<FrameAsset> => {
		const response = await fetch(`${WEBUI_API_BASE_URL}/files/${fileId}/content`, {
			headers: { Authorization: `Bearer ${localStorage.token}` }
		});
		if (!response.ok) throw new Error('A reference image could not be read.');
		const blob = await response.blob();
		const dataUrl = await new Promise<string>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(reader.result as string);
			reader.onerror = () => reject(new Error('A reference image could not be decoded.'));
			reader.readAsDataURL(blob);
		});
		const dimensions = await new Promise<{ width: number; height: number }>((resolve) => {
			const image = new Image();
			image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
			image.onerror = () => resolve({ width: 0, height: 0 });
			image.src = dataUrl;
		});
		return {
			name: `${label}.png`,
			type: blob.type || 'image/png',
			size: blob.size,
			width: dimensions.width,
			height: dimensions.height,
			dataUrl
		};
	};

	// A chat message can hand a scene over to the studio. The payload is read once
	// and removed, so reloading the page does not silently redraft an old scene.
	const consumeSceneHandoff = async (): Promise<boolean> => {
		let raw: string | null = null;
		try {
			raw = sessionStorage.getItem('owui-video-scene-handoff');
			if (raw) sessionStorage.removeItem('owui-video-scene-handoff');
		} catch {
			return false;
		}
		if (!raw) return false;
		try {
			const handoff = JSON.parse(raw);
			const scene = typeof handoff?.scene === 'string' ? handoff.scene.trim() : '';
			if (!scene) return false;

			creativeDirection = scene;
			sceneChatId = typeof handoff?.chatId === 'string' ? handoff.chatId : null;
			const context = Array.isArray(handoff?.context) ? handoff.context : [];
			sceneContext =
				context
					.map((item: { role?: string; content?: string }) =>
						typeof item?.content === 'string' && item.content.trim()
							? `${item.role === 'user' ? 'User' : 'Assistant'}: ${item.content.trim()}`
							: ''
					)
					.filter(Boolean)
					.join('\n\n') || null;

			// Only adopt the chat's model when the preference says to; otherwise the
			// saved default already chosen in onMount stands.
			if (
				useChatModel &&
				handoff?.modelId &&
				availableModels.some((model) => model.id === handoff.modelId)
			) {
				selectedModelId = handoff.modelId;
				lastPersistedModel = handoff.modelId;
			}

			// A roster turns this into a Ref2VA generation. Images arrive as file ids and
			// are fetched here, because Ref2VA needs the pixels at generation time.
			const roster = Array.isArray(handoff?.characters) ? handoff.characters : [];
			const loaded = await loadCharacterReferences(roster);
			if (loaded.images.length > 0) {
				selectMode('reference');
				referenceImages = loaded.images;
				sceneVoices = loaded.voices ?? [];
				sceneCharacters = loaded.summary;
			} else {
				// No usable references: fall back to the plain text-to-video path.
				selectMode('text');
				sceneVoices = [];
				sceneCharacters = null;
			}
			return true;
		} catch {
			toast.error('The scene from chat could not be read.');
			return false;
		}
	};

	const clearSceneContext = () => {
		sceneContext = null;
		sceneChatId = null;
		sceneCharacters = null;
		sceneVoices = [];
	};

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
					{
						role: 'system',
						content: workflowMode === 'reference' ? REFERENCE_PROMPT_SYSTEM : TIMELINE_PROMPT_SYSTEM
					},
					{
						role: 'user',
						content: `Creative direction:
${creativeDirection.trim()}

Hard constraints:
- Duration: ${duration} seconds
- Aspect ratio: ${aspectRatio}
- Workflow: ${workflowLabel()}
- Spoken words: ${speechBudget(duration)} at most, counted across every <d> tag combined
- Shots containing dialogue: ${dialogueShotBudget(duration)} at most

Frame metadata only (the image pixels are intentionally unavailable to you):
${frameMetadata()}
${
	sceneCharacters
		? `
Reference sheet for this scene, grouped by role. Each entry lists the pictures that show it, so cite exactly those <Picture N> tags in subject_definitions and never attribute one entry's pictures to another. CAST entries are people. SETTING entries are places and must be described as environment, never as a subject that acts or speaks. WARDROBE entries are clothing to place on the named person, taking garments only:
---
${sceneCharacters}
---
Where a character lists an <Audio N> tag, that recording defines their speaking voice: cite it in subject_definitions alongside their pictures and keep their dialogue in that voice. State that the recording supplies timbre, accent and delivery only, and that none of its original words carry into the video. Never give one character another's audio.
A character with an <Audio N> tag MUST either speak at least one line, written verbatim inside <d>[Language] words.</d>, or be described explicitly as silent in this shot. Never leave a voiced character with no stated speech: the model will invent unintelligible words to fill the gap. Carry dialogue over from the creative direction word for word, but only as much of it as the spoken-word budget allows. Choose the one or two lines that matter most and drop the rest entirely; never paraphrase or compress a long speech to make it fit. A clip that says one line clearly is correct, and one that crams a whole exchange in produces unintelligible speech.
Every named person below must be rendered from their own reference pictures. Where a character has no written description, describe them only as the references and the scene support.
`
		: ''
}${
							sceneContext
								? `
This brief depicts a scene from a chat conversation. The video model has no access to that conversation and no memory of it, so the brief must stand entirely on its own.

Preceding conversation, oldest first. Use it ONLY to establish how people, places, and objects look and sound:
---
${sceneContext}
---

Rules for this scene:
- Describe every visible character from scratch: apparent age, build, skin tone, hair, clothing, and distinguishing features. A name means nothing to the video model, so never identify anyone by name alone.
- Where the conversation does not state an attribute, choose one that fits and state it definitively. Never write "unspecified", "unknown", or hedge with "perhaps".
- Depict only what happens in the creative direction above. Earlier turns are background for appearance and continuity, not events to re-stage.
- The creative direction is one person's turn in that conversation, so it describes only what that person does. The final User turn above is what the other person said and did immediately before it, and this shot opens in its aftermath. Do not re-stage that turn as events, give it its own shot, or write dialogue for it. Use it instead to keep that person continuously present: the posture their action left them in, where they are looking, and how their expression shifts as they take in the reply.
- Give explicit camera framing and movement, and a full soundscape including any speech.
`
								: ''
						}${
							continuationSource
								? `
This clip is a direct continuation. The first-frame anchor is the final frame of the previous clip, so the new shot must begin exactly where that one ended. The previous clip's brief was:
---
${continuationSource.prompt}
---
Carry over the same subjects, setting, lighting, wardrobe, and audio character. Advance the action into new beats; do not restate or replay what already happened. Restart the timeline at [Shot 1] with no timestamp.
`
								: ''
						}
Write the final MiniMax H3 production brief now.`
					}
				]
			});
			const content = response?.choices?.[0]?.message?.content;
			if (!content || typeof content !== 'string') {
				throw new Error('The selected model returned an empty prompt.');
			}
			let draft = cleanModelPrompt(content);
			// The drafting rule forbids these, but a model working from a tracked outfit state
			// that says "lipstick smudged" still reaches for them. One targeted repair pass keeps
			// the sentences whole, where cutting the phrases out would not.
			const avoidedLooks = findAvoidedLooks(draft);
			let avoidedLeft: string[] = [];
			if (avoidedLooks.length) {
				try {
					draft = await removeAvoidedLooks(draft);
				} catch (error) {
					console.error(error);
				}
				avoidedLeft = findAvoidedLooks(draft);
				if (!avoidedLeft.length) {
					toast.info(`Removed ${describeLooks(avoidedLooks)} from the draft.`);
				}
			}
			productionPrompt = draft;
			if (avoidedLeft.length) {
				toast.warning(
					`The draft still mentions ${describeLooks(avoidedLeft)}. The video model renders these badly - edit them out before approving.`
				);
			}
			// The budget is guidance to the drafting model, not something it is bound by, so
			// check the result and say so before four minutes of render proves it the hard way.
			const spokenWords = countSpokenWords(productionPrompt);
			const budget = speechBudget(duration);
			if (spokenWords > budget) {
				toast.warning(
					`Draft ready, but it has ${spokenWords} spoken words for a ${duration}s clip (budget ${budget}). Speech that overruns the clip comes out garbled - trim the dialogue or raise the duration before generating.`
				);
			} else if (!avoidedLeft.length) {
				toast.success('Draft ready. Review and approve it before generating.');
			}
		} catch (error) {
			toast.error(`Prompt drafting failed: ${error}`);
		} finally {
			drafting = false;
		}
	};

	const removeAvoidedLooks = async (brief: string): Promise<string> => {
		const response = await generateOpenAIChatCompletion(localStorage.token, {
			model: selectedModelId,
			stream: false,
			messages: [
				{ role: 'system', content: AVOIDED_LOOKS_REPAIR_SYSTEM },
				{ role: 'user', content: brief }
			]
		});
		const content = response?.choices?.[0]?.message?.content;
		if (typeof content !== 'string' || !content.trim()) return brief;
		const repaired = cleanModelPrompt(content);
		// Dropping a few phrases barely changes the length. A repair that shrinks or grows
		// the brief by much has rewritten it, so keep the original and warn instead.
		const ratio = repaired.length / Math.max(1, brief.length);
		return ratio >= 0.8 && ratio <= 1.1 ? repaired : brief;
	};

	const approvePrompt = () => {
		if (!productionPrompt.trim()) {
			toast.error('Write or draft a production prompt first.');
			return;
		}
		promptApproved = true;
		// Warn rather than repair here: what is approved is exactly what gets sent.
		const looks = findAvoidedLooks(productionPrompt);
		if (looks.length) {
			toast.warning(
				`Approved, but it mentions ${describeLooks(looks)}, which the video model renders badly.`
			);
		}
		const spokenWords = countSpokenWords(productionPrompt);
		const budget = speechBudget(duration);
		if (spokenWords > budget) {
			toast.warning(
				`Approved, but ${spokenWords} spoken words is over the ${budget} that fit in ${duration}s. Expect garbled speech.`
			);
		} else if (!looks.length) {
			toast.success('Prompt approved for generation.');
		}
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

	const archiveCurrentVideo = () => {
		const previous = currentVideo;
		currentVideo = null;
		if (previous && !videoHistory.some((item) => item.url === previous.url)) {
			videoHistory = [previous, ...videoHistory];
		}
	};

	const activeJobStorageKey = () => `owui-video-generation-job:${$user?.id ?? 'default'}`;

	const readTrackedJobs = (): string[] => {
		const raw = localStorage.getItem(activeJobStorageKey());
		if (!raw) return [];
		try {
			const parsed = JSON.parse(raw);
			return Array.isArray(parsed) ? parsed.filter((id) => typeof id === 'string') : [];
		} catch {
			// Before the queue this key held one job id as a bare string, which is not
			// JSON. Carry that job over rather than dropping a render already running.
			return [raw];
		}
	};

	const writeTrackedJobs = (ids: string[]) => {
		if (ids.length) localStorage.setItem(activeJobStorageKey(), JSON.stringify(ids));
		else localStorage.removeItem(activeJobStorageKey());
	};

	const untrackJob = (jobId: string) => {
		trackedJobIds = trackedJobIds.filter((id) => id !== jobId);
		writeTrackedJobs(trackedJobIds);
	};
	const wait = (milliseconds: number) =>
		new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

	// crypto.randomUUID() only exists in secure contexts. This studio is regularly
	// opened over the LAN on plain http, where it is undefined; getRandomValues is
	// available there, so fall back to composing a v4 UUID by hand. The backend
	// validates job_id as a UUID, so the shape has to stay exact.
	const createJobId = (): string => {
		if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
			return crypto.randomUUID();
		}
		const bytes = new Uint8Array(16);
		if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
			crypto.getRandomValues(bytes);
		} else {
			for (let index = 0; index < bytes.length; index += 1) {
				bytes[index] = Math.floor(Math.random() * 256);
			}
		}
		bytes[6] = (bytes[6] & 0x0f) | 0x40;
		bytes[8] = (bytes[8] & 0x3f) | 0x80;
		const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
		return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
	};

	// One loop follows every job this browser is tracking. Each tick asks the server
	// for each job rather than trusting a local model of the queue, because the queue
	// is shared: another tab, or a phone, can add to it or cancel from it.
	const pollQueue = async (resumed = false) => {
		if (queuePolling) return;
		queuePolling = true;
		if (resumed && trackedJobIds.length) toast.info('Reconnected to the video generation queue.');
		try {
			while (!destroyed && trackedJobIds.length > 0) {
				const active: VideoGenerationJob[] = [];
				for (const jobId of [...trackedJobIds]) {
					let job: VideoGenerationJob;
					try {
						job = await getVideoGenerationJob(localStorage.token, jobId);
					} catch (error) {
						const message = String(error);
						if (message.includes('Video generation job not found')) {
							// Pruned, or lost when the server restarted. Nothing to wait for.
							untrackJob(jobId);
							toast.error('A generation is no longer on the server and has been dropped.');
						}
						// Browsers suspend requests while a phone is locked, so any other
						// failure keeps the job and retries on the next tick.
						continue;
					}
					if (job.status === 'completed') {
						untrackJob(jobId);
						const result = job.result?.[0] as GeneratedVideo | undefined;
						if (result?.url) {
							// Only now does the previous clip move to history: with a queue,
							// submitting is no longer the moment a new video replaces it.
							archiveCurrentVideo();
							currentVideo = result;
							// It may already be a history card if it finished before this page loaded.
							videoHistory = videoHistory.filter((item) => item.url !== result.url);
							// The seed field is not filled from the finished job. With a queue it is often
							// holding the next submission's settings, and filling it made "queue again" silently
							// reuse this seed and render the same clip. "Use prompt" on a card restores a seed.
							toast.success('MiniMax H3 video completed.');
						} else {
							toast.error('A completed generation returned no video.');
						}
						continue;
					}
					if (job.status === 'failed') {
						untrackJob(jobId);
						toast.error(`Video generation failed: ${job.error ?? 'unknown error'}`);
						continue;
					}
					if (job.status === 'cancelled') {
						untrackJob(jobId);
						continue;
					}
					active.push(job);
				}
				queueJobs = active;
				const running = active.find((job) => job.status === 'running');
				generating = active.length > 0;
				if (running) {
					// Time the render, not the wait: started_at is when it left the queue.
					const since = running.started_at ?? running.created_at;
					elapsedSeconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
					if (!elapsedTimer) startElapsedTimer();
				} else {
					stopElapsedTimer();
					elapsedSeconds = 0;
				}
				if (!trackedJobIds.length) break;
				await wait(3000);
			}
		} finally {
			queuePolling = false;
			queueJobs = [];
			generating = false;
			stopElapsedTimer();
		}
	};

	const cancelQueuedJob = async (jobId: string) => {
		try {
			await cancelVideoGenerationJob(localStorage.token, jobId);
			untrackJob(jobId);
			queueJobs = queueJobs.filter((job) => job.job_id !== jobId);
			toast.success('Queued generation cancelled.');
		} catch (error) {
			toast.error(`${error}`);
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
		// An empty seed input binds to null, not ''. Treat both as "pick a fresh random seed".
		const seedText = seed === null || seed === undefined ? '' : String(seed).trim();
		const parsedSeed = seedText === '' ? undefined : seedText;
		if (
			parsedSeed !== undefined &&
			(!/^\d+$/.test(parsedSeed) || BigInt(parsedSeed) > 9223372036854775807n)
		) {
			toast.error('Seed must be a non-negative whole number.');
			return;
		}
		// Build everything that can throw BEFORE the request goes out, so a failure
		// here cannot leave the studio thinking a job is in flight.
		// Tolerate a malformed roster rather than throwing inside the prepare step.
		const attachedVoices = Array.isArray(sceneVoices) ? sceneVoices : [];
		const hasDialogue = promptHasDialogue(productionPrompt);
		const voiceUrls = hasDialogue ? attachedVoices : [];
		// Send a brief that cannot ask for speech it never specifies.
		const outboundPrompt = hasDialogue
			? productionPrompt.trim()
			: silenceProductionPrompt(productionPrompt.trim());
		if (attachedVoices.length > 0 && !hasDialogue) {
			toast.warning(
				'No dialogue in the brief, so the voice references were dropped and the shot was marked silent. A voice with nothing to say produces garbled speech. Put the spoken lines in <d>...</d> tags and redraft to use it.'
			);
		}
		let jobId: string;
		let options: VideoGenerationOptions;
		try {
			jobId = createJobId();
			options = {
				mode: workflowMode === 'reference' ? 'reference' : 'text',
				motion_lora: motionLora,
				turbo_lora: turboLora,
				aspect_ratio: aspectRatio,
				megapixels,
				duration,
				...(parsedSeed !== undefined ? { seed: parsedSeed } : {}),
				...(workflowMode !== 'text' && firstFrame
					? { first_frame_data_url: firstFrame.dataUrl }
					: {}),
				...(workflowMode === 'reference'
					? {
							reference_image_data_urls: referenceImages.map((image) => image.dataUrl),
							...(voiceUrls.length > 0 ? { reference_audio_data_urls: voiceUrls } : {})
						}
					: {}),
				...(workflowMode === 'first-last' && lastFrame
					? { last_frame_data_url: lastFrame.dataUrl }
					: {})
			};
		} catch (error) {
			toast.error(`The generation request could not be prepared: ${error}`);
			return;
		}
		// Track the job before submitting: if the response is lost in transit the job
		// may still have been accepted, and the poller will find it.
		trackedJobIds = [...trackedJobIds, jobId];
		writeTrackedJobs(trackedJobIds);
		submitting = true;
		try {
			let accepted: VideoGenerationJob | null = null;
			try {
				accepted = await startVideoGenerationJob(
					localStorage.token,
					jobId,
					outboundPrompt,
					options
				);
			} catch (error) {
				if (!String(error).includes('Server connection failed')) {
					untrackJob(jobId);
					toast.error(`Video generation failed: ${error}`);
					return;
				}
				await wait(1500);
				try {
					// Submitting the same job id twice is idempotent server-side, so a retry
					// after a dropped response cannot queue the same render twice.
					accepted = await startVideoGenerationJob(
						localStorage.token,
						jobId,
						outboundPrompt,
						options
					);
				} catch (retryError) {
					if (!String(retryError).includes('Server connection failed')) {
						untrackJob(jobId);
						toast.error(`Video generation failed: ${retryError}`);
						return;
					}
				}
			}
			const ahead = accepted?.position ?? 0;
			approvedSubmissions += 1;
			lastSubmittedAt = Date.now();
			toast.success(
				ahead > 0
					? `Queued with ${ahead} generation${ahead === 1 ? '' : 's'} ahead of it.`
					: 'Generation started.'
			);
		} finally {
			submitting = false;
		}
		void pollQueue();
	};

	const loadVideoHistory = async (showSuccess = false) => {
		historyLoading = true;
		try {
			const items: GeneratedVideo[] = await getVideoHistory(localStorage.token, 50);
			// The video on screen is already stored server-side; keep it out of the history list.
			videoHistory = currentVideo ? items.filter((item) => item.url !== currentVideo?.url) : items;
			if (showSuccess) {
				toast.success('Video history refreshed.');
			}
		} catch (error) {
			toast.error(`Video history could not be loaded: ${error}`);
		} finally {
			historyLoading = false;
		}
	};

	const reuseVideoPrompt = (video: GeneratedVideo) => {
		const mode: WorkflowMode =
			video.mode === 'reference'
				? 'reference'
				: video.mode === 'first-last'
					? 'first-last'
					: video.mode === 'image'
						? 'first'
						: 'text';
		selectMode(mode);
		productionPrompt = video.prompt;
		promptApproved = false;
		if (['16:9', '9:16', '1:1'].includes(video.aspect_ratio)) {
			aspectRatio = video.aspect_ratio as AspectRatio;
		}
		if (video.megapixels === 0.2 || video.megapixels === 0.4) {
			megapixels = video.megapixels;
		}
		if ([3, 5, 10, 15].includes(video.duration)) {
			duration = video.duration;
		}
		seed = video.seed;
		toast.success(
			mode === 'text'
				? 'Prompt and settings loaded for review.'
				: mode === 'reference'
					? 'Prompt and settings loaded. Reattach the reference images in the same order.'
					: 'Prompt and settings loaded. Reattach the frame images before generating.'
		);
	};

	const formatVideoDate = (timestamp?: number) =>
		timestamp ? new Date(timestamp * 1000).toLocaleString() : 'Just generated';

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

	const requestVideoDeletion = (video: GeneratedVideo) => {
		if (!video.id) {
			toast.error('This video cannot be deleted because its file record is unavailable.');
			return;
		}
		videoPendingDeletion = video;
		showDeleteVideoConfirm = true;
	};

	const deleteHistoryVideo = async () => {
		const video = videoPendingDeletion;
		videoPendingDeletion = null;
		if (!video?.id) return;

		deletingVideoId = video.id;
		try {
			await deleteFileById(localStorage.token, video.id);
			videoHistory = videoHistory.filter((item) => item.id !== video.id);
			if (currentVideo?.id === video.id) currentVideo = null;
			toast.success('Video deleted permanently.');
		} catch (error) {
			toast.error(`The video could not be deleted: ${error}`);
		} finally {
			deletingVideoId = null;
		}
	};

	onMount(async () => {
		if (!canUseVideo) {
			await goto('/');
			return;
		}
		useChatModel = $settings?.videoPromptUseChatModel ?? true;
		// A saved default wins over the generic chat defaults, but only if the model
		// still exists; a deleted model would otherwise leave the selector empty.
		const savedModel = $settings?.videoPromptModel;
		selectedModelId =
			(savedModel && availableModels.some((model) => model.id === savedModel)
				? savedModel
				: undefined) ??
			$settings?.models?.[0] ??
			$config?.default_models?.split(',')?.[0] ??
			availableModels[0]?.id ??
			'';
		lastPersistedModel = selectedModelId;
		loaded = true;
		const handoff = await consumeSceneHandoff();
		await loadVideoHistory();
		// Merge what this browser was following with what the server still has, so a
		// generation queued from a phone shows up here too.
		const stored = readTrackedJobs();
		let serverJobIds: string[] = [];
		try {
			serverJobIds = (await listVideoGenerationJobs(localStorage.token)).map((job) => job.job_id);
		} catch (error) {
			// A queue listing failure must not stop the studio from loading.
			console.error(error);
		}
		trackedJobIds = [...new Set([...stored, ...serverJobIds])];
		writeTrackedJobs(trackedJobIds);
		if (trackedJobIds.length) {
			void pollQueue(true);
		}
		// Draft after history so the studio is usable while the model is thinking.
		if (handoff) void draftPrompt();
	});
	onDestroy(() => {
		destroyed = true;
		queuePolling = false;
		stopElapsedTimer();
	});
</script>

<ConfirmDialog
	bind:show={showDeleteVideoConfirm}
	title="Delete generated video?"
	message="This permanently removes the video file from your OWUI storage. This action cannot be undone."
	confirmLabel="Delete video"
	onConfirm={deleteHistoryVideo}
/>

<svelte:head>
	<title>Video Studio - {$WEBUI_NAME}</title>
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
		<div class="ml-auto text-xs text-gray-500">MiniMax H3 | ComfyUI</div>
	</nav>

	{#if loaded}
		<div class="flex-1 overflow-y-auto" bind:this={scrollContainer}>
			<div class="mx-auto flex w-full max-w-7xl flex-col gap-5 p-4 lg:p-6">
				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<div class="mb-4">
						<h1 class="text-xl font-semibold">Create a video</h1>
						<p class="mt-1 text-sm text-gray-500">
							Shape the idea with a chat model, approve the resulting H3 prompt, then send it to
							ComfyUI.
						</p>
					</div>
					{#if sceneContext}
						<div
							class="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs dark:border-violet-900 dark:bg-violet-950/40"
						>
							<span class="text-violet-800 dark:text-violet-200">
								Scene imported from chat. The surrounding turns are passed to the prompt model so it
								can describe the characters and setting from scratch.
							</span>
							<span class="ml-auto flex shrink-0 gap-3">
								{#if sceneChatId}
									<a
										class="font-medium text-violet-800 hover:underline dark:text-violet-200"
										href="/c/{sceneChatId}">Back to chat</a
									>
								{/if}
								<button
									class="font-medium text-violet-800 hover:underline dark:text-violet-200"
									on:click={clearSceneContext}>Drop context</button
								>
							</span>
						</div>
					{/if}
					{#if continuationSource}
						<div
							class="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs dark:border-blue-900 dark:bg-blue-950/40"
						>
							<span class="text-blue-800 dark:text-blue-200">
								Continuing from a {continuationSource.duration}s clip. Its final frame is the
								starting image and its brief is passed to the prompt model as context, so describe
								only what happens next.
							</span>
							<button
								class="ml-auto shrink-0 font-medium text-blue-800 hover:underline dark:text-blue-200"
								on:click={clearContinuation}>Start fresh instead</button
							>
						</div>
					{/if}
					<div
						class="grid grid-cols-2 gap-1 rounded-xl bg-gray-100 p-1 sm:grid-cols-4 dark:bg-gray-850"
					>
						{#each [{ id: 'text', label: 'Text' }, { id: 'first', label: 'Image' }, { id: 'first-last', label: 'First + last' }, { id: 'reference', label: 'Reference' }] as item}
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
					{#if workflowMode === 'first' || workflowMode === 'first-last'}
						<div class="mt-4 grid gap-3 {workflowMode === 'first-last' ? 'sm:grid-cols-2' : ''}">
							{#each workflowMode === 'first-last' ? [{ role: 'first', label: 'First frame', frame: firstFrame }, { role: 'last', label: 'Last frame', frame: lastFrame }] : [{ role: 'first', label: continuationSource ? 'Final frame of the previous clip' : 'Starting image', frame: firstFrame }] as slot}
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
												{slot.frame.width}x{slot.frame.height}
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
					{#if workflowMode === 'reference'}
						<div class="mt-4">
							<div class="mb-1.5 flex items-center justify-between gap-3">
								<div class="text-xs font-medium text-gray-600 dark:text-gray-300">
									Reference images
								</div>
								<div class="text-xs text-gray-500">{referenceImages.length}/9</div>
							</div>
							<button
								class="flex min-h-24 w-full items-center justify-center rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 text-sm text-gray-500 hover:border-gray-500 dark:border-gray-700 dark:bg-gray-900"
								on:click={() => referenceFileInput.click()}
								on:drop={handleReferenceDrop}
								on:dragover|preventDefault
							>
								Drop reference images or click to browse
							</button>
							<input
								class="hidden"
								type="file"
								multiple
								accept="image/png,image/jpeg,image/webp"
								bind:this={referenceFileInput}
								on:change={handleReferenceInput}
							/>
							<input
								class="hidden"
								type="file"
								accept="image/png,image/jpeg,image/webp"
								bind:this={replaceFileInput}
								on:change={handleReplaceInput}
							/>
							{#if referenceImages.length}
								<div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
									{#each referenceImages as image, index}
										<div
											class="relative overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700"
										>
											<button
												type="button"
												class="group relative block w-full"
												aria-label={image.origin
													? `Swap Picture ${index + 1} (${image.origin}) for this generation`
													: `Swap Picture ${index + 1} for this generation`}
												on:click={() => replaceReferenceImage(index)}
											>
												<img
													src={image.dataUrl}
													alt={`Picture ${index + 1}`}
													class="aspect-video w-full object-contain"
												/>
												<span
													class="absolute inset-0 hidden items-center justify-center bg-black/50 text-xs font-medium text-white group-hover:flex group-focus-visible:flex"
												>
													Swap image
												</span>
											</button>
											<div
												class="flex items-center gap-2 border-t border-gray-200 px-2 py-1.5 text-xs dark:border-gray-700"
											>
												<span class="min-w-0 flex-1 truncate"
													>&lt;Picture {index + 1}&gt;{#if image.origin}
														· {image.origin}{/if}</span
												>
												<button
													class="shrink-0 text-gray-500 hover:text-gray-900 dark:hover:text-white"
													aria-label={`Swap Picture ${index + 1}`}
													on:click={() => replaceReferenceImage(index)}>Swap</button
												>
												<button
													class="shrink-0 text-gray-500 hover:text-red-600"
													aria-label={`Remove Picture ${index + 1}`}
													on:click={() => removeReferenceImage(index)}>Remove</button
												>
											</div>
										</div>
									{/each}
								</div>
							{/if}
							<p class="mt-2 text-xs text-gray-500">
								Order matters: images are connected to Ref2VA as Picture 1 through Picture 9. Pixels
								go only to ComfyUI; the prompt model receives metadata and labels. Click a picture
								to swap it for this generation only — a character's saved images are never changed.
							</p>
						</div>
					{/if}
				</section>

				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<h2 class="font-semibold">1. Generation settings</h2>
					<div class="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4 lg:items-start">
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
								<option value={0.2}>0.2 MP - faster</option>
								<option value={0.4}>0.4 MP - sharper</option>
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
								<option value={15}>15 seconds</option>
							</select>
						</label>
						<div>
							<div class="mb-1.5 flex items-center justify-between text-xs font-medium">
								<label for="video-seed">Seed</label>
								<button
									class="font-normal text-gray-500 hover:underline disabled:opacity-40"
									disabled={seed === null || seed === ''}
									on:click={() => (seed = '')}
								>
									Clear for random
								</button>
							</div>
							<input
								id="video-seed"
								class="w-full rounded-xl border border-gray-200 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								type="number"
								min="0"
								step="1"
								placeholder="Random"
								bind:value={seed}
							/>
							<p class="mt-1.5 text-xs text-gray-500">
								Leave this blank to let each generation pick a new random seed.
							</p>
						</div>

						<div class="sm:col-span-2 lg:col-span-4 space-y-2">
							<label class="flex items-start gap-2 text-xs">
								<input
									type="checkbox"
									class="mt-0.5 size-3.5 accent-gray-700"
									bind:checked={turboLora}
								/>
								<span>
									<span class="font-medium">Turbo LoRA</span>
									<span class="block text-gray-500">Speed. Strength 0.5.</span>
								</span>
							</label>
							<label class="flex items-start gap-2 text-xs">
								<input
									type="checkbox"
									class="mt-0.5 size-3.5 accent-gray-700"
									bind:checked={motionLora}
								/>
								<span>
									<span class="font-medium">Motion LoRA (hmmotion)</span>
									<span class="block text-gray-500">More motion. Strength 1.0.</span>
								</span>
							</label>
							<p class="text-[11px] text-gray-500">
								{motionLora || turboLora
									? 'euler / 12 steps / shift 6 — the same for every combination, so switching one changes only which weights load.'
									: 'Both off: the original 20-step res_multistep result.'}
							</p>
						</div>
					</div>
				</section>

				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<div class="mb-3 flex items-center justify-between gap-3">
						<div>
							<h2 class="font-semibold">2. Creative direction</h2>
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
							<label class="mt-1.5 flex items-center justify-end gap-1.5 text-[11px] text-gray-500">
								<input
									type="checkbox"
									class="size-3 accent-gray-700"
									bind:checked={useChatModel}
									on:change={persistModelPreference}
								/>
								<span>Follow the chat's model</span>
								{#if savingModelPreference}
									<span class="text-gray-400">saving...</span>
								{/if}
							</label>
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
								? 'Drafting prompt...'
								: productionPrompt
									? 'Redraft H3 prompt'
									: 'Draft H3 prompt'}
						</button>
					</div>
				</section>

				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<div class="mb-3 flex items-center justify-between">
						<div>
							<h2 class="font-semibold">3. Review the production prompt</h2>
							<p class="mt-0.5 text-xs text-gray-500">
								Edit anything you want. Edits require approval again.
							</p>
						</div>
						<span
							class="rounded-full px-2.5 py-1 text-xs {!promptApproved
								? 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
								: approvedSubmissions > 0
									? 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
									: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300'}"
						>
							{!promptApproved
								? 'Needs approval'
								: approvedSubmissions > 0
									? 'Submitted'
									: 'Approved'}
						</span>
					</div>
					{#if voicesWithoutDialogue}
						<div
							class="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
						>
							A voice reference is attached but this brief contains no dialogue. A voice with
							nothing to say makes the model invent unintelligible speech, so the voice will be left
							out. Add the spoken lines inside
							<code>&lt;d&gt;[English] ...&lt;/d&gt;</code> tags, or redraft.
						</div>
					{/if}
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
							{!promptApproved
								? 'Approve this prompt'
								: approvedSubmissions > 0
									? 'Prompt submitted'
									: 'Prompt approved'}
						</button>
					</div>
				</section>

				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<h2 class="font-semibold">4. Generate</h2>
					<button
						class="mt-4 flex w-full max-w-sm items-center justify-center gap-2 rounded-xl bg-black px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black"
						disabled={submitting || !promptApproved || !framesReady}
						on:click={generateVideo}
					>
						<VideoCamera className="size-4.5" strokeWidth="2" />
						{submitting
							? 'Submitting...'
							: approvedSubmissions > 0
								? 'Queue this prompt again'
								: queueJobs.length
									? 'Add to queue'
									: 'Generate video'}
					</button>
					{#if promptApproved && approvedSubmissions > 0}
						<p class="mt-2 text-xs font-medium text-blue-700 dark:text-blue-300">
							✓ This prompt was submitted {approvedSubmissions === 1
								? `at ${formatClock(lastSubmittedAt)}`
								: `${approvedSubmissions} times, most recently at ${formatClock(lastSubmittedAt)}`}.
							Edit or redraft it to make a new one, or queue it again for another take.{#if seedIsSet}
								The seed field is set, so another take will come out identical; clear it for a
								different result.{/if}
						</p>
					{/if}
					{#if queueJobs.length}
						<div class="mt-3 rounded-xl border border-gray-200 dark:border-gray-700">
							<div
								class="border-b border-gray-200 px-3 py-2 text-xs font-medium dark:border-gray-700"
							>
								Queue ({queueJobs.length})
							</div>
							<ul class="divide-y divide-gray-200 dark:divide-gray-700">
								<!-- Newest first, so the oldest - the one rendering - sits at the bottom. -->
								{#each [...queueJobs].reverse() as job (job.job_id)}
									<li class="flex items-center gap-3 px-3 py-2 text-xs">
										<span class="w-16 shrink-0 tabular-nums text-gray-500">
											{job.status === 'running' ? 'Rendering' : `Waiting`}
										</span>
										<span class="min-w-0 flex-1 truncate text-gray-600 dark:text-gray-300">
											{job.label || 'Untitled brief'}
										</span>
										{#if job.status === 'running'}
											<span class="shrink-0 tabular-nums text-gray-500">{elapsedSeconds}s</span>
										{:else}
											<span class="shrink-0 tabular-nums text-gray-400"
												>{(job.position ?? 0) + 1} in line</span
											>
											<button
												class="shrink-0 text-gray-500 hover:text-red-600"
												on:click={() => cancelQueuedJob(job.job_id)}>Cancel</button
											>
										{/if}
									</li>
								{/each}
							</ul>
						</div>
						<p class="mt-2 text-xs text-gray-500">
							Generations run one at a time. You can draft and queue another now, or lock your phone
							and come back; Video Studio reconnects to the queue.
						</p>
					{:else if !framesReady}
						<p class="mt-2 text-xs text-amber-600">Attach the required frame images.</p>
					{:else if !promptApproved}
						<p class="mt-2 text-xs text-gray-500">Approve the production prompt to continue.</p>
					{/if}
					<div
						class="mt-5 border-t border-gray-200 pt-4 text-xs leading-5 text-gray-500 dark:border-gray-800"
					>
						<p>
							Reference mode uses the dedicated Ref2VA model and supports up to 9 ordered images.
						</p>
						<p class="mt-2">
							The normal chat pipeline remains text-only for video generation; frame and reference
							workflows live only in this studio.
						</p>
					</div>
				</section>

				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<div class="mb-3 flex items-center justify-between gap-3">
						<div>
							<h2 class="font-semibold">Current video</h2>
							<p class="mt-0.5 text-xs text-gray-500">
								The latest result. It moves to the history below when the next one finishes.
							</p>
						</div>
						{#if currentVideo}
							<span class="shrink-0 text-xs text-gray-400"
								>{formatVideoDate(currentVideo.created_at)}</span
							>
						{/if}
					</div>
					<!-- A finished clip always shows. Progress on anything still queued is in
					     the queue panel, so the placeholder is only for when nothing is done yet. -->
					{#if currentVideo}
						{@const video = currentVideo}
						<article
							class="mx-auto w-full max-w-3xl overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800"
						>
							<!-- svelte-ignore a11y-media-has-caption -->
							<video class="aspect-video w-full bg-black object-contain" src={video.url} controls
							></video>
							<div class="flex flex-wrap items-center gap-3 p-3 text-xs text-gray-500">
								<span>{video.duration}s | {video.aspect_ratio} | seed {video.seed}</span>
								<div class="ml-auto flex shrink-0 gap-3">
									<button
										class="font-medium text-gray-800 hover:underline disabled:opacity-40 dark:text-gray-200"
										disabled={capturingFrame}
										on:click={() => continueFromVideo(video)}
										>{capturingFrame ? 'Capturing...' : 'Continue from end'}</button
									>
									<button
										class="font-medium text-gray-800 hover:underline dark:text-gray-200"
										on:click={() => reuseVideoPrompt(video)}>Use prompt</button
									>
									<button
										class="font-medium text-gray-800 hover:underline dark:text-gray-200"
										on:click={() => downloadVideo(video)}>Download</button
									>
								</div>
							</div>
						</article>
					{:else if generating}
						<p
							class="rounded-xl bg-gray-50 py-10 text-center text-sm text-gray-500 dark:bg-gray-900"
						>
							Rendering with ComfyUI... {elapsedSeconds}s
						</p>
					{:else}
						<p
							class="rounded-xl bg-gray-50 py-10 text-center text-sm text-gray-500 dark:bg-gray-900"
						>
							Your next generated video will appear here.
						</p>
					{/if}
				</section>

				<section class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
					<div class="flex items-center justify-between gap-3">
						<div>
							<h2 class="font-semibold">Video history</h2>
							<p class="mt-0.5 text-xs text-gray-500">Your 50 most recent generated videos.</p>
						</div>
						<button
							class="shrink-0 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium hover:bg-gray-50 disabled:opacity-40 dark:border-gray-700 dark:hover:bg-gray-850"
							disabled={historyLoading}
							on:click={() => loadVideoHistory(true)}
						>
							{historyLoading ? 'Refreshing...' : 'Refresh'}
						</button>
					</div>
					{#if historyLoading && videoHistory.length === 0}
						<p
							class="mt-4 rounded-xl bg-gray-50 py-8 text-center text-sm text-gray-500 dark:bg-gray-900"
						>
							Loading video history...
						</p>
					{:else if videoHistory.length === 0}
						<p
							class="mt-4 rounded-xl bg-gray-50 py-8 text-center text-sm text-gray-500 dark:bg-gray-900"
						>
							Previously generated videos will appear here.
						</p>
					{:else}
						<div class="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
							{#each videoHistory as video (video.url)}
								<article
									class="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800"
								>
									<!-- svelte-ignore a11y-media-has-caption -->
									<video
										class="aspect-video w-full bg-black object-contain"
										src={video.url}
										preload="metadata"
										controls
									></video>
									<div
										class="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-xs text-gray-500"
									>
										<span>{video.duration}s | {video.aspect_ratio} | seed {video.seed}</span>
										<span class="text-gray-400">{formatVideoDate(video.created_at)}</span>
										<div class="ml-auto flex shrink-0 gap-3">
											<button
												class="font-medium text-gray-800 hover:underline disabled:opacity-40 dark:text-gray-200"
												disabled={capturingFrame}
												on:click={() => continueFromVideo(video)}
												>{capturingFrame ? 'Capturing...' : 'Continue from end'}</button
											>
											<button
												class="font-medium text-gray-800 hover:underline dark:text-gray-200"
												on:click={() => reuseVideoPrompt(video)}>Use prompt</button
											>
											<button
												class="font-medium text-gray-800 hover:underline dark:text-gray-200"
												on:click={() => downloadVideo(video)}>Download</button
											>
											<button
												class="font-medium text-red-600 hover:underline disabled:opacity-40 dark:text-red-400"
												disabled={!video.id || deletingVideoId === video.id}
												on:click={() => requestVideoDeletion(video)}
												>{deletingVideoId === video.id ? 'Deleting...' : 'Delete'}</button
											>
										</div>
									</div>
								</article>
							{/each}
						</div>
					{/if}
				</section>
			</div>
		</div>
	{:else}
		<div class="flex flex-1 items-center justify-center text-sm text-gray-500">
			Loading Video Studio...
		</div>
	{/if}
</div>
