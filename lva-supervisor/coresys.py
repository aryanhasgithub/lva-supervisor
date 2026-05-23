"""LVA Supervisor CoreSys.

Central shared state object passed to every component.
Owns the single D-Bus MessageBus connection shared across all D-Bus classes.
"""
import logging

from dbus_fast.aio import MessageBus
from dbus_fast import BusType

from typing import Union

from .const import (
    CONTAINER_AUDIO,
    CONTAINER_LVA,
    CONTAINER_PORTAL,
    CONTAINER_START_ORDER,
)
from .docker.manager import DockerManager
from .containers.audio import Audio
from .containers.lva import LVA
from .containers.portal import Portal
from .containers.base import ContainerBase
from .dbus.agent import OSAgent
from .dbus.hostname import Hostname
from .dbus.network import NetworkManager
from .dbus.rauc import RAUC
from .watchdog import Watchdog

_LOGGER = logging.getLogger(__name__)


class CoreSys:
    """Central shared state for the LVA supervisor."""

    def __init__(self) -> None:
        # Docker
        self._docker: DockerManager = DockerManager(self)

        # Managed containers
        self._containers: dict[str, ContainerBase] = {
            CONTAINER_AUDIO:  Audio(self),
            CONTAINER_LVA:    LVA(self),
            CONTAINER_PORTAL: Portal(self),
        }

        # Shared D-Bus bus — owned here, passed to all D-Bus classes
        self._bus: MessageBus | None = None

        # D-Bus interfaces
        self._agent:   OSAgent        = OSAgent()
        self._hostname: Hostname      = Hostname()
        self._network:  NetworkManager = NetworkManager()
        self._rauc:    RAUC           = RAUC()

        # Watchdog
        self._watchdog: Watchdog = Watchdog(self)

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def docker(self) -> DockerManager:
        """Docker manager."""
        return self._docker

    @property
    def containers(self) -> dict[str, ContainerBase]:
        """Dict of managed containers."""
        return self._containers

    @property
    def agent(self) -> OSAgent:
        """D-Bus interface to lva-os-agent."""
        return self._agent

    @property
    def hostname(self) -> Hostname:
        """D-Bus interface to system hostname."""
        return self._hostname

    @property
    def network(self) -> NetworkManager:
        """D-Bus interface to NetworkManager."""
        return self._network

    @property
    def rauc(self) -> RAUC:
        """D-Bus interface to RAUC installer."""
        return self._rauc

    @property
    def watchdog(self) -> Watchdog:
        """Watchdog manager."""
        return self._watchdog

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def setup(self) -> None:
        """Connect all components."""
        _LOGGER.info("CoreSys setting up")

        await self._docker.connect()
        _LOGGER.info("Docker connected")

        # Connect shared D-Bus bus
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            _LOGGER.info("Connected to system D-Bus")
            await self._connect_dbus_interfaces()
        except Exception as err: # pylint: disable=broad-exception-caught
            _LOGGER.warning("D-Bus connect failed: %s", err)

        await self._start_containers()
        await self._watchdog.start()

        _LOGGER.info("CoreSys setup complete")

    async def _connect_dbus_interfaces(self) -> None:
        """Load all D-Bus interfaces from the shared bus.

        Each interface is non-fatal if one fails the others still load.
        """
        interfaces: list[tuple[str, Union[OSAgent, Hostname, NetworkManager, RAUC]]] = [
            ("os-agent",       self._agent),
            ("hostname1",      self._hostname),
            ("NetworkManager", self._network),
            ("rauc",           self._rauc),
        ]
        for name, obj in interfaces:
            try:
                await obj.connect(self._bus) # type: ignore[reportUnknownMemberType]
            except Exception as err: # pylint: disable=broad-exception-caught
                _LOGGER.warning("D-Bus [%s] failed to load: %s", name, err)


    async def teardown(self) -> None:
        """Disconnect all components cleanly."""
        _LOGGER.info("CoreSys tearing down")

        await self._watchdog.stop()

        self._agent.disconnect()
        self._hostname.disconnect()
        self._network.disconnect()
        self._rauc.disconnect()

        if self._bus:
            self._bus.disconnect()
            self._bus = None

        await self._docker.disconnect()

        _LOGGER.info("CoreSys teardown complete")

    # =========================================================================
    # Container startup
    # =========================================================================

    async def _start_containers(self) -> None:
        """Start all managed containers in order."""
        for name in CONTAINER_START_ORDER:
            container = self._containers[name]
            try:
                await container.load()
            except Exception as err: # pylint: disable=broad-exception-caught
                _LOGGER.error(
                    "Failed to start [%s] during setup: %s — watchdog will retry",
                    name, err
                )