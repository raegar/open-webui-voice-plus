<script lang="ts">
	import { config, models, settings, showCallOverlay, TTSWorker } from '$lib/stores';
	import { onMount, tick, getContext, onDestroy, createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	import { blobToFile } from '$lib/utils';
	import { generateEmoji } from '$lib/apis';
	import { synthesizeOpenAISpeech, transcribeAudio } from '$lib/apis/audio';

	import { toast } from 'svelte-sonner';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import VideoInputMenu from './CallOverlay/VideoInputMenu.svelte';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	export let eventTarget: EventTarget;
	export let submitPrompt: Function;
	export let stopResponse: Function;
	export let files;
	export let chatId;
	export let modelId;
	export let generatedImageUrl: string | null = null;

	let wakeLock = null;
	let model = null;
	let loading = false;
	let confirmed = false;
	let interrupted = false;
	let assistantSpeaking = false;
	let emoji = null;
	let camera = false;
	let cameraStream = null;
	let chatStreaming = false;
	let rmsLevel = 0;
	let hasStartedSpeaking = false;
	let mediaRecorder;
	let audioStream = null;
	let audioChunks = [];
	let videoInputDevices = [];
	let selectedVideoInputDeviceId = null;

	// ── Noise-adaptive threshold state ──────────────────────────────────────
	let noisePeak = -65;         // slow-moving average of background noise PEAKS (not floor)
	let thresholdDb = -35;       // dB level above which we consider it voice
	let useManualThreshold = false;
	const NOISE_OFFSET = 8;      // threshold sits this many dB above the noise peak ceiling
	const CALIB_FRAMES = 60;     // ~1.5s of calibration at rAF rate
	let calibFrames = 0;
	let isCalibrating = true;

	// Meter display values (reactive, updated each frame)
	let meterDb = -65;
	let noiseFloorDb = -65;      // tracks noisePeak for meter display
	let thresholdMarkerDb = -35;
	let peakDb = -65;

	// Threshold panel state
	let showThreshPanel = false;
	let panelThreshValue = -35;

	// Send countdown
	let silencePct = 0;
	let showCountdown = false;

	// Avatar pulse values driven by JS each frame
	let avatarScale = 1;
	let avatarGlow = 0;
	let ripple1Opacity = 0;
	let ripple2Opacity = 0;
	let ripple2Scale = 1;
	let ripple2Phase = 0;

	let isMuted = false;         // user-toggled mic mute (pauses silence detection)

	// Analyser ref so we can share it between startRecording and meter loop
	let sharedAnalyser = null;
	let sharedTimeDomainData: Uint8Array = null;
	let sharedFreqData: Uint8Array = null;

	const DB_MIN = -65;
	const DB_MAX = 0;

	function dbToPct(db: number): number {
		return Math.max(0, Math.min(100, (db - DB_MIN) / (DB_MAX - DB_MIN) * 100));
	}

	// ── Camera helpers (unchanged) ───────────────────────────────────────────
	const getVideoInputDevices = async () => {
		const devices = await navigator.mediaDevices.enumerateDevices();
		videoInputDevices = devices.filter((device) => device.kind === 'videoinput');
		if (!!navigator.mediaDevices.getDisplayMedia) {
			videoInputDevices = [...videoInputDevices, { deviceId: 'screen', label: 'Screen Share' }];
		}
		if (selectedVideoInputDeviceId === null && videoInputDevices.length > 0) {
			selectedVideoInputDeviceId = videoInputDevices[0].deviceId;
		}
	};

	const startCamera = async () => {
		await getVideoInputDevices();
		if (cameraStream === null) {
			camera = true;
			await tick();
			try { await startVideoStream(); } catch (err) { console.error('Error accessing webcam: ', err); }
		}
	};

	const startVideoStream = async () => {
		const video = document.getElementById('camera-feed');
		if (video) {
			if (selectedVideoInputDeviceId === 'screen') {
				cameraStream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: 'always' }, audio: false });
			} else {
				cameraStream = await navigator.mediaDevices.getUserMedia({ video: { deviceId: selectedVideoInputDeviceId ? { exact: selectedVideoInputDeviceId } : undefined } });
			}
			if (cameraStream) {
				await getVideoInputDevices();
				video.srcObject = cameraStream;
				await video.play();
			}
		}
	};

	const stopVideoStream = async () => {
		if (cameraStream) { cameraStream.getTracks().forEach((t) => t.stop()); }
		cameraStream = null;
	};

	const takeScreenshot = () => {
		const video = document.getElementById('camera-feed');
		const canvas = document.getElementById('camera-canvas');
		if (!canvas) return;
		const context = canvas.getContext('2d');
		canvas.width = video.videoWidth;
		canvas.height = video.videoHeight;
		context.drawImage(video, 0, 0, video.videoWidth, video.videoHeight);
		return canvas.toDataURL('image/png');
	};

	const stopCamera = async () => { await stopVideoStream(); camera = false; };

	// ── Audio / recording (structure preserved, threshold logic replaced) ───
	const MIN_DECIBELS = -55;

	// ── Whisper hallucination filter ───────────────────────────────────────
	// Whisper commonly hallucinates these when given noise-only audio.
	// Matched case-insensitively against the trimmed transcript.
	const HALLUCINATION_PATTERNS = [
		/^(\s*(thank(s| you\.?|you\.?)|thanks\.?|bye\.?|goodbye\.?|you\.?)\s*[.!]?\s*)+$/i,
		/^[\d.,\s]*(kg|g|lb|oz|km|m|cm|mm|ml|l|%)+[\d.,\s]*/i,  // weight/measurement spam
		/^(\s*[\u4e00-\u9fff]{1,4}\s*)+$/,                       // CJK filler (short repeated chars)
		/^(\W|\s)+$/,                                              // punctuation / whitespace only
		/\b\d{1,2}\s*[ap]m\b/i,                                   // any timestamp token (4PM, 12am etc)
		/\d+\s*[°º]\s*[cCfF]/,                                    // temperature values (2.5°C, 4.5°F)
		/\d+\s*[°º]/,                                               // degree symbols with numbers
		/[\u0400-\u04ff]/,                                          // Cyrillic characters
		/^[\w\s]*\d+([.,]\d+)?\s*[a-z]{1,3}(\s+[\d.,]+\s*[a-z°]{0,3}){2,}/i,  // repeated measurement pattern
		/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/,            // any CJK/Japanese characters
	];

	// Returns true if the text looks like a Whisper hallucination
	const isHallucination = (text: string): boolean => {
		const t = text.trim();
		if (t.length === 0) return true;
		// Repeated token detection — if splitting on spaces gives ≥3 tokens
		// and >60% are identical, it's almost certainly a hallucination
		const tokens = t.toLowerCase().split(/\s+/);
		if (tokens.length >= 3) {
			const freq = new Map<string, number>();
			for (const tok of tokens) freq.set(tok, (freq.get(tok) ?? 0) + 1);
			const maxFreq = Math.max(...freq.values());
			if (maxFreq / tokens.length > 0.4) {
				console.log('%c%s', 'color:orange;font-size:14px;', `🚫 Hallucination (repeated token "${[...freq.entries()].find(([,v])=>v===maxFreq)?.[0]}"): "${t}"`);
				return true;
			}
		}
		// Timestamp-density: >35% of tokens look like times => keyboard/noise hallucination
		if (tokens.length >= 2) {
			const timeTokens = tokens.filter(tok => /^\d{1,2}[ap]m$/.test(tok));
			if (timeTokens.length / tokens.length > 0.35) {
				console.log('%c%s', 'color:orange;font-size:14px;', '🚫 Hallucination (timestamp spam): ' + t);
				return true;
			}
		}

		// Short filler word repetition — catches 'and and and and', 'the the the'
		const fillerWords = ['and','the','or','but','so','to','of','a','an','it','is','in','on','at'];
		const fillerTokens = tokens.filter(tok => fillerWords.includes(tok));
		if (tokens.length >= 3 && fillerTokens.length / tokens.length > 0.5) {
			console.log('%c%s', 'color:orange;font-size:14px;', '🚫 Hallucination (filler spam): ' + t);
			return true;
		}

		// Digit-stream check — mostly single digits/short numbers with few real words
		// catches '2 2 3 4 4 5 5 5 10 5 5' style noise hallucinations
		const digitTokens = tokens.filter(tok => /^\d+([.,]\d+)?$/.test(tok));
		if (tokens.length >= 4 && digitTokens.length / tokens.length > 0.5) {
			console.log('%c%s', 'color:orange;font-size:14px;', '🚫 Hallucination (digit stream): ' + t);
			return true;
		}

		// Mixed digit+gibberish check — catches '1 holi 2 holivi 1 loss 1 holi 2 hol Stacy'
		// Signals: multiple digit tokens AND average word length is very short (garbled fragments)
		if (tokens.length >= 4 && digitTokens.length >= 2) {
			const wordTokens = tokens.filter(tok => /^[a-z]+$/i.test(tok));
			const avgWordLen = wordTokens.length
				? wordTokens.reduce((sum, tok) => sum + tok.length, 0) / wordTokens.length
				: 0;
			// Short avg word length + multiple numbers = noise hallucination
			if (avgWordLen > 0 && avgWordLen < 5 && digitTokens.length / tokens.length > 0.25) {
				console.log('%c%s', 'color:orange;font-size:14px;', '🚫 Hallucination (digit+gibberish, avg word len ' + avgWordLen.toFixed(1) + '): ' + t);
				return true;
			}
		}

		for (const pattern of HALLUCINATION_PATTERNS) {
			if (pattern.test(t)) {
				console.log('%c%s', 'color:orange;font-size:14px;', `🚫 Hallucination (pattern match): "${t}"`);
				return true;
			}
		}
		return false;
	};

	const transcribeHandler = async (audioBlob) => {
		await tick();
		const file = blobToFile(audioBlob, 'recording.wav');
		const res = await transcribeAudio(localStorage.token, file, $settings?.audio?.stt?.language).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			console.log('[transcribe] full response:', JSON.stringify(res));
			if (res.text !== '' && !isHallucination(res.text)) {
				const _responses = await submitPrompt(res.text, { _raw: true });
				console.log(_responses);
			}
		}
	};

	const stopRecordingCallback = async (_continue = true) => {
		if ($showCallOverlay) {
			console.log('%c%s', 'color: red; font-size: 20px;', '🚨 stopRecordingCallback 🚨');
			const _audioChunks = audioChunks.slice(0);
			audioChunks = [];
			mediaRecorder = false;
			if (_continue) { startRecording(); }
			if (confirmed) {
				loading = true;
				emoji = null;
				if (cameraStream) {
					files = [{ type: 'image', url: takeScreenshot() }];
				}
				const audioBlob = new Blob(_audioChunks, { type: 'audio/wav' });
				await transcribeHandler(audioBlob);
				confirmed = false;
				loading = false;
			}
		} else {
			audioChunks = [];
			mediaRecorder = false;
			if (audioStream) { audioStream.getTracks().forEach((t) => t.stop()); }
			audioStream = null;
		}
	};

	const startRecording = async () => {
		if ($showCallOverlay) {
			if (!audioStream) {
				audioStream = await navigator.mediaDevices.getUserMedia({
					audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
				});
			}
			mediaRecorder = new MediaRecorder(audioStream);
			mediaRecorder.onstart = () => { console.log('Recording started'); audioChunks = []; };
			mediaRecorder.ondataavailable = (event) => { if (hasStartedSpeaking) { audioChunks.push(event.data); } };
			mediaRecorder.onstop = (e) => { console.log('Recording stopped', audioStream, e); stopRecordingCallback(); };
			analyseAudio(audioStream);
		}
	};

	const stopAudioStream = async () => {
		try { if (mediaRecorder) { mediaRecorder.stop(); } } catch (error) { console.log('Error stopping audio stream:', error); }
		if (!audioStream) return;
		audioStream.getAudioTracks().forEach((track) => track.stop());
		audioStream = null;
	};

	const calculateRMS = (data: Uint8Array) => {
		let sumSquares = 0;
		for (let i = 0; i < data.length; i++) {
			const normalizedValue = (data[i] - 128) / 128;
			sumSquares += normalizedValue * normalizedValue;
		}
		return Math.sqrt(sumSquares / data.length);
	};

	// ── Manual send (called by Send button) ─────────────────────────────────
	const manualSend = () => {
		if (isCalibrating || !mediaRecorder) return;
		console.log('%c%s', 'color: blue; font-size: 20px;', '✉️ Manual send triggered');
		confirmed = true;
		mediaRecorder.stop();
		// Reset silence tracking
		showCountdown = false;
		silencePct = 0;
	};

	// ── Core audio analysis — replaces the original analyseAudio ────────────
	const analyseAudio = (stream) => {
		const audioContext = new AudioContext();
		const audioStreamSource = audioContext.createMediaStreamSource(stream);
		const analyser = audioContext.createAnalyser();
		analyser.minDecibels = MIN_DECIBELS;
		analyser.fftSize = 512;
		analyser.smoothingTimeConstant = 0.5;
		audioStreamSource.connect(analyser);

		sharedAnalyser = analyser;

		const bufferLength = analyser.frequencyBinCount;
		const domainData = new Uint8Array(bufferLength);
		const timeDomainData = new Uint8Array(analyser.fftSize);
		sharedTimeDomainData = timeDomainData;
		sharedFreqData = domainData;

		// Silence tracking
		let lastSoundTime = Date.now();
		hasStartedSpeaking = false;

		// Noise floor calibration accumulators
		let noiseAccum = 0;
		let noiseCount = 0;

		console.log('🔊 Sound detection started', lastSoundTime, hasStartedSpeaking);

		const detectSound = () => {
			const processFrame = () => {
				if (!mediaRecorder || !$showCallOverlay) return;

				if (assistantSpeaking && !($settings?.voiceInterruption ?? false)) {
					analyser.maxDecibels = 0;
					analyser.minDecibels = -1;
				} else {
					analyser.minDecibels = MIN_DECIBELS;
					analyser.maxDecibels = -30;
				}

				analyser.getByteTimeDomainData(timeDomainData);
				analyser.getByteFrequencyData(domainData);

				rmsLevel = calculateRMS(timeDomainData);

				// Convert RMS to dB — proper log scale, same as the original uses internally
				const currentDb = rmsLevel > 0
					? Math.max(DB_MIN, 20 * Math.log10(rmsLevel))
					: DB_MIN;

				// ── Noise peak ceiling + threshold tracking ──────────────────
				// Instead of tracking the quietest moments (floor), we track
				// the average PEAK level of background noise during silence.
				// This gives us the top of the noise band, so threshold sits
				// just above where background noise actually reaches.
				// Only updates when user is not speaking.

				const userIsSpeaking = currentDb > thresholdDb;

				if (calibFrames < CALIB_FRAMES) {
					calibFrames++;
					// During calibration: fast EMA tracking peaks
					// rises quickly to catch real noise ceiling, falls slowly
					if (currentDb > noisePeak) noisePeak = noisePeak * 0.5 + currentDb * 0.5;
					else                       noisePeak = noisePeak * 0.85 + currentDb * 0.15;
					if (!useManualThreshold) {
						thresholdDb = noisePeak + NOISE_OFFSET;
						panelThreshValue = Math.round(thresholdDb);
					}
					if (calibFrames >= CALIB_FRAMES) {
						isCalibrating = false;
					}
				} else if (!userIsSpeaking) {
					// Post-calibration, silence only:
					// rises fairly readily to follow increasing noise (e.g. motorway)
					// falls slowly when it gets quieter
					if (currentDb > noisePeak) noisePeak = noisePeak * 0.97 + currentDb * 0.03;
					else                       noisePeak = noisePeak * 0.998 + currentDb * 0.002;
					if (!useManualThreshold) {
						thresholdDb = noisePeak + NOISE_OFFSET;
						panelThreshValue = Math.round(thresholdDb);
					}
				}
				// If userIsSpeaking: noisePeak and threshold frozen — voice can't corrupt baseline

				// Update peak
				if (currentDb > peakDb) peakDb = currentDb;
				else peakDb = peakDb * 0.991 + currentDb * 0.009;

				// Update reactive meter values (Svelte will update DOM)
				meterDb = currentDb;
				noiseFloorDb = noisePeak;   // meter shows noise peak ceiling, not the floor
				thresholdMarkerDb = thresholdDb;

				// ── Voice / silence detection (adaptive) ─────────────────────
				const hasSound = domainData.some((value) => value > 0);
				const aboveThreshold = currentDb > thresholdDb;

				if (isMuted) {
					// While muted: keep the frame loop alive but reset silence clock
					// so we don't auto-send the moment user unmutes
					lastSoundTime = Date.now();
					showCountdown = false;
					silencePct = 0;
				} else if (hasSound && !isCalibrating) {
					if (aboveThreshold) {
						console.log('%c%s', 'color: green; font-size: 16px;', '🔊 Sound detected');
						if (mediaRecorder && mediaRecorder.state !== 'recording') {
							mediaRecorder.start();
						}
						if (!hasStartedSpeaking) {
							hasStartedSpeaking = true;
							stopAllAudio();
						}
						lastSoundTime = Date.now();
						showCountdown = false;
						silencePct = 0;
					}
				}

				// ── Adaptive silence detection ────────────────────────────────
				if (!isMuted && hasStartedSpeaking && !isCalibrating) {
					const silenceMs = Date.now() - lastSoundTime;
					if (!aboveThreshold) {
						// Show countdown and fire when 2000ms of silence
						showCountdown = true;
						silencePct = Math.min(100, (silenceMs / 2000) * 100);
						if (silenceMs > 2000) {
							confirmed = true;
							showCountdown = false;
							silencePct = 0;
							console.log('%c%s', 'color: red; font-size: 20px;', '🔇 Silence detected (adaptive)');
							if (mediaRecorder) {
								mediaRecorder.stop();
								return;
							}
						}
					} else {
						showCountdown = false;
						silencePct = 0;
					}
				}

				// ── Avatar pulse (reactive vars updated, template uses them) ──
				const voiceIntensity = (!isCalibrating && !isMuted && aboveThreshold)
					? Math.min(1, (currentDb - thresholdDb) / 18)
					: 0;

				avatarScale = avatarScale + (( 1 + voiceIntensity * 0.12) - avatarScale) * (aboveThreshold ? 0.35 : 0.07);
				avatarGlow  = avatarGlow  + (voiceIntensity * 0.22 - avatarGlow)          * (aboveThreshold ? 0.35 : 0.07);
				ripple1Opacity = ripple1Opacity + (voiceIntensity * 0.7 - ripple1Opacity)  * (aboveThreshold ? 0.3 : 0.06);
				ripple2Phase += 0.04;
				const r2Target = aboveThreshold ? Math.max(0, Math.sin(ripple2Phase) * 0.4 * voiceIntensity) : 0;
				ripple2Opacity = ripple2Opacity * 0.93 + r2Target * 0.07;
				ripple2Scale = 1 + ripple2Opacity * 0.18;

				window.requestAnimationFrame(processFrame);
			};

			window.requestAnimationFrame(processFrame);
		};

		detectSound();
	};

	// ── Threshold panel controls ─────────────────────────────────────────────
	const applyThreshold = (db: number) => {
		thresholdDb = db;
		panelThreshValue = db;
		useManualThreshold = true;
	};

	// ── TTS / audio playback (100% unchanged from original) ─────────────────
	let finishedMessages = {};
	let currentMessageId = null;
	let currentUtterance = null;

	const getVoiceId = () => {
		if (model?.info?.meta?.tts?.voice) return model.info.meta.tts.voice;
		if ($settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice) {
			return $settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice;
		}
		return $config?.audio?.tts?.voice;
	};

	const speakSpeechSynthesisHandler = (content) => {
		if ($showCallOverlay) {
			return new Promise((resolve) => {
				let voices = [];
				const getVoicesLoop = setInterval(async () => {
					voices = await speechSynthesis.getVoices();
					if (voices.length > 0) {
						clearInterval(getVoicesLoop);
						const voiceId = getVoiceId();
						const voice = voices?.filter((v) => v.voiceURI === voiceId)?.at(0) ?? undefined;
						currentUtterance = new SpeechSynthesisUtterance(content);
						currentUtterance.rate = $settings.audio?.tts?.playbackRate ?? 1;
						if (voice) { currentUtterance.voice = voice; }
						speechSynthesis.speak(currentUtterance);
						currentUtterance.onend = async (e) => { await new Promise((r) => setTimeout(r, 200)); resolve(e); };
					}
				}, 100);
			});
		} else { return Promise.resolve(); }
	};

	const playAudio = (audio) => {
		if ($showCallOverlay) {
			return new Promise((resolve) => {
				const audioElement = document.getElementById('audioElement') as HTMLAudioElement;
				if (audioElement) {
					audioElement.src = audio.src;
					audioElement.muted = true;
					audioElement.playbackRate = $settings.audio?.tts?.playbackRate ?? 1;
					audioElement.play().then(() => { audioElement.muted = false; }).catch(console.error);
					audioElement.onended = async (e) => { await new Promise((r) => setTimeout(r, 100)); resolve(e); };
				}
			});
		} else { return Promise.resolve(); }
	};

	const stopAllAudio = async () => {
		assistantSpeaking = false;
		interrupted = true;
		if (chatStreaming) { stopResponse(); }
		if (currentUtterance) { speechSynthesis.cancel(); currentUtterance = null; }
		const audioElement = document.getElementById('audioElement');
		if (audioElement) { audioElement.muted = true; audioElement.pause(); audioElement.currentTime = 0; }
	};

	let audioAbortController = new AbortController();
	const audioCache = new Map();
	const emojiCache = new Map();

	const fetchAudio = async (content) => {
		if (!audioCache.has(content)) {
			try {
				if ($settings?.showEmojiInCall ?? false) {
					const e = await generateEmoji(localStorage.token, modelId, content, chatId);
					if (e) { emojiCache.set(content, e); }
				}
				if ($settings.audio?.tts?.engine === 'browser-kokoro') {
					const url = await $TTSWorker.generate({ text: content, voice: getVoiceId() }).catch((e) => { console.error(e); toast.error(`${e}`); });
					if (url) { audioCache.set(content, new Audio(url)); }
				} else if ($config.audio.tts.engine !== '') {
					const res = await synthesizeOpenAISpeech(localStorage.token, getVoiceId(), content).catch((e) => { console.error(e); return null; });
					if (res) { const blob = await res.blob(); audioCache.set(content, new Audio(URL.createObjectURL(blob))); }
				} else {
					audioCache.set(content, true);
				}
			} catch (error) { console.error('Error synthesizing speech:', error); }
		}
		return audioCache.get(content);
	};

	let messages = {};

	const monitorAndPlayAudio = async (id, signal) => {
		while (!signal.aborted) {
			if (messages[id] && messages[id].length > 0) {
				const content = messages[id].shift();
				if (audioCache.has(content)) {
					if (($settings?.showEmojiInCall ?? false) && emojiCache.has(content)) { emoji = emojiCache.get(content); } else { emoji = null; }
					if ($config.audio.tts.engine !== '') {
						try {
							console.log('%c%s', 'color: red; font-size: 20px;', `Playing audio for content: ${content}`);
							await playAudio(audioCache.get(content));
							await new Promise((r) => setTimeout(r, 200));
						} catch (error) { console.error('Error playing audio:', error); }
					} else {
						await speakSpeechSynthesisHandler(content);
					}
				} else {
					messages[id].unshift(content);
					console.log(`Audio for "${content}" not yet available in the cache, re-queued...`);
					await new Promise((r) => setTimeout(r, 200));
				}
			} else if (finishedMessages[id] && messages[id] && messages[id].length === 0) {
				assistantSpeaking = false;
				break;
			} else {
				await new Promise((r) => setTimeout(r, 200));
			}
		}
		console.log(`Audio monitoring and playing stopped for message ID ${id}`);
	};

	const chatStartHandler = async (e) => {
		const { id } = e.detail;
		chatStreaming = true;
		if (currentMessageId !== id) {
			console.log(`Received chat start event for message ID ${id}`);
			currentMessageId = id;
			if (audioAbortController) { audioAbortController.abort(); }
			audioAbortController = new AbortController();
			assistantSpeaking = true;
			monitorAndPlayAudio(id, audioAbortController.signal);
		}
	};

	const chatEventHandler = async (e) => {
		const { id, content } = e.detail;
		if (currentMessageId === id) {
			console.log(`Received chat event for message ID ${id}: ${content}`);
			try {
				if (messages[id] === undefined) { messages[id] = [content]; } else { messages[id].push(content); }
				fetchAudio(content);
			} catch (error) { console.error('Failed to fetch or play audio:', error); }
		}
	};

	const chatFinishHandler = async (e) => {
		const { id } = e.detail;
		finishedMessages[id] = true;
		chatStreaming = false;
	};

	onMount(async () => {
		const setWakeLock = async () => {
			try { wakeLock = await navigator.wakeLock.request('screen'); } catch (err) { console.log(err); }
			if (wakeLock) { wakeLock.addEventListener('release', () => { console.log('Wake Lock released'); }); }
		};
		if ('wakeLock' in navigator) {
			await setWakeLock();
			document.addEventListener('visibilitychange', async () => {
				if (wakeLock !== null && document.visibilityState === 'visible') { await setWakeLock(); }
			});
		}
		model = $models.find((m) => m.id === modelId);
		startRecording();
		eventTarget.addEventListener('chat:start', chatStartHandler);
		eventTarget.addEventListener('chat', chatEventHandler);
		eventTarget.addEventListener('chat:finish', chatFinishHandler);
		return async () => {
			await stopAllAudio();
			stopAudioStream();
			eventTarget.removeEventListener('chat:start', chatStartHandler);
			eventTarget.removeEventListener('chat', chatEventHandler);
			eventTarget.removeEventListener('chat:finish', chatFinishHandler);
			audioAbortController.abort();
			await tick();
			await stopAllAudio();
			await stopRecordingCallback(false);
			await stopCamera();
		};
	});

	onDestroy(async () => {
		await stopAllAudio();
		await stopRecordingCallback(false);
		await stopCamera();
		await stopAudioStream();
		eventTarget.removeEventListener('chat:start', chatStartHandler);
		eventTarget.removeEventListener('chat', chatEventHandler);
		eventTarget.removeEventListener('chat:finish', chatFinishHandler);
		audioAbortController.abort();
		await tick();
		await stopAllAudio();
	});
</script>

{#if $showCallOverlay}
<div class="max-w-lg w-full h-full max-h-[100dvh] flex flex-col justify-between"
	 style="background:#000;position:relative;">
	<div style="position:absolute;top:8px;right:12px;font-size:9px;letter-spacing:0.05em;color:rgba(255,255,255,0.12);z-index:10;pointer-events:none;">v1.2.0</div>

	<!-- ── Status top ─────────────────────────────────────────────────────── -->
	<div class="flex flex-col items-center pt-14 pb-0 gap-1.5">
		<button
			type="button"
			class="text-xs tracking-widest transition-colors duration-500"
			style="color: {loading ? 'rgba(255,255,255,0.8)' : assistantSpeaking ? 'rgba(255,255,255,0.8)' : isCalibrating ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.5)'}; letter-spacing:0.08em; text-transform:uppercase;"
			on:click={() => { if (assistantSpeaking) stopAllAudio(); }}
		>
			{#if isCalibrating}
				Calibrating…
			{:else if loading}
				{$i18n.t('Thinking...')}
			{:else if assistantSpeaking}
				{$i18n.t('Tap to interrupt')}
			{:else if isMuted}
				Muted — tap to unmute
			{:else}
				{$i18n.t('Listening...')}
			{/if}
		</button>

		<!-- Silence countdown strip -->
		<div class="w-10 rounded-full overflow-hidden transition-opacity duration-300"
			 style="height:1.5px; background:rgba(255,255,255,0.1); opacity:{showCountdown ? 1 : 0};">
			<div class="h-full rounded-full" style="width:{silencePct}%; background:rgba(255,255,255,0.55);"></div>
		</div>
	</div>

	<!-- ── Centre: avatar + meter ─────────────────────────────────────────── -->
	<div class="flex flex-col items-center justify-center flex-1 gap-10">

		{#if !camera}
			<!-- Generated image preview card -->
			{#if generatedImageUrl}
				<div class="w-full px-9 flex justify-center" style="max-height:180px;margin-bottom:1rem;">
					<div class="relative rounded-2xl overflow-hidden" style="max-height:180px;">
						<img src={generatedImageUrl} alt="Generated"
							style="width:100%;height:100%;object-fit:cover;border-radius:1rem;
							       box-shadow:0 4px 24px rgba(0,0,0,0.6);" />
					</div>
				</div>
			{/if}

			<!-- Avatar with live-driven ripple rings -->
			<div class="relative flex items-center justify-center" style="width:180px;height:180px;">

				<!-- Outer ripple ring -->
				<div class="absolute rounded-full border pointer-events-none"
					 style="inset:-44px; border-color:rgba(255,255,255,0.09);
					        opacity:{ripple2Opacity}; transform:scale({ripple2Scale});
					        transition:none;"></div>

				<!-- Inner ripple ring -->
				<div class="absolute rounded-full border pointer-events-none"
					 style="inset:-22px; border-color:rgba(255,255,255,0.18);
					        opacity:{ripple1Opacity}; transform:scale({1 + ripple1Opacity * 0.12});
					        transition:none;"></div>

				<!-- Calibration spinner -->
				{#if isCalibrating}
					<div class="absolute rounded-full pointer-events-none"
						 style="inset:-16px; border:1.5px solid rgba(255,255,255,0.07); animation:none;">
						<div style="position:absolute;inset:0;border-radius:50%;border:1.5px solid transparent;
						            border-top-color:rgba(255,255,255,0.3);animation:owui-spin 1.8s linear infinite;"></div>
					</div>
				{/if}

				<!-- Avatar circle — scale + glow driven by JS each frame -->
				<button
					type="button"
					on:click={() => { if (assistantSpeaking) stopAllAudio(); }}
					style="width:160px;height:160px;border-radius:50%;overflow:hidden;
					       background-image:url('{WEBUI_API_BASE_URL}/models/model/profile/image?id={model?.id}&lang={$i18n.language}&voice=true');
					       background-size:cover;background-position:center;
					       transform:scale({avatarScale});
					       box-shadow:0 0 {Math.round(avatarGlow * 80)}px rgba(255,255,255,{avatarGlow.toFixed(3)});
					       transition:none;
					       will-change:transform,box-shadow;"
				>
					{#if isMuted}
						<div class="w-full h-full flex items-center justify-center" style="background:rgba(0,0,0,0.55);">
							<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-12 text-white" style="opacity:0.9;">
								<path d="M13.5 4.06c0-1.336-1.616-2.005-2.56-1.06l-4.5 4.5H4.508c-1.141 0-2.318.664-2.66 1.905A9.76 9.76 0 0 0 1.5 12c0 .898.121 1.768.35 2.595.341 1.24 1.518 1.905 2.659 1.905h1.93l4.5 4.5c.945.945 2.561.276 2.561-1.06V4.06ZM17.78 9.22a.75.75 0 1 0-1.06 1.06L18.44 12l-1.72 1.72a.75.75 0 1 0 1.06 1.06l1.72-1.72 1.72 1.72a.75.75 0 1 0 1.06-1.06L20.56 12l1.72-1.72a.75.75 0 1 0-1.06-1.06L19.5 10.94l-1.72-1.72Z"/>
							</svg>
						</div>
					{:else if emoji}
						<div class="w-full h-full flex items-center justify-center text-7xl">{emoji}</div>
					{:else if loading || assistantSpeaking}
						<!-- Thinking spinner centred in avatar -->
						<div class="w-full h-full flex items-center justify-center" style="background:rgba(0,0,0,0.15);">
							<svg class="size-12 text-white" viewBox="0 0 24 24" fill="currentColor">
								<style>.sp{animation:sp 1.05s infinite}.sp2{animation-delay:.1s}.sp3{animation-delay:.2s}
								@keyframes sp{0%,57.14%{animation-timing-function:cubic-bezier(.33,.66,.66,1);transform:translate(0)}28.57%{animation-timing-function:cubic-bezier(.33,0,.66,.33);transform:translateY(-6px)}100%{transform:translate(0)}}</style>
								<circle class="sp"  cx="4"  cy="12" r="3"/>
								<circle class="sp sp2" cx="12" cy="12" r="3"/>
								<circle class="sp sp3" cx="20" cy="12" r="3"/>
							</svg>
						</div>
					{/if}
				</button>
			</div>

		{:else}
			<!-- Camera view (unchanged layout) -->
			<div class="relative flex w-full max-h-full pt-2 pb-4 px-2 h-full" style="max-height:60dvh;">
				<!-- svelte-ignore a11y-media-has-caption -->
				<video id="camera-feed" autoplay class="rounded-2xl h-full min-w-full object-cover object-center" playsinline/>
				<canvas id="camera-canvas" style="display:none;"/>
				<div class="absolute top-4 left-4">
					<button type="button"
						class="p-1.5 text-white backdrop-blur-xl bg-black/10 rounded-full"
						on:click={stopCamera}>
						<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-6">
							<path d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z"/>
						</svg>
					</button>
				</div>
			</div>
		{/if}

		<!-- ── Meter bar ─────────────────────────────────────────────────── -->
		<div class="w-full flex flex-col items-center gap-3 px-9">
			<div class="w-full text-center" style="font-size:9px;letter-spacing:0.09em;text-transform:uppercase;color:rgba(255,255,255,0.16);">
				Hold to adjust threshold
			</div>

			<!-- Track — long-press opens panel -->
			<!-- svelte-ignore a11y-no-static-element-interactions -->
			<div class="w-full relative" style="height:3px;"
				 on:touchstart={(e) => { const t = setTimeout(() => { showThreshPanel = true; }, 500); e.currentTarget._lp = t; }}
				 on:touchend={(e) => clearTimeout(e.currentTarget._lp)}
				 on:touchmove={(e) => clearTimeout(e.currentTarget._lp)}>

				<!-- Track bg -->
				<div class="absolute inset-0 rounded-full" style="background:rgba(255,255,255,0.07);"></div>

				<!-- Fill -->
				<div class="absolute top-0 bottom-0 left-0 rounded-full"
					 style="width:{dbToPct(meterDb)}%;background:rgba(255,255,255,0.45);"></div>

				<!-- Peak dot -->
				<div class="absolute rounded-full" style="width:5px;height:5px;top:50%;
				     transform:translate(-50%,-50%);background:rgba(255,255,255,0.55);
				     left:{dbToPct(peakDb)}%;transition:left 0.25s ease-out;"></div>

				<!-- Noise floor tick -->
				<div class="absolute rounded-full" style="width:1.5px;top:-4px;bottom:-4px;
				     background:rgba(255,255,255,0.22);left:{dbToPct(noiseFloorDb)}%;
				     transition:left 0.5s ease-out;">
					<div style="position:absolute;top:12px;left:50%;transform:translateX(-50%);
					     font-size:8px;letter-spacing:0.05em;color:rgba(255,255,255,0.2);white-space:nowrap;">noise</div>
				</div>

				<!-- Threshold handle — draggable circle -->
				<!-- svelte-ignore a11y-no-static-element-interactions -->
				<div class="absolute" style="width:28px;height:28px;
				     top:50%;transform:translate(-50%,-50%);
				     left:{dbToPct(thresholdMarkerDb)}%;
				     touch-action:none;cursor:ew-resize;z-index:5;
				     display:flex;align-items:center;justify-content:center;"
					 on:touchstart|stopPropagation={(e) => {
						 const track = e.currentTarget.parentElement;
						 const move = (ev) => {
							 ev.preventDefault();
							 const r = track.getBoundingClientRect();
							 const pct = Math.max(0, Math.min(1, (ev.touches[0].clientX - r.left) / r.width));
							 applyThreshold(Math.round(pct * (DB_MAX - DB_MIN) + DB_MIN));
						 };
						 const up = () => { window.removeEventListener('touchmove', move); window.removeEventListener('touchend', up); };
						 window.addEventListener('touchmove', move, { passive: false });
						 window.addEventListener('touchend', up);
					 }}
					 on:mousedown|stopPropagation={(e) => {
						 const track = e.currentTarget.parentElement;
						 const move = (ev) => {
							 const r = track.getBoundingClientRect();
							 const pct = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
							 applyThreshold(Math.round(pct * (DB_MAX - DB_MIN) + DB_MIN));
						 };
						 const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
						 window.addEventListener('mousemove', move);
						 window.addEventListener('mouseup', up);
					 }}>
					<!-- Visible circle -->	
					<div style="width:18px;height:18px;border-radius:50%;
					     background:rgba(255,255,255,0.9);
					     box-shadow:0 0 8px rgba(255,255,255,0.4);
					     pointer-events:none;"></div>
					<!-- dB label above circle -->
					<div style="position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);
					     font-size:8px;letter-spacing:0.04em;color:rgba(255,255,255,0.65);white-space:nowrap;font-weight:500;">
						{Math.round(thresholdMarkerDb)} dB
					</div>
				</div>
			</div>

			<div class="w-full flex justify-between" style="font-size:9px;letter-spacing:0.08em;text-transform:uppercase;color:rgba(255,255,255,0.18);">
				<span>quiet</span><span>loud</span>
			</div>
		</div>
	</div>

	<!-- ── Mute button ──────────────────────────────────────────────────────── -->
	<div class="flex justify-center pb-2">
		<button
			type="button"
			on:click={() => { isMuted = !isMuted; }}
			class="flex items-center gap-2 px-5 py-2.5 rounded-full transition-all"
			style="background:{isMuted ? 'rgba(255,80,80,0.18)' : 'rgba(255,255,255,0.08)'};
			       border:1px solid {isMuted ? 'rgba(255,80,80,0.45)' : 'rgba(255,255,255,0.12)'};"
		>
			{#if isMuted}
				<!-- Speaker off icon -->
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-4" style="color:rgba(255,100,100,0.9);">
					<path d="M13.5 4.06c0-1.336-1.616-2.005-2.56-1.06l-4.5 4.5H4.508c-1.141 0-2.318.664-2.66 1.905A9.76 9.76 0 0 0 1.5 12c0 .898.121 1.768.35 2.595.341 1.24 1.518 1.905 2.659 1.905h1.93l4.5 4.5c.945.945 2.561.276 2.561-1.06V4.06ZM17.78 9.22a.75.75 0 1 0-1.06 1.06L18.44 12l-1.72 1.72a.75.75 0 1 0 1.06 1.06l1.72-1.72 1.72 1.72a.75.75 0 1 0 1.06-1.06L20.56 12l1.72-1.72a.75.75 0 1 0-1.06-1.06L19.5 10.94l-1.72-1.72Z"/>
				</svg>
				<span style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:rgba(255,100,100,0.9);font-weight:500;">Muted</span>
			{:else}
				<!-- Microphone icon -->
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-4" style="color:rgba(255,255,255,0.45);">
					<path d="M8.25 4.5a3.75 3.75 0 1 1 7.5 0v8.25a3.75 3.75 0 1 1-7.5 0V4.5Z"/>
					<path d="M6 10.5a.75.75 0 0 1 .75.75v1.5a5.25 5.25 0 1 0 10.5 0v-1.5a.75.75 0 0 1 1.5 0v1.5a6.751 6.751 0 0 1-6 6.709v2.291h3a.75.75 0 0 1 0 1.5h-7.5a.75.75 0 0 1 0-1.5h3v-2.291a6.751 6.751 0 0 1-6-6.709v-1.5A.75.75 0 0 1 6 10.5Z"/>
				</svg>
				<span style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:rgba(255,255,255,0.3);font-weight:500;">Mute</span>
			{/if}
		</button>
	</div>

	<!-- ── Bottom row: camera | send | close ─────────────────────────────── -->
	<div class="grid items-center pb-12 px-11" style="grid-template-columns:1fr auto 1fr;">

		<!-- Camera -->
		<div>
			{#if camera}
				<VideoInputMenu devices={videoInputDevices}
					on:change={async (e) => { selectedVideoInputDeviceId = e.detail; await stopVideoStream(); await startVideoStream(); }}>
					<button class="p-3 rounded-full" style="background:rgba(255,255,255,0.1);" type="button">
						<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-5 text-white">
							<path fill-rule="evenodd" d="M15.312 11.424a5.5 5.5 0 0 1-9.201 2.466l-.312-.311h2.433a.75.75 0 0 0 0-1.5H3.989a.75.75 0 0 0-.75.75v4.242a.75.75 0 0 0 1.5 0v-2.43l.31.31a7 7 0 0 0 11.712-3.138.75.75 0 0 0-1.449-.39Zm1.23-3.723a.75.75 0 0 0 .219-.53V2.929a.75.75 0 0 0-1.5 0V5.36l-.31-.31A7 7 0 0 0 3.239 8.188a.75.75 0 1 0 1.448.389A5.5 5.5 0 0 1 13.89 6.11l.311.31h-2.432a.75.75 0 0 0 0 1.5h4.243a.75.75 0 0 0 .53-.219Z" clip-rule="evenodd"/>
						</svg>
					</button>
				</VideoInputMenu>
			{:else}
				<Tooltip content={$i18n.t('Camera')}>
					<button class="p-3 rounded-full" style="background:rgba(255,255,255,0.1);" type="button"
						on:click={async () => { await navigator.mediaDevices.getUserMedia({ video: true }); startCamera(); }}>
						<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-5 text-white">
							<path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"/>
							<path stroke-linecap="round" stroke-linejoin="round" d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"/>
						</svg>
					</button>
				</Tooltip>
			{/if}
		</div>

		<!-- Send — large white circle, bottom centre -->
		<button
			type="button"
			on:click={manualSend}
			class="flex items-center justify-center rounded-full text-black"
			style="width:68px;height:68px;background:#fff;
			       box-shadow:0 0 {Math.round(silencePct / 100 * 24)}px rgba(255,255,255,{(silencePct / 100 * 0.4).toFixed(2)});
			       opacity:{isCalibrating ? 0.35 : 1};transition:opacity 0.3s;"
			disabled={isCalibrating}
		>
			<svg viewBox="0 0 24 24" fill="currentColor" class="size-6" style="margin-left:3px;">
				<path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
			</svg>
		</button>

		<!-- Close -->
		<div class="flex justify-end">
			<button
				class="p-3 rounded-full"
				style="background:rgba(255,255,255,0.1);"
				on:click={async () => {
					await stopAudioStream();
					await stopVideoStream();
					showCallOverlay.set(false);
					dispatch('close');
				}}
				type="button"
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-5 text-white">
					<path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"/>
				</svg>
			</button>
		</div>
	</div>

	<!-- ── Threshold panel (slides up from bottom) ────────────────────────── -->
	{#if showThreshPanel}
		<!-- Scrim -->
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<div class="fixed inset-0 z-40" on:click={() => showThreshPanel = false}></div>

		<div class="fixed bottom-0 left-0 right-0 z-50 flex flex-col gap-4 rounded-t-2xl p-6 pb-12"
			 style="background:#111;border-top:1px solid rgba(255,255,255,0.09);">

			<div class="w-9 h-1 rounded-full mx-auto" style="background:rgba(255,255,255,0.18);"></div>

			<div class="flex justify-between items-baseline">
				<span style="font-size:11px;font-weight:500;letter-spacing:0.07em;text-transform:uppercase;color:rgba(255,255,255,0.4);">
					Send Threshold
				</span>
				<span style="font-size:28px;font-weight:300;letter-spacing:-0.02em;">
					{panelThreshValue} <span style="font-size:13px;color:rgba(255,255,255,0.35);">dB</span>
				</span>
			</div>

			<input type="range" min="-65" max="0" step="1" bind:value={panelThreshValue}
				on:input={() => applyThreshold(panelThreshValue)}
				class="w-full" style="height:2px;accent-color:#fff;"/>

			<div class="grid gap-2" style="grid-template-columns:repeat(4,1fr);">
				{#each [['Quiet',-52],['Road',-28],['Loud',-16],['Very Loud',-8]] as [label, val]}
					<button
						type="button"
						on:click={() => applyThreshold(val)}
						class="py-2.5 rounded-xl text-xs border transition-all"
						style="border-color:{panelThreshValue === val ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.1)'};
						       background:{panelThreshValue === val ? 'rgba(255,255,255,0.1)' : 'transparent'};
						       color:{panelThreshValue === val ? '#fff' : 'rgba(255,255,255,0.35)'};">
						{label}
					</button>
				{/each}
			</div>

			<button type="button" on:click={() => showThreshPanel = false}
				class="py-3.5 rounded-xl text-sm border"
				style="border-color:rgba(255,255,255,0.08);background:rgba(255,255,255,0.05);color:rgba(255,255,255,0.45);">
				Done
			</button>
		</div>
	{/if}
</div>

<!-- Audio element (unchanged) -->
<audio id="audioElement" style="display:none;" />

<!-- Spinner keyframe -->
<style>
@keyframes owui-spin { to { transform: rotate(360deg); } }
</style>
{/if}
