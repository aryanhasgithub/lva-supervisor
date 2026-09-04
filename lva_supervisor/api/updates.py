"""Updates API routes.

GET  /updates                → compare local OCI version label vs lva-version manifest (semver)
POST /updates/update         → pull latest image for a component
GET  /updates/versions       → current local versions of all components
GET  /updates/os             → OS update info from lva-version manifest
"""

import asyncio
import logging

import aiohttp
from aiohttp import web

from ..const import CONTAINER_SUPERVISOR, MANAGED_CONTAINERS, RELEASE_URL
from ..coresys import CoreSys
from ..exceptions import DockerError
from ..utils.updates import (
    fetch_manifest,
    get_local_version,
    get_manifest_component,
    is_update_available,
    is_version_satisfied,
)

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
    """Compare local image version labels vs lva-version manifest using semver.

    Also checks each component's "requires" block (if present) against the
    other managed containers' *current local* versions — since updates only
    ever move a component to the manifest's latest, a component whose
    requirements aren't met by what's currently running should be blocked
    from updating until its dependencies are updated first.
    """
    coresys = _get_coresys(request)

    async with aiohttp.ClientSession() as session:
        manifest = await fetch_manifest(session)

    # Local versions for every managed container, gathered up front so the
    # requires-check below doesn't need to re-inspect images per dependency.
    # "lva-os" is included here too (read from hostname1, not a container
    # image label) since components can depend on a minimum OS version —
    # e.g. lva-supervisor requiring lva-os >= 0.1 because it needs a D-Bus
    # interface only present from that OS release onward.
    local_versions: dict[str, str | None] = {}
    for name in MANAGED_CONTAINERS:
        container = coresys.containers[name]
        local_versions[name] = await get_local_version(coresys, container.image)
    try:
        local_versions["lva-os"] = await coresys.hostname.get_os_version()
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not read OS version from hostname1: %s", err)
        local_versions["lva-os"] = None

    results: list[dict[str, object]] = []
    for name in MANAGED_CONTAINERS:
        container = coresys.containers[name]
        local_ver = local_versions[name]
        component = get_manifest_component(manifest, name)
        remote_ver = component.get("version")
        requires: dict[str, str] = component.get("requires") or {}

        unmet: dict[str, dict[str, str | None]] = {}
        for dep_name, dep_min in requires.items():
            dep_local = local_versions.get(dep_name)
            if not is_version_satisfied(dep_local, dep_min):
                unmet[dep_name] = {"current": dep_local, "required": dep_min}

        results.append(
            {
                "name": name,
                "image": container.image,
                "update_available": is_update_available(local_ver, remote_ver),
                "local_version": local_ver,
                "remote_version": remote_ver,
                "requires": requires,
                "requirements_met": not unmet,
                "unmet_requirements": unmet,
            }
        )

    return web.json_response(results)


@routes.post("/updates/update")
async def update_component(request: web.Request) -> web.Response:
    """Pull latest image for a specific component.

    Body: { "name": "lva-audio" }

    Refuses to update if the component's manifest "requires" block isn't
    satisfied by the currently running versions of its dependencies.
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

    async with aiohttp.ClientSession() as session:
        manifest = await fetch_manifest(session)
    component = get_manifest_component(manifest, name)
    requires: dict[str, str] = component.get("requires") or {}

    if requires:
        unmet: dict[str, dict[str, str | None]] = {}
        for dep_name, dep_min in requires.items():
            if dep_name == "lva-os":
                # OS version comes from hostname1, not a container image.
                try:
                    dep_local = await coresys.hostname.get_os_version()
                except Exception as err:  # pylint: disable=broad-exception-caught
                    _LOGGER.warning("Could not read OS version from hostname1: %s", err)
                    dep_local = None
            else:
                dep_container = coresys.containers.get(dep_name)
                dep_local = (
                    await get_local_version(coresys, dep_container.image)
                    if dep_container
                    else None
                )
            if not is_version_satisfied(dep_local, dep_min):
                unmet[dep_name] = {"current": dep_local, "required": dep_min}
        if unmet:
            return _err_response(
                ValueError(
                    f"'{name}' requires updated dependencies first: {unmet}"
                ),
                409,
            )

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

@routes.post("/updates/supervisor/update")
async def update_supervisor(request: web.Request) -> web.Response:
    """Trigger a supervisor self-update.

    Unlike the generic /updates/update route, this does NOT wait for
    container.update() to return "successfully" — it can't. Supervisor.update()
    pulls the new image and then calls exit_system(code=100), which tears the
    process down so systemd/the host script can stop, remove, and recreate the
    container from outside. The HTTP connection serving this request dies with
    the process partway through.

    So: kick off update() as a background task rather than awaiting it inline,
    and return immediately once the pull has *started*. The UI should treat a
    dropped connection after this call as the expected path, then poll
    GET /updates/versions to confirm when the new version has actually landed.
    """
    coresys = _get_coresys(request)
    supervisor = coresys.containers[CONTAINER_SUPERVISOR]

    if supervisor.is_updating():
        return _err_response(
            RuntimeError("Supervisor update already in progress"), 409
        )

    async def _run_update() -> None:
        try:
            await supervisor.update()
        except DockerError as err:
            # We're very unlikely to still be alive to log this by the time
            # exit_system() fires, but if the pull itself fails we never get
            # that far, so this branch does get hit.
            _LOGGER.error("[%s] Self-update failed: %s", CONTAINER_SUPERVISOR, err)

    # Fire-and-forget: don't await this. Awaiting it inline means this handler
    # (and its response) dies with the process before it can reply.
    asyncio.create_task(_run_update())

    return web.json_response(
        {
            "result": "started",
            "message": (
                "Supervisor update started. The connection will drop when "
                "the update completes — this is expected. Poll "
                "/updates/versions to confirm."
            ),
        },
        status=202,
    )

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
        manifest: dict[str, Any] | None = await fetch_manifest(session)

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
                f"{RELEASE_URL}{remote_ver}"
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