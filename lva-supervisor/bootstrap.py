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
from .const import SUPERVISOR_SOCKET

_LOGGER = logging.getLogger(__name__)


async def run_supervisor() -> None:
    """Main entry point which sets up and runs the supervisor until shutdown."""

    coresys = CoreSys()
    app = _build_app(coresys)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        _LOGGER.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    # Setup all components
    try:
        await coresys.setup()
    except Exception as err:
        _LOGGER.critical("CoreSys setup failed: %s", err)
        raise

    # handle_signals=False we manage signals ourselves above
    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()

    # Ensure socket directory exists
    SUPERVISOR_SOCKET.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale socket from a previous crash
    if SUPERVISOR_SOCKET.exists():
        SUPERVISOR_SOCKET.unlink()
        _LOGGER.debug("Removed stale socket at %s", SUPERVISOR_SOCKET)

    site = web.UnixSite(runner, path=str(SUPERVISOR_SOCKET))
    await site.start()
    _LOGGER.info("Supervisor API listening on %s", SUPERVISOR_SOCKET)

    # Block until shutdown signal
    await stop_event.wait()

    # Clean shutdown which coresys and runner stop serving before disconnecting components
    _LOGGER.info("Shutting down supervisor")
    await runner.cleanup()
    await coresys.teardown()

    # Clean up socket file on exit
    if SUPERVISOR_SOCKET.exists():
        SUPERVISOR_SOCKET.unlink()

    _LOGGER.info("Supervisor shutdown complete")


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
