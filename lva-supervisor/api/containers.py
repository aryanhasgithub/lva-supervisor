"""Container API routes.

GET  /containers                    → list all containers + status
POST /containers/{name}/start       → start a container
POST /containers/{name}/stop        → stop a container
POST /containers/{name}/restart     → restart a container
POST /containers/{name}/update      → pull latest image
GET  /containers/{name}/stats       → cpu/memory stats
GET  /containers/{name}/logs        → last N log lines
"""
import logging

from aiohttp import web

from ..const import MANAGED_CONTAINERS
from ..exceptions import (
    APINotFound,
    APIError,
    DockerContainerNotFound,
    DockerError,
)
from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


def _get_coresys(request: web.Request) -> CoreSys:
    return request.app["coresys"]


def _get_container(request: web.Request):
    """Get container by name from URL, raise 404 if not managed."""
    coresys = _get_coresys(request)
    name = request.match_info["name"]
    if name not in MANAGED_CONTAINERS:
        raise APINotFound(f"Container '{name}' is not managed by lva-supervisor")
    return coresys.containers[name]


def _err_response(err: Exception, status: int = 500) -> web.Response:
    return web.json_response({"error": str(err)}, status=status)


# =============================================================================
# Routes
# =============================================================================

@routes.get("/containers")
async def list_containers(request: web.Request) -> web.Response:
    """List all managed containers with their current state."""
    coresys = _get_coresys(request)
    result : list[dict[str, str]] = []
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
        except DockerError as err:
            _LOGGER.error("Failed to get state for [%s]: %s", name, err)
            state = "unknown"

        result.append({
            "name": name,
            "image": container.image,
            "state": state,
        })

    return web.json_response(result)


@routes.post("/containers/{name}/start")
async def start_container(request: web.Request) -> web.Response:
    """Start a container."""
    try:
        container = _get_container(request)
        await container.start()
        return web.json_response({"result": "ok"})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerContainerNotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.post("/containers/{name}/stop")
async def stop_container(request: web.Request) -> web.Response:
    """Stop a container."""
    try:
        container = _get_container(request)
        await container.stop()
        return web.json_response({"result": "ok"})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.post("/containers/{name}/restart")
async def restart_container(request: web.Request) -> web.Response:
    """Restart a container."""
    try:
        container = _get_container(request)
        await container.restart()
        return web.json_response({"result": "ok"})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerContainerNotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.post("/containers/{name}/update")
async def update_container(request: web.Request) -> web.Response:
    """Pull latest image for a container."""
    try:
        container = _get_container(request)
        await container.update()
        return web.json_response({"result": "ok"})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.get("/containers/{name}/stats")
async def container_stats(request: web.Request) -> web.Response:
    """Get cpu/memory stats for a container."""
    try:
        container = _get_container(request)
        stats = await container.stats()
        return web.json_response(stats)
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerContainerNotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.get("/containers/{name}/logs")
async def container_logs(request: web.Request) -> web.Response:
    """Get recent log lines for a container.

    Query param: tail (int, default 100)
    """
    try:
        container = _get_container(request)
        tail = int(request.query.get("tail", "100"))
        lines = await container.logs(tail=tail)
        return web.json_response({"logs": lines})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerContainerNotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)
    except ValueError:
        return _err_response(APIError("'tail' must be an integer"), 400)


# =============================================================================
# Registration
# =============================================================================

def setup_routes(app: web.Application) -> None:
    """Register container routes on the aiohttp app."""
    app.add_routes(routes)