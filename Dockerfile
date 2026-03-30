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
COPY --from=builder /build/backend/open_webui/routers/tasks.py /app/backend/open_webui/routers/tasks.py
COPY --from=builder /build/backend/open_webui/config.py /app/backend/open_webui/config.py
COPY --from=builder /build/backend/open_webui/routers/auths.py /app/backend/open_webui/routers/auths.py
# Patch main.py in place — add REPLACE_EMDASH_WITH_SEMICOLON import and app.state assignment
RUN sed -i 's/    RESPONSE_WATERMARK,$/    RESPONSE_WATERMARK,\n    REPLACE_EMDASH_WITH_SEMICOLON,/' /app/backend/open_webui/main.py
RUN sed -i 's/app\.state\.config\.RESPONSE_WATERMARK = RESPONSE_WATERMARK/app.state.config.RESPONSE_WATERMARK = RESPONSE_WATERMARK\napp.state.config.REPLACE_EMDASH_WITH_SEMICOLON = REPLACE_EMDASH_WITH_SEMICOLON/' /app/backend/open_webui/main.py
LABEL description="Open WebUI with adaptive voice threshold"
