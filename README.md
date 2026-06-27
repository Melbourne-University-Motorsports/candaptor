# CANdapter

Python driver and GUI for the CANdapter USB-to-CAN adapter

## Requirements

- Python 3.8+
- [`uv`](https://docs.astral.sh/uv/)
- tkinter (bundled with Python on macOS/Windows; Linux: `sudo apt install python3-tk`)
- A CANdapter USB-to-CAN adapter

## Setup

```bash
uv sync --extra dev
```

## Start the GUI

```bash
uv run candapter-gui
```

`--port` is optional. The window opens with a default port pre-filled — pick the
real port from the dropdown and click **Connect**. No need to pass a port (or
even have hardware plugged in) to launch the GUI.

```bash
# override the default port:
uv run candapter-gui --port /dev/ttyUSB0
```

## CLI

```bash
uv run candapter monitor --port /dev/ttyUSB0
uv run candapter send --port /dev/ttyUSB0 3CF 11 22 33 44
uv run candapter version --port /dev/ttyUSB0
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
  `candapter-*.whl` / `candapter-*.tar.gz` (gives you the `candapter` and
  `candapter-gui` console scripts via `pip install`).
- **Standalone GUI executables** — PyInstaller bundles `candapter-gui` into a
  self-contained app for Windows, macOS, and Linux, so it runs without a
  Python install.

Pushing a tag (`v*`, e.g. `git tag v0.2.0 && git push --tags`) creates a
GitHub Release with all artifacts attached.

### Downloading artifacts

- **From a release**: grab the assets on the release page (e.g.
  `candapter-gui-windows.zip`, `candapter-gui-macos.tar.gz`,
  `candapter-gui-linux.tar.gz`).
- **From any commit**: download the workflow run's artifacts from the Actions
  tab (kept for 90 days).

### Running the bundled app

```bash
# Linux
chmod +x candapter-gui
./candapter-gui

# macOS (the .app is unsigned — first run may need Gatekeeper approval)
open candapter-gui.app
# or, if blocked:  xattr -dr com.apple.quarantine candapter-gui.app
```

Windows: unzip and double-click `candapter-gui.exe`. SmartScreen may warn
because the executable is unsigned — choose **More info → Run anyway**.
