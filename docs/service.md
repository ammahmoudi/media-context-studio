# Local course video service

The service now includes a [local web dashboard](http://127.0.0.1:18765/) and offline Swagger at <http://127.0.0.1:18765/docs>. See [dashboard instructions](./dashboard.md) for job submission, live queue/history, result browsing, and frontend builds. `GET /v1/jobs` exposes running jobs, queue positions, and paginated history for other clients.

This laptop now uses verified CUDA speech with `large-v3-turbo` and `int8_float16`, persisted in ignored `config.local.json`. Environment variables override local settings; without either, portable defaults remain CPU/base. See [optimization measurements and setup](./optimization.md).

OCR defaults to `ocr_deduplicate: true` in job options. Set it to `false` for full OCR on every sampled frame. Reused observations retain one text ID with multiple `frame_ids` and `observed_at_seconds`; each frame lists its current `ocr.text_ids`. Time-range queries include reused text visible in matching frames. No fixed screen area is excluded.

## Purpose and boundaries

Provide reusable tools that chatbots and applications can call with a video and receive structured results. The first use case is understanding course recordings through speech, shots, and text on slides or screens.

The HTTP API is the initial integration interface. Python callers can also use the shared library or CLI. An MCP adapter or skill can be added around this interface after testing with real course videos. Neither is required to call the service today.

This release extracts evidence. It does not yet generate course summaries, infer concepts from diagrams, parse mathematical formulas into LaTeX, or run a visual language model. OCR quality on equations, handwriting, and small text requires checking against the frames.

## Installation

Requires Python 3.12 and FFmpeg/ffprobe on PATH. Use a project virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m video_context.cli prepare-models --ocr-language en
```

For a slow or unreliable connection, an installed uv can cache completed downloads locally:

```powershell
$env:UV_HTTP_TIMEOUT = '120'
$env:UV_HTTP_RETRIES = '5'
uv pip install --python .venv/Scripts/python.exe --index-url https://pypi.org/simple `
    --cache-dir .uv-cache -e '.[dev]'
```

Model preparation downloads a multilingual Whisper base model and compact official PP-OCRv5 mobile models. Initial OCR profiles are `en`, `fa`, and `ar`; Persian and Arabic use the Arabic-script recognizer. Prepare each required OCR profile before using it. These profiles need quality validation on actual slides. `language` controls spoken-language recognition; `ocr_language` controls OCR. These settings are independent. Other OCR languages require adding and validating a model profile.

The defaults use CPU INT8 speech inference and CPU OCR. The laptop has an NVIDIA RTX 4060 Laptop GPU with approximately 8 GiB VRAM. GPU execution is configurable for speech but requires a separate compatible CUDA runtime setup and verification; it is not assumed to work from the graphics driver alone.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `VIDEO_CONTEXT_WHISPER_MODEL` | `base` | Multilingual model size or local model path; larger models need preparation and benchmarking |
| `VIDEO_CONTEXT_WHISPER_DEVICE` | `cpu` | Speech inference device |
| `VIDEO_CONTEXT_WHISPER_COMPUTE` | `int8` | Speech inference precision |

The installed OpenCV base and contrib distributions are pinned to the same build because upstream packages request different distribution names while sharing the `cv2` module.

The released faster-whisper 1.2.1 audio-file decoder passes an argument removed in PyAV 19. The service keeps current PyAV for video and passes FFmpeg-decoded PCM samples through faster-whisper's documented array input, avoiding a second decode and the incompatible file-decoder path. See the [upstream compatibility discussion](https://github.com/SYSTRAN/faster-whisper/discussions/1494).

All project output lives under `data/`, and model caches under `.models/`. Both are ignored by Git. `VIDEO_CONTEXT_DATA` and `VIDEO_CONTEXT_MODELS` can override these locations. Configuration is read when the Python process starts.

If pip's global configuration adds an unreachable extra index, a project-only `.venv/pip.ini` can override it:

```ini
[global]
index-url = https://pypi.org/simple
extra-index-url =
```

## Start the service

```powershell
.\.venv\Scripts\python.exe -m video_context.cli serve --port 18765
```

The VS Code task **Run local video context service** starts the same process. Keep one service process and one worker for this initial implementation.

- Interactive API and request forms: <http://127.0.0.1:18765/docs>
- Machine-readable OpenAPI contract: <http://127.0.0.1:18765/openapi.json>
- Health and media-tool availability: <http://127.0.0.1:18765/health>

This is a local, trusted-caller service. It accepts local file paths, has no authentication, and binds to loopback. It must be redesigned with authentication and input access controls before deployment to a shared or public network. Browser cross-origin access is not enabled.

## Submit a course recording

Use a full path to avoid copying a large local course file:

```powershell
$request = @{
    path = 'C:/Courses/lecture-01.mp4'
    options = @{
        language = 'en'
        ocr_language = 'en'
        sample_interval_seconds = 30
        max_frames = 300
    }
} | ConvertTo-Json -Depth 4

$job = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:18765/v1/jobs' `
    -ContentType 'application/json' -Body $request
$job
```

This returns HTTP 202 and a job ID immediately. Omit `language` for automatic speech-language detection. `speech` and `ocr` default to true and may be disabled independently.

For tools that supply file bytes, use `POST /v1/jobs/upload` with multipart fields `file` and optional `options_json`. The upload limit is 8 GiB; local-path submission avoids the upload copy and limit. Uploaded sources are retained in the job folder until manually removed.

## Poll and retrieve results

```powershell
$status = Invoke-RestMethod "http://127.0.0.1:18765/v1/jobs/$($job.id)"
$status

# After status is complete, partial, or failed:
$context = Invoke-RestMethod "http://127.0.0.1:18765/v1/jobs/$($job.id)/result"
$context.shots
$context.on_screen_text
$context.speech_segments
```

Statuses: `queued`, `running`, `complete`, `partial`, and `failed`. A partial result includes stage errors and successful evidence. A failed job may have no context; check its status error. The status includes the current stage, including OCR frame progress.

One background worker serializes expensive jobs, with at most eight active/queued jobs. Additional submissions receive HTTP 429. Job status and outputs persist on disk. After a service restart, unfinished jobs are marked failed and can be submitted again; automatic resume and content caching are not implemented.

| Endpoint | Result |
| --- | --- |
| `GET /v1/jobs/{id}` | Status and stage |
| `GET /v1/jobs/{id}/result` | Full JSON context |
| `GET /v1/jobs/{id}/context?start=60&end=120` | Evidence overlapping that time interval |
| `GET /v1/jobs/{id}/artifacts/context.md` | Readable report |
| `GET /v1/jobs/{id}/artifacts/captions/speech.srt` | Speech captions |
| `GET /v1/jobs/{id}/artifacts/frames/frame_00001.jpg` | Source evidence frame |

Manifest artifact paths are relative to the job folder and map to the artifact endpoint. The readable report lists observed slide text and the speech transcript; it is not a model-generated summary.

## Call from code

```python
import time
import httpx

with httpx.Client(base_url="http://127.0.0.1:18765", timeout=30) as client:
    response = client.post("/v1/jobs", json={
        "path": "C:/Courses/lecture-01.mp4",
        "options": {"language": "en", "ocr_language": "en"},
    })
    response.raise_for_status()
    job = response.json()
    while True:
        response = client.get(f"/v1/jobs/{job['id']}")
        response.raise_for_status()
        status = response.json()
        if status["status"] not in {"queued", "running"}:
            break
        time.sleep(2)
    if status["status"] == "failed":
        raise RuntimeError(status.get("error", "Video processing failed"))
    response = client.get(job["result_url"])
    response.raise_for_status()
    context = response.json()
```

## Direct CLI use

```powershell
.\.venv\Scripts\python.exe -m video_context.cli analyze 'C:/Courses/lecture-01.mp4' `
    --output data/lecture-01 --language en --ocr-language en
```

The output directory must be empty. A partial extraction exits with code 2 while preserving its outputs. `--no-ocr` and `--no-speech` support targeted extraction.

## Sampling and timing

Shots are detected with PySceneDetect. Each shot is sampled near its beginning, with periodic samples for long shots. If the frame cap is reached, samples are spread across the recording; some shots can then have no frame. Sampling coverage and the cap are recorded in JSON.

PyAV decodes frames and records their actual presentation timestamps and time base. Images are rotated for display, and OCR coordinates are normalized to each exported image. Speech is placed on the same source timeline using the audio stream offset. Timings use seconds from the format start, with inclusive starts and exclusive ends.

Sparse sampling can miss short text appearances, annotations, or diagram changes within a shot. Increase sampling density for important sections. No coverage guarantee is made for all writing in a lecture.

## Local processing and offline operation

Inference uses installed libraries and downloaded model weights. Speech inference disables automatic downloads. OCR uses explicit local model directories recorded during preparation, and model-source availability checks are disabled. Run `prepare-models` before the first job; missing assets produce a stage error. Model preparation is the online phase; media is not sent to an external inference API.

An agent that receives the result may itself be cloud-hosted. That is separate from local extraction. Fully offline use requires the caller and any later interpretation models to run locally too.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/make_sample.py --speech
.\.venv\Scripts\python.exe -m video_context.cli analyze data/sample/course.mp4 --output data/sample-result
.\.venv\Scripts\python.exe scripts/smoke_models.py
```

The generated sample has two course-style slides and optional Windows synthesized narration. Automated tests cover real media decoding, shot boundaries, variable frame rate, rotation, audio offsets, missing audio, partial failures, failed jobs, uploads, result retrieval, and status persistence. The model smoke script verifies actual slide OCR and speech recognition with outbound sockets blocked in the Python process. See the [first course recording test](./course-recording-test.md) for measured performance and quality gaps on the actual course video.
