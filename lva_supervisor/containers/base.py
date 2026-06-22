"""LVA container base class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
import asyncio

from ..docker.interface import DockerInterface
from ..exceptions import DockerError, DockerPullError
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)

# Type alias for an optional async progress reporter.
# Callers (e.g. SSE routes, the first-boot marker-file writer) pass an async
# callable that receives a dict, e.g. {"pull_percent": 42, "status": "..."}
# or {"status": "Stopping..."} for non-pull steps.
ProgressCallback = Callable[[dict], Awaitable[None]] | None


class ContainerBase(ABC):
    """Base class for all LVA managed containers.

    Subclasses must provide an `instance` property pointing to their
    DockerInterface subclass.
    """

    def __init__(self, coresys: "CoreSys") -> None:
        self.coresys = coresys
        self._updating: bool = False
        self._stopped_intentionally: bool = False

    # -------------------------------------------------------------------------
    # Properties — subclasses provide these
    # -------------------------------------------------------------------------

    @property
    @abstractmethod
    def instance(self) -> DockerInterface:
        """DockerInterface subclass for this container."""

    @property
    def name(self) -> str:
        """Container name — delegates to docker instance."""
        return self.instance.name

    @property
    def image(self) -> str:
        """Image reference — delegates to docker instance."""
        return self.instance.image

    def is_stopped_intentionally(self) -> bool:
        """Return True if container was stopped by an explicit stop() call."""
        return self._stopped_intentionally

    # -------------------------------------------------------------------------
    # Core lifecycle
    # -------------------------------------------------------------------------

    async def load(self) -> None:
        """Load container on supervisor startup.

        Mirrors HA's PluginBase.load():
          1. Try to attach to existing container.
          2. If not found → pull + run.
          3. If image mismatch → remove + pull + run.
          4. If not running → start.

        On the first-boot path, pull progress is written to a per-container
        file under FIRSTBOOT_PROGRESS_DIR so the supervisor's bare HTML page
        can poll it before lva-portal exists to show this itself.
        """
        _LOGGER.info("[%s] Loading container", self.name)

        async def _write_progress(event: dict) -> None:
            from ..const import FIRSTBOOT_PROGRESS_FILE

            pct = event.get("pull_percent", 0)
            FIRSTBOOT_PROGRESS_FILE.write_text(f"{self.name}-{pct}")

        try:
            attached = await self.instance.attach()
        except DockerError:
            _LOGGER.warning("[%s] Reinstalling due to image mismatch", self.name)
            await self.instance.remove()
            await self.instance.pull(progress=_write_progress)
            await self.instance.run()
            return

        if not attached:
            _LOGGER.info("[%s] First boot — pulling image", self.name)
            await self.instance.pull(progress=_write_progress)
            await self.instance.run()
            return

        if not await self.instance.is_running():
            _LOGGER.info("[%s] Container exists but not running — starting", self.name)
            await self.start()

    async def update(self, progress: ProgressCallback = None) -> None:
        """Pull latest image then safely replace the running container.

        Order is intentional:
          1. Pull new image first — if the registry is unreachable the running
             container is never touched.
          2. Stop the container.
          3. Remove the old container (image stays on disk).
          4. Create and start the new container from the freshly pulled image.

        Args:
            progress: optional async callable receiving a dict, e.g.
                      {"pull_percent": 42, "status": "Downloading"} during
                      the pull, or {"status": "Stopping..."} for other
                      steps. Used by SSE update routes to stream progress.
        """

        async def _report(status: str, **extra) -> None:
            _LOGGER.info("[%s] %s", self.name, status)
            if progress is not None:
                await progress({"status": status, **extra})

        self._updating = True
        try:
            await _report(f"Pulling new image for {self.name}...")
            try:
                await self.instance.pull(progress=progress)
            except DockerPullError as err:
                await _report(f"Pull failed: {err}")
                raise

            await _report("Pull complete.")

            await _report(f"Stopping {self.name}...")
            try:
                await self.instance.stop()
            except DockerError as err:
                _LOGGER.warning(
                    "[%s] Stop failed (may already be stopped): %s", self.name, err
                )
                await _report("Container was already stopped.")

            await _report(f"Removing old container for {self.name}...")
            await self.instance.remove()
            await _report("Old container removed.")

            await _report(f"Starting {self.name} with new image...")
            await self.instance.run()
            await _report(f"{self.name} updated and started successfully.")
        finally:
            self._updating = False

    def is_updating(self) -> bool:
        """Return True if an update is currently in progress."""
        return self._updating

    async def state(self) -> str:
        """Return container state."""
        return await self.instance.state()

    # -------------------------------------------------------------------------
    # Lifecycle delegates
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the container."""
        self._stopped_intentionally = False
        try:
            await self.instance.start()
        except DockerError as err:
            _LOGGER.error("[%s] Start failed: %s", self.name, err)
            raise

    async def stop(self) -> None:
        """Stop the container."""
        self._stopped_intentionally = True
        try:
            await self.instance.stop()
        except DockerError as err:
            _LOGGER.error("[%s] Stop failed: %s", self.name, err)
            raise

    async def restart(self) -> None:
        """Restart the container."""
        self._stopped_intentionally = False
        try:
            await self.instance.restart()
        except DockerError as err:
            _LOGGER.error("[%s] Restart failed: %s", self.name, err)
            raise

    # -------------------------------------------------------------------------
    # State delegates
    # -------------------------------------------------------------------------

    async def exists(self) -> bool:
        """Return True if the container exists."""
        return await self.instance.exists()

    async def is_running(self) -> bool:
        """Return True if the container is running."""
        return await self.instance.is_running()

    async def is_failed(self) -> bool:
        """Return True if the container has failed."""
        return await self.instance.is_failed()

    async def stats(self) -> dict[str, Any]:
        """Return cpu and memory stats."""
        return await self.instance.stats()

    async def logs(self, tail: int = 100) -> list[str]:
        """Get recent logs from the container."""
        return await self.instance.logs(tail=tail)

    async def stream_logs(self, tail: int = 100) -> AsyncGenerator[str, None]:
        """Async generator — streams logs from the container."""
        async for line in self.instance.stream_logs(tail=tail):
            yield line

    # Helpers
    async def wait_until_running(
        self, timeout: int = 30, interval: float = 0.5
    ) -> bool:
        """Poll until container is running or timeout expires.

        Returns True if running, False if timed out.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.instance.is_running():
                return True
            await asyncio.sleep(interval)
        return False