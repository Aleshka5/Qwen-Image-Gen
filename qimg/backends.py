"""Mongo, MinIO, and RabbitMQ. Constructed only when the environment points at them."""

from __future__ import annotations

import json
import threading


class MongoStore:
    def __init__(self, url: str, database: str) -> None:
        from pymongo import MongoClient

        self._db = MongoClient(url, serverSelectionTimeoutMS=4000)[database]
        self._db["projects"].create_index("user_id")
        self._db["tasks"].create_index([("user_id", 1), ("idempotency_key", 1)])
        self._db["shapes"].create_index("project_id")

    def insert(self, collection: str, doc: dict) -> dict:
        self._db[collection].insert_one({**doc, "_id": doc["id"]})
        return doc

    def get(self, collection: str, doc_id: str) -> dict | None:
        doc = self._db[collection].find_one({"_id": doc_id})
        return _plain(doc)

    def update(self, collection: str, doc_id: str, **fields) -> dict:
        self._db[collection].update_one({"_id": doc_id}, {"$set": fields})
        return self.get(collection, doc_id)

    def delete(self, collection: str, doc_id: str) -> None:
        self._db[collection].delete_one({"_id": doc_id})

    def claim(self, collection: str, doc_id: str, expect: str, **fields) -> bool:
        result = self._db[collection].update_one(
            {"_id": doc_id, "status": expect},
            {"$set": fields},
        )
        return result.modified_count == 1

    def query(self, collection: str, **equals) -> list[dict]:
        return [_plain(doc) for doc in self._db[collection].find(dict(equals))]


def _plain(doc):
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


class MinioBlobs:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, *, secure: bool) -> None:
        from minio import Minio

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        import io

        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type,
        )

    def get(self, key: str):
        try:
            response = self._client.get_object(self._bucket, key)
        except Exception as exc:
            if getattr(exc, "code", "") in {"NoSuchKey", "NoSuchObject"}:
                return None
            raise
        try:
            return response.read(), response.headers.get("Content-Type", "image/png")
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def delete_prefix(self, prefix: str) -> None:
        for item in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
            self._client.remove_object(self._bucket, item.object_name)


class RabbitQueue:
    """One connection, two queues on the ``qimg`` topic exchange: intake and gen.

    ``pika.BlockingConnection`` is not thread-safe, and the web process shares this
    instance across every gunicorn thread. Two concurrent ``basic_publish`` calls tear
    the outbound frame buffer (``StreamLostError`` / ``IndexError: pop from an empty
    deque``), and a message can be lost while the publish reports success — the task
    then stays ``queued`` in Mongo forever. Every use of the connection holds the lock.

    A connection also dies while nobody uses it: the broker closes a socket that has
    been idle past its heartbeat timeout, and pika only learns that when the socket is
    read. ``is_open`` therefore stays ``True`` on a socket the broker already reset, so
    the first publish after an idle period wrote into a dead socket and surfaced as
    ``StreamLostError: ConnectionResetError(104, 'Connection reset by peer')``. Every
    publish now polls for that close first and repeats once on a fresh connection.
    Delivery is at-least-once, which both handlers tolerate: they are idempotent on the
    task id.
    """

    def __init__(self, url: str) -> None:
        import pika

        self._params = pika.URLParameters(url)
        self._params.heartbeat = 600
        self._connection = None
        self._channel = None
        self._lock = threading.RLock()
        with self._lock:
            self._connect()

    def _connect(self) -> None:
        import pika

        self._drop()
        connection = pika.BlockingConnection(self._params)
        self._connection = connection
        channel = connection.channel()
        channel.exchange_declare("qimg", exchange_type="topic", durable=True)
        for name in ("intake", "gen"):
            channel.queue_declare(f"qimg.{name}", durable=True)
            channel.queue_bind(f"qimg.{name}", "qimg", routing_key=name)
        self._channel = channel

    def _drop(self) -> None:
        """Forget the current connection, closing it best-effort."""
        connection, channel = self._connection, self._channel
        self._connection = self._channel = None
        for closer in (channel, connection):
            close = getattr(closer, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:
                pass

    def _alive(self) -> bool:
        return bool(
            self._connection is not None
            and self._channel is not None
            and self._connection.is_open
            and self._channel.is_open
        )

    def publish(self, name: str, body: dict) -> None:
        import pika
        import pika.exceptions

        payload = json.dumps(body).encode("utf-8")
        with self._lock:
            for attempt in (1, 2):
                try:
                    self._ensure().basic_publish(
                        "qimg",
                        name,
                        payload,
                        properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                    )
                    return
                except (pika.exceptions.AMQPError, OSError):
                    if attempt == 2:
                        raise
                    self._drop()

    def pull(self, name: str):
        with self._lock:
            method, _props, body = self._ensure().basic_get(f"qimg.{name}", auto_ack=False)
            if method is None:
                return None
            payload = json.loads((body or b"{}").decode("utf-8"))
            tag = method.delivery_tag

        def ack() -> None:
            with self._lock:
                self._ensure().basic_ack(tag)

        def nack() -> None:
            with self._lock:
                self._ensure().basic_nack(tag, requeue=False)

        return _Delivery(payload, ack, nack)

    def _ensure(self):
        """Return an open channel, reconnecting when the socket is dead.

        ``is_open`` alone is not a liveness check: pika learns about a broker-side
        close only when the socket is read, so an idle-timed-out connection still
        claims to be open and the next ``basic_publish`` is what fails.
        """
        connection = self._connection
        if connection is None or not self._alive():
            self._connect()
        else:
            try:
                connection.process_data_events(time_limit=0)
            except Exception:
                self._connect()
            else:
                if not self._alive():
                    self._connect()
        if self._channel is None:
            raise RuntimeError("queue connection is not open")
        return self._channel


class _Delivery:
    def __init__(self, body, ack, nack) -> None:
        self.body = body
        self.ack = ack
        self.nack = nack

