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

import datetime
import json
from aiohttp import web
import asyncio

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
    result: list[dict[str, str | int | float]] = []
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

        result.append(
            {
                "name": name,
                "image": container.image,
                "state": state,
            }
        )

    return web.json_response(result)


@routes.post("/containers/{name}/start")
async def start_container(request: web.Request) -> web.Response:
    """Start a container."""
    try:
        container = _get_container(request)
        await container.start()

        if not await container.wait_until_running(timeout=30):
            return _err_response(
                DockerError("Container failed to come up after start"), 500
            )

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

        if not await container.wait_until_running(timeout=30):
            return _err_response(
                DockerError("Container failed to come back up after restart"), 500
            )

        return web.json_response({"result": "ok"})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerContainerNotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.get("/containers/{name}/state")
async def container_state(request: web.Request) -> web.Response:
    """Get state for a container"""
    try:
        container = _get_container(request)
        status = await container.state()
        return web.json_response({"state": _map_state(status)})
    except APINotFound as err:
        return _err_response(err, 404)
    except DockerError as err:
        return _err_response(err, 500)


@routes.get("/containers/{name}/update/stream")
async def update_stream(request: web.Request) -> web.StreamResponse:
    """SSE stream for a container image update.

    Calls container.update(progress=...) and streams each step to the
    client as a JSON dict, e.g.:
      {"status": "Pulling new image for lva...", "type": "log"}
      {"status": "Downloading", "pull_percent": 42, "type": "log"}
      {"status": "lva updated and started successfully.", "type": "success"}

    On error, emits {"type": "error", "status": "..."} and closes.
    """
    coresys = _get_coresys(request)
    name = request.match_info["name"]

    if name not in MANAGED_CONTAINERS:
        return web.json_response({"error": f"Unknown container '{name}'"}, status=404)

    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await resp.prepare(request)

    def _sse(event: dict) -> bytes:
        return f"data: {json.dumps(event)}\n\n".encode()

    # Queue bridges the async callback from ContainerBase.update() to this
    # streaming response — progress() puts dicts in, the loop below drains them.
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def progress(event: dict) -> None:
        await queue.put(event)

    async def _run_update() -> None:
        try:
            container = coresys.containers[name]
            await container.update(progress=progress)
        except Exception as err:  # pylint: disable=broad-exception-caught
            await queue.put({"__error__": str(err)})
        finally:
            await queue.put(None)  # sentinel — tells the drain loop to stop

    update_task = asyncio.ensure_future(_run_update())

    try:
        while True:
            event = await queue.get()
            if event is None:
                # Update finished — send final success marker and close
                await resp.write(
                    _sse({"type": "success", "status": f"{name} update complete."})
                )
                break
            if "__error__" in event:
                await resp.write(
                    _sse({"type": "error", "status": event["__error__"]})
                )
                break
            await resp.write(_sse({**event, "type": "log"}))
    finally:
        await resp.write_eof()

    return resp


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


@routes.get("/containers/{name}/logs/stream")
async def container_logs_stream(request: web.Request) -> web.StreamResponse:
    """Stream logs from a container as SSE.

    Query param: tail (int, default 100) — how many historical lines to include.
    Client receives: data: {"time": "...", "message": "..."}
    """
    try:
        container = _get_container(request)
        tail = int(request.query.get("tail", "100"))
    except APINotFound as err:
        return _err_response(err, 404)
    except ValueError:
        return _err_response(APIError("'tail' must be an integer"), 400)

    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)

    try:
        async for line in container.stream_logs(tail=tail):
            if not line:
                continue
            event = json.dumps(
                {
                    "time": datetime.datetime.now().isoformat(),
                    "message": line,
                }
            )
            await response.write(f"data: {event}\n\n".encode())
    except DockerContainerNotFound:
        pass
    except DockerError as err:
        _LOGGER.error("Log stream error: %s", err)
    except (ConnectionResetError, OSError):
        # Client disconnected
        pass
    finally:
        await response.write_eof()

    return response


# Helpers
def _map_state(status: str) -> str:
    return {
        "running": "Running",
        "restarting": "Restarting",
        "exited": "Stopped",
        "created": "Stopped",
        "paused": "Stopped",
        "dead": "Error",
        "not_found": "Stopped",
    }.get(status, "Error")


# =============================================================================
# Registration
# =============================================================================


def setup_routes(app: web.Application) -> None:
    """Register container routes on the aiohttp app."""
    app.add_routes(routes)