"""Wire a store, a blob bucket, and a queue. Memory when no backends are configured."""

from __future__ import annotations

from .store import MemoryBlobs, MemoryQueue, MemoryStore


class Runtime:
    def __init__(self, store, blobs, queue, dispatch=None) -> None:
        self.store = store
        self.blobs = blobs
        self.queue = queue
        self.dispatch = dispatch


def build_runtime() -> Runtime:
    from app.config import settings

    configured = [settings.mongo_url, settings.rabbit_url, settings.minio_endpoint]
    if not any(configured):
        return Runtime(MemoryStore(), MemoryBlobs(), MemoryQueue())
    if not all(configured) or not settings.minio_access_key or not settings.minio_secret_key:
        raise RuntimeError(
            "MONGO_URL, RABBIT_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY and "
            "MINIO_SECRET_KEY must be set together"
        )
    from .backends import MinioBlobs, MongoStore, RabbitQueue

    return Runtime(
        MongoStore(settings.mongo_url, settings.mongo_db),
        MinioBlobs(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key,
            settings.minio_bucket,
            secure=settings.minio_secure,
        ),
        RabbitQueue(settings.rabbit_url),
    )
