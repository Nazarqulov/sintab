"""Oddiy davriy vazifalar (asyncio). Har vazifa o'z sessiyasini ochadi; xato butun siklni to'xtatmaydi."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    def every(self, seconds: float, name: str, job: Callable[[], Awaitable[None]], *, initial_delay: float = 5) -> None:
        self._tasks.append(asyncio.create_task(self._loop(seconds, name, job, initial_delay), name=name))

    async def _loop(self, seconds: float, name: str, job: Callable[[], Awaitable[None]], initial_delay: float) -> None:
        await asyncio.sleep(initial_delay)
        while True:
            try:
                await job()
            except Exception:
                logger.exception("Davriy vazifa '%s' xato bilan tugadi", name)
            await asyncio.sleep(seconds)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
