FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/voice/package.json frontend/voice/package-lock.json ./
COPY frontend/voice/vendor ./vendor
RUN npm ci --ignore-scripts
COPY frontend/voice/index.html frontend/voice/main.js frontend/voice/style.css frontend/voice/build.mjs ./
RUN npm run build

FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libasound2 libssl3 libgomp1 libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY questionnaires ./questionnaires
COPY frontend/survey-chat ./frontend/survey-chat
COPY scripts ./scripts
COPY --from=frontend /frontend/dist ./frontend/voice/dist
RUN useradd --create-home agent
USER agent
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" HOST="0.0.0.0"
EXPOSE 7860
CMD ["python", "-m", "voice_agent"]
