# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN corepack prepare pnpm@10.30.1 --activate && pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# Keep dependency layers independent of application source changes.
FROM python:3.12-slim-bookworm AS api-deps
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 VIDEO_CONTEXT_DATA=/data VIDEO_CONTEXT_MODELS=/models
WORKDIR /app
COPY requirements-api.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-api.lock.txt
RUN useradd --uid 10001 --create-home app && mkdir /data /models && chown app:app /data /models

FROM api-deps AS worker-deps
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.lock.txt

FROM api-deps AS api
COPY pyproject.toml README.md ./
COPY video_context/ ./video_context/
COPY --from=frontend /build/video_context/static/ ./video_context/static/
RUN pip install --no-deps --no-build-isolation .
USER app
EXPOSE 18765
CMD ["python", "-m", "video_context.cli", "serve", "--host", "0.0.0.0"]

FROM worker-deps AS worker
COPY pyproject.toml README.md ./
COPY video_context/ ./video_context/
COPY --from=frontend /build/video_context/static/ ./video_context/static/
RUN pip install --no-deps --no-build-isolation .
USER app
CMD ["python", "-m", "video_context.cli", "worker"]

FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04 AS gpu-deps
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 VIDEO_CONTEXT_DATA=/data VIDEO_CONTEXT_MODELS=/models PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg libgl1 libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
WORKDIR /app
COPY requirements.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.lock.txt
RUN useradd --uid 10001 --create-home app && mkdir /data /models && chown app:app /data /models

FROM gpu-deps AS gpu
COPY pyproject.toml README.md ./
COPY video_context/ ./video_context/
COPY --from=frontend /build/video_context/static/ ./video_context/static/
RUN pip install --no-deps .
USER app
CMD ["python", "-m", "video_context.cli", "worker"]
