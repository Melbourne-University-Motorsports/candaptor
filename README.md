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
