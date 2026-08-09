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
from .const import (
    SUPERVISOR_SOCKET,
    STARTUP_MARKER,
    FIRSTBOOT_DONE,
    FIRSTBOOT_PROGRESS_FILE,
)
from .temppage import _FIRSTBOOT_HTML
FIRSTBOOT_PORT = 8080

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

    tcp_site = None
    if not FIRSTBOOT_DONE.exists():
        tcp_site = web.TCPSite(runner, host="0.0.0.0", port=FIRSTBOOT_PORT)
        await tcp_site.start()
        _LOGGER.info("First-boot page listening on :%s", FIRSTBOOT_PORT)

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

    if tcp_site is not None:
        await tcp_site.stop()
        _LOGGER.info("First-boot page closed on :%s", FIRSTBOOT_PORT)

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

    app.router.add_get("/firstboot", _firstboot_page)
    app.router.add_get("/firstboot/status", _firstboot_status)

    return app


async def _firstboot_page(request: web.Request) -> web.Response:
    """Bare first-boot page — only served while FIRSTBOOT_DONE does not exist.

    Once coresys.setup() has started all managed containers, FIRSTBOOT_DONE
    is created and this just tells the browser to go to the real portal.
    """
    if FIRSTBOOT_DONE.exists():
        return web.Response(
            text='<meta http-equiv="refresh" content="0; url=http://'
            f'{request.host.split(":")[0]}:8000">',
            content_type="text/html",
        )
    return web.Response(text=_FIRSTBOOT_HTML, content_type="text/html")


async def _firstboot_status(request: web.Request) -> web.Response:
    """Plain JSON snapshot of the current pull, read from a single file.

    The file content is "<container_name>-<percent>", written by whichever
    container's load() is currently pulling — only one pulls at a time
    since CONTAINER_START_ORDER runs sequentially.
    """
    name, pct = None, 0
    if FIRSTBOOT_PROGRESS_FILE.exists():
        try:
            raw = FIRSTBOOT_PROGRESS_FILE.read_text().strip()
            name, pct_str = raw.rsplit("-", 1)
            pct = int(pct_str)
        except (ValueError, OSError):
            pass

    return web.json_response(
        {"in_progress": not FIRSTBOOT_DONE.exists(), "name": name, "pull_percent": pct}
    )