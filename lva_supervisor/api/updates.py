"""Updates API routes.

GET  /updates                → compare local OCI version label vs lva-version manifest (semver)
POST /updates/update         → pull latest image for a component
GET  /updates/versions       → current local versions of all components
GET  /updates/os             → OS update info from lva-version manifest
"""

import logging

import aiohttp
from aiohttp import web

from ..const import MANAGED_CONTAINERS
from ..coresys import CoreSys
from ..exceptions import DockerError
from ..utils.updates import fetch_manifest, get_local_version, is_update_available

from typing import Any

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


def _get_coresys(request: web.Request) -> CoreSys:
    return request.app["coresys"]


def _err_response(err: Exception, status: int = 500) -> web.Response:
    return web.json_response({"error": str(err)}, status=status)


# =============================================================================
# Routes
# =============================================================================


@routes.get("/updates")
async def check_updates(request: web.Request) -> web.Response:
    """Compare local image version labels vs lva-version manifest using semver."""
    coresys = _get_coresys(request)

    async with aiohttp.ClientSession() as session:
        manifest = await fetch_manifest(session)

    results: list[dict[str, object]] = []
    for name in MANAGED_CONTAINERS:
        container = coresys.containers[name]
        local_ver = await get_local_version(coresys, container.image)
        remote_ver = manifest.get(name) if manifest else None

        results.append(
            {
                "name": name,
                "image": container.image,
                "update_available": is_update_available(local_ver, remote_ver),
                "local_version": local_ver,
                "remote_version": remote_ver,
            }
        )

    return web.json_response(results)


@routes.post("/updates/update")
async def update_component(request: web.Request) -> web.Response:
    """Pull latest image for a specific component.

    Body: { "name": "lva-audio" }
    """
    coresys = _get_coresys(request)

    try:
        body = await request.json()
    except Exception:  # pylint: disable=broad-exception-caught
        return _err_response(ValueError("Invalid or missing JSON body"), 400)

    name = body.get("name", "").strip()
    if not name:
        return _err_response(ValueError("'name' is required"), 400)
    if name not in MANAGED_CONTAINERS:
        return _err_response(ValueError(f"Unknown component '{name}'"), 404)

    try:
        container = coresys.containers[name]
        await container.update()
        return web.json_response(
            {
                "result": "ok",
                "message": f"{name} updated successfully.",
            }
        )
    except DockerError as err:
        return _err_response(err, 500)


@routes.get("/updates/versions")
async def get_versions(request: web.Request) -> web.Response:
    """Return current local OCI version labels for all components."""
    coresys = _get_coresys(request)
    versions: list[dict[str, object]] = []

    for name in MANAGED_CONTAINERS:
        container = coresys.containers[name]
        version = await get_local_version(coresys, container.image)
        versions.append(
            {
                "name": name,
                "image": container.image,
                "version": version,
            }
        )

    return web.json_response(versions)


# =============================================================================
# OS Update
# =============================================================================


@routes.get("/updates/os")
async def check_os_update(request: web.Request) -> web.Response:
    """Check lva-version manifest for the latest lva-os bundle."""
    from ..const import MACHINE

    coresys = _get_coresys(request)

    async with aiohttp.ClientSession() as session:
        manifest: dict[str, str] | None = await fetch_manifest(session)

    if not manifest:
        return web.json_response(
            {"error": "Could not fetch version manifest"}, status=503
        )

    os_versions: dict[str, Any] = (
        data if isinstance(data := manifest.get("lva-os"), dict) else {}
    )
    remote_ver: str | None = os_versions.get(MACHINE)
    ota_template: str = manifest.get("ota", "")

    bundle_url = None
    if remote_ver and ota_template:
        bundle_url = ota_template.replace("{version}", remote_ver).replace(
            "{board}", MACHINE
        )

    # Read current OS version from hostname1 D-Bus (OperatingSystemPrettyName)
    current_version: str | None = None
    try:
        current_version = await coresys.hostname.get_os_version()
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not read OS version from hostname1: %s", err)

    return web.json_response(
        {
            "tag": remote_ver or "unknown",
            "bundle_url": bundle_url,
            "machine": MACHINE,
            "current_version": current_version,
            "update_available": is_update_available(current_version, remote_ver),
            "notes": "",
            "url": (
                f"https://github.com/aryanhasgithub/lva-os/releases/tag/{remote_ver}"
                if remote_ver
                else ""
            ),
        }
    )


# =============================================================================
# Registration
# =============================================================================


def setup_routes(app: web.Application) -> None:
    """Register API routes."""
    app.add_routes(routes)