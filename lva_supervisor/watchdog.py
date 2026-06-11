"""LVA Supervisor watchdog."""

import asyncio
import logging
from typing import TYPE_CHECKING
from .containers.base import ContainerBase
from .const import (
    CONTAINER_START_ORDER,
    WATCHDOG_INTERVAL,
    WATCHDOG_RESTART_BACKOFF,
    CONTAINER_LVA,
)
from .exceptions import DockerError, DockerContainerNotFound

if TYPE_CHECKING:
    from .coresys import CoreSys

_LOGGER = logging.getLogger(__name__)

WATCHDOG_MISS_COUNT = 2


class Watchdog:
    """Monitors managed containers and restarts them if they die."""

    def __init__(self, coresys: "CoreSys") -> None:
        self.coresys = coresys
        self._task: asyncio.Task[None] | None = None
        self._miss_counts: dict[str, int] = {name: 0 for name in CONTAINER_START_ORDER}
        self._restart_counts: dict[str, int] = {
            name: 0 for name in CONTAINER_START_ORDER
        }

    async def start(self) -> None:
        """Start the watchdog loop as a background task."""
        _LOGGER.info(
            "Watchdog starting, interval=%ds miss_threshold=%d",
            WATCHDOG_INTERVAL,
            WATCHDOG_MISS_COUNT,
        )
        self._task = asyncio.create_task(self._loop(), name="watchdog")

    async def stop(self) -> None:
        """Cancel the watchdog loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            _LOGGER.info("Watchdog stopped")

    async def _loop(self) -> None:
        """Run forever, checking containers every WATCHDOG_INTERVAL seconds."""
        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                await self._check_all()
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.error("Watchdog loop error: %s", err)

    async def _check_all(self) -> None:
        """Check all managed containers in start order."""
        if not await self.coresys.docker.is_healthy():
            _LOGGER.warning("Watchdog: Docker daemon unreachable, skipping check")
            return

        for name in CONTAINER_START_ORDER:
            try:
                await self._check_container(name)
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.error("Watchdog: error checking [%s]: %s", name, err)

    async def _check_container(self, name: str) -> None:
        """Check a single container, restart only after WATCHDOG_MISS_COUNT misses."""
        container: ContainerBase = self.coresys.containers[name]

        # Special handling for LVA container
        if name == CONTAINER_LVA:
            if await container.is_failed() and not container.is_stopped_intentionally():
                # Launch as non-blocking task so it doesn't freeze the loop
                asyncio.create_task(
                    self._restart_with_backoff(CONTAINER_LVA, container),
                    name=f"watchdog_restart_{CONTAINER_LVA}",
                )
            elif (
                await container.is_running()
                and not container.is_stopped_intentionally()
            ):
                self._restart_counts[CONTAINER_LVA] = 0
            return

        # Standard container logic
        if await container.is_running():
            self._miss_counts[name] = 0
            self._restart_counts[name] = 0
            return

        if container.is_updating() or container.is_stopped_intentionally():
            _LOGGER.info(
                "Watchdog: [%s] is currently updating or stopped intentionally, skipping check",
                name,
            )
            return

        miss = self._miss_counts[name] + 1
        self._miss_counts[name] = miss
        _LOGGER.warning(
            "Watchdog: [%s] not running (miss %d/%d)", name, miss, WATCHDOG_MISS_COUNT
        )

        if miss >= WATCHDOG_MISS_COUNT:
            self._miss_counts[name] = 0
            # Launch as non-blocking task so it doesn't freeze the loop
            asyncio.create_task(
                self._restart_with_backoff(name, container),
                name=f"watchdog_restart_{name}",
            )

    async def _restart_with_backoff(self, name: str, container: ContainerBase) -> None:
        """Restart a container with exponential backoff."""
        count = self._restart_counts[name]
        delay = WATCHDOG_RESTART_BACKOFF[min(count, len(WATCHDOG_RESTART_BACKOFF) - 1)]

        _LOGGER.info(
            "Watchdog: restarting [%s] (attempt %d, backoff %ds)",
            name,
            count + 1,
            delay,
        )

        if delay > 0:
            await asyncio.sleep(delay)

        try:
            await container.start()
            _LOGGER.info("Watchdog: [%s] restarted successfully", name)
            # Reset restart count only on an explicit successful boot sequence
            self._restart_counts[name] = 0
        except (DockerError, DockerContainerNotFound) as err:
            _LOGGER.error("Watchdog: [%s] restart failed: %s", name, err)
            self._restart_counts[name] = count + 1
