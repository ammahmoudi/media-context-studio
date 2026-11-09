# Video Context Service

Turn course recordings into structured, timestamped evidence: speech captions, screen text, scene boundaries, and sampled frames. Run locally or deploy separate API and inference workers with PostgreSQL and private S3-compatible storage.

The web studio provides an operations overview, durable run history, worker resources, video playback with captions, transcript/frame seeking, and full run-detail pages. Agents use the same HTTP API and offline Swagger documentation.

## Docker deployment

```powershell
python scripts/setup_env.py
docker compose build api worker
python scripts/start_stack.py
```

Open [the studio](http://127.0.0.1:18765/) and sign in with `VIDEO_CONTEXT_API_KEY` from the generated `.env`. [Swagger](http://127.0.0.1:18765/docs) shares the authenticated browser session; other clients send `X-API-Key`.

Prepare models on a fresh deployment before speech/OCR jobs:

```powershell
docker compose run --rm worker python -m video_context.cli prepare-models --ocr-language en
```

The default stack has a lean API, CPU worker, PostgreSQL, and RustFS object storage. Original MinIO/S3 endpoints are supported by the storage adapter. Source videos are shared across repeated runs; durable results are stored per attempt. Queue claims, leases, recovery, cancellation/retry, and resource reporting live in the database.

For the laptop's existing CUDA models, run a native GPU worker connected to Docker infrastructure:

```powershell
.\.venv\Scripts\python.exe scripts/with_stack.py worker --queues gpu
```

Choose the GPU queue in the studio. An optional GPU-container profile is also provided; see the deployment guide for its runtime requirements and validation limits.

## Laptop mode without Docker

```powershell
.\.venv\Scripts\python.exe -m video_context.cli serve
```

This uses persistent SQLite/local-object storage and an embedded worker. Stop the Docker API or choose a different port before starting laptop mode. Initial setup: `pip install -e '.[worker,dev]'` and `python -m video_context.cli prepare-models`. Existing JSON history is imported idempotently; the explicit migration command copies artifacts/videos into durable storage.

## Documentation

- [Deployment, queue/storage design, scaling, access, backups, and migration](./docs/production.md)
- [Dashboard usage](./docs/dashboard.md) and [API reference](./docs/service.md)
- [Full course validation](./docs/full-course-validation.md): the 57-minute recording extracted in 29 minutes
- [Optimization measurements](./docs/optimization.md) and [original course test](./docs/course-recording-test.md)
- [Research and scope](./docs/video-context.md)

Speech/OCR outputs are machine-generated evidence and may contain errors. Visual-model descriptions, speaker identification, and automatic semantic summaries are not implemented. The bundled single-node infrastructure is a validated deployment baseline, not high availability; external hosting needs HTTPS, backups, and an appropriate operational policy.

Repository: [ammahmoudi/video-context-service](https://github.com/ammahmoudi/video-context-service). Videos, model weights, databases, and deployment credentials are excluded from Git.
