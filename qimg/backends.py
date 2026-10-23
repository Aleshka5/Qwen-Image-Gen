"""Mongo, MinIO, and RabbitMQ. Constructed only when the environment points at them."""

from __future__ import annotations

import json



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
    """One connection, two queues on the ``qimg`` topic exchange: intake and gen."""

    def __init__(self, url: str) -> None:
        import pika

        self._params = pika.URLParameters(url)
        self._params.heartbeat = 600
        self._connect()

    def _connect(self) -> None:
        import pika

        self._connection = pika.BlockingConnection(self._params)
        channel = self._connection.channel()
        channel.exchange_declare("qimg", exchange_type="topic", durable=True)
        for name in ("intake", "gen"):
            channel.queue_declare(f"qimg.{name}", durable=True)
            channel.queue_bind(f"qimg.{name}", "qimg", routing_key=name)
        self._channel = channel

    def publish(self, name: str, body: dict) -> None:
        import pika

        self._ensure()
        self._channel.basic_publish(
            "qimg",
            name,
            json.dumps(body).encode("utf-8"),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )

    def pull(self, name: str):
        self._ensure()
        method, _props, body = self._channel.basic_get(f"qimg.{name}", auto_ack=False)
        if method is None:
            return None
        payload = json.loads(body.decode("utf-8"))

        def ack() -> None:
            self._channel.basic_ack(method.delivery_tag)

        def nack() -> None:
            self._channel.basic_nack(method.delivery_tag, requeue=False)

        return _Delivery(payload, ack, nack)

    def _ensure(self) -> None:
        if self._connection.is_open and self._channel.is_open:
            return
        self._connect()


class _Delivery:
    def __init__(self, body, ack, nack) -> None:
        self.body = body
        self.ack = ack
        self.nack = nack

