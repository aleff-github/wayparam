# SPDX-License-Identifier: GPL-3.0

"""Tests for the global rate limiter, on a fake clock.

Wall-clock assertions would be flaky on a loaded CI runner, so both the clock
and the sleep are replaced and the *scheduled* delays are what gets asserted.
"""

from __future__ import annotations

import asyncio

import pytest

from wayparam import ratelimit
from wayparam.ratelimit import RateLimiter


@pytest.fixture
def fake_clock(monkeypatch):
    now = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(ratelimit.asyncio, "sleep", fake_sleep)
    return slept


def _wait_n(limiter: RateLimiter, n: int) -> None:
    async def go() -> None:
        for _ in range(n):
            await limiter.wait()

    asyncio.run(go())


def test_requests_are_spaced_by_one_over_rps(fake_clock):
    _wait_n(RateLimiter(2.0), 3)
    # The first call is free; each later one waits out the 0.5s gap.
    assert fake_clock == [0.5, 0.5]


def test_a_non_positive_rps_disables_the_limiter(fake_clock):
    _wait_n(RateLimiter(0.0), 5)
    _wait_n(RateLimiter(-1.0), 5)
    assert fake_clock == []


def test_time_already_spent_counts_towards_the_gap(fake_clock, monkeypatch):
    """A request that took longer than the gap must not be delayed again."""
    limiter = RateLimiter(1.0)
    _wait_n(limiter, 1)  # free; the next slot is scheduled for t=1.0
    assert fake_clock == []

    # The caller then spent 1.5s doing work, so the gap has already elapsed.
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: 1.5)
    _wait_n(limiter, 1)
    assert fake_clock == []


def test_concurrent_waiters_are_serialised(fake_clock):
    limiter = RateLimiter(4.0)  # 0.25s apart

    async def go() -> None:
        await asyncio.gather(*(limiter.wait() for _ in range(4)))

    asyncio.run(go())
    assert fake_clock == [0.25, 0.25, 0.25]
