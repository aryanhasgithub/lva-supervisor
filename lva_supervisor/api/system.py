"""System API routes.

POST /system/reboot       → reboot host via os-agent
POST /system/poweroff     → poweroff host via os-agent
POST /system/os-update    → trigger RAUC OTA update
GET  /system/info         → OS version, hardware info, docker info
GET  /system/health       → supervisor alive + all container states
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path

import json

import aiohttp
from aiohttp import web

from ..coresys import CoreSys
from ..const import MANAGED_CONTAINERS, SUPERVISOR_DATA
from ..exceptions import DBusConnectionError, DBusMethodError, DockerError
from ..utils.updates import strip_v

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()

# RAUC runs on the host and, without HTTP streaming support, would buffer the
# whole bundle in /tmp (a 16 MB zram device). Like the HA Supervisor, download
# the bundle ourselves onto the data partition and hand RAUC a local file.
#
# OTA_TMP_DIR      - where this process writes the file
# OTA_TMP_DIR_HOST - the same directory as the host (RAUC) sees it. Identical
#                    unless the supervisor container maps it elsewhere.
# The OS start script mounts host /mnt/data/lva-supervisor at /data in this
# container (-v ...:/data:rw,slave), so the same directory is /data/tmp here
# and /mnt/data/lva-supervisor/tmp on the host, where RAUC opens it.
OTA_TMP_DIR = Path(os.environ.get("LVA_OTA_TMP_DIR", str(SUPERVISOR_DATA / "tmp")))
OTA_TMP_DIR_HOST = Path(
    os.environ.get("LVA_OTA_TMP_DIR_HOST", "/mnt/data/lva-supervisor/tmp")
)
_OTA_PREFIX = "os-update-"
_OTA_CHUNK = 1_048_576
_OTA_TIMEOUT = aiohttp.ClientTimeout(total=60 * 60, connect=180)
# Only one OS update at a time (download + install can take many minutes).
_OTA_LOCK = asyncio.Lock()


def _prepare_ota_dir() -> None:
    OTA_TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Remove leftovers from an interrupted earlier update.
    for stale in OTA_TMP_DIR.glob(f"{_OTA_PREFIX}*.raucb"):
        stale.unlink(missing_ok=True)


async def _download_bundle(url: str, dest: Path) -> None:
    """Stream a bundle to dest, raising on HTTP errors or short downloads."""
    async with aiohttp.ClientSession(timeout=_OTA_TIMEOUT) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Bundle server returned HTTP {resp.status}")
            expected = resp.content_length
            received = 0
            fh = await asyncio.to_thread(dest.open, "wb")
            try:
                async for chunk in resp.content.iter_chunked(_OTA_CHUNK):
                    await asyncio.to_thread(fh.write, chunk)
                    received += len(chunk)
            finally:
                await asyncio.to_thread(fh.close)
    if expected is not None and received != expected:
        raise RuntimeError(f"Incomplete download: got {received} of {expected} bytes")
    _LOGGER.info("Downloaded OTA bundle to %s (%d bytes)", dest, received)


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
        await coresys.logind.reboot()
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
        await coresys.logind.power_off()
        return web.json_response({"result": "ok"})
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)


@routes.post("/system/os-update")
async def system_os_update(request: web.Request) -> web.Response:
    """Trigger a RAUC OTA update.

    Body: { "bundle_url": "https://...", "version": "0.3" (optional) }
    If "version" matches the running OS it is rejected (409), as is a second
    concurrent update or a busy RAUC.
    Blocks until RAUC completes (or times out after 10 minutes).
    After success the system needs a reboot to boot the new slot.
    """
    coresys = _get_coresys(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):  # pylint: disable=broad-exception-caught
        return _err_response(ValueError("Invalid or missing JSON body"), 400)

    try:
        bundle_url = body.get("bundle_url", "").strip()
        if not bundle_url:
            return _err_response(ValueError("'bundle_url' is required"), 400)

        # Guards (same intent as the HA Supervisor's checks). The lock is taken
        # before any await so two concurrent requests can't both pass.
        if _OTA_LOCK.locked():
            return _err_response(RuntimeError("An OS update is already in progress"), 409)

        async with _OTA_LOCK:
            version = str(body.get("version") or "").strip()
            if version:
                current = await coresys.hostname.get_os_version()
                if strip_v(current) == strip_v(version):
                    return _err_response(
                        RuntimeError(f"Version {version} is already installed"), 409
                    )

            if await coresys.rauc.get_operation() != "idle":
                return _err_response(
                    RuntimeError("RAUC is busy with another operation"), 409
                )

            name = f"{_OTA_PREFIX}{uuid.uuid4().hex}.raucb"
            local_file = OTA_TMP_DIR / name
            host_file = OTA_TMP_DIR_HOST / name
            try:
                await asyncio.to_thread(_prepare_ota_dir)
                await _download_bundle(bundle_url, local_file)
                await coresys.rauc.install(str(host_file))
            finally:
                await asyncio.to_thread(local_file.unlink, missing_ok=True)

        return web.json_response(
            {
                "result": "ok",
                "message": "Update installed. Reboot to apply.",
            }
        )
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return _err_response(RuntimeError(f"Bundle download failed: {err}"), 502)
    except Exception as err:  # pylint: disable=broad-exception-caught
        return _err_response(err, 500)


@routes.get("/system/health")
async def system_health(request: web.Request) -> web.Response:
    """Return supervisor health — docker daemon + all container states."""
    coresys = _get_coresys(request)

    docker_healthy = await coresys.docker.is_healthy()

    containers: dict[str, str] = {}
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

    return web.json_response(
        {
            "supervisor": "ok",
            "docker_healthy": docker_healthy,
            "containers": containers,
            "healthy": docker_healthy and all_running,
        }
    )


# =============================================================================
# Registration
# =============================================================================


def setup_routes(app: web.Application) -> None:
    """Add system routes to the application."""
    app.add_routes(routes)
