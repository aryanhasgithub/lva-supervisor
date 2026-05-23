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
    try:
        asyncio.run(run_supervisor())
    except KeyboardInterrupt:
        pass
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.critical("Supervisor crashed: %s", err)
        sys.exit(1)
    _LOGGER.info("LVA Supervisor exited")


if __name__ == "__main__":
    main()
