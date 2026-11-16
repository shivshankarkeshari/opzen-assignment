import hashlib
import io
import json
from functools import lru_cache

from minio import Minio

from backend.config import settings


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache
def client() -> Minio:
    s = settings()
    return Minio(s.minio_endpoint, s.minio_access_key, s.minio_secret_key, secure=s.minio_secure)


def put(key: str, data: bytes, content_type="application/octet-stream") -> dict:
    s = settings()
    result = client().put_object(s.minio_bucket, key, io.BytesIO(data), len(data), content_type)
    return {
        "bucket": s.minio_bucket,
        "key": key,
        "checksum": digest(data),
        "version": result.version_id,
    }


def get(ref: dict) -> bytes:
    if ref.get("bucket") != settings().minio_bucket:
        raise ValueError("Invalid artifact bucket")
    response = client().get_object(ref["bucket"], ref["key"], version_id=ref.get("version"))
    try:
        data = response.read()
    finally:
        response.close()
        response.release_conn()
    if digest(data) != ref["checksum"]:
        raise ValueError("Artifact checksum mismatch")
    return data


def put_json(key: str, value) -> dict:
    return put(key, json.dumps(value, ensure_ascii=False).encode(), "application/json")


def get_json(ref: dict):
    return json.loads(get(ref))
