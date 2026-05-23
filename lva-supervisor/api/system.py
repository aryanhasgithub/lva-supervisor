"""System API routes.

POST /system/reboot       → reboot host via os-agent
POST /system/poweroff     → poweroff host via os-agent
POST /system/os-update    → trigger RAUC OTA update
GET  /system/info         → OS version, hardware info, docker info
GET  /system/health       → supervisor alive + all container states
"""
import logging

import json

from aiohttp import web

from ..coresys import CoreSys
from ..const import MANAGED_CONTAINERS
from ..exceptions import DBusConnectionError, DBusMethodError, DockerError

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


def _get_coresys(request: web.Request) -> CoreSys:
    return request.app["coresys"]


def _err_response(err: Exception, status: int = 500) -> web.Response:
    return web.json_response({"error": str(err)}, status=status)


# =============================================================================
# Routes
# =============================================================================

@routes.post("/system/reboot")
async def system_reboot(request: web.Request) -> web.Response:
    """Reboot the host system via os-agent."""
    coresys = _get_coresys(request)
    try:
        await coresys.agent.reboot()
        return web.json_response({"result": "ok"})
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)


@routes.post("/system/poweroff")
async def system_poweroff(request: web.Request) -> web.Response:
    """Power off the host system via os-agent."""
    coresys = _get_coresys(request)
    try:
        await coresys.agent.poweroff()
        return web.json_response({"result": "ok"})
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)


@routes.post("/system/os-update")
async def system_os_update(request: web.Request) -> web.Response:
    """Trigger a RAUC OTA update.

    Body: { "bundle_url": "https://..." }
    Blocks until RAUC completes (or times out after 10 minutes).
    After success the system needs a reboot to boot the new slot.
    """
    coresys = _get_coresys(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception): # pylint: disable=broad-exception-caught
        return _err_response(ValueError("Invalid or missing JSON body"), 400)

    try:
        bundle_url = body.get("bundle_url", "").strip()
        if not bundle_url:
            return _err_response(ValueError("'bundle_url' is required"), 400)
        await coresys.rauc.install(bundle_url)
        return web.json_response({
            "result": "ok",
            "message": "Update installed. Reboot to apply.",
        })
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)
    except Exception as err: # pylint: disable=broad-exception-caught
        return _err_response(err, 500)


@routes.get("/system/info")
async def system_info(request: web.Request) -> web.Response:
    """Return OS version, hardware info, and Docker info."""
    coresys = _get_coresys(request)
    info: dict[str, object] = {}

    # OS version from os-agent
    try:
        info["os_version"] = await coresys.agent.get_os_version()
    except Exception as err: # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not get OS version: %s", err)
        info["os_version"] = None

    # Hardware info from os-agent
    try:
        info["hardware"] = await coresys.agent.get_hardware_info()
    except Exception as err: # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not get hardware info: %s", err)
        info["hardware"] = None

    # RAUC slot status
    try:
        info["slots"] = await coresys.rauc.get_slot_status()
        info["booted_slot"] = await coresys.rauc.get_booted_slot()
    except Exception as err: # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not get RAUC info: %s", err)
        info["slots"] = None
        info["booted_slot"] = None

    # Docker daemon info
    try:
        docker_info = await coresys.docker.info()
        info["docker"] = {
            "version":    docker_info.get("ServerVersion"),
            "containers": docker_info.get("Containers"),
            "images":     docker_info.get("Images"),
        }
    except Exception as err: # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not get Docker info: %s", err)
        info["docker"] = None

    return web.json_response(info)


@routes.get("/system/health")
async def system_health(request: web.Request) -> web.Response:
    """Return supervisor health — docker daemon + all container states."""
    coresys = _get_coresys(request)

    docker_healthy = await coresys.docker.is_healthy()

    containers : dict[str, str] = {}
    for name in MANAGED_CONTAINERS:
        container = coresys.containers[name]
        try:
            if not await container.exists():
                state = "not_found"
            elif await container.is_running():
                state = "running"
            elif await container.is_failed():
                state = "failed"
            else:
                state = "stopped"
        except DockerError:
            state = "unknown"
        containers[name] = state

    all_running = all(s == "running" for s in containers.values())

    return web.json_response({
        "supervisor":     "ok",
        "docker_healthy": docker_healthy,
        "containers":     containers,
        "healthy":        docker_healthy and all_running,
    })


# =============================================================================
# Registration
# =============================================================================

def setup_routes(app: web.Application) -> None:
    """Add system routes to the application."""
    app.add_routes(routes)