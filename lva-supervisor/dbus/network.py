"""NetworkManager D-Bus interface.

Wraps org.freedesktop.NetworkManager for:
  - listing interfaces and their current state
  - getting IP/DHCP info per interface
  - switching between DHCP and static IP

Key dbus-fast facts:
  - Properties accessed as await iface.get_property_name() in snake_case
  - Methods called as await iface.call_method_name() in snake_case
  - Property getters return Python native types directly — no .value unwrap needed
  - Variant required for typed values when updating NM connection settings

Shares the MessageBus instance owned by coresys.
"""

import logging
from typing import Any, Protocol

from dbus_fast.aio import MessageBus
from dbus_fast import Variant

from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

DBUS_NAME = "org.freedesktop.NetworkManager"
DBUS_OBJECT = "/org/freedesktop/NetworkManager"
DBUS_IFACE_NM = "org.freedesktop.NetworkManager"

# Device and IP4 interface names for get_interface() calls
IFACE_DEVICE = "org.freedesktop.NetworkManager.Device"
IFACE_IP4 = "org.freedesktop.NetworkManager.IP4Config"
IFACE_CONN = "org.freedesktop.NetworkManager.Settings.Connection"
IFACE_ACTIVE = "org.freedesktop.NetworkManager.Connection.Active"

# NM device state constants
NM_DEVICE_STATE_ACTIVATED = 100


class NetworkDeviceInterface(Protocol):
    async def get_interface(self) -> str: ...
    async def get_state(self) -> int: ...  # Change int/str based on DBus spec
    async def get_device_type(self) -> int: ...
    async def get_ip4_config(self) -> str: ...  # Returns an object path string
    async def get_active_connection(
        self,
    ) -> str: ...  # Returns an object path string or "/" if none
class IP4ConfigInterface(Protocol):
    async def get_address_data(self) -> list[dict[str, Any]]: ...
    async def get_gateway(self) -> str: ...
    async def get_nameserver_data(self) -> list[str]: ...
class ActiveConnectionInterface(Protocol):
    async def get_connection(self) -> str: ...
class ConnectionInterface(Protocol):
    async def call_get_settings(self) -> dict[str, Any]: ...
    async def call_update(self, settings: dict[str, Any]) -> None: ...
class NetworkManager:
    """Interface to org.freedesktop.NetworkManager over D-Bus."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface_nm: Any = None

    async def connect(self, bus: MessageBus) -> None:
        """Load NetworkManager interface from shared bus."""
        self._bus = bus
        try:
            introspection = await bus.introspect(DBUS_NAME, DBUS_OBJECT)
            proxy = bus.get_proxy_object(DBUS_NAME, DBUS_OBJECT, introspection)
            self._iface_nm = proxy.get_interface(DBUS_IFACE_NM)
            _LOGGER.info("D-Bus NetworkManager interface loaded")
        except Exception as err:
            raise DBusConnectionError(
                f"Failed to load NetworkManager D-Bus interface: {err}"
            ) from err

    def disconnect(self) -> None:
        self._iface_nm = None
        self._bus = None

    def _check_connected(self) -> None:
        if not self._bus or not self._iface_nm:
            raise DBusConnectionError("NetworkManager not connected")

    # =========================================================================
    # Read
    # =========================================================================

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return list of network devices with state and IP info."""
        self._check_connected()
        try:
            device_paths = await self._iface_nm.call_get_devices()
            devices: list[dict[str, Any]] = []
            for path in device_paths:
                try:
                    dev = await self._get_device_info(path)
                    devices.append(dev)
                except Exception as err: # pylint: disable=broad-exception-caught
                    _LOGGER.warning("Could not read device %s: %s", path, err)
            return devices
        except Exception as err:
            raise DBusMethodError(f"GetDevices failed: {err}") from err

    async def _get_device_info(self, path: str) -> dict[str, Any]:
        """Read device properties and current IP config for one device."""
        introspection = await self._bus.introspect(DBUS_NAME, path)  # type: ignore[reportUnknownMemberType]
        proxy = self._bus.get_proxy_object(DBUS_NAME, path, introspection)  # type: ignore[reportUnknownMemberType]
        dev_iface: NetworkDeviceInterface = proxy.get_interface(IFACE_DEVICE)  # type: ignore[reportAssignmentType]

        iface_name = await dev_iface.get_interface()
        state = await dev_iface.get_state()
        dev_type = await dev_iface.get_device_type()
        ip4_path = await dev_iface.get_ip4_config()

        info: dict[str, Any] = {
            "interface": iface_name,
            "state": state,
            "type": dev_type,
            "ip4": None,
        }

        # Ip4Config only valid when device is activated (state == 100)
        # path will be "/" if not available
        if state == NM_DEVICE_STATE_ACTIVATED and ip4_path and ip4_path != "/":
            try:
                info["ip4"] = await self._get_ip4_info(ip4_path)
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Could not read IP4 config for %s: %s", path, err)

        return info

    async def _get_ip4_info(self, path: str) -> dict[str, Any]:
        """Read IPv4 address info from an IP4Config object.

        Uses AddressData (not deprecated Addresses) per NM docs.
        """
        introspection = await self._bus.introspect(DBUS_NAME, path)  # type: ignore[reportUnknownMemberType]
        proxy = self._bus.get_proxy_object(DBUS_NAME, path, introspection)  # type: ignore[reportUnknownMemberType]
        ip4_iface: IP4ConfigInterface = proxy.get_interface(IFACE_IP4)  # type: ignore[reportAssignmentType]

        # AddressData: array of dicts with "address" (str) and "prefix" (uint)
        addresses = await ip4_iface.get_address_data()
        gateway = await ip4_iface.get_gateway()
        dns = await ip4_iface.get_nameserver_data()

        return {
            "addresses": addresses,
            "gateway": gateway,
            "dns": dns,
        }

    # =========================================================================
    # Write — DHCP / Static IP
    # =========================================================================

    async def set_dhcp(self, interface: str) -> None:
        """Switch an interface to DHCP."""
        self._check_connected()
        _LOGGER.info("Setting %s to DHCP", interface)
        conn_path, dev_path = await self._find_connection(interface)
        settings = await self._get_connection_settings(conn_path)

        settings["ipv4"] = {
            "method": Variant("s", "auto"),
        }
        await self._update_and_reactivate(conn_path, dev_path, settings)

    async def set_static_ip(
        self,
        interface: str,
        address: str,
        prefix: int,
        gateway: str,
        dns: list[str],
    ) -> None:
        """Switch an interface to a static IP.

        NM uses method='manual' for static IP.
        address: e.g. '192.168.1.10'
        prefix:  subnet prefix length e.g. 24
        """
        self._check_connected()
        _LOGGER.info("Setting %s to static %s/%d", interface, address, prefix)
        conn_path, dev_path = await self._find_connection(interface)
        settings = await self._get_connection_settings(conn_path)

        settings["ipv4"] = {
            "method": Variant("s", "manual"),
            "address-data": Variant(
                "aa{sv}",
                [
                    {
                        "address": Variant("s", address),
                        "prefix": Variant("u", prefix),
                    }
                ],
            ),
            "gateway": Variant("s", gateway),
            "dns": Variant("as", dns),
        }
        await self._update_and_reactivate(conn_path, dev_path, settings)

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _find_connection(self, interface: str) -> tuple[str, str]:
        """Find active connection path and device path for an interface."""
        device_paths = await self._iface_nm.call_get_devices()
        for dev_path in device_paths:
            introspection = await self._bus.introspect(DBUS_NAME, dev_path)  # type: ignore[reportUnknownMemberType]
            proxy = self._bus.get_proxy_object(DBUS_NAME, dev_path, introspection)  # type: ignore[reportUnknownMemberType]
            dev_iface: NetworkDeviceInterface = proxy.get_interface(IFACE_DEVICE)  # type: ignore[reportAssignmentType]

            iface_name = await dev_iface.get_interface()
            if iface_name != interface:
                continue

            active_conn = await dev_iface.get_active_connection()
            if not active_conn or active_conn == "/":
                raise DBusMethodError(f"No active connection on {interface}")

            # Get Settings.Connection path from the active connection
            ac_intro = await self._bus.introspect(DBUS_NAME, active_conn)  # type: ignore[reportUnknownMemberType]
            ac_proxy = self._bus.get_proxy_object(DBUS_NAME, active_conn, ac_intro)  # type: ignore[reportUnknownMemberType]
            ac_iface: ActiveConnectionInterface = ac_proxy.get_interface(IFACE_ACTIVE)  # type: ignore[reportAssignmentType]
            conn_path = await ac_iface.get_connection()
            return conn_path, dev_path

        raise DBusMethodError(f"Interface '{interface}' not found")

    async def _get_connection_settings(self, conn_path: str) -> dict[str, Any]:
        """Get current connection settings dict."""
        introspection = await self._bus.introspect(DBUS_NAME, conn_path)  # type: ignore[reportUnknownMemberType]
        proxy = self._bus.get_proxy_object(DBUS_NAME, conn_path, introspection)  # type: ignore[reportUnknownMemberType]
        conn_iface: ConnectionInterface = proxy.get_interface(IFACE_CONN)  # type: ignore[reportAssignmentType]
        return await conn_iface.call_get_settings()

    async def _update_and_reactivate(
        self, conn_path: str, dev_path: str, settings: dict[str, Any]
    ) -> None:
        """Update connection settings and reactivate."""
        introspection = await self._bus.introspect(DBUS_NAME, conn_path)  # type: ignore[reportUnknownMemberType]
        proxy = self._bus.get_proxy_object(DBUS_NAME, conn_path, introspection)  # type: ignore[reportUnknownMemberType]
        conn_iface: ConnectionInterface = proxy.get_interface(IFACE_CONN)  # type: ignore[reportAssignmentType]
        await conn_iface.call_update(settings)
        await self._iface_nm.call_activate_connection(conn_path, dev_path, "/")
        _LOGGER.info("Connection updated and reactivated on %s", dev_path)
