"""LVA OS Agent D-Bus interface."""

import logging
from typing import Any

from dbus_fast.aio import MessageBus

from ..const import (
    DBUS_NAME,
    DBUS_OBJECT,
    DBUS_IFACE_SYSTEM,
    DBUS_IFACE_INFO,
)
from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)


class OSAgent:
    """Interface to lva-os-agent over D-Bus."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface_system: Any = None
        self._iface_info: Any = None

    # =========================================================================
    # Connection
    # =========================================================================

    async def connect(self, bus: MessageBus) -> None:
        """Cache proxy interfaces from a shared MessageBus instance."""
        self._bus = bus
        try:
            introspection = await bus.introspect(DBUS_NAME, DBUS_OBJECT)
            proxy = bus.get_proxy_object(DBUS_NAME, DBUS_OBJECT, introspection)
            self._iface_system = proxy.get_interface(DBUS_IFACE_SYSTEM)
            self._iface_info   = proxy.get_interface(DBUS_IFACE_INFO)
            _LOGGER.info("D-Bus os-agent interfaces loaded")
        except Exception as err:
            raise DBusConnectionError(
                f"Failed to load os-agent D-Bus interfaces: {err}"
            ) from err

    def disconnect(self) -> None:
        """Clear cached interfaces (bus is closed by coresys)."""
        self._iface_system = None
        self._iface_info   = None
        self._bus          = None

    def _check_connected(self) -> None:
        if not self._bus or not self._iface_system:
            raise DBusConnectionError("os-agent not connected")

    # =========================================================================
    # System
    # =========================================================================

    async def reboot(self) -> None:
        """Reboot the host system."""
        self._check_connected()
        _LOGGER.warning("Requesting system reboot via os-agent")
        try:
            await self._iface_system.call_reboot()
        except Exception as err:
            raise DBusMethodError(f"Reboot failed: {err}") from err

    async def poweroff(self) -> None:
        """Power off the host system."""
        self._check_connected()
        _LOGGER.warning("Requesting system poweroff via os-agent")
        try:
            await self._iface_system.call_power_off()
        except Exception as err:
            raise DBusMethodError(f"PowerOff failed: {err}") from err


    # =========================================================================
    # Info
    # =========================================================================

    async def get_os_version(self) -> str:
        """Get LVA-OS version string."""
        self._check_connected()
        try:
            return await self._iface_info.call_get_os_version()
        except Exception as err:
            raise DBusMethodError(f"GetOsVersion failed: {err}") from err

    async def get_hardware_info(self) -> dict[str, Any]:
        """Get hardware info (board, arch, memory)."""
        self._check_connected()
        try:
            return await self._iface_info.call_get_hardware_info()
        except Exception as err:
            raise DBusMethodError(f"GetHardwareInfo failed: {err}") from err