"""LVA Audio container.

Owns /dev/snd, runs PipeWire/PulseAudio.
Exposes PulseAudio socket at /run/lva/audio for lva container.
Also exposes a small HTTP agent on /run/lva/audio/agent.sock
for device listing queries from the supervisor API.
"""
import logging
from typing import TYPE_CHECKING

from aiodocker.exceptions import DockerError as AioDockerError

from ..const import CONTAINER_AUDIO, DOCKER_NETWORK, IMAGE_AUDIO
from ..docker.interface import DockerInterface
from ..exceptions import DockerError
from .base import ContainerBase

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class DockerAudio(DockerInterface):
    """Docker interface for lva-audio container."""

    @property
    def name(self) -> str:
        return CONTAINER_AUDIO

    @property
    def image(self) -> str:
        return IMAGE_AUDIO

    async def run(self) -> None:
        """Create and start the lva-audio container."""
        _LOGGER.info("[%s] Creating container", self.name)
        config : dict[str, object] = {
            "Image": self.image,
            "Env": [
                "XDG_RUNTIME_DIR=/run/user/0",
                "PULSE_RUNTIME_PATH=/run/lva/audio/pulse",
            ],
            "HostConfig": {
                "NetworkMode": DOCKER_NETWORK,
                "Devices": [
                    {
                        "PathOnHost":      "/dev/snd",
                        "PathInContainer": "/dev/snd",
                        "CgroupPermissions": "mrw",
                    }
                ],
                "Binds": [
                    "/run/lva/audio:/run/lva/audio:rw",  # pulse socket at /run/lva/audio/pulse/native
                    "/var/lib/lva/audio:/var/lib/lva/audio:rw",
                ],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }
        try:
            await self.coresys.docker.containers.run(config, name=self.name) # type: ignore[reportUnknownMemberType]
            _LOGGER.info("[%s] Container started", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Failed to run: {err}") from err


class Audio(ContainerBase):
    """LVA Audio plugin."""

    def __init__(self, coresys: "CoreSys") -> None:
        super().__init__(coresys)
        self._instance = DockerAudio(coresys)

    @property
    def instance(self) -> DockerInterface:
        return self._instance