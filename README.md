# LVA Supervisor

The supervisor for LVA-OS manages all LVA containers, handles OTA updates, and exposes a Unix socket API to the portal and CLI.

## What it does

LVA Supervisor is a container-based system for managing your LVA Core installation and related components. It is controlled via the LVA Portal, which communicates with the Supervisor over a Unix socket. The Supervisor provides an API to manage the installation, including changing network settings, managing containers, and installing updates.

## Installation

LVA Supervisor ships as part of LVA-OS. Installation instructions can be found at [github.com/aryanhasgithub/lva-os](https://github.com/aryanhasgithub/lva-os).

## Development

Clone the repo and set up a virtual environment:

```bash
git clone https://github.com/aryanhasgithub/lva-supervisor
cd lva-supervisor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Run the supervisor locally (requires Docker and D-Bus):

```bash
python3 -m lva_supervisor
```

## Release

Releases are triggered by pushing a semver tag. The GitHub Actions workflow builds multi-arch images (`amd64` + `arm64`) and pushes them to GHCR.

1. Pull requests are merged to the `main` branch.
2. A new tag is pushed (e.g. `v1.0.0`).
3. The build workflow produces and pushes the image to `ghcr.io/aryanhasgithub/lva-supervisor`.
4. The `stable.json` manifest in [lva-version](https://github.com/aryanhasgithub/lva-version) is updated.
5. The supervisor's background updater picks up the new version and self-updates.
