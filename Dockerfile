# Build custom Open WebUI with noise-adaptive voice
FROM ghcr.io/open-webui/open-webui:main AS base
FROM node:20-slim AS builder
WORKDIR /build
# Copy local source (avoids git clone cache issues and allows local-only changes)
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY . .
RUN NODE_OPTIONS=--max-old-space-size=4096 APP_BUILD_HASH="$(date +%s)" npm run build
FROM base
COPY --from=builder /build/build /app/build
# Python backend patches (not part of the npm build)
COPY --from=builder /build/backend/open_webui/utils/middleware.py /app/backend/open_webui/utils/middleware.py
COPY --from=builder /build/backend/open_webui/utils/character_personality.py /app/backend/open_webui/utils/character_personality.py
COPY --from=builder /build/backend/open_webui/utils/chat_instructions.py /app/backend/open_webui/utils/chat_instructions.py
COPY --from=builder /build/backend/open_webui/utils/task.py /app/backend/open_webui/utils/task.py
COPY --from=builder /build/backend/open_webui/utils/tools.py /app/backend/open_webui/utils/tools.py
# Patch tools.py — add has_tool_server_access stub (referenced by middleware.py but missing from fork)
RUN printf '\n\ndef has_tool_server_access(user, server_connection: dict) -> bool:\n    return True\n' >> /app/backend/open_webui/utils/tools.py
COPY --from=builder /build/backend/open_webui/routers/tasks.py /app/backend/open_webui/routers/tasks.py
COPY --from=builder /build/backend/open_webui/routers/auths.py /app/backend/open_webui/routers/auths.py
COPY --from=builder /build/backend/open_webui/routers/images.py /app/backend/open_webui/routers/images.py
COPY --from=builder /build/backend/open_webui/routers/videos.py /app/backend/open_webui/routers/videos.py
COPY --from=builder /build/backend/open_webui/utils/videos /app/backend/open_webui/utils/videos
COPY --from=builder /build/backend/open_webui/models/video_characters.py /app/backend/open_webui/models/video_characters.py
# Private chats for work mode: the fork's own table and router.
COPY --from=builder /build/backend/open_webui/models/private_chats.py /app/backend/open_webui/models/private_chats.py
COPY --from=builder /build/backend/open_webui/routers/privacy.py /app/backend/open_webui/routers/privacy.py
# Patch main.py — add REPLACE_EMDASH_WITH_SEMICOLON import and app.state assignment
RUN sed -i 's/    RESPONSE_WATERMARK,$/    RESPONSE_WATERMARK,\n    REPLACE_EMDASH_WITH_SEMICOLON,/' /app/backend/open_webui/main.py
RUN sed -i 's/app\.state\.config\.RESPONSE_WATERMARK = RESPONSE_WATERMARK/app.state.config.RESPONSE_WATERMARK = RESPONSE_WATERMARK\napp.state.config.REPLACE_EMDASH_WITH_SEMICOLON = REPLACE_EMDASH_WITH_SEMICOLON/' /app/backend/open_webui/main.py
# Patch config.py — add REPLACE_EMDASH_WITH_SEMICOLON persistent config
RUN sed -i 's/RESPONSE_WATERMARK = PersistentConfig/REPLACE_EMDASH_WITH_SEMICOLON = PersistentConfig(\n    "REPLACE_EMDASH_WITH_SEMICOLON",\n    "ui.replace_emdash_with_semicolon",\n    os.environ.get("REPLACE_EMDASH_WITH_SEMICOLON", "False") == "True",\n)\n\nRESPONSE_WATERMARK = PersistentConfig/' /app/backend/open_webui/config.py
# MiniMax H3 video generation configuration and API registration.
# Each patch has an assertion so an incompatible upstream layout fails the build loudly.
RUN sed -i 's/ENABLE_IMAGE_GENERATION = PersistentConfig/ENABLE_VIDEO_GENERATION = PersistentConfig(\n    "ENABLE_VIDEO_GENERATION",\n    "video_generation.enable",\n    os.environ.get("ENABLE_VIDEO_GENERATION", "false").lower() == "true",\n)\n\nCOMFYUI_VIDEO_BASE_URL = PersistentConfig(\n    "COMFYUI_VIDEO_BASE_URL",\n    "video_generation.comfyui.base_url",\n    os.getenv("COMFYUI_VIDEO_BASE_URL", "http:\/\/host.docker.internal:8188"),\n)\n\nCOMFYUI_VIDEO_API_KEY = PersistentConfig(\n    "COMFYUI_VIDEO_API_KEY",\n    "video_generation.comfyui.api_key",\n    os.getenv("COMFYUI_VIDEO_API_KEY", ""),\n)\n\nCOMFYUI_VIDEO_TIMEOUT = PersistentConfig(\n    "COMFYUI_VIDEO_TIMEOUT",\n    "video_generation.comfyui.timeout",\n    int(os.getenv("COMFYUI_VIDEO_TIMEOUT", "600")),\n)\n\nENABLE_IMAGE_GENERATION = PersistentConfig/' /app/backend/open_webui/config.py \
    && grep -q 'COMFYUI_VIDEO_TIMEOUT' /app/backend/open_webui/config.py
RUN sed -i 's/    images,$/    images,\n    videos,/' /app/backend/open_webui/main.py \
    && sed -i 's/    IMAGE_STEPS,$/    IMAGE_STEPS,\n    ENABLE_VIDEO_GENERATION,\n    COMFYUI_VIDEO_BASE_URL,\n    COMFYUI_VIDEO_API_KEY,\n    COMFYUI_VIDEO_TIMEOUT,/' /app/backend/open_webui/main.py \
    && sed -i 's/app\.state\.config\.COMFYUI_WORKFLOW_NODES = COMFYUI_WORKFLOW_NODES/app.state.config.COMFYUI_WORKFLOW_NODES = COMFYUI_WORKFLOW_NODES\napp.state.config.ENABLE_VIDEO_GENERATION = ENABLE_VIDEO_GENERATION\napp.state.config.COMFYUI_VIDEO_BASE_URL = COMFYUI_VIDEO_BASE_URL\napp.state.config.COMFYUI_VIDEO_API_KEY = COMFYUI_VIDEO_API_KEY\napp.state.config.COMFYUI_VIDEO_TIMEOUT = COMFYUI_VIDEO_TIMEOUT/' /app/backend/open_webui/main.py \
    && sed -i "s|app.include_router(images.router, prefix='/api/v1/images', tags=\['images'\])|app.include_router(images.router, prefix='/api/v1/images', tags=['images'])\napp.include_router(videos.router, prefix='/api/v1/videos', tags=['videos'])|" /app/backend/open_webui/main.py \
    && sed -i "s/'enable_image_generation': app\.state\.config\.ENABLE_IMAGE_GENERATION,/'enable_image_generation': app.state.config.ENABLE_IMAGE_GENERATION,\n                    'enable_video_generation': app.state.config.ENABLE_VIDEO_GENERATION,/" /app/backend/open_webui/main.py \
    && grep -q 'include_router(videos.router' /app/backend/open_webui/main.py \
    && grep -q "'enable_video_generation'" /app/backend/open_webui/main.py
# Register the privacy router next to videos, which the step above just added.
RUN sed -i 's/^    videos,$/    videos,\n    privacy,/' /app/backend/open_webui/main.py \
    && sed -i "s|app.include_router(videos.router, prefix='/api/v1/videos', tags=\['videos'\])|app.include_router(videos.router, prefix='/api/v1/videos', tags=['videos'])\napp.include_router(privacy.router, prefix='/api/v1/privacy', tags=['privacy'])|" /app/backend/open_webui/main.py \
    && grep -q '^    privacy,$' /app/backend/open_webui/main.py \
    && grep -q 'include_router(privacy.router' /app/backend/open_webui/main.py
# Game Master: the fork's own tables, pass logic and router, registered after privacy.
COPY --from=builder /build/backend/open_webui/models/game_master.py /app/backend/open_webui/models/game_master.py
COPY --from=builder /build/backend/open_webui/utils/game_master.py /app/backend/open_webui/utils/game_master.py
COPY --from=builder /build/backend/open_webui/routers/game_master.py /app/backend/open_webui/routers/game_master.py
RUN sed -i 's/^    privacy,$/    privacy,\n    game_master,/' /app/backend/open_webui/main.py \
    && sed -i "s|app.include_router(privacy.router, prefix='/api/v1/privacy', tags=\['privacy'\])|app.include_router(privacy.router, prefix='/api/v1/privacy', tags=['privacy'])\napp.include_router(game_master.router, prefix='/api/v1/gm', tags=['game-master'])|" /app/backend/open_webui/main.py \
    && grep -q '^    game_master,$' /app/backend/open_webui/main.py \
    && grep -q 'include_router(game_master.router' /app/backend/open_webui/main.py
# Scheduled jobs feature (local files — not from fork, no GitHub push needed)
COPY backend/open_webui/models/scheduled_jobs.py /app/backend/open_webui/models/scheduled_jobs.py
COPY backend/open_webui/routers/scheduled_jobs.py /app/backend/open_webui/routers/scheduled_jobs.py
COPY backend/open_webui/utils/scheduler.py /app/backend/open_webui/utils/scheduler.py
# Register scheduled_jobs router in main.py
RUN sed -i 's/from open_webui.routers import (/from open_webui.routers import (\n    scheduled_jobs,/' /app/backend/open_webui/main.py
RUN sed -i "s|app.include_router(tasks.router, prefix='/api/v1/tasks', tags=\['tasks'\])|app.include_router(scheduled_jobs.router, prefix='/api/v1/scheduled-jobs', tags=['scheduled-jobs'])\napp.include_router(tasks.router, prefix='/api/v1/tasks', tags=['tasks'])|" /app/backend/open_webui/main.py
# Start scheduler loop in lifespan hook
RUN sed -i 's/asyncio.create_task(periodic_usage_pool_cleanup())/asyncio.create_task(periodic_usage_pool_cleanup())\n    from open_webui.utils.scheduler import scheduler_loop\n    asyncio.create_task(scheduler_loop(app))/' /app/backend/open_webui/main.py
RUN test -f /app/backend/open_webui/utils/character_personality.py && grep -q character_personality /app/backend/open_webui/utils/middleware.py
LABEL description="Open WebUI with adaptive voice threshold"
