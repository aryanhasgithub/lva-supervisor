"""LVA Supervisor constants."""

from pathlib import Path
import os

# =============================================================================
# Version
# =============================================================================
SUPERVISOR_VERSION = "0.1.0"

# =============================================================================
# Paths
# =============================================================================
SUPERVISOR_SOCKET = Path("/run/lva/supervisor.sock")
SUPERVISOR_DATA = Path("/data")
STARTUP_MARKER = Path("/data/supervisor-started")

# First-boot pull progress. FIRSTBOOT_MARKER existing is what gates whether
# the supervisor's bare HTML page is served at all — created when load()
# starts the first-boot install sequence, removed once _start_containers()
# finishes. Only one container is ever pulling at a time (containers start
# sequentially in CONTAINER_START_ORDER), so a single file is enough —
# written as "<container_name>-<percent>", e.g. "lva-42".
FIRSTBOOT_DONE = Path("/data/firstboot-done")
FIRSTBOOT_PROGRESS_FILE = Path("/data/firstboot-progress")

# =============================================================================
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
CONTAINER_CLI = "lva-cli"
CONTAINER_SUPERVISOR = "lva-supervisor"

MANAGED_CONTAINERS = [CONTAINER_AUDIO, CONTAINER_LVA, CONTAINER_PORTAL, CONTAINER_CLI]
CONTAINER_START_ORDER = [CONTAINER_CLI, CONTAINER_AUDIO, CONTAINER_LVA, CONTAINER_PORTAL]

# =============================================================================
# Container images
# =============================================================================
GHCR_BASE = "ghcr.io/aryanhasgithub"
IMAGE_LVA = "ghcr.io/ohf-voice/linux-voice-assistant"
IMAGE_AUDIO = f"{GHCR_BASE}/lva-audio"
IMAGE_PORTAL = f"{GHCR_BASE}/lva-portal"
IMAGE_CLI = f"{GHCR_BASE}/lva-cli"
IMAGE_SUPERVISOR = f"{GHCR_BASE}/lva-supervisor"
VERSION_MANIFEST_URL = (
    "https://raw.githubusercontent.com/aryanhasgithub/lva-version/main/stable.json"
)

# =============================================================================
# Docker
# =============================================================================
DOCKER_SOCKET = Path("/var/run/docker.sock")
DOCKER_NETWORK = "lva"

# =============================================================================
# D-Bus os-agent only
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
MACHINE = os.environ.get("LVA_MACHINE", "generic")

# =============================================================================
# API
# =============================================================================
API_HOST = "localhost"