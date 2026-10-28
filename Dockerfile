FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/vendor ./vendor
RUN npm ci --ignore-scripts
COPY frontend/index.html frontend/main.js frontend/style.css frontend/build.mjs ./
RUN npm run build

FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libasound2 libssl3 libgomp1 libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY bot.py main.py server.py config.py azure_services.py instrumentation.py verify_services.py diagnostics.py ./
COPY ice_lifecycle.py survey.py survey_voice.py questionnaire.example.json ./
COPY --from=frontend /frontend/dist ./frontend/dist
RUN useradd --create-home agent
USER agent
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" HOST="0.0.0.0"
EXPOSE 7860
CMD ["python", "server.py"]
