"""Test helpers shared across modules."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

POLL_LIMIT = 2.0


async def eventually(predicate: Callable[[], object]) -> None:
    """Poll until the predicate holds or the limit passes."""
    async with asyncio.timeout(POLL_LIMIT):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.005)
