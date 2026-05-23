"""LVA Supervisor watchdog."""
import asyncio
import logging
from typing import TYPE_CHECKING
from .docker.interface import DockerInterface
from .const import CONTAINER_START_ORDER, WATCHDOG_INTERVAL, WATCHDOG_RESTART_BACKOFF
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
        # Consecutive failed check counter per container
        self._miss_counts: dict[str, int] = {
            name: 0 for name in CONTAINER_START_ORDER
        }
        # Consecutive restart attempt counter per container for backoff
        self._restart_counts: dict[str, int] = {
            name: 0 for name in CONTAINER_START_ORDER
        }

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the watchdog loop as a background task."""
        _LOGGER.info("Watchdog starting, interval=%ds miss_threshold=%d",
                     WATCHDOG_INTERVAL, WATCHDOG_MISS_COUNT)
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

    # =========================================================================
    # Main loop
    # =========================================================================

    async def _loop(self) -> None:
        """Run forever, checking containers every WATCHDOG_INTERVAL seconds."""
        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                await self._check_all()
            except Exception as err: # pylint: disable=broad-exception-caught
                # Never let the loop die log and keep going
                _LOGGER.error("Watchdog loop error: %s", err)

    async def _check_all(self) -> None:
        """Check all managed containers in start order."""
        # Check Docker daemon first as no point checking containers if daemon is down
        if not await self.coresys.docker.is_healthy():
            _LOGGER.warning("Watchdog: Docker daemon unreachable, skipping check")
            return

        for name in CONTAINER_START_ORDER:
            try:
                await self._check_container(name)
            except Exception as err: # pylint: disable=broad-exception-caught
                _LOGGER.error("Watchdog: error checking [%s]: %s", name, err)

    async def _check_container(self, name: str) -> None:
        """Check a single container, restart only after WATCHDOG_MISS_COUNT misses."""
        container : DockerInterface = self.coresys.containers[name] # type: ignore[reportUnknownMemberType]

        if await container.is_running():
            # Healthy — reset both counters
            if self._miss_counts[name] > 0:
                _LOGGER.info("Watchdog: [%s] recovered", name)
            self._miss_counts[name] = 0
            self._restart_counts[name] = 0
            return

        # Container is not running, increment miss counter
        self._miss_counts[name] += 1
        miss = self._miss_counts[name]

        _LOGGER.warning(
            "Watchdog: [%s] not running (miss %d/%d)",
            name, miss, WATCHDOG_MISS_COUNT
        )

        if miss < WATCHDOG_MISS_COUNT:
            # Not enough misses yet, wait for next check cycle
            return

        # Hit the threshold restart
        self._miss_counts[name] = 0
        await self._restart_with_backoff(name, container)

    async def _restart_with_backoff(self, name: str, container: DockerInterface ) -> None:
        """Restart a container with exponential backoff."""
        count = self._restart_counts[name]
        delay = WATCHDOG_RESTART_BACKOFF[min(count, len(WATCHDOG_RESTART_BACKOFF) - 1)]

        _LOGGER.info(
            "Watchdog: restarting [%s] (attempt %d, backoff %ds)",
            name, count + 1, delay
        )

        await asyncio.sleep(delay)

        try:
            await container.start()
            _LOGGER.info("Watchdog: [%s] restarted successfully", name)
        except (DockerError, DockerContainerNotFound) as err:
            _LOGGER.error("Watchdog: [%s] restart failed: %s", name, err)
        finally:
            # Always increment so backoff keeps stepping up on repeated failures
            self._restart_counts[name] = count + 1