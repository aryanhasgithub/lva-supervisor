"""LVA Supervisor Docker manager.

Owns the aiodocker client lifecycle and the lva Docker network.
All other components access Docker through coresys.docker (this class).
"""
import logging
from typing import TYPE_CHECKING, Mapping, Any

import aiodocker
from aiodocker.exceptions import DockerError as AioDockerError

from ..const import DOCKER_NETWORK, DOCKER_SOCKET
from ..exceptions import DockerConnectionError, DockerError

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class DockerManager:
    """Manages the aiodocker client and the lva internal Docker network.

    Lifecycle:
        await coresys.docker.connect()    ← called by bootstrap
        ...supervisor runs...
        await coresys.docker.disconnect() ← called on shutdown

    All aiodocker sub-APIs are accessed directly via the client:
        coresys.docker.containers
        coresys.docker.images
        coresys.docker.networks
        coresys.docker.volumes
    """

    def __init__(self, coresys: "CoreSys") -> None:
        self.coresys = coresys
        self._client: aiodocker.Docker | None = None

    # =========================================================================
    # Client access
    # =========================================================================

    @property
    def client(self) -> aiodocker.Docker:
        """Return the aiodocker client, raise if not connected."""
        if self._client is None:
            raise DockerConnectionError("Docker client is not connected")
        return self._client

    @property
    def containers(self):
        """Return the docker containers."""
        return self.client.containers

    @property
    def images(self):
        """Return the docker images."""
        return self.client.images

    @property
    def networks(self):
        """Return the docker networks."""
        return self.client.networks

    @property
    def volumes(self):
        """Return the docker volumes."""
        return self.client.volumes

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def connect(self) -> None:
        """Connect to Docker daemon via unix socket and ensure lva network exists."""
        _LOGGER.info("Connecting to Docker daemon at %s", DOCKER_SOCKET)
        try:
            self._client = aiodocker.Docker(url=f"unix://{DOCKER_SOCKET}")
            await self._client.version()
            assert self._client is not None
            _LOGGER.info("Connected to Docker daemon")
        except AioDockerError as err:
            raise DockerConnectionError(
                f"Cannot connect to Docker daemon: {err}"
            ) from err

        await self._ensure_network()

    async def disconnect(self) -> None:
        """Close the aiodocker client cleanly."""
        if self._client:
            await self._client.close()
            self._client = None
            _LOGGER.info("Disconnected from Docker daemon")

    # =========================================================================
    # Network
    # =========================================================================

    async def _ensure_network(self) -> None:
        """Create the lva internal Docker network if it does not exist.

        networks.get() raises DockerError with status=404 when not found.
        Any other status is a real error and should propagate.
        """
        try:
            await self._client.networks.get(DOCKER_NETWORK)  # type: ignore[reportUnknownMemberType]
            _LOGGER.debug("Docker network '%s' already exists", DOCKER_NETWORK)
        except AioDockerError as err:
            if err.status == 404:
                _LOGGER.info("Creating Docker network '%s'", DOCKER_NETWORK)
                await self._create_network()
            else:
                raise DockerError(
                    f"Failed to check Docker network '{DOCKER_NETWORK}': {err}"
                ) from err

    async def _create_network(self) -> None:
        """Create the lva bridge network."""
        try:
            await self._client.networks.create(  # type: ignore[reportUnknownMemberType]
                {
                    "Name": DOCKER_NETWORK,
                    "Driver": "bridge",
                    "Internal": True,
                    "Labels": {
                        "io.lva.network": "true",
                    },
                }
            )
            _LOGGER.info("Docker network '%s' created", DOCKER_NETWORK)
        except AioDockerError as err:
            raise DockerError(
                f"Failed to create Docker network '{DOCKER_NETWORK}': {err}"
            ) from err

    # =========================================================================
    # Daemon health
    # =========================================================================

    async def is_healthy(self) -> bool:
        """Return True if Docker daemon is reachable."""
        if self._client is None:
            return False
        try:
            await self._client.version()
            return True
        except AioDockerError:
            return False

    async def info(self) -> Mapping[str, Any]:
        """Return Docker daemon system info."""
        try:
            return await self.client.system.info()  # type: ignore[reportUnknownMemberType]
        except AioDockerError as err:
            raise DockerError(f"Failed to get Docker info: {err}") from err