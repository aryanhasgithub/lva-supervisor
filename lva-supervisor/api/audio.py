import logging

import aiohttp
from aiohttp import web

_LOGGER = logging.getLogger(__name__)

# Unix socket exposed by lva-audio container agent
AUDIO_AGENT_SOCK = "/run/lva/audio/agent.sock"
AUDIO_AGENT_URL  = "http://localhost/devices"

routes = web.RouteTableDef()


async def _query_audio_agent() -> dict[str, list[dict[str, str]]]:
    """Connect to lva-audio agent over unix socket and get device list."""
    connector = aiohttp.UnixConnector(path=AUDIO_AGENT_SOCK)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(AUDIO_AGENT_URL) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Audio agent returned status {resp.status}"
                )
            return await resp.json()


@routes.get("/audio/devices")
async def get_audio_devices(request: web.Request) -> web.Response: # pylint: disable=unused-argument
    """Return available input and output audio devices from lva-audio."""
    try:
        data = await _query_audio_agent()
        return web.json_response(data)
    except FileNotFoundError:
        return web.json_response(
            {"error": "lva-audio agent socket not found — is lva-audio running?"},
            status=503,
        )
    except Exception as err: # pylint: disable=broad-exception-caught
        _LOGGER.error("Failed to query audio agent: %s", err)
        return web.json_response({"error": str(err)}, status=500)


def setup_routes(app: web.Application) -> None:
    """Add audio routes to the application."""
    app.add_routes(routes)