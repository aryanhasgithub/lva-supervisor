"""LVA Supervisor bootstrap."""

import asyncio
import logging
import signal

from aiohttp import web

from .coresys import CoreSys
from .api.containers import setup_routes as setup_container_routes
from .api.system import setup_routes as setup_system_routes
from .api.audio import setup_routes as setup_audio_routes
from .api.updates import setup_routes as setup_update_routes
from .api.network import setup_routes as setup_network_routes
from .const import SUPERVISOR_SOCKET, STARTUP_MARKER

_LOGGER = logging.getLogger(__name__)

# The exact location where the host bash script writes the startup marker


async def run_supervisor() -> int:
    """Main entry point which sets up and runs the supervisor until shutdown."""

    coresys = CoreSys()
    app = _build_app(coresys)

    stop_event = asyncio.Event()

    coresys.stop_event = stop_event
    coresys.exit_code = 0

    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        _LOGGER.info("Shutdown signal received")
        coresys.exit_code = 0
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    # Setup all components
    

    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()

    SUPERVISOR_SOCKET.parent.mkdir(parents=True, exist_ok=True)

    if SUPERVISOR_SOCKET.exists():
        SUPERVISOR_SOCKET.unlink()
        _LOGGER.debug("Removed stale socket at %s", SUPERVISOR_SOCKET)

    site = web.UnixSite(runner, path=str(SUPERVISOR_SOCKET))
    await site.start()
    _LOGGER.info("Supervisor API listening on %s", SUPERVISOR_SOCKET)

    try:
        await coresys.setup()
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.critical("CoreSys setup failed: %s", err)
        return 1
    # =========================================================================
    # STARTUP MARKER LOGIC
    # =========================================================================
    # The API is up and CoreSys is set up! The boot was completely successful.
    if STARTUP_MARKER.exists():
        try:
            STARTUP_MARKER.unlink()
            _LOGGER.info("Supervisor startup marker file removed successfully")
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Could not remove supervisor startup marker file: %s", err)
    # =========================================================================

    # Block until shutdown signal or internal exit request
    await stop_event.wait()

    _LOGGER.info("Shutting down supervisor")
    await runner.cleanup()
    await coresys.teardown()

    if SUPERVISOR_SOCKET.exists():
        SUPERVISOR_SOCKET.unlink()

    _LOGGER.info("Supervisor shutdown complete with code %s", coresys.exit_code)

    return coresys.exit_code


def _build_app(coresys: CoreSys) -> web.Application:
    """Build the aiohttp application and register all routes."""
    app = web.Application()
    app["coresys"] = coresys

    setup_container_routes(app)
    setup_system_routes(app)
    setup_audio_routes(app)
    setup_update_routes(app)
    setup_network_routes(app)

    return app
