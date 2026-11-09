# Local dashboard

The current studio has dedicated Overview, Runs, Workers, New Run, and full run-detail pages, plus video playback with WebVTT captions derived from the saved SRT/transcript. The deployed service now uses PostgreSQL/S3 and authenticated sessions. See [current deployment and migration](./production.md); the original single-page workflow below is retained as initial validation history.

Open <http://127.0.0.1:18765/> to use the dashboard. Swagger is at <http://127.0.0.1:18765/docs>. The API serves both; there is no separate frontend server to start for normal use. The existing **Run local video context service** task starts everything.

To inspect the full course result directly, open <http://127.0.0.1:18765/ui/?job=0231b5bb002148dba9f3ccc9a333b38f>.

## Using it

- **New run:** provide an absolute video path on this laptop, or choose a file to upload. Select speech/OCR languages, sampling interval, maximum frames, and whether to extract speech/screen text or reuse unchanged text. The OCR language menu shows prepared profiles.
- **Live runs:** view current extraction stage and stage progress when available. Shot detection and frame extraction show their stage names without an invented completion percentage. Updates arrive every three seconds while the page is active.
- **Queue:** view waiting videos in execution order with their positions. One worker runs videos sequentially. Capacity is eight accepted jobs including the active job; the submit button disables when the queue is full, and the API independently enforces capacity.
- **Run history:** browse newest-first records, including complete, partial, and failed runs. History is paginated in groups of ten in the UI and persists across service restarts.
- **Context explorer:** inspect a run, read transcript and OCR evidence, view frame thumbnails, open context JSON/report, or download SRT captions. Query a start/end time in seconds, then return to the full video. Persian text uses automatic text direction.
- **Swagger:** use the complete interactive API, including health, submission/upload, job listing/status, context/results, and artifacts. Swagger scripts/styles and dashboard fonts/assets are served locally, with no CDN needed at runtime.

The service remains bound to localhost and has no authentication. Uploading copies the input into the local job folder; submitting a path reads the existing file. Stage failures and unavailable results appear as errors rather than an empty success state.

## Listing runs from another tool

`GET /v1/jobs?limit=20&offset=0` returns:

```json
{
  "running": [],
  "queued": [],
  "history": [],
  "total_history": 0,
  "limit": 20,
  "offset": 0,
  "counts": {"running": 0, "queued": 0, "complete": 0, "partial": 0, "failed": 0},
  "capacity": 8,
  "available_slots": 8
}
```

Running/queued entries are always included regardless of history pagination. Queued entries have a one-based `queue_position`. History accepts limits 1–100 and nonnegative offsets. New runs record `started_at` and `finished_at`; older records may lack these fields. Restarted unfinished jobs remain marked failed under the existing restart policy; they are not silently requeued.

## Development and packaging

The frontend source is in [web/](../web/), using React, TypeScript, Vite, TanStack Query, Tailwind, and shadcn components. Its build is served from [video_context/static/](../video_context/static/), with bundled Swagger assets copied there by the build script. Python package data includes the compiled assets.

Run the **Build video context dashboard** task, or:

```powershell
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

Then start the service normally. Node/pnpm are needed to rebuild the UI, not to run the built dashboard. Rebuild before building a Python wheel; backend tests that verify dashboard routes also require the compiled assets.

For frontend development, run `pnpm --dir web dev` and open the printed `/ui/` URL. Vite proxies local API and Swagger requests to port 18765. The Python API must already be running.

## Validation on November 9, 2026

- Frontend production build and TypeScript checks passed. Lint passed.
- Python suite: **18 tests passed**, including FIFO execution/visible queue positions, full queue rejection, history pagination/restart, dashboard routing, and locally served Swagger assets.
- Browser checks: real local-path submissions and file upload completed; active status appeared and transitioned to completed history. The queue API was observed with one active and one waiting job.
- The real full-course result rendered Persian captions, stage timings, and artifact links. A 3,390–3,452 second query returned 28 speech segments and four frame previews.
- A 390-pixel viewport had no document horizontal overflow. Desktop layout and frame previews were inspected.
- Swagger rendered eight API operations with external HTTPS requests blocked.
- Invalid local-path submission displayed the API's validation error in the form without creating a job.

These checks validate the interface. The underlying [full-course extraction limitations](./full-course-validation.md) still apply to speech/OCR accuracy.
