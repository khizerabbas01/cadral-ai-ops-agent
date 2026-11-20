"""In-process pub/sub used to stream tokens and trace steps to the dashboard over SSE.
Worker threads publish; async SSE handlers consume via thread-safe queues."""
from __future__ import annotations

import queue
import threading
from collections import defaultdict

_lock = threading.Lock()
_subs: dict[str, list[queue.Queue]] = defaultdict(list)


def subscribe(topic: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=2000)
    with _lock:
        _subs[topic].append(q)
    return q


def unsubscribe(topic: str, q: queue.Queue) -> None:
    with _lock:
        if q in _subs.get(topic, []):
            _subs[topic].remove(q)


def publish(topic: str, event: dict) -> None:
    with _lock:
        targets = list(_subs.get(topic, [])) + (list(_subs.get("*", [])) if topic != "*" else [])
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def conv_topic(conversation_id: int) -> str:
    return f"conv:{conversation_id}"
