"""LVA Supervisor self-container management class."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

import logging
from .base import ContainerBase, ProgressCallback
from ..docker.interface import DockerInterface
from ..exceptions import DockerPullError
from ..const import CONTAINER_SUPERVISOR ,IMAGE_SUPERVISOR

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class SupervisorDockerWrapper(DockerInterface):
    """Minimal runtime wrapper for the supervisor docker properties."""

    @property
    def name(self) -> str:
        return CONTAINER_SUPERVISOR

    @property
    def image(self) -> str:
        return IMAGE_SUPERVISOR

    async def run(self) -> None:
        """Overridden configuration hook — host script handles supervisor container creation."""


class Supervisor(ContainerBase):
    """Manages the Supervisor's own container instance lifecycle."""

    def __init__(self, coresys: "CoreSys") -> None:
        super().__init__(coresys)
        # Instantiate the wrapper directly here instead of an external module file
        self._instance = SupervisorDockerWrapper(coresys)

    @property
    def instance(self) -> DockerInterface:
        """Return the supervisor docker configuration interface."""
        return self._instance

    async def update(self, progress: ProgressCallback = None) -> None:
        """Custom Supervisor update method override."""
        
        async def _report(status: str, **extra) -> None:
            _LOGGER.info("[%s] %s", self.name, status)
            if progress is not None:
                await progress({"status": status, **extra})

        self._updating = True
        try:
            await _report("Initiating self-update...")

            try:
                async def _pull_progress(event: dict) -> Any:
                    if progress is not None:
                        await progress(event)

                await self.instance.pull(progress=_pull_progress)
            except DockerPullError as err:
                await _report(f"Self-update download failed: {err}")
                raise

            await _report("Image layers downloaded successfully. Restarting...")

            # Fire the soft exit block to teardown runner, sockets, and D-Bus interfaces
            await self.coresys.exit_system(code=100)
        finally:
            self._updating = False
