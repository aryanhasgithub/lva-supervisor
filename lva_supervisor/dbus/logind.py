"""LVA OSlogind."""

import logging
from typing import Any

from dbus_fast.aio import MessageBus

from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

# org.freedesktop.login1 constants
DBUS_NAME_LOGIND    = "org.freedesktop.login1"
DBUS_OBJECT_LOGIND  = "/org/freedesktop/login1"
DBUS_IFACE_MANAGER  = "org.freedesktop.login1.Manager"


class Logind:
    """Interface to systemd-logind over D-Bus.

    https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html
    """

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface_manager: Any = None

    # =========================================================================
    # Connection
    # =========================================================================

    async def connect(self, bus: MessageBus) -> None:
        """Cache the logind Manager proxy interface from a shared MessageBus."""
        self._bus = bus
        try:
            introspection = await bus.introspect(DBUS_NAME_LOGIND, DBUS_OBJECT_LOGIND)
            proxy = bus.get_proxy_object(DBUS_NAME_LOGIND, DBUS_OBJECT_LOGIND, introspection)
            self._iface_manager = proxy.get_interface(DBUS_IFACE_MANAGER)
            _LOGGER.info("D-Bus logind Manager interface loaded")
        except Exception as err:
            raise DBusConnectionError(
                f"Failed to connect to systemd-logind: {err}"
            ) from err

    def disconnect(self) -> None:
        """Clear cached interfaces (bus lifecycle is managed by coresys)."""
        self._iface_manager = None
        self._bus = None

    def _check_connected(self) -> None:
        if not self._bus or not self._iface_manager:
            raise DBusConnectionError("systemd-logind not connected")

    # =========================================================================
    # Power / reboot — org.freedesktop.login1.Manager
    # =========================================================================

    async def reboot(self) -> None:
        """Reboot the host system.

        Maps to: Reboot(in b interactive)
        interactive=False → polkit handled externally.
        """
        self._check_connected()
        _LOGGER.warning("Requesting system reboot via logind")
        try:
            await self._iface_manager.call_reboot(False)
        except Exception as err:
            raise DBusMethodError(f"Reboot failed: {err}") from err

    async def power_off(self) -> None:
        """Power off the host system.

        Maps to: PowerOff(in b interactive)
        """
        self._check_connected()
        _LOGGER.warning("Requesting system power-off via logind")
        try:
            await self._iface_manager.call_power_off(False)
        except Exception as err:
            raise DBusMethodError(f"PowerOff failed: {err}") from err

    async def halt(self) -> None:
        """Halt the host system (shutdown without cutting power).

        Maps to: Halt(in b interactive)
        """
        self._check_connected()
        _LOGGER.warning("Requesting system halt via logind")
        try:
            await self._iface_manager.call_halt(False)
        except Exception as err:
            raise DBusMethodError(f"Halt failed: {err}") from err

    