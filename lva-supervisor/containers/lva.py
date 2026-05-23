"""LVA container.

Runs the OHF-Voice linux-voice-assistant.
Config matches upstream docker-compose but adapted for LVA-OS:
  - PULSE_SERVER points to lva-audio socket instead of host PulseAudio
  - Uses named Docker volumes for wakeword/config data
  - network_mode: host (matches upstream)
  - user: 1000:1000 with audio group and SYS_NICE cap
"""
import logging
from typing import TYPE_CHECKING

from aiodocker.exceptions import DockerError as AioDockerError

from ..const import CONTAINER_LVA, IMAGE_LVA, LVA_VOLUMES
from ..docker.interface import DockerInterface
from ..exceptions import DockerError
from .base import ContainerBase

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)

class DockerLVA(DockerInterface):
    """Docker interface for lva container."""

    @property
    def name(self) -> str:
        return CONTAINER_LVA

    @property
    def image(self) -> str:
        return IMAGE_LVA

    async def run(self) -> None:
        """Create and start the lva container."""
        _LOGGER.info("[%s] Creating container", self.name)

        # Ensure named volumes exist before creating container
        await self._ensure_volumes()

        config: dict[str, object] = {
            "Image": self.image,
            "Env": [
                # PulseAudio — point to lva-audio socket not host
                "XDG_RUNTIME_DIR=/run/user/0",
                "PULSE_SERVER=/run/lva/audio/pulse/native",
                "PULSE_COOKIE=/run/lva/audio/pulse/cookie",
            ],
            "Healthcheck": {
                "Test": ["CMD", "pgrep", "-f", "linux_voice_assistant"],
                "Interval": 30_000_000_000,   # 30s in nanoseconds
                "Timeout":   5_000_000_000,   # 5s
                "Retries":   3,
                "StartPeriod": 90_000_000_000, # 90s
            },
            "HostConfig": {
                # host network mode matches upstream
                "NetworkMode": "host",
                "CapAdd": ["SYS_NICE"],
                "Binds": [
                    # PulseAudio socket from lva-audio
                    "/run/lva/audio:/run/lva/audio:ro",
                    # Time and timezone
                    "/etc/localtime:/etc/localtime:ro",
                    "/etc/timezone:/etc/timezone:ro",
                    # master.env config
                    "/etc/lva:/etc/lva:ro",
                    # Named volumes
                    "lva_wakeword_data:/app/local",
                    "lva_wakeword_custom:/app/wakewords/custom",
                    "lva_configuration:/app/configuration",
                    "lva_sounds_custom:/app/sounds/custom",
                ],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
        }

        try:
            await self.coresys.docker.containers.run(config, name=self.name) # type: ignore[reportUnknownMemberType]
            _LOGGER.info("[%s] Container started", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Failed to run: {err}") from err

    async def _ensure_volumes(self) -> None:
        """Create named Docker volumes if they don't exist."""
        for vol_name in LVA_VOLUMES:
            try:
                await self.coresys.docker.client.volumes.get(vol_name) # type: ignore[reportUnknownMemberType]
            except AioDockerError:
                _LOGGER.info("[%s] Creating volume %s", self.name, vol_name)
                await self.coresys.docker.client.volumes.create({ # type: ignore[reportUnknownMemberType]
                    "Name": vol_name,
                    "Labels": {"io.lva.volume": "true"},
                })


class LVA(ContainerBase):
    """LVA plugin."""

    def __init__(self, coresys: "CoreSys") -> None:
        super().__init__(coresys)
        self._instance = DockerLVA(coresys)

    @property
    def instance(self) -> DockerInterface:
        return self._instance