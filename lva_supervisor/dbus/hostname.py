"""Hostname D-Bus interface."""

import logging
from typing import Any

from dbus_fast.aio import MessageBus

from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

DBUS_NAME = "org.freedesktop.hostname1"
DBUS_OBJECT = "/org/freedesktop/hostname1"
DBUS_IFACE = "org.freedesktop.hostname1"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


class Hostname:
    """Interface to org.freedesktop.hostname1 over D-Bus."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface: Any = None
        self._props_iface: Any = None

    async def connect(self, bus: MessageBus) -> None:
        """Load hostname1 interface from shared bus."""
        self._bus = bus
        try:
            introspection = await bus.introspect(DBUS_NAME, DBUS_OBJECT)
            proxy = bus.get_proxy_object(DBUS_NAME, DBUS_OBJECT, introspection)
            self._iface = proxy.get_interface(DBUS_IFACE)
            self._props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
            _LOGGER.info("D-Bus hostname1 interface loaded")
        except Exception as err:
            raise DBusConnectionError(
                f"Failed to load hostname1 D-Bus interface: {err}"
            ) from err

    def disconnect(self) -> None:
        """Clear cached interface (bus is closed by coresys)."""
        self._iface = None
        self._props_iface = None
        self._bus = None

    def _check_connected(self) -> None:
        if not self._bus or not self._iface or not self._props_iface:
            raise DBusConnectionError("hostname1 not connected")

    async def get_hostname(self) -> str:
        """Get the current system hostname."""
        self._check_connected()
        try:
            variant = await self._props_iface.call_get(
                DBUS_IFACE, "Hostname"
            )
            _LOGGER.info("Current hostname: %s", variant.value)
            return variant.value

        except Exception as err:
            raise DBusMethodError(f"GetHostname failed: {err}") from err

    async def set_hostname(self, hostname: str) -> None:
        """Set the system hostname permanently.

        SetStaticHostname(name, user_interaction) pass False for no polkit prompt.
        """
        self._check_connected()
        _LOGGER.info("Setting hostname to: %s", hostname)
        try:
            await self._iface.call_set_static_hostname(hostname, False)
        except Exception as err:
            raise DBusMethodError(f"SetHostname failed: {err}") from err

    async def get_os_version(self) -> str:
        """Get the OS version string from OperatingSystemPrettyName.

        e.g. 'LVA-OS 0.1' -> '0.1'
        """
        self._check_connected()
        try:
            variant = await self._props_iface.call_get(
                DBUS_IFACE, "OperatingSystemPrettyName"
            )
            pretty_name: str = variant.value
            return pretty_name.split()[-1]
        except Exception as err:
            raise DBusMethodError(f"GetOSVersion failed: {err}") from err