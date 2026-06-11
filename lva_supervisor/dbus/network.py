"""NetworkManager D-Bus interface.

Wraps org.freedesktop.NetworkManager for:
  - listing interfaces and their current state
  - getting IP/DHCP info per interface
  - switching between DHCP and static IP
  - scanning for WiFi networks
  - connecting to / disconnecting from WiFi
"""

import logging
import socket
import struct
import uuid
from typing import Any, Protocol

from dbus_fast.aio import MessageBus
from dbus_fast import Variant

from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

DBUS_NAME = "org.freedesktop.NetworkManager"
DBUS_OBJECT = "/org/freedesktop/NetworkManager"
DBUS_IFACE_NM = "org.freedesktop.NetworkManager"

IFACE_DEVICE          = "org.freedesktop.NetworkManager.Device"
IFACE_DEVICE_WIRELESS = "org.freedesktop.NetworkManager.Device.Wireless"
IFACE_ACCESS_POINT    = "org.freedesktop.NetworkManager.AccessPoint"
IFACE_IP4             = "org.freedesktop.NetworkManager.IP4Config"
IFACE_CONN            = "org.freedesktop.NetworkManager.Settings.Connection"
IFACE_ACTIVE          = "org.freedesktop.NetworkManager.Connection.Active"

NM_DEVICE_STATE_ACTIVATED = 100
NM_DEVICE_TYPE_WIFI       = 2

# AP security flag masks
NM_802_11_AP_FLAGS_PRIVACY = 0x1
NM_802_11_AP_SEC_KEY_MGMT_PSK = 0x100


def _ip4_to_uint32(ip: str) -> int:
    return struct.unpack("=I", socket.inet_aton(ip))[0]


def _uint32_to_ip4(n: int) -> str:
    return socket.inet_ntoa(struct.pack("=I", n))


# ---------------------------------------------------------------------------
# Protocol stubs
# ---------------------------------------------------------------------------

class NetworkDeviceInterface(Protocol):
    async def get_interface(self) -> str: ...
    async def get_state(self) -> int: ...
    async def get_device_type(self) -> int: ...
    async def get_ip4_config(self) -> str: ...
    async def get_active_connection(self) -> str: ...


class WirelessDeviceInterface(Protocol):
    async def call_request_scan(self, options: dict) -> None: ...
    async def call_get_all_access_points(self) -> list[str]: ...


class AccessPointInterface(Protocol):
    async def get_ssid(self) -> bytes: ...
    async def get_strength(self) -> int: ...
    async def get_frequency(self) -> int: ...
    async def get_flags(self) -> int: ...
    async def get_rsn_flags(self) -> int: ...
    async def get_hw_address(self) -> str: ...


class IP4ConfigInterface(Protocol):
    async def get_address_data(self) -> list[dict[str, Any]]: ...
    async def get_gateway(self) -> str: ...
    async def get_nameserver_data(self) -> list[dict[str, Any]]: ...


class ActiveConnectionInterface(Protocol):
    async def get_connection(self) -> str: ...


class ConnectionInterface(Protocol):
    async def call_get_settings(self) -> dict[str, Any]: ...
    async def call_update_unsaved(self, settings: dict[str, Any]) -> None: ...


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
                except Exception as err:  # pylint: disable=broad-exception-caught
                    _LOGGER.warning("Could not read device %s: %s", path, err)
            return devices
        except Exception as err:
            raise DBusMethodError(f"GetDevices failed: {err}") from err

    async def _get_device_info(self, path: str) -> dict[str, Any]:
        """Read device properties and current IP config for one device."""
        introspection = await self._bus.introspect(DBUS_NAME, path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, path, introspection)  # type: ignore
        dev_iface: NetworkDeviceInterface = proxy.get_interface(IFACE_DEVICE)  # type: ignore

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

        if state == NM_DEVICE_STATE_ACTIVATED and ip4_path and ip4_path != "/":
            try:
                info["ip4"] = await self._get_ip4_info(ip4_path)
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Could not read IP4 config for %s: %s", path, err)

        return info

    async def _get_ip4_info(self, path: str) -> dict[str, Any]:
        """Read IPv4 address info from an IP4Config object."""
        introspection = await self._bus.introspect(DBUS_NAME, path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, path, introspection)  # type: ignore
        ip4_iface: IP4ConfigInterface = proxy.get_interface(IFACE_IP4)  # type: ignore

        addresses = await ip4_iface.get_address_data()
        gateway = await ip4_iface.get_gateway()

        raw_ns_data = await ip4_iface.get_nameserver_data()
        dns: list[str] = []
        for entry in raw_ns_data:
            addr_variant = entry.get("address")
            if addr_variant is not None:
                dns.append(
                    addr_variant.value
                    if hasattr(addr_variant, "value")
                    else str(addr_variant)
                )

        return {
            "addresses": addresses,
            "gateway": gateway,
            "dns": dns,
        }

    # =========================================================================
    # WiFi — scan
    # =========================================================================

    async def wifi_scan(self, interface: str) -> list[dict[str, Any]]:
        """Trigger a scan and return visible access points on an interface.

        Returns a list of dicts:
          { ssid, bssid, strength (0-100), frequency, secured (bool) }
        """
        self._check_connected()
        dev_path = await self._find_wifi_device(interface)

        introspection = await self._bus.introspect(DBUS_NAME, dev_path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, dev_path, introspection)  # type: ignore
        wifi_iface: WirelessDeviceInterface = proxy.get_interface(IFACE_DEVICE_WIRELESS)  # type: ignore

        # Request a fresh scan — NM does it asynchronously.
        # We fire-and-forget and immediately read the cached list;
        # on first call the existing cache is still useful.
        try:
            await wifi_iface.call_request_scan({})
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.debug("RequestScan returned (may be rate-limited): %s", err)

        ap_paths = await wifi_iface.call_get_all_access_points()

        results: list[dict[str, Any]] = []
        for ap_path in ap_paths:
            try:
                ap_info = await self._get_ap_info(ap_path)
                results.append(ap_info)
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Could not read AP %s: %s", ap_path, err)

        # Sort by signal strength descending
        results.sort(key=lambda x: x["strength"], reverse=True)
        return results

    async def _get_ap_info(self, path: str) -> dict[str, Any]:
        """Read properties from one AccessPoint object."""
        introspection = await self._bus.introspect(DBUS_NAME, path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, path, introspection)  # type: ignore
        ap_iface: AccessPointInterface = proxy.get_interface(IFACE_ACCESS_POINT)  # type: ignore

        ssid_bytes = await ap_iface.get_ssid()
        strength = await ap_iface.get_strength()
        frequency = await ap_iface.get_frequency()
        flags = await ap_iface.get_flags()
        rsn_flags = await ap_iface.get_rsn_flags()
        bssid = await ap_iface.get_hw_address()

        # Decode SSID — bytes from dbus-fast
        try:
            if isinstance(ssid_bytes, (bytes, bytearray)):
                ssid = ssid_bytes.decode("utf-8", errors="replace")
            else:
                ssid = str(ssid_bytes)
        except Exception:  # pylint: disable=broad-exception-caught
            ssid = ""

        # Network is secured if it has privacy flag or RSN/WPA flags
        secured = bool(flags & NM_802_11_AP_FLAGS_PRIVACY) or rsn_flags != 0

        return {
            "ssid": ssid,
            "bssid": bssid,
            "strength": strength,
            "frequency": frequency,
            "secured": secured,
        }

    # =========================================================================
    # WiFi — connect
    # =========================================================================

    async def wifi_connect(
        self,
        interface: str,
        ssid: str,
        password: str | None = None,
    ) -> None:
        """Connect to a WiFi network.

        Builds the connection profile and calls AddAndActivateConnection.
        If password is None, connects to an open network.
        """
        self._check_connected()
        dev_path = await self._find_wifi_device(interface)

        ssid_bytes = ssid.encode("utf-8")

        conn: dict[str, Any] = {
            "connection": {
                "type": Variant("s", "802-11-wireless"),
                "uuid": Variant("s", str(uuid.uuid4())),
                "id": Variant("s", ssid),
            },
            "802-11-wireless": {
                "ssid": Variant("ay", ssid_bytes),
                "mode": Variant("s", "infrastructure"),
            },
            "ipv4": {
                "method": Variant("s", "auto"),
            },
            "ipv6": {
                "method": Variant("s", "ignore"),
            },
        }

        if password:
            conn["802-11-wireless"]["security"] = Variant("s", "802-11-wireless-security")
            conn["802-11-wireless-security"] = {
                "key-mgmt": Variant("s", "wpa-psk"),
                "auth-alg": Variant("s", "open"),
                "psk": Variant("s", password),
            }

        _LOGGER.info("Connecting to WiFi SSID '%s' on %s", ssid, interface)
        try:
            await self._iface_nm.call_add_and_activate_connection(
                conn,
                dev_path,
                "/",  # specific_object — "/" lets NM pick the best AP
            )
        except Exception as err:
            raise DBusMethodError(f"WiFi connect failed: {err}") from err

    # =========================================================================
    # WiFi — disconnect
    # =========================================================================

    async def wifi_disconnect(self, interface: str) -> None:
        """Disconnect the active connection on a WiFi interface."""
        self._check_connected()
        dev_path = await self._find_wifi_device(interface)

        introspection = await self._bus.introspect(DBUS_NAME, dev_path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, dev_path, introspection)  # type: ignore
        dev_iface: NetworkDeviceInterface = proxy.get_interface(IFACE_DEVICE)  # type: ignore

        active_conn = await dev_iface.get_active_connection()
        if not active_conn or active_conn == "/":
            raise DBusMethodError(f"No active connection on {interface}")

        try:
            await self._iface_nm.call_deactivate_connection(active_conn)
            _LOGGER.info("Disconnected %s", interface)
        except Exception as err:
            raise DBusMethodError(f"WiFi disconnect failed: {err}") from err

    # =========================================================================
    # Write — DHCP / Static IP  (unchanged)
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
        """Switch an interface to a static IP."""
        self._check_connected()
        _LOGGER.info("Setting %s to static %s/%d", interface, address, prefix)
        conn_path, dev_path = await self._find_connection(interface)
        settings = await self._get_connection_settings(conn_path)

        try:
            dns_uint32 = [_ip4_to_uint32(d) for d in dns]
        except (OSError, struct.error) as err:
            raise DBusMethodError(f"Invalid DNS address: {err}") from err

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
            "dns": Variant("au", dns_uint32),
        }
        await self._update_and_reactivate(conn_path, dev_path, settings)

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _find_wifi_device(self, interface: str) -> str:
        """Return the D-Bus object path for a WiFi device by interface name."""
        device_paths = await self._iface_nm.call_get_devices()
        for dev_path in device_paths:
            introspection = await self._bus.introspect(DBUS_NAME, dev_path)  # type: ignore
            proxy = self._bus.get_proxy_object(DBUS_NAME, dev_path, introspection)  # type: ignore
            dev_iface: NetworkDeviceInterface = proxy.get_interface(IFACE_DEVICE)  # type: ignore

            iface_name = await dev_iface.get_interface()
            dev_type = await dev_iface.get_device_type()

            if iface_name == interface and dev_type == NM_DEVICE_TYPE_WIFI:
                return dev_path

        raise DBusMethodError(f"WiFi interface '{interface}' not found")

    async def _find_connection(self, interface: str) -> tuple[str, str]:
        """Find active connection path and device path for an interface."""
        device_paths = await self._iface_nm.call_get_devices()
        for dev_path in device_paths:
            introspection = await self._bus.introspect(DBUS_NAME, dev_path)  # type: ignore
            proxy = self._bus.get_proxy_object(DBUS_NAME, dev_path, introspection)  # type: ignore
            dev_iface: NetworkDeviceInterface = proxy.get_interface(IFACE_DEVICE)  # type: ignore

            iface_name = await dev_iface.get_interface()
            if iface_name != interface:
                continue

            active_conn = await dev_iface.get_active_connection()
            if not active_conn or active_conn == "/":
                raise DBusMethodError(f"No active connection on {interface}")

            ac_intro = await self._bus.introspect(DBUS_NAME, active_conn)  # type: ignore
            ac_proxy = self._bus.get_proxy_object(DBUS_NAME, active_conn, ac_intro)  # type: ignore
            ac_iface: ActiveConnectionInterface = ac_proxy.get_interface(IFACE_ACTIVE)  # type: ignore
            conn_path = await ac_iface.get_connection()
            return conn_path, dev_path

        raise DBusMethodError(f"Interface '{interface}' not found")

    async def _get_connection_settings(self, conn_path: str) -> dict[str, Any]:
        introspection = await self._bus.introspect(DBUS_NAME, conn_path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, conn_path, introspection)  # type: ignore
        conn_iface: ConnectionInterface = proxy.get_interface(IFACE_CONN)  # type: ignore
        return await conn_iface.call_get_settings()

    async def _update_and_reactivate(
        self, conn_path: str, dev_path: str, settings: dict[str, Any]
    ) -> None:
        introspection = await self._bus.introspect(DBUS_NAME, conn_path)  # type: ignore
        proxy = self._bus.get_proxy_object(DBUS_NAME, conn_path, introspection)  # type: ignore
        conn_iface: ConnectionInterface = proxy.get_interface(IFACE_CONN)  # type: ignore

        await conn_iface.call_update_unsaved(settings)
        _LOGGER.info("Connection settings updated in-memory for %s", dev_path)
        await self._iface_nm.call_activate_connection(conn_path, dev_path, "/")
        _LOGGER.info("Connection reactivated on %s", dev_path)