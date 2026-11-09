"""Private local/S3 artifacts. API streams byte ranges without buffering videos."""
import json
import mimetypes
import re
import shutil
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse


def safe_key(key):
    parts = PurePosixPath(key).parts
    if not parts or key.startswith("/") or "\\" in key or any(p in ("..", ".") for p in parts):
        raise ValueError("Invalid storage key")
    return key


class Storage:
    def __init__(self, root, bucket=None, endpoint=None):
        self.root, self.bucket = Path(root), bucket
        self.client = None
        if bucket:
            import boto3
            from botocore.config import Config
            from boto3.s3.transfer import TransferConfig
            self.client = boto3.client("s3", endpoint_url=endpoint,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                              connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}))
            self.transfer = TransferConfig(max_concurrency=2, multipart_threshold=8 * 1024**2,
                                           multipart_chunksize=8 * 1024**2)
        else:
            self.root.mkdir(parents=True, exist_ok=True)

    @property
    def kind(self):
        return "s3" if self.client else "local"

    def path(self, key):
        path = (self.root / safe_key(key)).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("Invalid storage path")
        return path

    def ensure_bucket(self):
        if not self.client:
            return
        from botocore.exceptions import ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            if str(error.response["Error"]["Code"]) not in ("404", "NoSuchBucket"):
                raise
            region = self.client.meta.region_name
            kwargs = {} if region in (None, "us-east-1") else {"CreateBucketConfiguration": {"LocationConstraint": region}}
            self.client.create_bucket(Bucket=self.bucket, **kwargs)

    def put_file(self, key, path):
        safe_key(key)
        if self.client:
            self.client.upload_file(str(path), self.bucket, key, Config=self.transfer,
                ExtraArgs={"ContentType": mimetypes.guess_type(str(path))[0] or "application/octet-stream"})
        else:
            target = self.path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def download(self, key, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.client:
            self.client.download_file(self.bucket, safe_key(key), str(path), Config=self.transfer)
        else:
            shutil.copy2(self.path(key), path)

    def read_json(self, key):
        if self.client:
            obj = self.client.get_object(Bucket=self.bucket, Key=safe_key(key))
            try:
                return json.loads(obj["Body"].read())
            finally:
                obj["Body"].close()
        return json.loads(self.path(key).read_text(encoding="utf-8"))

    def size(self, key):
        if self.client:
            return self.client.head_object(Bucket=self.bucket, Key=safe_key(key))["ContentLength"]
        return self.path(key).stat().st_size

    def exists(self, key):
        if not self.client:
            return self.path(key).is_file()
        from botocore.exceptions import ClientError
        try:
            self.size(key)
            return True
        except ClientError as error:
            if str(error.response["Error"]["Code"]) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def response(self, key, request, media_type=None):
        safe_key(key)
        content_type = media_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        if not self.client:
            path = self.path(key)
            if not path.is_file():
                raise HTTPException(404, "Artifact not found")
            return FileResponse(path, media_type=content_type)
        from botocore.exceptions import ClientError
        try:
            total = self.size(key)
            headers = {"Accept-Ranges": "bytes", "Content-Length": str(total)}
            kwargs = {}
            status = 200
            byte_range = request.headers.get("range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
                if not match or not any(match.groups()) or total == 0:
                    raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{total}"})
                a, b = match.groups()
                start = int(a) if a else max(0, total - int(b))
                end = min(int(b), total - 1) if a and b else total - 1
                if start >= total or start > end:
                    raise HTTPException(416, "Unsatisfiable byte range", headers={"Content-Range": f"bytes */{total}"})
                kwargs["Range"] = f"bytes={start}-{end}"
                headers.update({"Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(end-start+1)})
                status = 206
            if request.method == "HEAD":
                return Response(status_code=status, headers=headers, media_type=content_type)
            body = self.client.get_object(Bucket=self.bucket, Key=key, **kwargs)["Body"]
            def chunks():
                try:
                    while chunk := body.read(1024 * 1024):
                        yield chunk
                finally:
                    body.close()
            return StreamingResponse(chunks(), status_code=status, media_type=content_type, headers=headers)
        except ClientError as error:
            if str(error.response["Error"]["Code"]) in ("404", "NoSuchKey", "NotFound"):
                raise HTTPException(404, "Artifact not found") from error
            raise
