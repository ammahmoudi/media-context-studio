# OCR reuse and GPU speech improvements

This update implements three requested improvements: reuse OCR for unchanged content, enable NVIDIA GPU speech inference, and evaluate a stronger multilingual speech model for the Persian lecture.

## Incremental OCR

`ocr_deduplicate` defaults to `true` in job options. Setting it to `false` restores full-frame OCR for every sampled frame.

The service compares consecutive sampled images and recognizes changed regions. Stable text observations retain their original ID, bounding box, and recognition score, and gain references to additional frames where the change detector considers their image region sufficiently similar. An edit overlapping a recognized text box invalidates that observation for the new frame and reprocesses the complete affected box. Broad layout changes and resolution changes trigger a full pass.

No fixed course-content crop is applied: the original frames and interface regions remain available. Localized updates, including changes outside the slide, can be recognized as patches rather than repeating a full-page pass.

The comparison uses an 8-bit grayscale difference threshold of 22 and a minimum of six changed pixels per component, with margins around recognition patches. This is approximate image-change detection. Very faint or tiny changes can fall below its thresholds; use `ocr_deduplicate: false` when checking such material. It does not create exact appearance/disappearance intervals between sampled frames.

### Output additions

- Text observations retain `frame_id` as their first evidence frame, with `frame_ids` and `observed_at_seconds` listing subsequent evidence.
- Each frame's `ocr.text_ids` resolves its current text observations. `reused_text_ids` distinguishes reused observations.
- Each frame records whether recognition was `full`, `patches`, or `reuse`, and the changed-region coordinates.
- `ocr_stats` records full passes, patch passes, reuse, and the fraction of pixels sent for inference.
- Time-range API queries include reused text when a referenced frame falls inside the requested range.
- The readable report prints newly recognized observations and notes reuse, avoiding repeated full-slide text dumps.

### Measured excerpt result

The same eight frames from the two-minute course excerpt were processed with the same CPU OCR engine and models.

| Measurement | Original | Incremental |
| --- | --- | --- |
| OCR elapsed time | 208.653 seconds | 57.117 seconds |
| Full-frame passes | 8 | 1 |
| Patch passes | 0 | 18 |
| Completely reused frames | 0 | 2 |
| Frames processed incrementally | 0 | 5 |
| Text observations | 393 | 74, with shared frame references |
| Pixels submitted for inference | All frame pixels | Approximately 15.05% of all frame pixels |

OCR was approximately 3.65 times faster in this measurement. The inspected slide title and exhaustive-search heading each retain references to all eight frames. The two measurements are separate runs, not a controlled hardware benchmark or a guarantee for other recordings.

See the [incremental OCR context](../data/course-test/optimized-ocr/context.json) and [readable report](../data/course-test/optimized-ocr/context.md).

### Full lecture OCR

The 57-minute recording's 232 cached frames were also processed. OCR completed in 1,236.261 seconds, or approximately 20 minutes 36 seconds. It performed 21 full-frame passes and 430 smaller patch passes; 48 frames reused existing text completely and 163 were updated incrementally. Approximately 13.08% of total frame pixels were sent to OCR.

This run reused the earlier frame extraction and did not include speech recognition or a fresh scene scan. The earlier full-frame OCR estimate was an extrapolation, not a measured full-recording baseline, so this result does not establish a measured full-lecture speedup factor.

See [full lecture OCR JSON](../data/course-test/full-optimized-ocr/context.json) and [readable text context](../data/course-test/full-optimized-ocr/context.md).

## GPU runtime setup

GPU speech uses CTranslate2, whose [documented requirements](https://github.com/SYSTRAN/faster-whisper#gpu) include CUDA 12 cuBLAS and cuDNN 9. The laptop's CUDA 13 installation does not provide the CUDA 12 DLL names requested by the Windows CTranslate2 wheel.

The optional Windows dependency group installs the NVIDIA cuBLAS runtime inside the project environment:

```powershell
uv pip install --python .venv/Scripts/python.exe --index-url https://pypi.org/simple `
    --cache-dir .uv-cache -e '.[dev,gpu-windows]'
```

The GPU loader registers project-local NVIDIA DLL directories and reuses the laptop's installed cuDNN 9 libraries for CUDA 12. It retains loader handles for the process lifetime. It does not modify the system PATH, registry, CUDA installation, or driver. Other Windows machines also need compatible cuDNN 9 libraries.

GPU failures are reported as stage errors rather than silently switching a large model to CPU. Environment variables continue to override local settings.

## Machine settings and model selection

An optional, Git-ignored `config.local.json` at the project root persists this laptop's selected configuration. The portable defaults remain CPU inference with Whisper base when neither local settings nor environment overrides exist.

The selected stronger model is multilingual `large-v3-turbo`, loaded using GPU `int8_float16` precision to reduce memory use. A model change requires its weights to be prepared before offline jobs run. Model choice, runtime device, and compute type are recorded in the result.

Language detection and model confidence do not establish transcript accuracy. Inspect the Persian output against the recording before treating it as dependable course notes; no reference transcript is available for an accuracy percentage.

## Verified GPU speech and selected settings

This laptop now uses cached `large-v3-turbo`, CUDA, and `int8_float16`, persisted in ignored `config.local.json`. The GPU probe consumed an actual encoder/decoder pass successfully.

The same 120-second excerpt produced these speech-stage times, including model startup:

| Model | Device / precision | Speech time |
| --- | --- | ---: |
| base, original baseline | CPU / int8 | 150.621 s |
| base | RTX 4060 / int8_float16 | 38.800 s |
| large-v3-turbo | RTX 4060 / int8_float16 | 19.503 s |

Base GPU was about 3.88 times faster than the original CPU run. The selected turbo run took about 7.72 times less time than that baseline while also changing the model. These are separate local runs, not a controlled model benchmark. The turbo Persian transcript is more readable on inspection but still contains errors in names and phrases. No reference transcript or accuracy percentage is available.

See [GPU base context](../data/course-test/gpu-base-speech/context.json), [GPU turbo context](../data/course-test/gpu-turbo-speech/context.json), and [turbo captions](../data/course-test/gpu-turbo-speech/captions/speech.srt).

The initial excerpt-based estimate was roughly 34.5 minutes total, including 9.3 minutes for speech. The [November 9 full-course test](./full-course-validation.md) now supersedes that estimate: **29m 00s total**, including **4m 11s speech** and **19m 58s OCR**. Recording content and laptop load can change runtime.

All 14 automated tests passed, including changed-region OCR, stable text reuse, invalidation, fallback, and interval retrieval of reused observations. Dependency validation passed. The [optional Windows GPU lock](../requirements.gpu-windows.lock.txt) records installed NVIDIA packages separately from the portable dependency snapshot. Compatible cuDNN 9 remains a machine prerequisite. One-time runtime/model downloads are excluded from processing timings.

## Final API validation

After restarting the service with the saved laptop settings, a fresh two-minute excerpt job completed successfully in **84.676 seconds**, compared with the original **368.529 seconds**: approximately **4.35 times faster overall** across these separate runs. The new job performed fresh scene detection and frame extraction, GPU turbo speech, and incremental OCR.

| Stage | Seconds |
| --- | ---: |
| Shots | 8.581 |
| Frames | 1.056 |
| GPU speech | 18.499 |
| Incremental OCR | 56.357 |

The result had no warnings. The report artifact returned HTTP 200, and a 105–120 second context query returned one frame, 46 visible-text observations (including reused text), and five speech segments. Visual-model descriptions remain unimplemented; success here covers the requested extraction stages.

Job: `4890eea66b2d4211b0bae77e9d1a6fb5`. See [final context JSON](../data/jobs/4890eea66b2d4211b0bae77e9d1a6fb5/context.json), [readable report](../data/jobs/4890eea66b2d4211b0bae77e9d1a6fb5/context.md), and [Persian captions](../data/jobs/4890eea66b2d4211b0bae77e9d1a6fb5/captions/speech.srt).

## Reproduce the stage benchmark

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_course.py `
    --baseline data/jobs/e5f6bc9697734647b286ae4e10654aa3/context.json `
    --video data/course-test/excerpt.mp4 `
    --output data/course-test/new-benchmark --stage both
```

The benchmark checks the input hash, reuses the previously extracted frames and shot timings, and writes a separate result without modifying the baseline. Its timings cover the requested stages; a normal API job also includes media inspection, shot detection, and frame extraction. `--stage ocr` and `--stage speech` isolate individual stages.
