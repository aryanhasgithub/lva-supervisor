"""LVA CLI container.

Long-running container that hosts the interactive lva-cli shell.
lva-cli@.service (the getty-attached unit) docker-execs into this
container via `docker container exec -ti lva-cli /usr/bin/cli.sh`,
rather than starting a fresh container per login — so this container
must stay alive in the background like audio/lva/portal, not run
once and exit.
"""

import logging
from typing import TYPE_CHECKING

from aiodocker.exceptions import DockerError as AioDockerError

from ..const import CONTAINER_CLI, IMAGE_CLI
from ..docker.interface import DockerInterface
from ..exceptions import DockerError
from .base import ContainerBase

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class DockerCli(DockerInterface):
    """Docker interface for lva-cli container."""

    @property
    def name(self) -> str:
        return CONTAINER_CLI

    @property
    def image(self) -> str:
        return IMAGE_CLI

    async def run(self) -> None:
        """Create and start the lva-cli container."""
        _LOGGER.info("[%s] Creating container", self.name)

        config: dict[str, object] = {
            "Image": self.image,
            "HostConfig": {
                # Host network mode matches the other lva-* containers,
                # and lets the CLI reach the supervisor socket and any
                # network-diagnostic tooling without extra port mapping.
                "NetworkMode": "host",
                "Binds": [
                    "/run/lva/supervisor.sock:/run/lva/supervisor.sock:rw",
                ],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }

        try:
            await self.coresys.docker.containers.run(config, name=self.name)  # type: ignore[reportUnknownMemberType]
            _LOGGER.info("[%s] Container started", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Failed to run: {err}") from err


class Cli(ContainerBase):
    """LVA CLI plugin."""

    def __init__(self, coresys: "CoreSys") -> None:
        super().__init__(coresys)
        self._instance = DockerCli(coresys)

    @property
    def instance(self) -> DockerInterface:
        return self._instance