"""In-memory documents, blobs, and queues. Tests and local UI use these."""

from __future__ import annotations

import copy
import threading
from collections import defaultdict


class MemoryStore:
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, dict]] = defaultdict(dict)
        self._lock = threading.Lock()

    def insert(self, collection: str, doc: dict) -> dict:
        with self._lock:
            self._docs[collection][doc["id"]] = copy.deepcopy(doc)
        return copy.deepcopy(doc)

    def get(self, collection: str, doc_id: str) -> dict | None:
        with self._lock:
            doc = self._docs[collection].get(doc_id)
            return copy.deepcopy(doc) if doc else None

    def update(self, collection: str, doc_id: str, **fields) -> dict:
        with self._lock:
            doc = self._docs[collection][doc_id]
            doc.update(fields)
            return copy.deepcopy(doc)

    def claim(self, collection: str, doc_id: str, expect: str, **fields) -> bool:
        with self._lock:
            doc = self._docs[collection].get(doc_id)
            if doc is None or doc.get("status") != expect:
                return False
            doc.update(fields)
            return True

    def query(self, collection: str, **equals) -> list[dict]:
        with self._lock:
            found = []
            for doc in self._docs[collection].values():
                if all(doc.get(key) == value for key, value in equals.items()):
                    found.append(copy.deepcopy(doc))
            return found


class MemoryBlobs:
    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        with self._lock:
            self._blobs[key] = (data, content_type)

    def get(self, key: str) -> tuple[bytes, str] | None:
        with self._lock:
            item = self._blobs.get(key)
            return None if item is None else (item[0], item[1])

    def delete(self, key: str) -> None:
        with self._lock:
            self._blobs.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [name for name in self._blobs if name.startswith(prefix)]:
                self._blobs.pop(key, None)


class MemoryQueue:
    def __init__(self) -> None:
        self._queues: dict[str, list[dict]] = defaultdict(list)
        self._lock = threading.Lock()

    def publish(self, name: str, body: dict) -> None:
        with self._lock:
            self._queues[name].append(copy.deepcopy(body))

    def pull(self, name: str):
        with self._lock:
            if not self._queues[name]:
                return None
            body = self._queues[name].pop(0)
        return _Delivery(body)


class _Delivery:
    def __init__(self, body: dict) -> None:
        self.body = body

    def ack(self) -> None:
        return None

    def nack(self) -> None:
        return None
