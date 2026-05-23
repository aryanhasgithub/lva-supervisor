"""RAUC D-Bus interface."""
import asyncio
import logging
from typing import Any

from dbus_fast.aio import MessageBus

from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

DBUS_NAME   = "de.pengutronix.rauc"
DBUS_OBJECT = "/"
DBUS_IFACE  = "de.pengutronix.rauc.Installer"


class RAUC:
    """Interface to de.pengutronix.rauc over D-Bus."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface: Any = None

    async def connect(self, bus: MessageBus) -> None:
        """Load RAUC installer interface from shared bus."""
        self._bus = bus
        try:
            introspection = await bus.introspect(DBUS_NAME, DBUS_OBJECT)
            proxy = bus.get_proxy_object(DBUS_NAME, DBUS_OBJECT, introspection)
            self._iface = proxy.get_interface(DBUS_IFACE)
            _LOGGER.info("D-Bus RAUC interface loaded")
        except Exception as err:
            raise DBusConnectionError(
                f"Failed to load RAUC D-Bus interface: {err}"
            ) from err

    def disconnect(self) -> None:
        """Clear cached interface (bus is closed by coresys)."""
        self._iface = None
        self._bus   = None

    def _check_connected(self) -> None:
        if not self._bus or not self._iface:
            raise DBusConnectionError("RAUC not connected")

    # =========================================================================
    # Install
    # =========================================================================

    async def install(self, bundle_url: str) -> None:
        """Trigger a RAUC bundle install.

        InstallBundle(source, args) — returns immediately, runs in background.
        We subscribe to the Completed signal and wait for it.
        bundle_url can be a local path or HTTP URL.
        """
        self._check_connected()
        _LOGGER.info("RAUC: installing bundle %s", bundle_url)

        completed_event = asyncio.Event()
        install_error: list[str] = []

        def on_completed(return_code: int, last_error: str) -> None:
            if return_code != 0:
                install_error.append(last_error)
                _LOGGER.error("RAUC: install failed (code %d): %s", return_code, last_error)
            else:
                _LOGGER.info("RAUC: install completed successfully")
            completed_event.set()

        # Subscribe to Completed signal before triggering install
        self._iface.on_completed(on_completed)

        try:
            # args dict is empty so use default install behaviour
            await self._iface.call_install_bundle(bundle_url, {})
            _LOGGER.info("RAUC: InstallBundle called, waiting for completion...")

            # Wait up to 10 minutes for install to complete
            await asyncio.wait_for(completed_event.wait(), timeout=600)

            if install_error:
                raise DBusMethodError(f"RAUC install failed: {install_error[0]}")

        except asyncio.TimeoutError as err:
            raise DBusMethodError("RAUC install timed out after 10 minutes") from err
        except DBusMethodError:
            raise
        except Exception as err:
            raise DBusMethodError(f"RAUC InstallBundle failed: {err}") from err
        finally:
            # Always unsubscribe the signal handler
            self._iface.off_completed(on_completed)

    # =========================================================================
    # Status
    # =========================================================================

    async def get_slot_status(self) -> list[dict[str, Any]]:
        """Return status of all RAUC slots (A and B).

        Returns list of (slot_name, slot_info_dict) pairs.
        """
        self._check_connected()
        try:
            slots = await self._iface.call_get_slot_status()
            result : list[dict[str, Any]] = []
            for slot_name, slot_info in slots:
                result.append({
                    "name":   slot_name,
                    "state":  slot_info.get("state", "unknown"),
                    "boot_status": slot_info.get("boot-status", "unknown"),
                    "version": slot_info.get("bundle.version", ""),
                    "installed_timestamp": slot_info.get("installed.timestamp", ""),
                })
            return result
        except Exception as err:
            raise DBusMethodError(f"RAUC GetSlotStatus failed: {err}") from err

    async def get_booted_slot(self) -> str:
        """Return the name of the currently booted slot (e.g. 'rootfs.0')."""
        self._check_connected()
        try:
            return await self._iface.get_booted()
        except Exception as err:
            raise DBusMethodError(f"RAUC GetBooted failed: {err}") from err

    async def get_operation(self) -> str:
        """Return current RAUC operation ('idle' or 'installing')."""
        self._check_connected()
        try:
            return await self._iface.get_operation()
        except Exception as err:
            raise DBusMethodError(f"RAUC GetOperation failed: {err}") from err