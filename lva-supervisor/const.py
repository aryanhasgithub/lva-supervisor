"""LVA Supervisor constants."""

from pathlib import Path

# =============================================================================
# Version
# =============================================================================
SUPERVISOR_VERSION = "0.1.0"

# =============================================================================
# Paths
# =============================================================================
SUPERVISOR_SOCKET = Path("/run/lva/supervisor.sock")
SUPERVISOR_DATA = Path("/data")
CONTAINERS_CONFIG = SUPERVISOR_DATA / "containers.json"

# ============================================================================
# Volumes
# =============================================================================
LVA_VOLUMES = [
    "lva_wakeword_data",
    "lva_wakeword_custom",
    "lva_configuration",
    "lva_sounds_custom",
]

# =============================================================================
# Container names
# =============================================================================
CONTAINER_LVA = "lva"
CONTAINER_AUDIO = "lva-audio"
CONTAINER_PORTAL = "lva-portal"

MANAGED_CONTAINERS = [CONTAINER_AUDIO, CONTAINER_LVA, CONTAINER_PORTAL]
CONTAINER_START_ORDER = [CONTAINER_AUDIO, CONTAINER_LVA, CONTAINER_PORTAL]

# =============================================================================
# Container images
# =============================================================================
GHCR_BASE = "ghcr.io/aryanhasgithub"
IMAGE_LVA = "ghcr.io/ohf-voice/linux-voice-assistant"
IMAGE_AUDIO = f"{GHCR_BASE}/lva-audio"
IMAGE_PORTAL = f"{GHCR_BASE}/lva-portal"

# =============================================================================
# Docker
# =============================================================================
DOCKER_SOCKET = Path("/var/run/docker.sock")
DOCKER_NETWORK = "lva"

# =============================================================================
# D-Bus — os-agent only (hostname and NM have their own constants)
# =============================================================================
DBUS_NAME = "io.lva.OsAgent"
DBUS_OBJECT = "/io/lva/OsAgent"
DBUS_IFACE_SYSTEM = "io.lva.OsAgent.System"
DBUS_IFACE_INFO = "io.lva.OsAgent.Info"

# =============================================================================
# Watchdog
# =============================================================================
WATCHDOG_INTERVAL = 30
WATCHDOG_RESTART_BACKOFF = [5, 10, 30, 60]

# =============================================================================
# Machine
# =============================================================================
import os

MACHINE = os.environ.get("LVA_MACHINE", "generic")

# =============================================================================
# API
# =============================================================================
API_HOST = "localhost"
