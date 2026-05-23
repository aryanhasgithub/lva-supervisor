"""LVA Portal container.

Runs the React + FastAPI web management portal.
Talks to supervisor via /run/lva/supervisor.sock.
Serves on port 8000.
"""
import logging
from typing import TYPE_CHECKING

from aiodocker.exceptions import DockerError as AioDockerError

from ..const import CONTAINER_PORTAL, DOCKER_NETWORK, IMAGE_PORTAL
from ..docker.interface import DockerInterface
from ..exceptions import DockerError
from .base import ContainerBase

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class DockerPortal(DockerInterface):
    """Docker interface for lva-portal container."""

    @property
    def name(self) -> str:
        return CONTAINER_PORTAL

    @property
    def image(self) -> str:
        return IMAGE_PORTAL

    async def run(self) -> None:
        """Create and start the lva-portal container."""
        _LOGGER.info("[%s] Creating container", self.name)
        config : dict[str, object]= {
            "Image": self.image,
            "ExposedPorts": {
                "8000/tcp": {},
            },
            "HostConfig": {
                "NetworkMode": DOCKER_NETWORK,
                "PortBindings": {
                    "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}],
                },
                "Binds": [
                    "/run/lva:/run/lva:rw",
                    "/etc/lva:/etc/lva:rw",
                ],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }
        try:
            await self.coresys.docker.containers.run(config, name=self.name) # type: ignore[reportUnknownMemberType]
            _LOGGER.info("[%s] Container started", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Failed to run: {err}") from err


class Portal(ContainerBase):
    """LVA Portal plugin."""

    def __init__(self, coresys: "CoreSys") -> None:
        super().__init__(coresys)
        self._instance = DockerPortal(coresys)

    @property
    def instance(self) -> DockerInterface:
        return self._instance