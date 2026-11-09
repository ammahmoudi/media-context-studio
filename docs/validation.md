# Local validation

November 9 update: the [full course API run](./full-course-validation.md) completed in 29 minutes with no extraction warnings. Coverage, timestamps, references, artifact downloads, and beginning/middle/end context queries passed. A transient Windows progress-file lock was fixed with bounded atomic-replacement retries; the automated suite now passes 16 tests.

Latest optimization validation: all 14 tests passed and dependency validation passed. See [measured optimization results](./optimization.md) for GPU setup, incremental OCR, benchmark scope, and current timings. Earlier checks below describe the initial baseline.

Validated on November 8, 2026.

## Installed configuration

- Windows, Python 3.12.4, approximately 31.8 GiB RAM, 16 logical CPU threads.
- NVIDIA RTX 4060 Laptop GPU, approximately 8 GiB VRAM; speech now uses verified CUDA `large-v3-turbo` / `int8_float16`. OCR remains on CPU.
- FastAPI 0.162.2, faster-whisper 1.2.1, PySceneDetect 0.7.2.
- PaddleOCR 3.7.0, PaddlePaddle 3.3.1, PyAV 19.0.2.
- OpenCV base and contrib 4.10.0.84.
- Multilingual Whisper base and official PP-OCRv5 mobile English OCR models downloaded locally.

The exact installed Python dependency versions are recorded in [requirements.lock.txt](../requirements.lock.txt). To reproduce them in an empty environment, install that file and then install the project with `pip install --no-deps -e .`. FFmpeg and model weights are separate assets.

## Automated tests

Nine tests passed after the audio compatibility change, in 5.19 seconds. They cover real shot and frame extraction, variable frame rate, display rotation, source audio offsets, missing audio, partial OCR failures, corrupt input, HTTP job flow, uploads, time-range retrieval, artifact restrictions, and status persistence. These tests exercise media timing and service behavior; they do not establish recognition quality on real lectures.

The environment also passed `pip check` with no broken requirements.

## Actual model inference with network blocked

Command:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_models.py --output data/model-smoke-2
```

The sample contains two course-style slides and synthesized English speech. Outbound socket connections were blocked inside the inference process, and the HF offline flags were enabled.

| Result | Observed value |
| --- | --- |
| Overall extraction status | Complete |
| Video duration | Approximately 10.2 seconds |
| Shots | 2 |
| Evidence frames | 2 |
| OCR observations | 4 |
| Speech segments | 2 |
| Total processing time | 17.062 seconds, including cold model initialization |

OCR recovered the four displayed lines: Graph Algorithms; Vertices and edges; Breadth First Search; Explore one level at a time. Speech recognition recovered the narration but transcribed “Breadth” as “Breath.” Technical terms still need checking against the audio and slide evidence.

This is a small functional check, not a benchmark for hour-long recordings. It does not verify equations, handwriting, diagrams, Persian-language quality, or GPU execution.

## Running service

The service runs through the VS Code task **Run local video context service**, at <http://127.0.0.1:18765/docs>. Windows refused binding port 8765; 18765 was available. The default and usage examples use 18765.

Actual HTTP checks verified health, asynchronous job submission, job completion, artifact results, and retrieval of evidence from a selected time interval. A full speech-and-OCR job completed through the running API in 17.969 seconds, returning two shots, two frames, four text observations, and two speech segments. Querying seconds 5 through 10 returned one frame and its two text observations.

## Real course validation

The [first course recording test](./course-recording-test.md) now covers a 57-minute recording's shots and frames, plus a two-minute OCR/speech excerpt. English slide OCR recovered inspected text, but produced repeated/interface observations. Persian transcription with Whisper base was visibly garbled. Course-specific slide-change detection, deduplication, and stronger speech recognition are still needed before treating the results as dependable course notes.
