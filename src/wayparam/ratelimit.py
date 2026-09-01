# SPDX-License-Identifier: GPL-3.0

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """
    Simple global rate limiter (requests per second) that works cross-platform.
    If rps <= 0, it is disabled.
    """

    def __init__(self, rps: float):
        self.rps = float(rps)
        self._lock: asyncio.Lock | None = None
        self._next = 0.0

    async def wait(self) -> None:
        if self.rps <= 0:
            return
        if self._lock is None:
            # Created on first use, not in __init__: on Python 3.9 asyncio.Lock()
            # binds to the current event loop at construction, which makes the
            # constructor unusable from outside a running loop. There is no await
            # between the check and the assignment, so this cannot race.
            self._lock = asyncio.Lock()
        lock = self._lock
        delay = 1.0 / self.rps
        async with lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            now = time.monotonic()
            self._next = max(self._next, now) + delay
