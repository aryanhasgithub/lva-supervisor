"""RAUC D-Bus interface."""

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncIterator

from dbus_fast.aio import MessageBus

from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

# RAUC always lives on the system bus at these fixed coordinates.
_DBUS_NAME = "de.pengutronix.rauc"
_DBUS_OBJECT = "/"
_DBUS_IFACE = "de.pengutronix.rauc.Installer"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

class SignalResult:

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()

    def send_signal(self, return_code: int, last_error: str) -> None:
        """Callback fed into dbus-fast to capture the signal data."""
        self._queue.put_nowait((return_code, last_error))

    async def wait_for_signal(self) -> tuple[int, str]:
        """Awaits indefinitely until the host OS finishes processing."""
        return await self._queue.get()


class RAUC:
    """Interface to de.pengutronix.rauc over D-Bus."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface: Any = None
        self._props_iface: Any = None

    async def connect(self, bus: MessageBus) -> None:
        """Load RAUC installer interface from shared bus."""
        self._bus = bus
        try:
            introspection = await bus.introspect(_DBUS_NAME, _DBUS_OBJECT)
            proxy = bus.get_proxy_object(_DBUS_NAME, _DBUS_OBJECT, introspection)
            self._iface = proxy.get_interface(_DBUS_IFACE)
            self._props_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
            _LOGGER.info("D-Bus RAUC interface loaded")
        except Exception as err:
            raise DBusConnectionError(
                f"Failed to load RAUC D-Bus interface: {err}"
            ) from err

    def disconnect(self) -> None:
        """Clear cached interface (bus is closed by coresys)."""
        self._iface = None
        self._props_iface = None
        self._bus = None

    def _check_connected(self) -> None:
        if not self._bus or not self._iface:
            raise DBusConnectionError("RAUC not connected")

    # =========================================================================
    # HA Production Signal Context Manager
    # =========================================================================

    @asynccontextmanager
    async def signal_completed(self) -> AsyncIterator[SignalResult]:
        """Context manager tracking signals indefinitely with no timeout."""
        self._check_connected()
        result = SignalResult()

        # Wire the queue sender directly into the dbus-fast event hook.
        self._iface.on_completed(result.send_signal)
        try:
            yield result
        finally:
            self._iface.off_completed(result.send_signal)

    # =========================================================================
    # Install
    # =========================================================================

    async def install(self, bundle_url: str) -> tuple[int, str]:
        """Trigger a RAUC bundle install"""
        self._check_connected()
        _LOGGER.info("RAUC: installing bundle %s", bundle_url)

        try:
            async with self.signal_completed() as signal:
                # Trigger background installation on the host via dbus-fast.
                await self._iface.call_install_bundle(bundle_url, {})
                _LOGGER.info("RAUC: InstallBundle called, awaiting signal...")

                # Wait indefinitely until the raw host engine returns.
                return_code, last_error = await signal.wait_for_signal()

                if return_code != 0:
                    raise DBusMethodError(
                        f"RAUC install failed with code {return_code}: {last_error}"
                    )

                _LOGGER.info("RAUC: install completed successfully")
                return return_code, last_error

        except DBusMethodError:
            raise
        except Exception as err:
            raise DBusMethodError(f"RAUC InstallBundle failed: {err}") from err

    # =========================================================================
    # Status
    # =========================================================================

    async def get_slot_status(self) -> list[dict[str, Any]]:
        """Return status of all RAUC slots (A and B)."""
        self._check_connected()
        try:
            # 1. Execute the native RAUC D-Bus method call helper
            slots = await self._iface.call_get_slot_status()
            result: list[dict[str, Any]] = []
            
        
            for _, slot_name, slot_info in slots:
                
                def _v(key: str, default: Any = "", slot_info=slot_info) -> Any:
                    val = slot_info.get(key)
                    return val.value if val is not None else default

                result.append(
                    {
                        "name": slot_name,
                        "state": _v("state", "unknown"),
                        "boot_status": _v("boot-status", "unknown"),
                        "version": _v("bundle.version", ""),
                        "installed_timestamp": _v("installed.timestamp", ""),
                    }
                )
            return result
        except Exception as err:
            raise DBusMethodError(f"RAUC GetSlotStatus failed: {err}") from err


    async def get_booted_slot(self) -> str:
        """Return the name of the currently booted RAUC slot (A or B)."""
        self._check_connected()
        try:
            variant = await self._props_iface.call_get(_DBUS_IFACE, "BootSlot")
            return variant.value

        except Exception as err:
            raise DBusMethodError(f"RAUC GetBootSlot failed: {err}") from err

    async def get_operation(self) -> str:
        """Return current RAUC operation ('idle' or 'installing').

        'Operation' is a readable D-Bus property → get_operation().
        """
        self._check_connected()
        try:
            variant = await self._props_iface.call_get(_DBUS_IFACE, "Operation")
            return variant.value
        except Exception as err:
            raise DBusMethodError(f"RAUC GetOperation failed: {err}") from err

    async def mark_good(self, slot: str = "booted") -> tuple[str, str]:
        """Mark a slot as good, preventing automatic rollback on next boot."""
        self._check_connected()
        try:
            return await self._iface.call_mark("good", slot)
        except Exception as err:
            raise DBusMethodError(f"RAUC MarkGood failed: {err}") from err