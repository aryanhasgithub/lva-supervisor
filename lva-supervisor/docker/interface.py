"""LVA Supervisor Docker interface base class."""
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from aiodocker.exceptions import DockerError as AioDockerError

from ..exceptions import (
    DockerContainerNotFound,
    DockerError,
    DockerPullError,
)

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


class DockerInterface(ABC):
    """Base class for all LVA managed containers."""

    def __init__(self, coresys: "CoreSys") -> None:
        self.coresys = coresys

    @property
    @abstractmethod
    def name(self) -> str:
        """Container name."""

    @property
    @abstractmethod
    def image(self) -> str:
        """Full image reference."""

    @abstractmethod
    async def run(self) -> None:
        """Create and start the container with full config.

        Subclass provides volumes, env vars, network, devices etc.
        Called by containers/base.py when container doesn't exist yet.
        """

    # =========================================================================
    # Attach  connect to existing container on startup
    # =========================================================================

    async def attach(self) -> bool:
        """Try to attach to an existing container.

        Returns True if container exists and image matches.
        Returns False if container doesn't exist.
        Raises DockerError if container exists but image doesn't match
        (triggers reinstall in containers/base.py).
        """
        container = await self._get_container()
        if container is None:
            _LOGGER.debug("[%s] No existing container found", self.name)
            return False

        try:
            info = await container.show() # type: ignore[reportUnknownMemberType]
            running_image = info["Config"]["Image"]
            if running_image != self.image:
                _LOGGER.warning(
                    "[%s] Image mismatch: running=%s expected=%s — will reinstall",
                    self.name, running_image, self.image,
                )
                raise DockerError(
                    f"[{self.name}] Image mismatch: {running_image} != {self.image}"
                )
            _LOGGER.info("[%s] Attached to existing container", self.name)
            return True
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Attach failed: {err}") from err

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start the container."""
        container = await self._get_container()
        if container is None:
            raise DockerContainerNotFound(
                f"[{self.name}] Container not found — call load() first"
            )
        try:
            await container.start() # type: ignore[reportUnknownMemberType]
            _LOGGER.info("[%s] Container started", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Start failed: {err}") from err

    async def stop(self) -> None:
        """Stop the container."""
        container = await self._get_container()
        if container is None:
            _LOGGER.debug("[%s] Stop called but container not found", self.name)
            return
        try:
            await container.stop()
            _LOGGER.info("[%s] Container stopped", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Stop failed: {err}") from err

    async def restart(self) -> None:
        """Restart the container."""
        container = await self._get_container()
        if container is None:
            raise DockerContainerNotFound(
                f"[{self.name}] Container not found call load() first"
            )
        try:
            await container.restart()
            _LOGGER.info("[%s] Container restarted", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Restart failed: {err}") from err

    async def remove(self) -> None:
        """Stop and remove the container."""
        container = await self._get_container()
        if container is None:
            return
        try:
            await container.stop()
            await container.delete(force=True)
            _LOGGER.info("[%s] Container removed", self.name)
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Remove failed: {err}") from err

    async def pull(self) -> None:
        """Pull the latest image."""
        try:
            await self.coresys.docker.images.pull(self.image, tag="latest")
            _LOGGER.info("[%s] Image pulled successfully", self.name)
        except AioDockerError as err:
            raise DockerPullError(
                f"[{self.name}] Failed to pull {self.image}: {err}"
            ) from err

    # =========================================================================
    # State inspection
    # =========================================================================

    async def exists(self) -> bool:
        """Return True if container exists at all."""
        return await self._get_container() is not None

    async def is_running(self) -> bool:
        """Return True if container is running."""
        container = await self._get_container()
        if container is None:
            return False
        try:
            info = await container.show() # type: ignore[reportUnknownMemberType]
            return info["State"]["Running"]
        except AioDockerError:
            return False

    async def is_failed(self) -> bool:
        """Return True if container exited with non-zero exit code."""
        container = await self._get_container()
        if container is None:
            return False
        try:
            info = await container.show() # type: ignore[reportUnknownMemberType]
            state = info["State"]
            return not state["Running"] and state.get("ExitCode", 0) != 0
        except AioDockerError:
            return False

    async def stats(self) -> dict[str, float | int]:
        """Return cpu and memory stats."""
        container = await self._get_container()
        if container is None:
            raise DockerContainerNotFound(f"[{self.name}] Container not found")
        try:
            raw  = await container.stats(stream=False)
            data = raw[0] if isinstance(raw, list) else raw # type: ignore[reportUnknownVariableType]

            cpu_delta = (
                data["cpu_stats"]["cpu_usage"]["total_usage"]
                - data["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                data["cpu_stats"]["system_cpu_usage"]
                - data["precpu_stats"]["system_cpu_usage"]
            )
            num_cpus    = data["cpu_stats"].get("online_cpus", 1)
            cpu_percent = (
                (cpu_delta / system_delta) * num_cpus * 100.0
                if system_delta > 0 else 0.0
            )
            mem_usage   = data["memory_stats"]["usage"]
            mem_limit   = data["memory_stats"]["limit"]
            mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0

            return {
                "cpu_percent":    round(cpu_percent, 2),
                "memory_usage":   mem_usage,
                "memory_limit":   mem_limit,
                "memory_percent": round(mem_percent, 2),
            }
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Stats failed: {err}") from err

    async def logs(self, tail: int = 100) -> list[str]:
        """Return recent log lines."""
        container = await self._get_container()
        if container is None:
            raise DockerContainerNotFound(f"[{self.name}] Container not found")
        try:
            return await container.log(stdout=True, stderr=True, tail=tail) # type: ignore[reportUnknownMemberType]
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Logs failed: {err}") from err

    # =========================================================================
    # Internal
    # =========================================================================

    async def _get_container(self):
        """Get container by name, returns None if not found."""
        try:
            return await self.coresys.docker.containers.get(self.name) # type: ignore[reportUnknownMemberType]
        except AioDockerError:
            return None