# Video, audio, and image inputs

The same local-path submission, multipart upload, durable queue, workers, cancellation/retry, and private storage work for all supported media. No additional containers or model downloads are needed beyond the existing speech/OCR models.

## Dashboard

In **New run**, choose **Video**, **Audio**, or **Image**. This selects the file picker and enables the appropriate settings. The worker detects the actual input type from its contents rather than trusting the selection or filename.

- Video: speech, scene boundaries, sampled frames, and frame OCR.
- Audio: speech transcription and captions; scene detection, frame extraction, and OCR are skipped.
- Image: one full-resolution, EXIF-oriented preview within the pixel limit, plus OCR; speech and scene detection are skipped. Animated or multipage images are rejected rather than silently extracting only the first page.

Run details show a video player, audio player with transcript seeking, or an image preview. Still images do not have a playback timeline. Download source media and results individually or in a selectable archive.

## API and CLI

Upload any supported file to `POST /v1/jobs/upload` using multipart `file`, `options_json`, and optional `queue`. Send the service key in `X-API-Key` when authentication is configured. Local-path submission remains `POST /v1/jobs` where enabled.

Example options for audio:

```json
{"speech": true, "ocr": false, "language": "en"}
```

Example options for an image:

```json
{"speech": false, "ocr": true, "ocr_language": "en"}
```

Defaults also work: stages inapplicable to the detected media type are skipped. Prepare speech/OCR models using the existing `prepare-models` command. Direct CLI extraction uses the same detector:

```sh
python -m video_context.cli analyze interview.wav --output data/interview --language en
python -m video_context.cli analyze slide.png --output data/slide --ocr-language en
```

Use a new or empty output directory for each extraction. On Windows, use the project virtual environment's Python executable.

## Results and compatibility

`source.media_type` is `video`, `audio`, or `image`. The existing context collections remain present for every input:

| Input | Speech segments | Frames and OCR | Shots | Duration |
| --- | --- | --- | --- | --- |
| Video | If enabled and audio exists | Sampled video frames | Scene intervals | Recording duration |
| Audio | If enabled | Empty | Empty | Recording duration |
| Image | Empty | One frame at `timestamp_seconds: 0`, with `shot_id: null`; OCR if enabled | Empty | `0` (no synthetic playback duration) |

Image OCR retains the same frame IDs and normalized bounding boxes as video OCR. The image report includes the recognized text and a preview link. Disabled stages and stages inapplicable to the input are recorded as skipped. Extraction-stage failures preserve successful evidence as partial results.

`GET` and `HEAD /v1/jobs/{id}/media` serve the original input with the existing authentication and byte-range behavior. `/video` remains a compatibility alias. `media_available` reports source availability; the historical `video_available` field is retained with its original source-availability meaning. Older video results without `media_type` remain valid.

`GET /v1/jobs/{id}/context` returns all evidence by default, including a still image's observation at zero. Explicit time ranges retain `[start, end)` semantics; images are included only when the range contains zero.

Archives use `source/video.ext`, `source/audio.ext`, or `source/image.ext` for completed jobs according to the detected type. Existing video archive paths are unchanged. Jobs without extracted source metadata retain the historical video naming fallback. JSON, Markdown, caption files, and frame paths retain their existing names. Caption files are empty for inputs without speech; the dashboard only offers caption links when speech segments exist.

## Formats and limits

Audio formats depend on the installed FFmpeg build, including common WAV, MP3, FLAC, M4A, AAC, and OGG inputs. Embedded cover art is not treated as a video stream. Still images support JPEG, PNG, WebP, BMP, and single-page TIFF through Pillow. Image previews are converted to JPEG so BMP/TIFF sources can also be inspected in a browser. Browser playback support varies for audio/video codecs; the original file remains downloadable.

The existing `VIDEO_CONTEXT_MAX_VIDEO_PIXELS` setting also limits decoded still-image pixels before orientation or conversion. The default is 8,294,400 pixels. `VIDEO_CONTEXT_MAX_DURATION_SECONDS` limits audio as well as video duration. Existing upload, scratch-space, RAM, context-size, and worker limits remain in effect. No semantic image descriptions or document layout interpretation are generated.

## Validation

The automated suite covers audio transcription dispatch, image formats/orientation/pixel limits, embedded audio cover art, corrupt and multipage inputs, upload/worker execution, byte-range playback, interval context, and source/result archive downloads. Existing video tests remain part of the suite.

Local verification also exercised real PaddleOCR and Whisper models on a generated text image and synthesized English speech, including an image job followed by an audio job in the same Windows worker. The Windows OCR loader initializes the CTranslate2 runtime before Paddle to avoid a native DLL import-order conflict; this does not load speech model weights for image jobs. Browser checks verified image previews, audio metadata/transcript seeking, and media-specific extraction controls. Docker API and worker images were rebuilt, and an audio upload completed through the PostgreSQL/S3 stack.
