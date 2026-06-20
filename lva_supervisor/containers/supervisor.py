"""LVA Supervisor self-container management class."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

import asyncio
import logging
import aiohttp
from .base import ContainerBase
from ..docker.interface import DockerInterface
from ..exceptions import DockerPullError
from ..const import CONTAINER_SUPERVISOR ,IMAGE_SUPERVISOR

# Pull straight from your shared helpers (using your exact folder spelling)
from ..utils.updates import fetch_manifest, get_local_version, is_update_available
if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)

# Polling frequency matching HA Supervisor standard (2 hours)
POLL_INTERVAL_SECONDS = 2 * 60 * 60


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

    async def start_background_updater(self) -> None:
        """Start the completely isolated background self-update worker loop."""
        _LOGGER.info("Supervisor background self-updater loop registered (Polling: 2h)")

        while self.coresys.stop_event is None or not self.coresys.stop_event.is_set():
            try:
                # Sleep first to let the rest of the application bootstrap cleanly
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                _LOGGER.debug("Polling remote stable.json version manifest...")

                async with aiohttp.ClientSession() as session:
                    manifest = await fetch_manifest(session)

                if not manifest:
                    continue

                # Read our local version from our image labels and compare it
                local_ver = await get_local_version(self.coresys, self.image)
                remote_ver = manifest.get("lva-supervisor")

                if is_update_available(local_ver, remote_ver):
                    _LOGGER.info(
                        "New Supervisor version detected! Local: %s -> Remote: %s",
                        local_ver,
                        remote_ver,
                    )
                    await self.update()
                    break  # Break out of the loop since we are restarting

            except asyncio.CancelledError:
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.exception(
                    "Unexpected error encountered inside background updater: %s", err
                )

    async def update(self, progress: Any = None) -> None:
        """Custom Supervisor update method override.

        Bypasses standard base class container stops/removals. Performs a silent
        background aiodocker layer download and flags exit code 100 to the host.
        """
        self._updating = True
        _LOGGER.info(
            "[%s] Initiating background self-update execution sequence...", self.name
        )

        try:
            try:
                # Pull the new image payload layer while completely active
                await self.instance.pull()
            except DockerPullError as err:
                _LOGGER.error(
                    "[%s] Self-update background download failed: %s", self.name, err
                )
                raise

            _LOGGER.info(
                "[%s] Self-update image layers downloaded successfully. Restarting...",
                self.name,
            )

            # Fire the soft exit block to teardown runner, sockets, and D-Bus interfaces
            await self.coresys.exit_system(code=100)
        finally:
            self._updating = False
