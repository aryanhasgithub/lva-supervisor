"""LVA Supervisor exceptions."""


# =============================================================================
# Base
# =============================================================================

class LVAError(Exception):
    """Base exception for all LVA supervisor errors."""


# =============================================================================
# Docker
# =============================================================================

class DockerError(LVAError):
    """Generic Docker operation failed."""


class DockerConnectionError(DockerError):
    """Cannot reach Docker daemon."""


class DockerContainerNotFound(DockerError):
    """Container does not exist."""


class DockerImageNotFound(DockerError):
    """Image does not exist locally."""


class DockerPullError(DockerError):
    """Failed to pull image from registry."""


# =============================================================================
# Watchdog
# =============================================================================

class WatchdogError(LVAError):
    """Watchdog encountered an unrecoverable error."""


# =============================================================================
# D-Bus / OS Agent
# =============================================================================

class DBusError(LVAError):
    """Generic D-Bus error."""


class DBusConnectionError(DBusError):
    """Cannot connect to D-Bus or os-agent is not running."""


class DBusMethodError(DBusError):
    """D-Bus method call failed."""


# =============================================================================
# API
# =============================================================================

class APIError(LVAError):
    """Generic API error, returned as 500."""


class APINotFound(APIError):
    """Resource not found, returned as 404."""


class APIBadRequest(APIError):
    """Bad request from client, returned as 400."""