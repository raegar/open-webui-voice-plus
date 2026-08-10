# Claude handover: OWUI MiniMax H3 video generation

## Mission

This repository is the deployable custom Open WebUI fork. The current feature set adds a dedicated **Video Studio** mode backed by a locally running ComfyUI/MiniMax-H3 workflow, while keeping normal chat video generation text-only. Continue development in this repository and preserve the existing Docker-based deployment shape.

## Current state

- Repository: `C:\\AI\\open-webui-voice-plus`
- Branch: `scheduled-tasks` (ahead of `origin/scheduled-tasks` by 8 commits at handover)
- Live UI: `http://127.0.0.1:3000`
- Live container: `open-webui`, image `open-webui-voice-plus:latest`, port `3000:8080`, restart policy inherited from the existing container, health status healthy.
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
6. The resulting MP4 is playable inline, downloadable, and shown in the current-session gallery.
7. Previously generated videos are loaded from the persistent history section in the same page.

Reference-to-Video is intentionally hidden/not implemented. It needs a model that is not installed in the current ComfyUI setup.

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

History:

```text
GET /api/v1/videos/history?limit=50
```

`limit` is 1–100. Results come from `Files.get_files_by_user_id(user.id)`, so history is strictly user-scoped even for admins. The backend filters video files whose nested `meta.data` contains a generation prompt, returns newest first, and includes file ID/URL/name/type plus prompt, mode, aspect, megapixels, duration, seed, frame flags, and `created_at`.

The seed is returned as a string. JavaScript-safe random seeds are generated in `[0, 2**53 - 1]`; this avoids regeneration failures such as “Seed must be a non-negative whole number” caused by unsafe or malformed values.

## ComfyUI assumptions

The installed tested models are:

```text
minimax_h3_fl2va_pruned_int8_convrot.safetensors
qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
minimax_h3_video_vae_fp16.safetensors
minimax_h3_audio_vae_fp32.safetensors
```

The workflow uses the tested FL2VA graph, including node `105:104` (`MiniMaxH3ImageToVideo`) and optional `LoadImage` nodes `114` (first frame) and `115` (last frame). Uploaded images receive unique names such as `owui-video-<uuid>-<filename>` via ComfyUI `/upload/image` with `overwrite=false`.

The resolution map is approximately:

```text
16:9: 0.2 MP = 608x352, 0.4 MP = 864x480
9:16: 0.2 MP = 352x608, 0.4 MP = 480x864
1:1:  0.2 MP = 448x448, 0.4 MP = 640x640
```

Frame count is derived from duration at 24 fps and rounded to the workflow’s required 17-frame cadence. Do not change model/node names without checking the installed ComfyUI workflow and the focused tests.

The prompt guide used for drafting is [Naxdy’s H3 guide](https://gist.github.com/Naxdy/43b7422a1e4a79fb8b0489c6c39eaace). Its key T2VA output fields are:

```text
integrated_multimodal_description:
overall_soundscape:
non_diegetic_music:
```

Keep one coherent timeline and camera description, and keep dialogue/on-screen-text instructions explicit. The current workflow is FL2VA, not full reference-to-video.

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

Build first, then preserve the current container’s environment and mounts. In particular, `IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE` is a multiline value. Passing every env entry as `--env <literal=value>` can make PowerShell/Docker split that value and accidentally try to pull an image named `detailed:latest`. Pass that variable through the process environment and use `--env IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE`.

```powershell
$current = (docker inspect open-webui | ConvertFrom-Json)[0]
$runArgs = @('run','-d','--name','open-webui','--restart',$current.HostConfig.RestartPolicy.Name,'-p','3000:8080')
foreach ($entry in $current.Config.Env) {
  if ($entry.StartsWith('IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE=')) {
    $env:IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = $entry.Substring($entry.IndexOf('=') + 1)
    $runArgs += @('--env','IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE')
  } else {
    $runArgs += @('--env',$entry)
  }
}
foreach ($mount in $current.Mounts) {
  if ($mount.Type -eq 'volume') { $source = $mount.Name } else { $source = $mount.Source }
  $runArgs += @('-v',"${source}:$($mount.Destination)")
}

docker stop open-webui
docker rename open-webui open-webui-rollback
$newId = docker @runArgs open-webui-voice-plus:latest
if ($LASTEXITCODE -ne 0) {
  docker rename open-webui-rollback open-webui
  docker start open-webui
  throw 'New container failed to start; rollback restored.'
}
```

Wait for health, run the checks above, and keep `open-webui-rollback` until the new container is confirmed healthy and the UI/API are usable. Only then remove it:

```powershell
docker rm open-webui-rollback
```

Never use `docker rm -v` here: the `open-webui` volume contains user data. If the new container fails, stop/remove only the failed replacement, rename `open-webui-rollback` back to `open-webui`, and start it. Confirm the exact container and mounts with `docker inspect` before any destructive operation.

## Known limitations and next work

- Reference-to-Video is not exposed; it requires `minimax_h3_ref2va_pruned_int8_convrot.safetensors` (and the corresponding workflow/model setup).
- History currently returns the newest 50 items by default and has no delete/management UI.
- Uploaded source frames are used by the workflow but are not persisted as OWUI files for later re-editing.
- Prompt drafting intentionally sends image metadata, never image bytes, to the selected chat model.
- Add/expand mocked router tests and a stable authenticated history test if changing metadata or access control.
- Keep regular chat on the text-only pipeline unless the product requirement explicitly changes.

## Safe working rules for Claude

Start each session with `git status --short --branch`, inspect existing local changes, and read the relevant current code before patching. Prefer `apply_patch`; if Windows ACLs reject it with “apply deny-read ACLs”, use a carefully scoped `git apply --recount --ignore-whitespace -` fallback. Do not reset hard, overwrite user changes, expose secrets, or modify the unrelated PaperRound workspace. Commit only intentional changes with a descriptive message, and deploy only after the focused tests and Docker build pass.

