"""LVA Supervisor Docker interface and container base."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Coroutine
from contextlib import suppress
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Callable

from aiodocker.exceptions import DockerError as AioDockerError

from ..exceptions import (
    DockerContainerNotFound,
    DockerError,
    DockerPullError,
)

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)

# Async callable that receives a human-readable status string.
# Used by SSE update routes to stream progress to the client.
ProgressCallback = Callable[[str], Coroutine[Any, Any, None]] | None


# =============================================================================
# DockerInterface — low-level aiodocker wrapper
# =============================================================================


class DockerInterface(ABC):
    """Base class for all LVA managed containers."""

    def __init__(self, coresys: CoreSys) -> None:
        self.coresys = coresys

    # -------------------------------------------------------------------------
    # Abstract — subclasses must implement
    # -------------------------------------------------------------------------

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

        Subclass provides volumes, env vars, network, devices, etc.
        Called by ContainerBase when the container does not exist yet.
        """

    # -------------------------------------------------------------------------
    # Attach — connect to an existing container on supervisor startup
    # -------------------------------------------------------------------------

    async def attach(self) -> bool:
        """Try to attach to an existing container.

        Returns True  — container exists and image matches.
        Returns False — container does not exist.
        Raises DockerError — container exists but image does not match
                             (triggers reinstall in ContainerBase.load).
        """
        try:
            container = await self.coresys.docker.containers.get(self.name)
            info = await container.show()
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                _LOGGER.debug("[%s] No existing container found", self.name)
                return False
            raise DockerError(
                f"[{self.name}] Could not inspect container for attach: {err!s}",
                _LOGGER.error,
            ) from err

        running_image = info["Config"]["Image"]
        if running_image != self.image:
            _LOGGER.warning(
                "[%s] Image mismatch: running=%s expected=%s — will reinstall",
                self.name,
                running_image,
                self.image,
            )
            raise DockerError(
                f"[{self.name}] Image mismatch: {running_image} != {self.image}"
            )

        _LOGGER.info("[%s] Attached to existing container", self.name)
        return True

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            container_metadata = await container.show()
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                _LOGGER.debug("[%s] Start called but container not found", self.name)
                return
            raise DockerError(
                f"Could not get container {self.name} for starting: {err!s}",
                _LOGGER.error,
            ) from err

        if container_metadata["State"]["Status"] != "running":
            _LOGGER.info("Starting %s", self.name)
            with suppress(AioDockerError):
                await container.start()

    async def stop(self) -> None:
        """Stop the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            container_metadata = await container.show()
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                _LOGGER.debug("[%s] Stop called but container not found", self.name)
                return
            raise DockerError(
                f"Could not get container {self.name} for stopping: {err!s}",
                _LOGGER.error,
            ) from err

        if container_metadata["State"]["Status"] == "running":
            _LOGGER.info("Stopping %s application", self.name)
            with suppress(AioDockerError):
                await container.stop()

    async def restart(self) -> None:
        """Restart the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            container_metadata = await container.show()
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                _LOGGER.debug("[%s] Restart called but container not found", self.name)
                return
            raise DockerError(
                f"Could not get container {self.name} for restarting: {err!s}",
                _LOGGER.error,
            ) from err

        if container_metadata["State"]["Status"] == "running":
            _LOGGER.info("Restarting %s container", self.name)
            with suppress(AioDockerError):
                await container.restart()

    async def remove(self) -> None:
        """Stop and remove the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                return
            raise DockerError(
                f"Could not get container {self.name} for removal: {err!s}",
                _LOGGER.error,
            ) from err

        _LOGGER.info("Removing %s container", self.name)
        with suppress(AioDockerError):
            await container.delete(force=True, v=True)

    async def pull(self) -> None:
        """Pull the image."""
        try:
            await self.coresys.docker.images.pull(self.image, stream=False)
            _LOGGER.info("[%s] Image pulled successfully", self.name)
        except AioDockerError as err:
            raise DockerPullError(
                f"[{self.name}] Failed to pull {self.image}: {err}"
            ) from err

    # -------------------------------------------------------------------------
    # State inspection
    # -------------------------------------------------------------------------

    async def exists(self) -> bool:
        """Return True if the container exists."""
        try:
            await self.coresys.docker.containers.get(self.name)
            return True
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                return False
            raise DockerError(
                f"Could not check existence of {self.name}: {err!s}",
                _LOGGER.error,
            ) from err

    async def is_running(self) -> bool:
        """Return True if the container is running."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            info = await container.show()
            return bool(info["State"]["Running"])
        except AioDockerError:
            return False

    async def is_failed(self) -> bool:
        """Return True if the container exited with a non-zero exit code."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            info = await container.show()
            state = info["State"]
            return not state["Running"] and state.get("ExitCode", 0) != 0
        except AioDockerError:
            return False

    async def stats(self) -> dict[str, Any]:
        """Return raw stats dict from the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            result = await container.stats(stream=False)
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                raise DockerContainerNotFound(
                    f"[{self.name}] Container not found for stats"
                ) from err
            raise DockerError(
                f"[{self.name}] Could not read stats: {err!s}", _LOGGER.error
            ) from err

        if not result:
            raise DockerError(f"[{self.name}] Empty stats response", _LOGGER.error)
        return result[-1]

    async def logs(self, tail: int = 100) -> list[str]:
        """Return recent log lines from the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            return await container.log(
                follow=False, stdout=True, stderr=True, tail=tail
            )
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                raise DockerContainerNotFound(
                    f"[{self.name}] Container not found for logs"
                ) from err
            raise DockerError(
                f"[{self.name}] Could not get logs: {err!s}", _LOGGER.warning
            ) from err

    async def stream_logs(self, tail: int = 100) -> AsyncGenerator[str, None]:
        """Async generator that streams logs from the container."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                raise DockerContainerNotFound(
                    f"[{self.name}] Container not found"
                ) from err
            raise DockerError(
                f"[{self.name}] Could not get container for log stream: {err!s}",
                _LOGGER.error,
            ) from err

        try:
            async for line in container.log(
                stdout=True, stderr=True, follow=True, tail=tail
            ):
                if isinstance(line, bytes):
                    yield line.decode("utf-8", errors="replace").rstrip()
                else:
                    yield str(line).rstrip()
        except AioDockerError as err:
            raise DockerError(f"[{self.name}] Log stream failed: {err}") from err

    async def state(self) -> str:
        """Return container state as a string."""
        try:
            container = await self.coresys.docker.containers.get(self.name)
            info = await container.show()
            return info["State"]["Status"]
        except AioDockerError as err:
            if err.status == HTTPStatus.NOT_FOUND:
                return "not_found"
            raise DockerError(
                f"Could not get state of {self.name}: {err!s}",
                _LOGGER.error,
            ) from err
