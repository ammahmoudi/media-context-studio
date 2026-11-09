# Full course recording validation

Run date: November 9, 2026. This follows the [excerpt optimization test](./optimization.md) and exercises a fresh end-to-end API job on the full recording.

## Input and settings

- Video duration: 3,451.85 seconds, or 57 minutes 31.85 seconds; 1280 × 720.
- Persian speech: locally cached `large-v3-turbo`, RTX 4060 Laptop, CUDA `int8_float16`.
- English visible-text recognition: PaddleOCR mobile models on CPU, incremental OCR enabled.
- Sampling: every 15 seconds plus shot representatives; frame limit 500, with no cap applied.
- Job: `0231b5bb002148dba9f3ccc9a333b38f`.

Outputs: [context JSON](../data/jobs/0231b5bb002148dba9f3ccc9a333b38f/context.json), [readable context](../data/jobs/0231b5bb002148dba9f3ccc9a333b38f/context.md), [speech captions](../data/jobs/0231b5bb002148dba9f3ccc9a333b38f/captions/speech.srt), and [visual overview](../data/course-test/full-optimized-overview.jpg).

## Measured stages

| Stage | Seconds | Result |
| --- | ---: | --- |
| Scene detection | 262.805 | Six shot intervals |
| Frame extraction | 27.193 | 232 frames, 0.50–3,439.50 seconds |
| Speech | 251.107 | 1,151 segments, 4,663 word observations |
| OCR | 1,198.377 | 2,455 text observations, 636 distinct recognized strings |
| Total | **1,740.425** | **29 minutes 0.425 seconds**, complete without warnings |

The full speech stage took about 4 minutes 11 seconds, substantially less than the earlier sample-based estimate of 9.3 minutes. Actual full-recording measurements should replace estimates when planning similar jobs.

Speech spans 9.81–3,451.58 seconds. Segment starts are ordered; all checked segment and word timestamps stay within the source duration. This does not prove that every spoken word was recognized. Silence and recognition omissions require listening checks to distinguish.

## Content and quality inspection

The visual overview confirms introductory algorithm slides, annotated graph examples, PDF contents, and the cover of *Combinatorial Algorithms: Generation, Enumeration, and Search*. Much of the later recording retains the book cover while people speak. Shot intervals mostly represent screen-layout/sharing transitions; six shots do not mean six PDF pages. Periodic frames provide additional evidence within long shots.

Transcript spot-checks near the beginning, middle, and end suggest course requirements, book/topic orientation, combinatorial problems, and student introductions. This is an interpretation of extracted evidence, not a reference transcript. Persian names, technical terms, and some ordinary phrases contain visible recognition errors. Treat captions as a searchable draft requiring correction, rather than verified course notes. No accuracy percentage has been measured.

English OCR is configured for slide/book text. Persian chat messages and attendee names are not comprehensively recognized by that profile. Full-screen frames retain those areas for later inspection. Sampling can miss content that appears and disappears between frames. Incremental OCR may miss tiny or faint changes; see the [OCR limitations](./optimization.md).

Visual model descriptions, speaker identification, semantic chapters, and automatic lecture summaries are not implemented in this extraction service.

## Windows reliability issue found and fixed

The first full attempt, `cffd66bf07a648fbac63ab64348c280c`, hit Windows access denial when atomically replacing a progress-status file. That exception interrupted the speech stage. The attempt was stopped and retained as failed/interrupted evidence; its timings do not count as the successful run.

The [atomic JSON writer](../video_context/pipeline.py) now retries temporary permission errors with bounded backoff, retaining the previous complete file until replacement succeeds. Permanent denial still raises after eight attempts. The entire automated suite passed **16 tests**, including regression checks for transient and permanent locks. The restarted full run completed speech with frequent progress updates without recurrence.

## Final validation

The job completed successfully with no extraction warnings. Total measured processing time was approximately 50.4% of video duration on this laptop. This is a single recording measurement, not a guarantee for other content. It excludes the interrupted first attempt and time spent fixing/testing the status writer. OCR accounted for approximately 68.9% of the successful job's processing time.

The [machine-readable validation record](../data/course-test/full-validation.json) passed all checked invariants:

- Source SHA-256 matches the earlier full-recording test.
- All six shot intervals cover the source timeline without gaps or overlaps.
- All 232 frame files exist. Frame times are within the recording, with a maximum sampling gap of 15 seconds; no frame cap was applied.
- Text IDs and frame references resolve; normalized bounding boxes are valid and reused observations have matching timestamp/reference counts.
- Speech segment starts are ordered, and segment/word timestamps stay within the recording duration.
- Context JSON, readable Markdown, and speech SRT are present and nonempty.

Incremental OCR performed 21 full-frame passes and 430 patch passes. It reused 48 frames entirely and updated 163 incrementally; 13.08% of all sampled frame pixels went to OCR. Observation counts are evidence records, not a count of unique facts or correctly recognized words.

API interval checks returned the following evidence:

| Interval, seconds | Frames | Text observations | Speech segments |
| --- | ---: | ---: | ---: |
| 0–60 | 4 | 107 | 4 |
| 1,700–1,760 | 4 | 46 | 24 |
| 3,390–3,452 | 4 | 62 | 28 |

Artifact downloads for context JSON, readable context, speech SRT, and the final frame each returned HTTP 200. The service remains available at <http://127.0.0.1:18765/docs>. Result endpoint: `/v1/jobs/0231b5bb002148dba9f3ccc9a333b38f/result`.

Structural validation establishes that evidence is internally consistent and retrievable. It does not establish transcription/OCR accuracy or complete semantic understanding. The Persian transcript still needs review before generating dependable study notes, particularly for names and technical vocabulary.
