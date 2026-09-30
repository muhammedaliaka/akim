"""Web paneline canlı olay yayını (Server-Sent Events) için basit yayın/abone yapısı."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_queue: int = 200):
        self._subs: set[asyncio.Queue] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: str, data: Any) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                # yavaş istemci: en eski olayı at
                try:
                    q.get_nowait()
                    q.put_nowait((event, data))
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
