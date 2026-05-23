"""LVA container base class.

Modeled after home-assistant/supervisor PluginBase.
Owns the high-level lifecycle: load, install, update, start, stop, restart.

Each container (audio, lva, portal) extends this and the DockerInterface
subclass provides the actual Docker run() config.

Startup flow:
    load()
      → attach() to existing container
      → if not found: install() → run()
      → if image mismatch: remove() → install() → run()
      → start() if not running
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..docker.interface import DockerInterface
from ..exceptions import DockerError, DockerPullError

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class ContainerBase(ABC):
    """Base class for all LVA managed containers.

    Subclasses must provide a `instance` property pointing to
    their DockerInterface subclass.
    """

    def __init__(self, coresys: "CoreSys") -> None:
        self.coresys = coresys

    # =========================================================================
    # Properties — subclasses provide these
    # =========================================================================

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

    # =========================================================================
    # Core lifecycle
    # =========================================================================

    async def run(self) -> None:
        """Create and start the container with full config.

        Subclass provides volumes, env vars, network, devices etc.
        Called by load() when container doesn't exist yet.
        """

    async def load(self) -> None:
        """Load container on supervisor startup.

        Mirrors HA's PluginBase.load():
          1. Try to attach to existing container
          2. If not found → install + run
          3. If image mismatch → remove + install + run
          4. If not running → start
        """
        _LOGGER.info("[%s] Loading container", self.name)

        try:
            attached = await self.instance.attach()
        except DockerError:
            # Image mismatch or corrupt container — reinstall
            _LOGGER.warning("[%s] Reinstalling due to image mismatch", self.name)
            await self.instance.remove()
            await self.install()
            await self.instance.run()
            return

        if not attached:
            # First boot  container doesn't exist yet
            _LOGGER.info("[%s] First boot — installing container", self.name)
            await self.install()
            await self.instance.run()
            return

        # Container exists start it if not already running
        if not await self.instance.is_running():
            _LOGGER.info("[%s] Container exists but not running — starting", self.name)
            await self.start()

    async def install(self) -> None:
        """Pull the container image."""
        _LOGGER.info("[%s] Pulling image %s", self.name, self.image)
        try:
            await self.instance.pull()
        except DockerPullError as err:
            _LOGGER.error("[%s] Image pull failed: %s", self.name, err)
            raise

    async def update(self) -> None:
        """Pull latest image and recreate the container."""
        _LOGGER.info("[%s] Updating", self.name)
        await self.stop()
        await self.instance.remove()
        await self.install()
        await self.instance.run()
        _LOGGER.info("[%s] Update complete", self.name)

    # =========================================================================
    # Lifecycle delegates to DockerInterface
    # =========================================================================

    async def start(self) -> None:
        """Start the container."""
        try:
            await self.instance.start()
        except DockerError as err:
            _LOGGER.error("[%s] Start failed: %s", self.name, err)
            raise

    async def stop(self) -> None:
        """Stop the container."""
        try:
            await self.instance.stop()
        except DockerError as err:
            _LOGGER.error("[%s] Stop failed: %s", self.name, err)
            raise

    async def restart(self) -> None:
        """Restart the container."""
        try:
            await self.instance.restart()
        except DockerError as err:
            _LOGGER.error("[%s] Restart failed: %s", self.name, err)
            raise

    # =========================================================================
    # State  delegates to DockerInterface
    # =========================================================================

    async def exists(self) -> bool:
        """Return True if container exists at all."""
        return await self.instance.exists()

    async def is_running(self) -> bool:
        """Return True if container is running."""
        return await self.instance.is_running()

    async def is_failed(self) -> bool:
        """Return True if container is in a failed state."""
        return await self.instance.is_failed()

    async def stats(self) -> dict[str, float | int]:
        """Return statistics for the container."""
        return await self.instance.stats()

    async def logs(self, tail: int = 100) -> list[str]:
        """Return logs for the container."""
        return await self.instance.logs(tail=tail)
