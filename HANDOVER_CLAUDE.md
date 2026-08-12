# Claude handover: OWUI MiniMax H3 video generation

## Mission

This repository is the deployable custom Open WebUI fork. The current feature set adds a dedicated **Video Studio** mode backed by a locally running ComfyUI/MiniMax-H3 workflow, while keeping normal chat video generation text-only. Continue development in this repository and preserve the existing Docker-based deployment shape.

## Current state

- Repository: `C:\\AI\\open-webui-voice-plus`
- Branch: `scheduled-tasks` (ahead of `origin/scheduled-tasks` by 8 commits at handover)
- Live UI: `http://127.0.0.1:3000`
- Live container: `open-webui`, image `open-webui-voice-plus:latest`, port `3000:8080`, restart policy `always`, health status healthy.
- Container mounts: named volume `open-webui` → `/app/backend/data` (user data), and bind `E:/Development/productivity/sweet-mail-biscuits` → `/app/sweetmail`.
- Storage: the `D:` drive was failing with write errors and the development tree was migrated to `E:`. Stale copies still exist on `D:`, so never point a mount, script, or config at a `D:` path. The repository itself remains on `C:`; there is no `E:\\AI`.
- ComfyUI/MiniMax-H3: host `http://127.0.0.1:8188`; from Docker use `http://host.docker.internal:8188`.
- Do not put secrets in this document. The live container has a `WEBUI_SECRET_KEY` and other existing environment values; preserve them by inspecting the current container during deployment.

Relevant commits (newest first):

```text
a89622da1 feat: add video generation history
5a89de39e fix: preserve video seeds across regeneration
b0c9662ef feat: add MiniMax video studio
1e677f86b feat: make chat video text-only and add 10s clips
ecbd347bd feat: add MiniMax H3 video generation
```

The working tree should be checked before editing. Preserve unrelated user changes; do not reset or discard them.

## User-visible behavior

### Regular chat

The existing chat video control is intentionally **Text-to-Video only**. It supports 3, 5, and 10 second clips. Images in a normal chat are not passed to the selected chat model for video generation.

### Video Studio

The sidebar has a separate Video Studio mode at `src/routes/(app)/video/+page.svelte`. It deliberately omits ordinary chat bells and whistles, but reuses the model selector and existing OpenAI-compatible chat completion helper for prompt drafting.

Flow:

1. Choose a chat model and workflow mode: Text, Image (first frame), or First + last frame.
2. Upload PNG/JPEG/WebP images (maximum 25 MB each). Images are kept in the browser as data URLs and sent only to the video endpoint; the prompt model receives metadata only (name, MIME type, dimensions, size, and role).
3. Ask the selected chat model to draft an H3 prompt, edit/review the draft, and explicitly approve it.
4. Choose aspect ratio (16:9, 9:16, 1:1), quality (0.2 or 0.4 MP), duration (3, 5, or 10 seconds), and seed.
5. Submit to ComfyUI. Progress/status and errors remain visible during the normal 1–2 minute generation.
6. The resulting MP4 lands in its own **Current video** section, playable inline and downloadable.
7. A separate **Video history** section below it lists previously generated videos loaded from the backend.

The current video and the history are distinct pieces of state (`currentVideo` and `videoHistory`). When a new generation starts, `archiveCurrentVideo()` moves the on-screen video into the history list, so the Current video section always shows exactly one result — the newest. Refreshing the history filters out the current video by URL, because the backend has already persisted it and it would otherwise appear twice.

### Continuing a clip

Every video card (current and history) has **Continue from end**, which chains clips into a longer sequence. `captureFinalFrame()` fetches the MP4 with the bearer token, wraps it in a blob URL, seeks a decoded `<video>` element to `duration - 0.05`, and paints that frame to a canvas as a PNG data URL. Seeking to exactly `duration` lands past the last frame and paints nothing, so the epsilon is required. The blob URL keeps the canvas untainted and avoids re-negotiating auth on the media element.

The captured frame becomes the first-frame anchor: `continueFromVideo()` switches to `first` mode, copies the source clip's aspect ratio and megapixels so the frame matches the workflow resolution, and clears the seed so the new shot gets a fresh random one. There is **no new backend mode** — a continuation is an ordinary `first` / `image` generation whose starting image happens to be captured rather than uploaded.

`continuationSource` holds the source clip while the continuation is being set up. It drives a banner in the Create section, relabels the frame slot, and appends the previous clip's brief to the drafting request so the model writes the next shot instead of restating the last one. It is cleared by `selectMode()` and by uploading a first frame by hand, since either means the anchor is no longer a continuation.

Layout note: the Current video and Video history sections sit **below** the two-column grid, not inside `<main>`. They were originally in the main column, which put the Generate button after the entire history in document order and made it unreachable without scrolling past every past clip. Keep the settings panel ahead of both results sections in document order.

Reference-to-Video is enabled in Video Studio for 1-9 ordered images. Image order maps directly to <Picture 1> through <Picture 9>; image pixels go only to ComfyUI and the prompt model receives metadata and labels.

## Code map

- `src/routes/(app)/video/+page.svelte` — Video Studio UI, prompt drafting/review, upload handling, generation, current-session gallery, persistent history.
- `src/lib/components/layout/Sidebar.svelte` — sidebar navigation entry.
- `src/lib/apis/videos/index.ts` — typed frontend calls for generation and history.
- `backend/open_webui/routers/videos.py` — authenticated generation and history routes, request validation, file storage and metadata.
- `backend/open_webui/utils/videos/comfyui.py` — MiniMax-H3 workflow construction, image upload, queueing, polling and MP4 download.
- `backend/open_webui/test/utils/test_videos_comfyui.py` — focused ComfyUI workflow tests.
- `Dockerfile` — builds from the upstream Open WebUI image and applies the local patches. Treat its patching/assertion logic as authoritative; do not edit the generated image directly.

## API contract

Generation:

```text
POST /api/v1/videos/generations
```

Form fields include `prompt`, `mode` (`text`, `image`, or `first-last`), `aspect_ratio`, `megapixels` (`0.2` or `0.4`), `duration` (`3`, `5`, or `10`), `seed`, optional `first_frame_data_url`, optional `last_frame_data_url`, and optional chat/message IDs.

Image data URLs are validated as PNG/JPEG/WebP and capped at 25 MB. A last frame requires a first frame. Raw data URLs are never written to generation metadata. Stored metadata includes prompt, mode, dimensions/settings, seed, frame-presence flags, and ownership.

Browser-facing generation uses resumable background jobs:

```text
POST /api/v1/videos/generations/jobs
GET  /api/v1/videos/generations/jobs/{job_id}
```

The browser creates and persists a UUID before submission. Submitting the same UUID again is idempotent for its owner, status is strictly user-scoped, and Video Studio resumes polling after sleep, reload, or a temporary network failure. Completed and failed in-memory job records are pruned after 24 hours; the generated MP4 remains in normal OWUI file storage/history. The deployment currently runs one Uvicorn worker, which is required by this in-memory registry. Move jobs to shared database/Redis storage before adding multiple workers or replicas.

History:

```text
GET /api/v1/videos/history?limit=50
```

`limit` is 1–100. Results come from `Files.get_files_by_user_id(user.id)`, so history is strictly user-scoped even for admins. The backend filters video files whose nested `meta.data` contains a generation prompt, returns newest first, and includes file ID/URL/name/type plus prompt, mode, aspect, megapixels, duration, seed, frame flags, and `created_at`.

The seed is returned as a string. JavaScript-safe random seeds are generated in `[0, 2**53 - 1]`; this avoids regeneration failures caused by unsafe or malformed values. Omitting `seed` entirely makes the backend pick a fresh random one.

Svelte’s `bind:value` on `<input type="number">` sets the bound variable to `null`, not `''`, when the field is cleared. The Video Studio seed check must treat `null`/`undefined` and `''` alike as “no seed”; a bare `String(seed).trim()` turns `null` into the literal `"null"` and produces a spurious “Seed must be a non-negative whole number” error instead of a new random seed. The seed box is auto-repopulated with the seed that was used after each generation, so clearing it is the deliberate way to get a new one.

## ComfyUI assumptions

The installed tested models are:

```text
minimax_h3_fl2va_pruned_int8_convrot.safetensors
minimax_h3_ref2va_pruned_int8_convrot.safetensors
qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
minimax_h3_video_vae_fp16.safetensors
minimax_h3_audio_vae_fp32.safetensors
```

The workflow uses the tested FL2VA graph, including node `105:104` (`MiniMaxH3ImageToVideo`) and optional `LoadImage` nodes `114` (first frame) and `115` (last frame). Uploaded images receive unique names such as `owui-video-<uuid>-<filename>` via ComfyUI `/upload/image` with `overwrite=false`.

Reference mode uses the official `MiniMaxH3ReferenceToVideo` graph and dedicated Ref2VA diffusion model. Ordered `LoadImage` nodes are wired into `ref_images.ref_image_0` through `ref_images.ref_image_8`; `ref_image_size` is `match`.

The resolution map is approximately:

```text
16:9: 0.2 MP = 608x352, 0.4 MP = 864x480
9:16: 0.2 MP = 352x608, 0.4 MP = 480x864
1:1:  0.2 MP = 448x448, 0.4 MP = 640x640
```

Frame count is derived from duration at 24 fps and rounded to the workflow’s required 17-frame cadence. Do not change model/node names without checking the installed ComfyUI workflow and the focused tests.

Production T2V/I2V prompts use contiguous `[Xs-Ys]` segments covering the exact duration, with 2-3 segments around 5 seconds and 4-5 around 10 seconds. Ref2VA prompts use the six-section template `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, and `non_diegetic_music`. Keep Picture labels stable and in upload order.

## Verification commands

Run from `C:\\AI\\open-webui-voice-plus`:

```powershell
$env:PYTHONPATH = 'backend'
.venv\Scripts\python.exe -m pytest backend/open_webui/test/utils/test_videos_comfyui.py -q
.venv\Scripts\python.exe -m compileall backend/open_webui/routers/videos.py backend/open_webui/utils/videos/comfyui.py
npx prettier --write 'src/routes/(app)/video/+page.svelte' src/lib/apis/videos/index.ts
npm run build
docker build --pull=false -t open-webui-voice-plus:latest .
```

`npm run check` currently reports many unrelated pre-existing diagnostics in files such as RichTextInput and Sidebar; use the targeted Svelte check/output for Video Studio when needed. The Docker build is the important release verification and can take about 1–2 minutes because the base build downloads Pyodide assets.

Useful live checks after deployment:

```powershell
docker inspect open-webui --format '{{.State.Health.Status}}'
Invoke-WebRequest http://127.0.0.1:3000/health
docker exec open-webui python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8188/system_stats',timeout=10).status)"
```

The authenticated history smoke test should verify that `/api/v1/videos/history` returns at least one user-owned video with a URL when the account has generated media. Do not print tokens or secret environment values.

## Windows deployment and rollback

Every code change ends with a rebuild and redeploy; a passing `npm run build` is not the finish line. Build first, then reconstruct the container from the current one’s environment and mounts.

Three things make a naive “preserve everything from `docker inspect`” loop wrong. Read all three before editing the script below.

**Multiline env values.** `IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE` contains newlines. Passing it as `--env <literal=value>` makes PowerShell/Docker split the value and try to pull an image named `detailed:latest`. Pass any multiline value through the process environment and reference it by name only. Detect these by value rather than hardcoding the one known name, so a second multiline variable does not reintroduce the bug.

**Bind sources are VM paths, not host paths.** `docker inspect` reports binds as `/run/desktop/mnt/host/<drive>/...` (the Docker Desktop VM view). Feeding that straight back into `docker run -v` risks creating a fresh empty directory inside the VM instead of re-binding the real host folder. Translate it back to `<DRIVE>:/...` first.

**The `D:` drive is dead.** `D:` was failing with write errors and the development tree was migrated to `E:`. Stale copies remain on `D:`, so a `D:` path can pass `Test-Path` while being abandoned. Any inherited bind still pointing at `D:` must be remapped to its `E:` equivalent, and the deploy must abort if that equivalent is missing. This is the trap that matters most: the old container carried a `D:` bind for `/app/sweetmail`, and faithfully “preserving the mounts” silently recreated it on every deploy. Verify each bind source exists before starting the container.

```powershell
$ErrorActionPreference = 'Stop'
$current = (docker inspect open-webui | ConvertFrom-Json)[0]
$runArgs = @('run','-d','--name','open-webui','--restart',$current.HostConfig.RestartPolicy.Name,'-p','3000:8080')

foreach ($entry in $current.Config.Env) {
  $name  = $entry.Substring(0, $entry.IndexOf('='))
  $value = $entry.Substring($entry.IndexOf('=') + 1)
  if ($value -match "`n" -or $value -match "`r") {
    Set-Item -Path "env:$name" -Value $value
    $runArgs += @('--env',$name)
  } else {
    $runArgs += @('--env',$entry)
  }
}

foreach ($mount in $current.Mounts) {
  if ($mount.Type -eq 'volume') {
    $runArgs += @('-v',"$($mount.Name):$($mount.Destination)")
    continue
  }
  $source = $mount.Source
  if ($source -match '^/run/desktop/mnt/host/([a-z])/(.*)$') {
    $source = "$($Matches[1].ToUpper()):/$($Matches[2])"
  }
  if ($source -match '^(?i)D:/(.*)$') {
    $candidate = "E:/$($Matches[1])"
    if (-not (Test-Path $candidate)) { throw "Bind $source is on the dead D: drive and $candidate is missing." }
    $source = $candidate
  }
  if (-not (Test-Path $source)) { throw "Bind source $source does not exist." }
  $runArgs += @('-v',"${source}:$($mount.Destination)")
}

docker stop open-webui
docker rename open-webui open-webui-rollback
docker update --restart=no open-webui-rollback
$newId = docker @runArgs open-webui-voice-plus:latest
if ($LASTEXITCODE -ne 0 -or -not $newId) {
  docker rm -f open-webui 2>$null
  docker rename open-webui-rollback open-webui
  docker update --restart=always open-webui
  docker start open-webui
  throw 'New container failed to start; rollback restored.'
}
```

`docker update --restart=no open-webui-rollback` is not optional. The rollback inherits `--restart always`, and Docker restarts `always` containers when the daemon starts back up — a Docker Desktop restart would otherwise race the live container for port 3000.

Wait for health, then run the checks above plus a bind-mount verification, since a broken bind still yields a healthy container:

```powershell
docker exec open-webui sh -c 'ls /app/sweetmail | wc -l'
docker exec open-webui sh -c 'echo ok > /app/sweetmail/.write-test && rm /app/sweetmail/.write-test && echo writable'
```

Keep `open-webui-rollback` until the new container is confirmed healthy and the UI/API are usable. Only then remove it:

```powershell
docker rm open-webui-rollback
```

Never use `docker rm -v` here: the `open-webui` volume contains user data. Plain `docker rm` never touches a named volume, so replacing a just-created container in place is safe. If the new container fails, stop/remove only the failed replacement, rename `open-webui-rollback` back to `open-webui`, restore its restart policy, and start it. Confirm the exact container and mounts with `docker inspect` before any destructive operation.

## Known limitations and next work

- Reference mode currently supports images only. The underlying node also supports reference video and audio, but those upload and loader paths are not exposed in Video Studio yet.
- Active job status survives browser suspension but not an OWUI container restart; completed MP4s remain persistent. A durable database/Redis job table would remove this limitation.
- History currently returns the newest 50 items by default and has no delete/management UI.
- Uploaded source frames are used by the workflow but are not persisted as OWUI files for later re-editing.
- Prompt drafting intentionally sends image metadata, never image bytes, to the selected chat model.
- Add/expand mocked router tests and a stable authenticated history test if changing metadata or access control.
- Keep regular chat on the text-only pipeline unless the product requirement explicitly changes.
- The history list contains adult content beyond the most recent few items. Do not open, render, or inspect history media when working on this page; change the code and let the user verify visually.
- The generation response has no `id`, so the current video is deduplicated against the refreshed history by `url`. Give the response an `id` if a sturdier key is ever needed.
- Continuation captures the final frame in the browser and re-uploads it as a PNG data URL, so each link in a chain is re-encoded. A backend ffmpeg extraction would avoid the round trip and the generation loss if chains get long.
- Continuation chains are not recorded anywhere. Nothing in the stored metadata links a clip to the one it continues from, so a multi-clip sequence cannot be reassembled after a reload. Add a `continued_from` file ID to the generation metadata if stitching or sequence views are ever wanted.

## Safe working rules for Claude

Start each session with `git status --short --branch`, inspect existing local changes, and read the relevant current code before patching. Prefer `apply_patch`; if Windows ACLs reject it with “apply deny-read ACLs”, use a carefully scoped `git apply --recount --ignore-whitespace -` fallback. Do not reset hard, overwrite user changes, expose secrets, or modify the unrelated PaperRound workspace. Commit only intentional changes with a descriptive message, and deploy only after the focused tests and Docker build pass.

