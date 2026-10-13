# CANdaptor

Python driver and GUI for the CANdaptor USB-to-CAN adapter

## Requirements

- Python 3.9+
- [`uv`](https://docs.astral.sh/uv/)
- A CANdaptor USB-to-CAN adapter

## Setup

```bash
uv sync --extra dev
```

## Quick Start (No Python Setup Required)

Just like `inverter-monitor`, launcher scripts are provided in the repo root so you can launch the GUI immediately without manual setup (they install `uv` automatically if missing):

- **macOS**: Double-click `run.command` in Finder (or `./run.sh`).
- **Linux**: `./run.sh`
- **Windows**: Double-click `run.cmd`

---

## Start the GUI manually

```bash
uv run candaptor-gui
```

`--port` is optional. The window opens with a default port pre-filled — pick the
real port from the dropdown and click **Connect**. No need to pass a port (or
even have hardware plugged in) to launch the GUI.

```bash
# override the default port:
uv run candaptor-gui --port /dev/ttyUSB0
```

## CLI

```bash
uv run candaptor monitor --port /dev/ttyUSB0
uv run candaptor send --port /dev/ttyUSB0 3CF 11 22 33 44
uv run candaptor version --port /dev/ttyUSB0
```

Supported CAN baudrates: `10000`, `20000`, `50000`, `100000`, `125000`,
`250000`, `500000`, `800000`, `1000000`.

## Tests

```bash
uv run pytest
```

## Builds & releases

GitHub Actions (`.github/workflows/build.yml`) runs on every push and pull
request:

- **Tests** — `uv run pytest`.
- **Wheel & sdist** — `uv build` produces the installable
  `candaptor-*.whl` / `candaptor-*.tar.gz` (gives you the `candaptor` and
  `candaptor-gui` console scripts via `pip install`).
- **Standalone GUI executables** — PyInstaller bundles `candaptor-gui` into a
  self-contained app for Windows, macOS, and Linux, so it runs without a
  Python install.

Every push to `main` publishes a rolling **"Latest" release** with all
build artifacts attached (always points to the newest commit). Pushing a
version tag (`v*`, e.g. `git tag v0.2.0 && git push --tags`) creates a proper
versioned GitHub Release instead.

### Downloading artifacts

- **From a release**: grab the assets on the release page (e.g.
  `candaptor-gui-windows.zip`, `candaptor-gui-macos.tar.gz`,
  `candaptor-gui-linux.tar.gz`).
- **From any commit**: download the workflow run's artifacts from the Actions
  tab (kept for 90 days).

### Running the bundled app

```bash
# Linux
chmod +x candaptor-gui
./candaptor-gui

# macOS (the .app is unsigned — first run may need Gatekeeper approval)
open candaptor-gui.app
# or, if blocked:  xattr -dr com.apple.quarantine candaptor-gui.app
```

Windows: unzip and double-click `candaptor-gui.exe`. SmartScreen may warn
because the executable is unsigned — choose **More info → Run anyway**.
