# Local Video Context

A local HTTP service and command-line tool that convert course videos into structured, timestamped context for code and AI agents.

The first milestone extracts shots, representative frames, visible text, and speech captions. Outputs retain timestamps and evidence references so an agent can inspect the source behind an answer. Visual-model descriptions and events are planned enrichment.

Priorities: free tools, open-source components, local processing, and practical speed.

See [Video context research and design](./docs/video-context.md) for the research, proposed architecture, output example, scope, and acceptance criteria.

See [Setup and API usage](./docs/service.md) to install, start, submit a course video, and retrieve results.

See [Local validation](./docs/validation.md) for installed versions, test results, and the offline model check.

See [First course recording test](./docs/course-recording-test.md) for full-recording frame extraction, excerpt OCR/speech results, and the remaining course-quality gaps.

See [Measured optimizations](./docs/optimization.md) for incremental OCR, verified RTX 4060 speech acceleration, and the stronger Persian speech model. This laptop uses GPU `large-v3-turbo`; portable defaults remain CPU `base`.

See [Full course validation](./docs/full-course-validation.md) for the complete recording test, output links, coverage, measured times, and quality limitations.

## Quick start on Windows

```powershell
.\.venv\Scripts\python.exe -m video_context.cli prepare-models
.\.venv\Scripts\python.exe -m video_context.cli serve
```

Open the interactive API at <http://127.0.0.1:18765/docs>. The service binds to the laptop only.

Open the [local dashboard](http://127.0.0.1:18765/) to submit videos, monitor live runs and the queue, and browse captions, frames, and context. See [dashboard usage and development](./docs/dashboard.md).

Current implementation: FastAPI, FFmpeg/ffprobe, PyAV, PySceneDetect, faster-whisper, and PaddleOCR. A single worker processes jobs while HTTP requests remain responsive. JSON, frames, speech SRT, and a readable context report are saved locally.
