"""Network API routes.

GET  /network/info              → IP, interface, MAC, connection type
GET  /network/interfaces        → list all network interfaces
POST /network/hostname          → set system hostname
POST /network/ip                → set static IP or switch to DHCP
"""

import logging

from aiohttp import web

from ..coresys import CoreSys
from ..exceptions import DBusConnectionError, DBusMethodError

_LOGGER = logging.getLogger(__name__)

routes = web.RouteTableDef()


def _get_coresys(request: web.Request) -> CoreSys:
    return request.app["coresys"]


def _err_response(err: Exception, status: int = 500) -> web.Response:
    return web.json_response({"error": str(err)}, status=status)


# =============================================================================
# Routes
# =============================================================================


@routes.get("/network/info")
async def network_info(request: web.Request) -> web.Response:
    """Return all network devices with state and IP info."""
    coresys = _get_coresys(request)
    try:
        devices = await coresys.network.get_devices()
        return web.json_response(devices)
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)


@routes.get("/network/interfaces")
async def network_interfaces(request: web.Request) -> web.Response:
    """Return just interface names and their states."""
    coresys = _get_coresys(request)
    try:
        devices = await coresys.network.get_devices()
        interfaces = [
            {
                "interface": d["interface"],
                "state": d["state"],
                "type": d["type"],
            }
            for d in devices
        ]
        return web.json_response(interfaces)
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)


@routes.post("/network/hostname")
async def set_hostname(request: web.Request) -> web.Response:
    """Set the system hostname.

    Body: { "hostname": "lva-living-room" }
    """
    coresys = _get_coresys(request)

    try:
        body = await request.json()
    except Exception:  # pylint: disable=broad-exception-caught
        return _err_response(ValueError("Invalid or missing JSON body"), 400)

    hostname = body.get("hostname", "").strip()
    if not hostname:
        return _err_response(ValueError("'hostname' is required"), 400)

    try:
        await coresys.hostname.set_hostname(hostname)
        return web.json_response({"result": "ok", "hostname": hostname})
    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)


@routes.post("/network/ip")
async def set_ip(request: web.Request) -> web.Response:
    """Set static IP or switch to DHCP for an interface.

    DHCP body:
    {
        "interface": "eth0",
        "method": "dhcp"
    }

    Static body:
    {
        "interface": "eth0",
        "method": "static",
        "address": "192.168.1.10",
        "prefix": 24,
        "gateway": "192.168.1.1",
        "dns": ["1.1.1.1", "8.8.8.8"]
    }
    """
    coresys = _get_coresys(request)

    try:
        body = await request.json()
    except Exception:  # pylint: disable=broad-exception-caught
        return _err_response(ValueError("Invalid or missing JSON body"), 400)

    interface = body.get("interface", "").strip()
    method = body.get("method", "").strip().lower()

    if not interface:
        return _err_response(ValueError("'interface' is required"), 400)
    if method not in ("dhcp", "static"):
        return _err_response(ValueError("'method' must be 'dhcp' or 'static'"), 400)

    try:
        if method == "dhcp":
            await coresys.network.set_dhcp(interface)
            return web.json_response(
                {
                    "result": "ok",
                    "interface": interface,
                    "method": "dhcp",
                }
            )

        # Static validate required fields
        address = body.get("address", "").strip()
        prefix = body.get("prefix")
        gateway = body.get("gateway", "").strip()
        dns: list[str] = body.get("dns", [])

        if not address:
            return _err_response(ValueError("'address' is required for static"), 400)
        if prefix is None:
            return _err_response(ValueError("'prefix' is required for static"), 400)
        if not gateway:
            return _err_response(ValueError("'gateway' is required for static"), 400)
        if not dns:
            return _err_response(ValueError("'dns' must be a list"), 400)

        await coresys.network.set_static_ip(
            interface=interface,
            address=address,
            prefix=int(prefix),
            gateway=gateway,
            dns=dns,
        )
        return web.json_response(
            {
                "result": "ok",
                "interface": interface,
                "method": "static",
                "address": address,
                "prefix": prefix,
                "gateway": gateway,
                "dns": dns,
            }
        )

    except DBusConnectionError as err:
        return _err_response(err, 503)
    except DBusMethodError as err:
        return _err_response(err, 500)
    except Exception as err:  # pylint: disable=broad-exception-caught
        return _err_response(err, 500)


# =============================================================================
# Registration
# =============================================================================


def setup_routes(app: web.Application) -> None:
    """Add network routes to the application."""
    app.add_routes(routes)
