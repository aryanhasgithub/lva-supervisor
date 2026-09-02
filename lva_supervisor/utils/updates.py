"""LVA Supervisor shared update and version tracking utilities."""
from __future__ import annotations

import logging
import aiohttp
import semver
from typing import TYPE_CHECKING, Any

from ..const import VERSION_MANIFEST_URL

if TYPE_CHECKING:
    from ..coresys import CoreSys

_LOGGER = logging.getLogger(__name__)


def strip_v(version: str) -> str:
    """Strip leading 'v' from version string for semver parsing."""
    return version.lstrip("v")


def is_update_available(local: str | None, remote: str | None) -> bool:
    """Compare two version strings using semver.

    Returns True if remote is strictly greater than local.
    """
    if not local or not remote:
        return False
    try:
        local_v = semver.Version.parse(strip_v(local))
        remote_v = semver.Version.parse(strip_v(remote))
        return remote_v > local_v
    except ValueError:
        # Not valid semver — fall back to plain string inequality comparison
        return local != remote


def is_version_satisfied(local: str | None, minimum: str | None) -> bool:
    """Return True if local >= minimum. Missing/unparsable versions fail closed."""
    if not minimum:
        return True
    if not local:
        return False
    try:
        local_v = semver.Version.parse(strip_v(local))
        minimum_v = semver.Version.parse(strip_v(minimum))
        return local_v >= minimum_v
    except ValueError:
        return local == minimum


def get_manifest_component(
    manifest: dict[str, Any] | None, name: str
) -> dict[str, Any]:
    """Return a component's manifest entry as {"version": ..., "requires": {...}}.

    Manifest entries are objects: {"version": "1.0.0", "requires": {...}}.
    "requires" is optional and omitted when a component has no minimum
    dependency versions.
    """
    if not manifest:
        return {}
    entry = manifest.get(name)
    if not isinstance(entry, dict):
        return {}
    return entry


async def fetch_manifest(session: aiohttp.ClientSession) -> dict[str, Any] | None:
    """Fetch the central stable.json manifest."""
    try:
        async with session.get(VERSION_MANIFEST_URL) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            _LOGGER.warning("Version manifest server returned HTTP %d", resp.status)
            return None
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning("Could not reach or parse remote version manifest: %s", err)
        return None


async def get_local_version(coresys: CoreSys, image: str) -> str | None:
    """Read org.opencontainers.image.version label from the local image via aiodocker."""
    try:
        info: dict[str, Any] = await coresys.docker.images.inspect(image)
        labels = (info.get("Config") or {}).get("Labels") or {}
        version = labels.get("org.opencontainers.image.version")
        if not version or version == "latest":
            return None
        return version
    except Exception:  # pylint: disable=broad-exception-caught
        return None