import json
import asyncio
import math
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.datastructures import Headers
from pydantic import BaseModel, Field

from .auth import issue_session, valid_session
from .config import DATA, MODELS, WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL
from .database import now
from .jobs import Jobs
from .models import AnalyzeRequest, Options
from .pipeline import analyze
from .settings import Settings
from .resources import ResourceExhausted, check_disk, memory_budget


class Login(BaseModel):
    api_key: str = Field(max_length=4096)


class BodyLimit:
    """Bound chunked bodies too, before multipart spooling/JSON parsing."""
    def __init__(self, app, upload_limit, scratch):
        self.app, self.upload_limit, self.scratch = app, upload_limit, scratch

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        uploading = scope["path"] == "/v1/jobs/upload"
        maximum = self.upload_limit + 2 * 1024**2 if uploading else 1024**2
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            body = message.get("body", b"")
            total += len(body)
            if total > maximum:
                raise HTTPException(413, "Request body exceeds configured limit")
            if uploading:
                try:
                    check_disk(self.scratch, len(body) * 2)
                except ResourceExhausted as error:
                    raise HTTPException(507, str(error)) from error
            return message

        await self.app(scope, bounded_receive, send)


class DashboardFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code == 404 and "text/html" in Headers(scope=scope).get("accept", "") and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise


def public_record(record):
    return {**{k: v for k, v in record.items() if k not in ("legacy_dir", "input_key", "result_prefix")},
            "video_available": bool(record.get("input_key"))}


def create_app(data_root=DATA, settings=None):
    settings = settings or Settings(data=data_root)

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = Jobs(settings, analyze)
        app.state.upload_slots = asyncio.BoundedSemaphore(2)
        app.state.result_slots = asyncio.BoundedSemaphore(1)
        yield
        app.state.jobs.close()

    app = FastAPI(title="Video Context", version="0.2.0", lifespan=lifespan, docs_url=None,
                  description="Durable video extraction jobs, worker leases, private artifacts and video context.")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(BodyLimit, upload_limit=settings.max_upload_bytes, scratch=settings.data)
    default_openapi = app.openapi

    def openapi():
        schema = default_openapi()
        if settings.api_key:
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["ServiceKey"] = {
                "type": "apiKey", "in": "header", "name": "X-API-Key"}
            for path, operations in schema.get("paths", {}).items():
                if path.startswith("/v1/"):
                    for operation in operations.values():
                        if isinstance(operation, dict):
                            operation["security"] = [{"ServiceKey": []}]
        return schema
    app.openapi = openapi

    @app.middleware("http")
    async def access(request: Request, call_next):
        protected = request.url.path.startswith("/v1/") or request.url.path in ("/docs", "/openapi.json", "/redoc")
        origin = request.headers.get("origin")
        # Cookie-authenticated writes must originate from this service. API-key
        # clients are independent of browser cookies and can call server-to-server.
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
        if protected and settings.api_key:
            header = request.headers.get("x-api-key", "")
            if not (secrets.compare_digest(header.encode(), settings.api_key.encode()) or
                    valid_session(settings.api_key, request.cookies.get("video_session", ""))):
                return JSONResponse({"detail": "Sign in with the service API key"}, status_code=401)
        raw_length = request.headers.get("content-length", "0")
        if raw_length.isdigit() and int(raw_length) > settings.max_upload_bytes + 2 * 1024**2:
            return JSONResponse({"detail": "Upload exceeds configured size limit"}, status_code=413)
        uploading = request.url.path == "/v1/jobs/upload" and request.method == "POST"
        reading_context = request.method == "GET" and request.url.path.startswith("/v1/jobs/") and request.url.path.endswith(("/result", "/context"))
        slot = app.state.upload_slots if uploading else app.state.result_slots if reading_context else None
        if slot and slot.locked():
            return JSONResponse({"detail": "Request capacity reached; retry later"}, status_code=429)
        if reading_context and memory_budget()["available_mb"] < 128:
            return JSONResponse({"detail": "API memory pressure; retry later"}, status_code=503)
        if uploading:
            try:
                check_disk(settings.data, int(raw_length) if raw_length.isdigit() else 0)
            except ResourceExhausted as error:
                return JSONResponse({"detail": str(error)}, status_code=507)
        if slot:
            await slot.acquire()
        try:
            response = await call_next(request)
        finally:
            if slot:
                slot.release()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    static = Path(__file__).parent / "static"
    app.mount("/ui", DashboardFiles(directory=static, html=True, check_dir=False), name="dashboard")

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/ui/")

    @app.get("/docs", include_in_schema=False)
    def swagger():
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Video Context API · Swagger",
            swagger_js_url="/ui/swagger/swagger-ui-bundle.js", swagger_css_url="/ui/swagger/swagger-ui.css",
            swagger_favicon_url="/ui/swagger/favicon-32x32.png", swagger_ui_parameters={"validatorUrl": None})

    @app.post("/session", tags=["Access"])
    def login(body: Login):
        if settings.api_key and not secrets.compare_digest(body.api_key.encode(), settings.api_key.encode()):
            raise HTTPException(401, "Invalid API key")
        response = JSONResponse({"authenticated": True})
        if settings.api_key:
            response.set_cookie("video_session", issue_session(settings.api_key), httponly=True,
                                secure=settings.secure_cookie, samesite="strict", max_age=43200)
        return response

    @app.delete("/session", tags=["Access"])
    def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("video_session")
        return response

    @app.get("/health", tags=["System"])
    def health():
        return {"status": "ok", "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
                "speech": {"model": WHISPER_MODEL, "device": WHISPER_DEVICE, "compute_type": WHISPER_COMPUTE},
                "prepared_ocr_profiles": sorted(p.stem for p in (MODELS / "ocr").glob("*.json")),
                "auth_required": bool(settings.api_key), "allow_path": settings.allow_path,
                "worker_mode": "embedded" if settings.embedded_worker else "external",
                "database": app.state.jobs.db.engine.dialect.name, "storage": app.state.jobs.storage.kind}

    @app.get("/ready", tags=["System"])
    def ready():
        try:
            with app.state.jobs.db.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            if app.state.jobs.storage.client:
                app.state.jobs.storage.client.head_bucket(Bucket=settings.bucket)
        except Exception:
            raise HTTPException(503, "Database or object storage unavailable")
        return {"status": "ready"}

    @app.get("/v1/jobs", tags=["Jobs"])
    def list_jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  status: Literal["complete", "partial", "failed", "cancelled"] | None = None,
                  search: str = Query("", max_length=200)):
        result = app.state.jobs.db.snapshot(limit, offset, status, search)
        result["storage"] = app.state.jobs.storage.kind
        for key in ("running", "queued", "uploading", "history"):
            result[key] = [public_record({k: v for k, v in r.items() if k != "artifacts"}) for r in result[key]]
        return result

    @app.post("/v1/jobs", status_code=202, tags=["Jobs"])
    def submit(body: AnalyzeRequest):
        if not settings.allow_path:
            raise HTTPException(403, "Server-side paths are disabled; upload a video instead")
        path = Path(body.path).expanduser().resolve()
        if not path.is_file():
            raise HTTPException(422, "Local video file does not exist")
        return public_record(app.state.jobs.submit(path, body.options, body.queue))

    @app.post("/v1/jobs/upload", status_code=202, tags=["Jobs"])
    def upload(file: UploadFile = File(...), options_json: str = Form("{}"),
               queue: Literal["default", "cpu", "gpu"] = Form("default")):
        try:
            options = Options.model_validate_json(options_json)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        name = Path((file.filename or "video.mp4").replace("\\", "/")).name[:160]
        manager = app.state.jobs
        record = manager.reserve(name, options, queue)
        incoming = settings.data / "incoming"
        incoming.mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=incoming) as temporary:
                suffix = Path(name).suffix.lower()
                path = Path(temporary) / ("input" + (suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"))
                total = 0
                with path.open("wb") as target:
                    while chunk := file.file.read(1024 * 1024):
                        check_disk(path.parent, len(chunk))
                        total += len(chunk)
                        if total > settings.max_upload_bytes:
                            raise HTTPException(413, "Upload exceeds configured size limit")
                        target.write(chunk)
                if not total:
                    raise HTTPException(422, "Uploaded file is empty")
                return public_record(manager.ingest(path, record))
        except Exception as error:
            manager.db.update(record["id"], status="failed", stage="failed", finished_at=now(),
                              error=error.detail if isinstance(error, HTTPException) else str(error))
            if isinstance(error, ResourceExhausted):
                raise HTTPException(507, str(error)) from error
            raise
        finally:
            file.file.close()

    @app.get("/v1/jobs/{job_id}", tags=["Jobs"])
    def status(job_id: str):
        return public_record(app.state.jobs.get(job_id))

    @app.post("/v1/jobs/{job_id}/retry", status_code=202, tags=["Jobs"])
    def retry(job_id: str):
        return public_record(app.state.jobs.retry(app.state.jobs.get(job_id)))

    @app.post("/v1/jobs/{job_id}/cancel", tags=["Jobs"])
    def cancel(job_id: str):
        app.state.jobs.get(job_id)
        try:
            return public_record(app.state.jobs.db.cancel(job_id))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/v1/jobs/{job_id}/result", tags=["Results"])
    def result(job_id: str):
        record = app.state.jobs.get(job_id)
        if record["status"] not in ("complete", "partial", "failed", "cancelled"):
            raise HTTPException(409, "Job is not finished; poll its status")
        return app.state.jobs.context(record)

    @app.get("/v1/jobs/{job_id}/video", tags=["Results"])
    @app.head("/v1/jobs/{job_id}/video", include_in_schema=False)
    def video(job_id: str, request: Request):
        record = app.state.jobs.get(job_id)
        if not record.get("input_key"):
            raise HTTPException(404, "Video is unavailable; import the original video or submit a new job")
        return app.state.jobs.storage.response(record["input_key"], request)

    @app.get("/v1/jobs/{job_id}/artifacts/{artifact:path}", tags=["Results"])
    def artifact(job_id: str, artifact: str, request: Request):
        return app.state.jobs.artifact(app.state.jobs.get(job_id), artifact, request)

    @app.get("/v1/jobs/{job_id}/context", tags=["Results"])
    def interval(job_id: str, start: float = 0, end: float | None = None):
        context = app.state.jobs.context(app.state.jobs.get(job_id))
        end = end if end is not None else context["source"]["duration_seconds"]
        if start < 0 or end <= start or not all(map(math.isfinite, (start, end))):
            raise HTTPException(422, "Expected finite times with 0 <= start < end")
        frames = [f for f in context["frames"] if start <= f["timestamp_seconds"] < end]
        ids = {f["id"] for f in frames}
        return {"start_seconds": start, "end_seconds": end,
                "shots": [s for s in context["shots"] if s["start_seconds"] < end and s["end_seconds"] > start],
                "frames": frames,
                "on_screen_text": [t for t in context["on_screen_text"] if ids.intersection(t.get("frame_ids", [t["frame_id"]]))],
                "speech_segments": [s for s in context["speech_segments"] if s["start_seconds"] < end and s["end_seconds"] > start]}
    return app


app = create_app()
