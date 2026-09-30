// Filming styles for the video studio. A style is direction for the drafting model
// about how a scene is shot and how it sounds, never about what happens in it. It is
// applied when the brief is drafted, so what the user approves is exactly what is sent.
// Directions describe the look in camera, lens, light and sound terms only: the video
// model knows nothing about films or directors, and a title in the brief is noise.

export type VideoStyleId =
	| 'default'
	| 'cinematic'
	| 'home-video'
	| 'found-footage'
	| 'documentary'
	| 'vintage-16mm'
	| 'film-noir'
	| 'horror'
	| 'music-video'
	| 'selfie-vlog'
	| 'nineties-tv'
	| 'smart-glasses'
	| 'cctv'
	| 'dreamlike'
	| 'anime'
	| 'ghibli';

export type VideoStyle = {
	id: VideoStyleId;
	label: string;
	/** One line for the settings panel. */
	summary: string;
	/** Instruction for the drafting model. Empty for the default. */
	direction: string;
	/** Filmed from one person's eyes, so the studio asks whose. */
	pov?: boolean;
};

export const VIDEO_STYLES: VideoStyle[] = [
	{
		id: 'default',
		label: 'Default',
		summary: 'No added style. The prompt model chooses the camera work.',
		direction: ''
	},
	{
		id: 'cinematic',
		label: 'Cinematic',
		summary: 'Feature-film polish: composed frames, smooth moves, shallow focus.',
		direction:
			'High-end feature film. Deliberate, balanced compositions with foreground depth. Smooth motivated camera moves: slow dolly push-ins, gentle tracking, crane or gimbal glides, never shaky. Shallow depth of field with soft background bokeh and occasional rack focus between subjects. Motivated, contrasty lighting with rim light and practical sources; rich filmic colour grade and fine grain. Clean, layered sound design with room tone and precise foley; any music is a restrained score.'
	},
	{
		id: 'home-video',
		label: 'Handheld home movie',
		summary: 'A friend filming on a budget phone: scrappy, unsteady and real, never cinematic.',
		direction:
			'Scrappy, low-budget amateur footage: real video someone took on their phone, not a film imitating it. The camera is the rear camera of an old or budget phone, held in one hand at chest or eye height by a friend who is standing in the room and part of the moment. It moves only as a hand holding a phone moves: a constant small wobble, a slight bob when the person filming shifts their weight or takes a step, and small corrections that overshoot and settle, turning toward whoever is talking a beat late. The motion is realistic and never smooth, but never wild either: no whip pans, no running, no shaking for effect. Write every camera movement as what the hand does, never as a camera move: there are no dolly, tracking, crane, gimbal, orbit or slow push-in moves, no zooms and no rack focus. The whole clip is one continuous recording with no cuts or shot changes. Framing is casual and slightly careless: off-centre, the horizon a few degrees off, too much headroom or the top of a head clipped, the edge of an object in the foreground. The people know the person filming and react to them: they glance at the lens, grin, talk to them or wave them off, and the operator’s own voice chats and laughs from right behind the phone. The image looks like a cheap phone sensor: slightly soft, digital noise indoors, smeared detail on anything moving, auto exposure and white balance drifting as the phone turns, windows and lamps blown out. The light is whatever is in the room, overhead lights, lamps or flat daylight, with no lighting set-up, no shallow depth of field, no lens flare and no colour grade. Audio is from the phone’s microphone: thin and compressed, the operator loudest of all, with handling bumps and room noise. No music.'
	},
	{
		id: 'found-footage',
		label: 'Found footage (Blair Witch)',
		summary: 'One character films everything: frantic, dark, raw and unscored.',
		direction:
			'Found footage. The entire shot is filmed by one character holding the camera, and the camera is their point of view: it never cuts to a clean outside angle. Jittery, reactive handheld motion that whips toward sounds, bobs with footsteps, and tilts or drops when the operator is startled. Low light with crushed blacks and heavy grain; where it is dark, a single on-camera light or torch beam picks out only what it points at. Framing is accidental: subjects are cut off, partly blocked by foreground, or briefly out of focus. Raw, close audio of the operator breathing hard, whispering, clothing rustle and footfalls, over an unsettling natural ambience. No music.'
	},
	{
		id: 'documentary',
		label: 'Documentary',
		summary: 'A professional crew observing from a distance: candid, clean, flat light.',
		direction:
			'Candid observational documentary filmed by a professional crew that stays out of the way. The camera is an unseen observer: nobody looks at it, speaks to it or reacts to it, and nobody behind it speaks. It films from across the room on a longer lens, so the background is compressed and people are often seen past foreground objects or other people. Steady shoulder-held camera work with slow, controlled reframes and a measured zoom to follow whoever is doing something; candid moments are caught mid-action rather than set up. A clean, sharp, correctly exposed professional image with natural colour and deep focus, the opposite of amateur footage, but never glamorous: flat, even, unflattering available light such as overhead fluorescents, overcast daylight or plain room lighting, with no rim light, no dramatic shadows, no shallow depth of field and no colour grade. Clear location sound recorded by a boom: voices at natural distance, ambience of the place. No music.'
	},
	{
		id: 'vintage-16mm',
		label: 'Vintage 16mm film',
		summary: '1970s film stock: warm faded colour, heavy grain, zooms.',
		direction:
			'Shot on 1970s 16mm film. Warm, slightly faded colour with soft halation around highlights, heavy organic grain, gentle gate weave and occasional dust or flicker. Handheld or tripod camera with slow period-style zoom-ins rather than dolly moves. Soft focus falloff and natural, low-contrast light. Slightly muffled, narrow-bandwidth sound with a faint projector-like hiss.'
	},
	{
		id: 'film-noir',
		label: 'Film noir',
		summary: 'Black and white people and places, hard shadows cut across faces.',
		direction:
			'Classic film noir. The entire frame is black and white, the people as much as the setting: skin, hair, eyes, lips and every garment are rendered only in shades of grey, with no trace of colour anywhere, even where reference pictures are in colour. Say so plainly at the start of the brief and again in each shot. Where appearance has to be described, give hair and clothing as tones (black, charcoal, mid-grey, pale grey, white) and skin as how it catches the light, never as colours. The lighting falls hard on the people, not only the background: a single hard key light low or to one side leaves half of each face in deep shadow, with a band of light across the eyes, sharp shadows under brows, cheekbones and hat brims, and slatted blind shadows striped across faces and bodies. Glossy highlights on skin, hair and fabric against near-black shadow, with faces emerging from darkness or turning into silhouette against a lit background. Performances are still and guarded: slow turns of the head, sidelong looks, a face held half in shadow. Smoke haze or rain where the setting allows. Low and tilted angles for tension, slow deliberate camera moves. Moody, sparse sound: footsteps, distant traffic, rain; any music is a lone muted instrument.'
	},
	{
		id: 'horror',
		label: 'Horror movie',
		summary: 'Cold, dark and dreadful: lurking shadows, slow creeping camera, uneasy sound.',
		direction:
			'Modern horror film. Build dread through framing, light and sound alone: do not add creatures, figures, threats or events that the creative direction does not contain. A cold, desaturated colour grade with sickly green or blue in the shadows and deep, murky blacks. Low-key lighting that falls on the people as much as the setting: faces lit from below or from one weak source such as a lamp, torch, phone screen or window, with eyes sunk in shadow, pale skin, and the rest of the frame swallowed by darkness. Flickering or failing practical lights, drifting haze where the setting allows. Compositions leave uneasy empty space around the subject: a dark doorway, a corridor or window behind them, shadowed corners that hold the eye. Slow, creeping camera moves: a gradual push-in on a face, a patient drift toward an open door, long still holds that refuse to cut away, and an occasional low or tilted angle. Performances are tense and wary: shallow breathing, eyes darting toward sounds, a slow reluctant turn to look behind. Sound is sparse and unsettling: a low droning undertone, heavy silences broken by creaks, distant knocks, the rustle of clothing and nervous breathing close to the subject.'
	},
	{
		id: 'music-video',
		label: 'Music video',
		summary: 'One stylised take: bold coloured light, slow camera, instrumental track.',
		direction:
			'Stylised music video, kept simple and readable. One continuous take with no cuts and a single slow camera move, such as a gentle push-in or a slow half-arc around the subject. Bold coloured lighting from one or two sources, such as neon, a coloured backlight or haze with light shafts, with deep shadows around them. The subject moves slowly and deliberately, swaying or turning in time with the music, and does not sing or mouth words. A steady, mid-tempo instrumental track carries the soundscape, with ambience low underneath it and no vocals.'
	},
	{
		id: 'selfie-vlog',
		label: 'Selfie vlog',
		summary: 'Phone front camera at arm’s length, talking to the viewer.',
		direction:
			'Smartphone selfie vlog. The camera is a phone front camera held at arm’s length by the main subject, who talks to and looks into the lens. Wide-angle close framing with slight distortion, small bobbing handheld motion as they move, the background swinging behind them. Bright, sharp phone image with auto exposure. Close, clear phone-mic voice with the surrounding ambience behind it. No music.'
	},
	{
		id: 'nineties-tv',
		label: '90s TV',
		summary: 'Standard-definition analogue broadcast: soft video, bright even studio light.',
		direction:
			'1990s television, shot on analogue standard-definition broadcast video. A soft, low-resolution image with slight colour bleed around reds, bright video highlights, faint interlacing shimmer on fast motion and a gentle tape softness. Bright, even, high-key studio lighting that fills in every shadow, with slightly oversaturated colour. Tripod-mounted camera work in the style of a sitcom or soap: steady medium and two-shot framing, slow zooms rather than moving the camera, and the action kept towards the centre of frame. Clean broadcast audio with a slightly boxy studio room sound. No on-screen text or logos.'
	},
	{
		id: 'smart-glasses',
		label: 'Smart glasses POV',
		summary: 'Filmed first-person from glasses worn by a character.',
		pov: true,
		direction:
			'First-person footage filmed by camera glasses worn by one character, so the camera is exactly their eye line and moves only as their head moves: turning to look, glancing down, nodding, and bobbing gently with each step. The wearer is never seen, apart from their own hands and forearms entering the lower frame when they reach for or hold something, and their reflection if a mirror is in view. Other people look at the wearer, which reads as looking straight into the lens. Wide-angle lens with slight edge distortion, a sharp processed phone-like image, auto exposure adjusting as the wearer looks toward and away from light, and slight rolling-shutter wobble on quick head turns. Audio from microphones at the wearer’s temples: their own voice and breathing are close and loud, other voices at natural conversational distance. No music. No on-screen overlays or text.'
	},
	{
		id: 'cctv',
		label: 'Security camera',
		summary: 'Fixed high-angle CCTV: static, wide, low-fidelity.',
		direction:
			'Security camera footage. A single fixed, locked-off camera mounted high in a corner, looking down at a wide angle with barrel distortion; it never moves, pans or cuts. Low-resolution, slightly washed-out image with compression smearing, and greenish monochrome night vision if the scene is dark. People move through the frame, sometimes partly out of view. Distant, slightly muffled room audio; any speech sounds far from the microphone but stays intelligible. No music. No on-screen text.'
	},
	{
		id: 'dreamlike',
		label: 'Dreamlike',
		summary: 'Soft, glowing, slow-motion and ethereal.',
		direction:
			'Dreamlike and ethereal. Slow motion throughout, with gentle floating camera drifts. Soft diffused glow, blooming highlights, pastel colour, and a hazy shallow focus with light leaks at the edges of frame. Movements are graceful and unhurried. Muted, reverberant sound with ambient pads; voices sound soft and close.'
	},
	{
		id: 'anime',
		label: 'Anime',
		summary: 'Modern Japanese animation: clean line art, cel shading, vivid colour.',
		direction:
			'Modern Japanese 2D anime. The entire frame is drawn animation, the people as much as the setting: every person is an anime character with clean line art, cel shading with hard-edged shadows, large expressive eyes and stylised hair, never a live-action or photoreal face, even where reference pictures are photographs. Say so plainly at the start of the brief and again in each shot. Keep each person recognisable by carrying their hair colour and style, build, clothing and distinguishing features into the character design. Vivid, saturated colour against detailed painted backgrounds, with animated light effects such as glinting highlights, lens flares and soft bloom. Anime camera language: held compositions with limited, deliberate character movement, slow pans across backgrounds, sudden push-ins on a face for emotional beats, and expressive close-ups on the eyes. Wind moves hair and clothing. Crisp, clean sound effects and expressive voice acting; any music is a bright orchestral or pop score.'
	},
	{
		id: 'ghibli',
		label: 'Studio Ghibli',
		summary: 'Hand-drawn, painterly and gentle: lush nature, soft light, quiet moments.',
		direction:
			'Hand-drawn traditional 2D animation in a gentle, painterly Japanese feature-film style. The entire frame is drawn and painted, the people as much as the setting: every person is a simply drawn animated character with soft outlines, rounded features, small noses, natural proportions and modestly sized expressive eyes, never a live-action or photoreal face, even where reference pictures are photographs. Say so plainly at the start of the brief and again in each shot. Keep each person recognisable by carrying their hair colour and style, build, clothing and distinguishing features into the character design. Lush watercolour and gouache backgrounds full of detail: rich greenery, towering billowing cumulus clouds, weathered homely buildings and cluttered lived-in interiors. Warm natural sunlight with soft shadows and a muted, earthy palette of greens, blues and warm creams. A gentle breeze moves grass, leaves, hair and clothing. Unhurried pacing that lingers on small everyday actions and quiet pauses; the camera holds wide shots of the scenery and pans slowly across it. Soft natural ambience of wind, birdsong and rustling leaves, warm natural voices, and a gentle, whimsical orchestral score led by piano and strings.'
	}
];

export const DEFAULT_VIDEO_STYLE: VideoStyleId = 'default';

export const isVideoStyleId = (value: unknown): value is VideoStyleId =>
	typeof value === 'string' && VIDEO_STYLES.some((style) => style.id === value);

export const getVideoStyle = (id: string | null | undefined): VideoStyle =>
	VIDEO_STYLES.find((style) => style.id === id) ?? VIDEO_STYLES[0];

/**
 * The style section of the drafting request, or '' for the default. It sits below the
 * hard constraints, so it shapes camera and sound without overriding duration, aspect
 * ratio or the dialogue budget.
 */
export const videoStyleDirection = (
	id: string | null | undefined,
	options: { povSubject?: string | null } = {}
): string => {
	const style = getVideoStyle(id);
	if (!style.direction) return '';
	const pov = style.pov ? (options.povSubject ?? '').trim() : '';
	return `Filming style: ${style.label}
${style.direction}${pov ? `\n${povDirection(pov)}` : ''}
Apply this style to the camera, lens, lighting, colour and sound of every shot. It shapes how the scene is filmed, not what happens in it: keep the action, subjects and dialogue of the creative direction. Where the creative direction asks for specific camera work, follow it for that moment. Describe the style in concrete filming terms and never name a film, director or franchise.`;
};

// The person behind the camera cannot see their own face, hair or clothes, and a brief
// that describes them invites the model to put them in shot. The drafting rules for
// chat scenes and reference casts both ask for every person's appearance, so this has
// to say explicitly that it wins over them.
const povDirection = (name: string) =>
	`The glasses are worn by ${name}, so the camera is ${name}’s own eyes. ${name} never appears in frame: do not describe their face, hair, head, upper-body clothing, posture or expression, because none of it can be seen from their own point of view. The only parts of ${name} that can be shown are their hands and forearms, with the cuffs or sleeves at their wrists, when they reach into the lower frame, and their reflection if a mirror is in view. Convey what ${name} does and feels through the camera instead: where they look, how their head moves, what their hands do, and their voice and breathing close to the microphones. Any line ${name} speaks comes from behind the camera. Other people who talk to ${name} look straight into the lens. This overrides any instruction to describe ${name}’s appearance or keep them visibly present, and any instruction to render every named person from their reference pictures: cite ${name}’s pictures only for their hands and sleeves, never as a visible subject.`;
