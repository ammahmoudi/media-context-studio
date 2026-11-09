# Durable service and deployment

This deployment replaces the original in-memory queue/JSON history with a database-backed service. PostgreSQL and S3-compatible storage are the deployed backends; SQLite and local object files remain available for laptop use. The original course evidence is preserved.

## Components and ownership

```mermaid
flowchart LR
  Client[Dashboard / agent / API client] --> API[Stateless HTTP API]
  API --> DB[(PostgreSQL: jobs, leases, workers)]
  API --> S3[(Private S3: inputs and artifacts)]
  Worker[CPU / GPU worker processes] --> DB
  Worker --> S3
  Worker --> Scratch[Temporary local scratch]
  Worker --> Models[Persistent model cache]
```

| Data | Location | Lifetime |
| --- | --- | --- |
| Job options, state, progress, attempts, artifact pointers | PostgreSQL; SQLite in laptop mode | Retained across API/worker restarts |
| Original video | Private S3 object keyed by source SHA-256 and suffix | Retained for playback and retry; repeated runs share the input |
| Context JSON, report, SRT, WebVTT, frames | Private per-job/per-attempt S3 prefix | Published only after upload finishes |
| Decoded audio, downloaded inputs, temporary outputs | Worker scratch directory | Removed after the attempt exits normally |
| Speech/OCR weights | Persistent model cache | Reused across runs; excluded from Git/images |
| Deployment credentials | Ignored `.env` or injected environment | Shared securely across API replicas; excluded from Git |

The database has schema version 1 and initializes tables under an advisory lock on PostgreSQL. Unsupported schema versions are rejected. Future schema changes require explicit migrations rather than silently changing existing tables.

## Queue and resource behavior

The database itself is the durable queue; Redis is not required. API replicas reserve queue capacity transactionally and finish storing input before making a job runnable. PostgreSQL workers claim eligible jobs using `FOR UPDATE SKIP LOCKED`. SQLite uses serialized write transactions and WAL mode for laptop operation.

States are `uploading → queued → running → complete / partial / failed`, with `cancelled` available for queued/running jobs. `GET /v1/jobs` includes live work, paginated/filterable history, queue positions, counts, and worker resources. Search and history filters are server-side.

- Each worker process runs one job at a time and retains its loaded model between jobs.
- Workers report heartbeats about every five seconds. The UI considers a worker offline after 30 seconds.
- A running job's default lease is 120 seconds, renewed independently of slow scene detection/inference. Expired leases requeue the attempt; the default maximum is three attempts.
- Ownership checks fence progress/final-state updates. Attempt-specific object prefixes prevent a stale worker from overwriting a newer attempt's published artifacts.
- Delivery is **at least once**, not exactly once. A killed/partitioned worker can overlap with a replacement until it detects lease loss. Only the owner can publish the database result pointer.
- Cancellation updates durable state immediately; computation stops at its next progress checkpoint. A long decoder/scene scan may take time to reach that checkpoint.
- Retry creates a new job linked by `retry_of`, reusing the source object. Published partial/failed evidence is retained.
- The `default` and `cpu` queues are handled by the default Docker CPU worker. The laptop GPU worker listens to `gpu`. The UI prefers the GPU queue when a matching GPU worker is online.
- CPU worker replicas can be increased independently of API processes. Run one worker per GPU/device to avoid memory contention; do not scale several workers onto one laptop GPU accidentally.

The worker page shows process CPU/RAM/threads, queue subscriptions, model, current job, heartbeat age, and GPU device memory/utilization when `nvidia-smi` is available. GPU memory is the device total, including other applications, not an allocation exclusive to this service.

## Storage backend choice

The S3 adapter uses boto3 and supports AWS S3, compatible MinIO endpoints, and RustFS. The bundled stack uses **RustFS 1.0.4**. The [original MinIO community repository](https://github.com/minio/minio) was archived on April 25, 2026. [RustFS](https://github.com/rustfs/rustfs) is maintained and S3-compatible; its upstream had approximately 35.9k stars when checked on November 9, 2026. No existing MinIO containers from other projects were changed.

The bucket is private. Authenticated API endpoints stream video byte ranges and artifacts without buffering entire videos or exposing storage credentials. The original SRT remains downloadable; browser captions use a generated WebVTT representation of the same timed transcript. Video codec compatibility still depends on the browser; automatic video transcoding is not implemented.

Normal processing removes scratch files. `gc-work` lists abandoned scratch directories from interrupted processes; `--apply` removes eligible old directories while excluding live job IDs. It never deletes durable source/result objects. Durable history and artifacts are retained by default. Configure and test a retention/backup policy before operating an unbounded video library; do not expire shared source objects while retained jobs reference them. Abandoned ingestion reservations are marked failed after two hours.

## Start the Docker stack

Docker must be running. Generate credentials once:

```powershell
python scripts/setup_env.py
docker compose up -d --build
```

Open <http://127.0.0.1:18765/> and sign in with `VIDEO_CONTEXT_API_KEY` from `.env`. The API, worker, PostgreSQL, and object store have separate containers and persistent volumes. API containers do not install ML inference runtimes. Images use pinned Python dependency snapshots, a locked frontend, cached dependency layers, non-root application processes, health checks, and CPU/memory limits.

Ports are API `18765`, PostgreSQL `55433`, S3 `19020`, and storage console `19021`, bound to localhost. The Docker-backed service now owns the main API URL on this laptop; the earlier SQLite API process was stopped, with its database/files preserved. Change `VIDEO_CONTEXT_HTTP_PORT` to run another instance. The console uses `S3_ACCESS_KEY` / `S3_SECRET_KEY` from `.env`.

The default model cache is a named volume. Before submitting speech/OCR jobs on a fresh deployment, prepare the weights:

```powershell
docker compose run --rm worker python -m video_context.cli prepare-models --ocr-language en
```

This laptop uses `VIDEO_CONTEXT_MODELS_VOLUME=./.models` in its ignored `.env` to reuse existing weights. On Linux, use the named-volume default or ensure the chosen bind mount is writable by UID 10001. Models are not downloaded by normal analysis jobs.

To scale CPU workers:

```powershell
docker compose up -d --scale worker=2
```

Each CPU worker is limited to four CPUs and 6 GiB RAM by default. API replicas share PostgreSQL/S3, but publishing multiple replicas on one fixed host port requires a reverse proxy/load balancer. The bundled single PostgreSQL and single object-store node are persistent local services, **not a high-availability deployment**.

## Use this laptop's NVIDIA GPU

Keep the native CUDA setup and connect an independent GPU worker to the Docker database/object store:

```powershell
.\.venv\Scripts\python.exe scripts/with_stack.py worker --queues gpu
```

Submit GPU jobs with `"queue": "gpu"`, or use the dashboard. The helper reads connection credentials from `.env` without putting them in process arguments or printing them. Native workers continue using the laptop's selected `large-v3-turbo` / CUDA / `int8_float16` settings.

An optional Linux CUDA container target/profile is also provided:

```powershell
docker compose --profile gpu up -d --build gpu-worker
```

It requires a working NVIDIA container runtime/GPU passthrough and the model weights in the shared cache. The CUDA image tag was verified; the GPU-container profile has not been built or runtime-validated on this laptop. The native GPU-worker path is used for local validation. Choose one GPU-worker path per physical GPU.

## Access and external hosting

The Docker stack requires a generated API key. Tools send `X-API-Key`; the dashboard exchanges the key for a 12-hour HttpOnly, SameSite browser session. Keys are not stored in browser local storage or query strings. Cookie-authenticated cross-origin writes are rejected. Swagger and private artifact/video routes require the same authentication; UI assets and basic health/readiness endpoints are public.

Server-side path submission is disabled in Docker; upload files instead. It remains enabled in laptop mode. For an external deployment, put the API behind HTTPS, set `VIDEO_CONTEXT_SECURE_COOKIE=1`, configure exact `VIDEO_CONTEXT_ALLOWED_HOSTS`, and inject credentials through your deployment secret manager. This is a single-tenant service key, not a multi-user identity/permission system. Use restricted S3 service credentials for an existing production bucket; the local stack credentials are intended to initialize this private local instance.

Relevant configuration:

| Variable | Purpose |
| --- | --- |
| `VIDEO_CONTEXT_DATABASE_URL` | SQLAlchemy PostgreSQL URL or SQLite file URL |
| `VIDEO_CONTEXT_S3_ENDPOINT`, `VIDEO_CONTEXT_S3_BUCKET` | Private S3-compatible storage |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | SDK credentials/region |
| `VIDEO_CONTEXT_EMBEDDED_WORKER=0` | API-only process; run independent workers |
| `VIDEO_CONTEXT_WORKER_QUEUES` | Comma-separated queue subscriptions |
| `VIDEO_CONTEXT_QUEUE_CAPACITY` | Accepted uploading/running/queued job bound |
| `VIDEO_CONTEXT_LEASE_SECONDS`, `VIDEO_CONTEXT_MAX_ATTEMPTS` | Recovery policy |
| `VIDEO_CONTEXT_CPU_THREADS` | Speech/OCR thread budget |
| `VIDEO_CONTEXT_MAX_UPLOAD_BYTES` | Per-video upload limit; default 8 GiB |
| `VIDEO_CONTEXT_API_KEY`, `VIDEO_CONTEXT_SECURE_COOKIE` | Service authentication/session transport |

Set a request-body limit and rate limits at the public reverse proxy too; multipart bodies can be spooled to disk before the upload handler runs. Resource limits and auth are deployment controls, not proof that this system is safe for arbitrary untrusted media at internet scale.

## Migration and backups

Laptop startup imports old JSON records into SQLite idempotently. To copy existing history, video sources, and results to the deployed PostgreSQL/S3 stack:

```powershell
.\.venv\Scripts\python.exe scripts/with_stack.py migrate-legacy --from-directory data/jobs
```

The importer copies objects first, then commits pointers. It does not delete the original evidence. All 12 pre-existing runs were copied to PostgreSQL/S3 on this laptop, including the full course and browser captions. Videos are found in the original job, data root, course-test, or sample locations. Missing original videos are reported rather than fabricated.

Back up PostgreSQL **and** the object bucket plus deployment credentials. A surviving database alone cannot restore video artifacts; a surviving bucket alone does not restore queue/history state. Model weights can be downloaded again from their recorded revisions. Test restoration by checking source hashes, job counts, artifact references, and playback. Avoid `docker compose down --volumes` on a deployment containing data; ordinary `down` preserves named volumes.

## Validation

- Automated unit/API suite: **26 tests passed**, covering existing extraction plus durable claims, restart persistence, leases/fencing, queue routing/capacity, cancellation/retry, input deduplication/integrity, authentication, CSRF checks, upload limits, video ranges, and Unicode history search.
- Two opt-in integration tests passed against real Docker PostgreSQL and RustFS: concurrent claims/capacity, separate worker execution, S3 artifacts, byte-range reads/HEAD, and persistence across API restarts. Each test uses its own temporary database and bucket.
- Browser inspection loaded the full course video (3,451.85 seconds) and 1,151 showing Persian caption cues.
- Frontend production build, TypeScript, and lint passed.
- Docker API and CPU-worker images built successfully, and API/PostgreSQL/RustFS health checks passed. The main endpoint reports PostgreSQL, S3, external workers, required authentication, and disabled server-side path submission.
- A real authenticated deployment smoke test submitted a CPU job to the Docker worker and a Persian speech job to the independent native GPU worker. Both completed and published retrievable S3 artifacts. CPU extraction took 1.407 seconds; the two-minute GPU extraction took 32.071 seconds (OCR disabled). These are extraction timings, excluding queue wait/ingestion and final artifact transfer.
- The same smoke test verified authenticated byte-range streaming of the migrated full course and all 1,151 WebVTT caption cues. [Local smoke report](../data/production-smoke.json).
- Real English OCR in the Linux CPU-worker container completed in 12.966 seconds with four observations and no extraction warnings, using the existing portable model cache. [Local OCR smoke report](../data/docker-ocr-smoke.json).
- Private GitHub repository: <https://github.com/ammahmoudi/video-context-service>.

Run integration checks after starting the infrastructure:

```powershell
$env:VIDEO_CONTEXT_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/test_integration.py -q -p no:cacheprovider
```

GitHub Actions validates frontend build/lint, extraction/API tests, PostgreSQL/S3 integration, and the API Docker build. Private video/model/data files and real deployment credentials are excluded from Git.
