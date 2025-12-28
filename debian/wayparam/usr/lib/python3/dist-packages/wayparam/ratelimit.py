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
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        if self.rps <= 0:
            return
        delay = 1.0 / self.rps
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            now = time.monotonic()
            self._next = max(self._next, now) + delay
