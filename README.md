# CANdapter

A Python driver and GUI for the **CANdapter** USB-to-CAN adapter. Send and
receive CAN frames over a serial link with a simple CLI or a tkinter-based
monitor.

## Features

- **CLI tool** — `monitor`, `send`, and `version` subcommands
- **GUI** — live frame viewer with send capability (tkinter)
- Cross-platform: macOS, Linux, Windows
- Pure Python — only `pyserial` for the driver; `tkinter` for the GUI

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.8+ | |
| pyserial | Installed automatically via `uv sync` / `pip install` |
| tkinter | Bundled with Python on macOS & Windows. On Linux: `sudo apt install python3-tk` |
| CANdapter hardware | USB-to-CAN adapter with a serial interface |
| Serial port access | On Linux, add your user to the `dialout` group (see below) |

## Quick Start with `uv` (recommended)

[`uv`](https://docs.astral.sh/uv/) is a fast Python package manager. If you
don't have it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then:

```bash
# Clone and enter the repo
git clone <your-repo-url> candapter
cd candapter

# Create a venv and install dependencies
uv sync

# Run the CLI
uv run candapter version --port /dev/ttyUSB0
uv run candapter monitor --port /dev/ttyUSB0

# Run the GUI
uv run candapter-gui --port /dev/ttyUSB0
```

You can also run the modules directly without installing entry points:

```bash
uv run python -m candapter monitor --port /dev/ttyUSB0
uv run python -m candapter.gui --port /dev/ttyUSB0
```

## Quick Start with `pip` + `venv`

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install .
candapter monitor --port /dev/ttyUSB0
candapter-gui --port /dev/ttyUSB0
```

## Finding Your Serial Port

The default port is `/dev/cu.usbserial-DN2RIILM` (a macOS example). Find yours:

**macOS:**
```bash
ls /dev/cu.usbserial* /dev/cu.usbmodem*
```

**Linux:**
```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

**Windows** — check Device Manager → Ports (COM & LPT), or:
```bash
python -c "from serial.tools import list_ports; [print(p.device) for p in list_ports.comports()]"
```

## CLI Usage

### Monitor (receive frames)

```bash
candapter monitor --port /dev/ttyUSB0 --can-baudrate 500000
```

| Option | Description |
|---|---|
| `--port` | Serial port path (default: `/dev/cu.usbserial-DN2RIILM`) |
| `--can-baudrate` | CAN bus bitrate — see supported values below |
| `--timestamp` | Append CANdapter hardware timestamps to each frame |

Supported CAN baudrates: `10000`, `20000`, `50000`, `100000`, `125000`,
`250000`, `500000`, `800000`, `1000000`.

Press `Ctrl-C` to stop monitoring.

### Send a frame

```bash
# Standard 11-bit frame, ID 0x3CF, data 11 22 33 44
candapter send --port /dev/ttyUSB0 3CF 11 22 33 44

# Extended 29-bit frame, ID 0x18DAF110, data 22 1A FF
candapter send --port /dev/ttyUSB0 18DAF110 22 1A FF --extended
```

Data bytes are hex, separated by spaces or commas.

### Firmware version

```bash
candapter version --port /dev/ttyUSB0
```

## GUI Usage

```bash
candapter-gui
# or with a specific port:
candapter-gui --port /dev/ttyUSB0
```

The GUI provides:

- Port and baudrate selection with auto-detection
- Live frame table (sequence #, wall-clock time, hardware timestamp, type, CAN ID, DLC, data)
- Pause, auto-scroll, and clear controls
- Send panel for transmitting individual frames
- Firmware version and status flag inspection

## Testing

```bash
# With uv
uv run pytest

# With pip + pytest
pip install -e ".[dev]"
pytest

# Without pytest (built-in test runner)
python test_candapter.py
```

## Platform Notes

### Linux

- Install tkinter if not present: `sudo apt install python3-tk`
  (or `python3-tkinter` on some distributions)
- Grant serial port access: `sudo usermod -aG dialout $USER`, then log out
  and back in
- Ports appear as `/dev/ttyUSB0`, `/dev/ttyACM0`, etc.

### macOS

- Ports appear as `/dev/cu.usbserial-*`, `/dev/cu.usbmodem-*`
- tkinter ships with the official Python from [python.org](https://python.org)
  and with Homebrew's `python@3.12`

### Windows

- Ports appear as `COM3`, `COM4`, etc.
- tkinter ships with the official Python installer

## Project Structure

```
candapter/
├── __init__.py       Driver library + CLI (monitor / send / version)
└── gui.py            Tkinter GUI
test_candapter.py     Unit tests (parsing, formatting, baudrate codes)
pyproject.toml        Package config, dependencies, entry points
```

## Development

```bash
uv sync --extra dev    # install dev dependencies (pytest)
uv run pytest          # run tests
```

## Install as a standalone tool

You can install the CLI globally with `uv tool` so it's available everywhere
without activating a venv:

```bash
uv tool install .
candapter monitor --port /dev/ttyUSB0
```
