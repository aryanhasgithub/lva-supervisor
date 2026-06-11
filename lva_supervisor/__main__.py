"""LVA Supervisor entry point."""

import asyncio
import logging
import sys

from .bootstrap import run_supervisor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Entry point."""
    _LOGGER.info("LVA Supervisor starting")

    # 1. Initialize a fallback exit code (defaults to standard clean exit)
    exit_code = 0

    try:
        # 2. Capture the integer returned by the updated bootstrap loop
        exit_code = asyncio.run(run_supervisor())
    except KeyboardInterrupt:
        pass
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.critical("Supervisor crashed: %s", err)
        exit_code = 1

    _LOGGER.info("LVA Supervisor exited with code %s", exit_code)

    # 3. Pass the exit code back to the host bash wrapper script
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
