FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libasound2 libssl3 libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY questionnaires ./questionnaires
COPY frontend/survey-chat ./frontend/survey-chat
COPY scripts ./scripts
COPY frontend/dialer ./frontend/dialer
RUN useradd --create-home agent
USER agent
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" HOST="0.0.0.0"
EXPOSE 7860
CMD ["python", "-m", "voice_agent"]
