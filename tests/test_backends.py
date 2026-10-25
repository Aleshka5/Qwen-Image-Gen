"""The real backends: only the invariants a broker-free test can pin down.

The pika connection is shared by every gunicorn thread, so publish must hold the
queue's lock; and an intake message that cannot reach the gen queue must fail the
task instead of leaving it "queued" forever.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

from qimg.backends import RabbitQueue
from qimg.runtime import Runtime
from qimg.store import MemoryBlobs, MemoryStore
from qimg.workers import fail_orphans, handle_intake


class _Channel:
    """Counts overlapping basic_publish calls."""

    is_open = True

    def __init__(self) -> None:
        self.inside = 0
        self.high_water = 0
        self._guard = threading.Lock()

    def basic_publish(self, *_args, **_kwargs) -> None:
        with self._guard:
            self.inside += 1
            self.high_water = max(self.high_water, self.inside)
        time.sleep(0.01)
        with self._guard:
            self.inside -= 1


def _queue(channel, reconnects: list | None = None) -> RabbitQueue:
    """A RabbitQueue wired to a fake channel, with _connect() re-installing it."""

    queue = object.__new__(RabbitQueue)

    def install() -> None:
        object.__setattr__(queue, "_channel", channel)
        object.__setattr__(
            queue, "_connection",
            types.SimpleNamespace(
                is_open=True,
                process_data_events=lambda time_limit: None,
                close=lambda: None,
            ),
        )

    def reconnect() -> None:
        if reconnects is not None:
            reconnects.append(channel)
        install()

    install()
    object.__setattr__(queue, "_lock", threading.RLock())
    object.__setattr__(queue, "_connect", reconnect)
    return queue


def test_publish_holds_the_connection_lock():
    channel = _Channel()
    queue = _queue(channel)
    threads = [
        threading.Thread(target=queue.publish, args=("intake", {"task_id": str(index)}))
        for index in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert channel.high_water == 1


class _DroppedIdleChannel:
    """A channel whose first publish lands on a socket the broker already reset."""

    is_open = True

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self.attempts = 0

    def basic_publish(self, _exchange, routing_key, body, properties=None) -> None:
        self.attempts += 1
        if self.attempts == 1:
            import pika.exceptions

            raise pika.exceptions.StreamLostError(
                "Stream connection lost: ConnectionResetError(104, 'Connection reset by peer')"
            )
        self.published.append((routing_key, body))

    def close(self) -> None:
        self.is_open = False


def test_publish_reconnects_after_the_broker_drops_the_idle_connection():
    channel = _DroppedIdleChannel()
    reconnects: list = []
    queue = _queue(channel, reconnects)

    queue.publish("intake", {"task_id": "t-1"})

    assert len(reconnects) == 1
    assert channel.attempts == 2
    assert channel.published == [("intake", b'{"task_id": "t-1"}')]


def test_publish_gives_up_after_the_second_failure():
    class _Dead(_DroppedIdleChannel):
        def basic_publish(self, *_args, **_kwargs) -> None:
            self.attempts += 1
            import pika.exceptions

            raise pika.exceptions.StreamLostError("stream lost")

    channel = _Dead()
    queue = _queue(channel)

    with pytest.raises(Exception):
        queue.publish("intake", {"task_id": "t-2"})

    assert channel.attempts == 2


class _BoomQueue:
    def publish(self, _name: str, _body: dict) -> None:
        raise OSError("broker went away")

    def pull(self, _name: str):
        return None


def test_intake_failure_marks_the_task_failed():
    store = MemoryStore()
    store.insert("tasks", {
        "id": "t-1",
        "status": "queued",
        "artifacts": [],
        "type": "gen",
    })
    runtime = Runtime(store, MemoryBlobs(), _BoomQueue())

    handle_intake(runtime, {"task_id": "t-1"})

    task = store.get("tasks", "t-1")
    assert task is not None
    assert task["status"] == "failed"
    assert task["error"] == "Queue rejected the task"


def test_orphaned_running_task_is_failed_at_startup():
    store = MemoryStore()
    store.insert("tasks", {"id": "t-run", "status": "running"})
    store.insert("tasks", {"id": "t-done", "status": "done"})
    runtime = Runtime(store, MemoryBlobs(), _BoomQueue())

    assert fail_orphans(runtime) == 1

    orphan = store.get("tasks", "t-run")
    done = store.get("tasks", "t-done")
    assert orphan is not None and done is not None
    assert orphan["status"] == "failed"
    assert "restarted" in orphan["error"]
    assert done["status"] == "done"
