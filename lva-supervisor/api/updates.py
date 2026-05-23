"""Updates API routes.

GET  /updates                → check GHCR for latest tags vs running images
POST /updates/update         → pull latest image for a component
GET  /updates/versions       → current running versions of all components
"""

import logging

import aiohttp
from aiohttp import web

from ..const import MANAGED_CONTAINERS
from ..coresys import CoreSys
from ..exceptions import DockerError

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()

# GHCR Docker Registry API v2 — works for public images without auth
GHCR_REGISTRY = "https://ghcr.io"


def _get_coresys(request: web.Request) -> CoreSys:
    return request.app["coresys"]


def _err_response(err: Exception, status: int = 500) -> web.Response:
    return web.json_response({"error": str(err)}, status=status)


async def _get_ghcr_token(session: aiohttp.ClientSession, name: str) -> str | None:
    """Get an anonymous pull token from GHCR for a given image name.

    GHCR requires token auth even for public images.
    Returns token string or None if auth fails.
    """
    url = f"https://ghcr.io/token?scope=repository:{name}:pull&service=ghcr.io"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("token")
            return None
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not get GHCR token for %s: %s", name, err)
        return None


async def _get_remote_digest(session: aiohttp.ClientSession, image: str) -> str | None:
    """Get the digest of the latest image tag from GHCR.

    Uses Docker Registry API v2 manifest list endpoint.
    Requests manifest list (not platform-specific manifest) for a stable
    digest that matches across amd64 and arm64.
    Returns digest string or None if unreachable.
    """
    # image format: ghcr.io/aryanhasgithub/lva → name = aryanhasgithub/lva
    name = image.removeprefix("ghcr.io/")
    token = await _get_ghcr_token(session, name)
    if not token:
        _LOGGER.warning("Could not get GHCR token for %s", image)
        return None

    url = f"{GHCR_REGISTRY}/v2/{name}/manifests/latest"
    headers = {
        "Authorization": f"Bearer {token}",
        # Request manifest list for stable multi-arch digest
        "Accept": "application/vnd.docker.distribution.manifest.list.v2+json",
    }
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return resp.headers.get("Docker-Content-Digest")
            _LOGGER.debug("GHCR manifest check returned %d for %s", resp.status, image)
            return None
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not reach GHCR for %s: %s", image, err)
        return None


async def _get_local_digest(coresys: CoreSys, image: str) -> str | None:
    """Get the digest of the locally pulled image via aiodocker."""
    try:
        info = await coresys.docker.images.get(image)
        digests = info.get("RepoDigests", [])
        if digests:
            # RepoDigests format: "ghcr.io/aryanhasgithub/lva@sha256:..."
            return digests[0].split("@")[-1]
        return None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


# =============================================================================
# Routes
# =============================================================================


@routes.get("/updates")
async def check_updates(request: web.Request) -> web.Response:
    """Check GHCR for updates — compare local image digests vs remote."""
    coresys = _get_coresys(request)
    results: list[dict[str, str | None | bool]] = []

    async with aiohttp.ClientSession() as session:
        for name in MANAGED_CONTAINERS:
            container = coresys.containers[name]
            image = container.image
            local_dig = await _get_local_digest(coresys, image)
            remote_dig = await _get_remote_digest(session, image)

            if local_dig and remote_dig:
                update_available = local_dig != remote_dig
            else:
                # Can't determine — assume up to date
                update_available = False

            results.append(
                {
                    "name": name,
                    "image": image,
                    "update_available": update_available,
                    "local_digest": local_dig,
                    "remote_digest": remote_dig,
                }
            )

    return web.json_response(results)


@routes.post("/updates/update")
async def update_component(request: web.Request) -> web.Response:
    """Pull latest image for a specific component.

    Body: { "name": "lva" }
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
                "message": f"{name} image updated. Restart to apply.",
            }
        )
    except DockerError as err:
        return _err_response(err, 500)


@routes.get("/updates/versions")
async def get_versions(request: web.Request) -> web.Response:
    """Return current local image digests for all components."""
    coresys = _get_coresys(request)
    versions: list[dict[str, str | None]] = []

    for name in MANAGED_CONTAINERS:
        container = coresys.containers[name]
        image = container.image
        digest = await _get_local_digest(coresys, image)
        versions.append(
            {
                "name": name,
                "image": image,
                "digest": digest,
            }
        )

    return web.json_response(versions)


# =============================================================================
# Registration
# =============================================================================


def setup_routes(app: web.Application) -> None:
    """Add update routes to the application."""
    app.add_routes(routes)
