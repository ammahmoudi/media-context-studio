# First course recording test

This document preserves the initial test. See [subsequent optimizations](./optimization.md) for full-lecture incremental OCR, GPU speech, the stronger Persian model, and revised runtime estimates.

Tested locally on November 8, 2026. This test exercises the user's real combinatorial algorithms recording, beyond the generated sample.

## Recording

- Duration: 3,451.85 seconds, or 57 minutes 31.85 seconds.
- File size: 348,887,362 bytes, approximately 333 MiB.
- Video: 1280 by 720, H.264, 20 frames per second.
- Audio: mono AAC at 48 kHz.
- English slides and PDF pages in an online-class interface; Persian detected in the audio excerpt.

Two jobs cover different scopes. The full recording was processed for shots and frames. OCR and speech were tested on a separate two-minute excerpt requested from source minutes 01:00 through 03:00. Excerpt timestamps are relative to that clip, not the original lecture; add approximately 60 seconds when locating them in the original stream-copy source.

## Full recording visual extraction

| Measurement | Result |
| --- | --- |
| Processing status | Complete for the requested visual stages |
| Shot detection | 6 segments |
| Timestamped frames | 232 |
| Sampling | Every 15 seconds within shots, plus a sample near each shot start |
| Frame cap | 300; cap was not reached |
| Shot detection time | 252.142 seconds |
| Frame extraction time | 25.048 seconds |
| Overall processing time | 277.716 seconds, approximately 4 minutes 38 seconds |
| Speech and OCR | Disabled for this full-recording job |

Outputs:

- [Full recording JSON context](../data/jobs/1f07756a389441449e43b3d867106486/context.json)
- [Full recording readable frame report](../data/jobs/1f07756a389441449e43b3d867106486/context.md)
- [Extracted frames](../data/jobs/1f07756a389441449e43b3d867106486/frames/)
- [Shot overview image](../data/course-test/shot-overview.jpg)

The six segments cover these intervals in seconds:

| Segment | Start | End |
| --- | --- | --- |
| 1 | 0.00 | 777.75 |
| 2 | 777.75 | 819.65 |
| 3 | 819.65 | 843.90 |
| 4 | 843.90 | 846.95 |
| 5 | 846.95 | 1984.00 |
| 6 | 1984.00 | 3451.85 |

These are visual shot segments, not six lecture slides or six topics. Inspection shows sharing/layout changes and the appearance of a webcam among the detected boundaries. PDF content also changes inside the long fifth segment: a preview near 900 seconds shows a contents page, while a preview near 1800 seconds shows the book cover. The hard-cut detector therefore does not provide a complete PDF page-change timeline. Periodic frames still preserve observations inside those segments.

## Two-minute speech and OCR test

| Measurement | Result |
| --- | --- |
| Clip duration | 120.011 seconds |
| Processing status | Complete |
| Shots | 1 |
| Timestamped frames | 8 |
| OCR text observations | 393, including repeated slide text and interface text |
| Speech segments | 6 |
| Detected speech language | Persian, `fa` |
| Speech processing time | 150.621 seconds |
| OCR processing time | 208.653 seconds |
| Overall processing time | 368.529 seconds, approximately 6 minutes 9 seconds |

Outputs:

- [Excerpt JSON context](../data/jobs/e5f6bc9697734647b286ae4e10654aa3/context.json)
- [Excerpt readable context](../data/jobs/e5f6bc9697734647b286ae4e10654aa3/context.md)
- [Excerpt speech captions](../data/jobs/e5f6bc9697734647b286ae4e10654aa3/captions/speech.srt)

OCR correctly recovered inspected slide lines about NP-hard problems, exhaustive search, backtracking and branch-and-bound, heuristic search, and approximation algorithms. Those inspected phrases had model confidence scores around 0.98 to 1.00. These are recognition scores, not a measured accuracy rate against a labeled reference.

The inspected early and late frames show the same slide. Repeating recognition at each sample produced duplicated text. The full-frame OCR also reads navigation and class interface elements, including content outside the course slide. These observations should not be treated as 393 distinct course facts.

The base Whisper transcript contains visibly garbled Persian wording and is not reliable enough for course notes. Language detection succeeding and a stage completing do not establish transcription accuracy. No reference transcript was available to calculate word error rate.

## GPU readiness check

The RTX 4060 is visible to CTranslate2. A local GPU inference attempt using the installed CUDA 13 and cuDNN libraries failed because `cublas64_12.dll` was missing. The current runtime needs compatible CUDA 12 cuBLAS libraries; the GPU driver and CUDA 13 installation alone do not satisfy that dependency. CPU processing remained the tested service configuration. No system CUDA configuration was changed.

## Required improvements for course context

1. Add a configurable content region so changes in the chat, attendee list, or webcam do not dominate slide analysis or OCR.
2. Detect slide/PDF changes independently of hard video cuts, including page scrolls and incremental annotations.
3. Deduplicate stable slide regions before OCR, retaining each observation's timestamps and original frame references.
4. Benchmark a stronger multilingual speech checkpoint on this audio and resolve CUDA 12 runtime dependencies before a full lecture transcription run.
5. Preserve the distinction between extracted evidence and a reviewed, trustworthy course explanation. Diagram and formula interpretation remain separate enrichment work.

No full-recording speech transcript or full-recording OCR was generated in this test. The full-recording output establishes frame coverage and baseline shot detection; the excerpt establishes current OCR behavior, speech quality limitations, and CPU cost.
