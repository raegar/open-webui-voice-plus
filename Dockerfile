# Build custom Open WebUI with noise-adaptive voice
FROM ghcr.io/open-webui/open-webui:main AS base
FROM node:20-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/raegar/open-webui-voice-plus.git .
RUN npm install --legacy-peer-deps
RUN npm run build
FROM base
COPY --from=builder /build/build /app/build
# Python backend patches (not part of the npm build)
COPY --from=builder /build/backend/open_webui/utils/middleware.py /app/backend/open_webui/utils/middleware.py
COPY --from=builder /build/backend/open_webui/utils/task.py /app/backend/open_webui/utils/task.py
COPY --from=builder /build/backend/open_webui/utils/tools.py /app/backend/open_webui/utils/tools.py
# Patch tools.py — add has_tool_server_access stub (referenced by middleware.py but missing from fork)
RUN printf '\n\ndef has_tool_server_access(user, server_connection: dict) -> bool:\n    return True\n' >> /app/backend/open_webui/utils/tools.py
COPY --from=builder /build/backend/open_webui/routers/tasks.py /app/backend/open_webui/routers/tasks.py
COPY --from=builder /build/backend/open_webui/routers/auths.py /app/backend/open_webui/routers/auths.py
# Patch main.py — add REPLACE_EMDASH_WITH_SEMICOLON import and app.state assignment
RUN sed -i 's/    RESPONSE_WATERMARK,$/    RESPONSE_WATERMARK,\n    REPLACE_EMDASH_WITH_SEMICOLON,/' /app/backend/open_webui/main.py
RUN sed -i 's/app\.state\.config\.RESPONSE_WATERMARK = RESPONSE_WATERMARK/app.state.config.RESPONSE_WATERMARK = RESPONSE_WATERMARK\napp.state.config.REPLACE_EMDASH_WITH_SEMICOLON = REPLACE_EMDASH_WITH_SEMICOLON/' /app/backend/open_webui/main.py
# Patch config.py — add REPLACE_EMDASH_WITH_SEMICOLON persistent config
RUN sed -i 's/RESPONSE_WATERMARK = PersistentConfig/REPLACE_EMDASH_WITH_SEMICOLON = PersistentConfig(\n    "REPLACE_EMDASH_WITH_SEMICOLON",\n    "ui.replace_emdash_with_semicolon",\n    os.environ.get("REPLACE_EMDASH_WITH_SEMICOLON", "False") == "True",\n)\n\nRESPONSE_WATERMARK = PersistentConfig/' /app/backend/open_webui/config.py
# Scheduled jobs feature (local files — not from fork, no GitHub push needed)
COPY backend/open_webui/models/scheduled_jobs.py /app/backend/open_webui/models/scheduled_jobs.py
COPY backend/open_webui/routers/scheduled_jobs.py /app/backend/open_webui/routers/scheduled_jobs.py
COPY backend/open_webui/utils/scheduler.py /app/backend/open_webui/utils/scheduler.py
# Register scheduled_jobs router in main.py
RUN sed -i 's/from open_webui.routers import (/from open_webui.routers import (\n    scheduled_jobs,/' /app/backend/open_webui/main.py
RUN sed -i "s|app.include_router(tasks.router, prefix='/api/v1/tasks', tags=\['tasks'\])|app.include_router(scheduled_jobs.router, prefix='/api/v1/scheduled-jobs', tags=['scheduled-jobs'])\napp.include_router(tasks.router, prefix='/api/v1/tasks', tags=['tasks'])|" /app/backend/open_webui/main.py
# Start scheduler loop in lifespan hook
RUN sed -i 's/asyncio.create_task(periodic_usage_pool_cleanup())/asyncio.create_task(periodic_usage_pool_cleanup())\n    from open_webui.utils.scheduler import scheduler_loop\n    asyncio.create_task(scheduler_loop(app))/' /app/backend/open_webui/main.py
LABEL description="Open WebUI with adaptive voice threshold"
