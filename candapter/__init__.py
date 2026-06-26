#!/usr/bin/env python3
"""CANdapter USB-to-CAN driver.

A thin pyserial wrapper implementing the CANdapter's ASCII serial protocol
for transmitting and receiving CAN frames.
"""

import argparse
import sys
import threading

try:
    import serial
except ImportError:
    sys.stderr.write("pyserial is required: pip install pyserial\n")
    sys.exit(1)


DEFAULT_PORT = "/dev/cu.usbserial-DN2RIILM"
DEFAULT_SERIAL_BAUDRATE = 115200
DEFAULT_CAN_BAUDRATE = 500000

ACK = b"\x06"
ERROR = b"\x07"

CAN_BAUDRATE_CODES = {
    10000: "S0",
    20000: "S1",
    50000: "S2",
    100000: "S3",
    125000: "S4",
    250000: "S5",
    500000: "S6",
    800000: "S7",
    1000000: "S8",
}


class CANdapterError(Exception):
    pass


class CANdapter:
    """Driver for the CANdapter USB-to-CAN adapter."""

    def __init__(self, port=DEFAULT_PORT, baudrate=DEFAULT_SERIAL_BAUDRATE):
        self.port = port
        self.serial_baudrate = baudrate
        self.timestamp_enabled = False
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.close()
        except Exception:
            pass
        self.close_port()
        return False

    def close_port(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _send(self, cmd):
        with self._lock:
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\r").encode("ascii"))
            self.ser.flush()
            resp = self.ser.read(1)
        if resp == ERROR:
            raise CANdapterError(f"CANdapter ERROR for command {cmd!r}")
        if resp != ACK:
            raise CANdapterError(f"Unexpected response {resp!r} for command {cmd!r}")
        return True

    def set_baudrate(self, baudrate=DEFAULT_CAN_BAUDRATE):
        """Set the CAN bus bitrate. Must be called before open()."""
        if isinstance(baudrate, str):
            code = baudrate
        else:
            code = CAN_BAUDRATE_CODES.get(baudrate)
            if code is None:
                raise CANdapterError(f"Unsupported CAN baudrate: {baudrate}")
        return self._send(code)

    def set_timestamp(self, on=False):
        """Enable/disable the timestamp on received frames. Must be called before open()."""
        code = "A1" if on else "A0"
        result = self._send(code)
        self.timestamp_enabled = on
        return result

    def open(self):
        """Open the CAN channel."""
        return self._send("O")

    def close(self):
        """Close the CAN channel."""
        return self._send("C")

    def transmit(self, frame_id, data, extended=False):
        """Transmit a single CAN frame.

        frame_id -- integer CAN ID.
        data     -- iterable of integer byte values (0-8 bytes).
        extended -- True for a 29-bit extended frame.
        """
        data = bytes(data)
        if len(data) > 8:
            raise CANdapterError("CAN frame data exceeds 8 bytes")
        if extended:
            if not 0 <= frame_id <= 0x1FFFFFFF:
                raise CANdapterError("Extended CAN ID out of range")
            cmd = "X" + f"{frame_id:08X}" + f"{len(data):X}"
        else:
            if not 0 <= frame_id <= 0x7FF:
                raise CANdapterError("Standard CAN ID out of range")
            cmd = "T" + f"{frame_id:03X}" + f"{len(data):X}"
        cmd += data.hex().upper()
        return self._send(cmd)

    def receive(self):
        """Read one CAN frame from the bus.

        Returns a dict {id, data, extended} or None on timeout.
        """
        with self._lock:
            line = self.ser.read_until(b"\r")
        if not line:
            return None
        return self._parse_frame(line.rstrip(b"\r"), timestamp=self.timestamp_enabled)

    @staticmethod
    def _parse_frame(line, timestamp=False):
        """Parse a received CAN frame line (without trailing CR).

        Returns a dict {id, data, extended} or None on parse failure.
        If timestamp is True, also extracts the 4-hex-digit millisecond
        counter appended by the CANdapter into 'timestamp'.
        """
        if isinstance(line, (bytes, bytearray)):
            line = line.decode("ascii", errors="replace")
        line = line.strip()
        if len(line) < 5:
            return None
        kind = line[0].upper()
        try:
            if kind == "T":
                frame_id = int(line[1:4], 16)
                dlc = int(line[4], 16)
                data_end = 5 + dlc * 2
                data = bytes.fromhex(line[5:data_end])
                frame = {"id": frame_id, "data": data, "extended": False}
            elif kind == "X":
                frame_id = int(line[1:9], 16)
                dlc = int(line[9], 16)
                data_end = 10 + dlc * 2
                data = bytes.fromhex(line[10:data_end])
                frame = {"id": frame_id, "data": data, "extended": True}
            else:
                return None
            if timestamp:
                ts_hex = line[data_end:data_end + 4]
                if len(ts_hex) == 4:
                    frame["timestamp"] = int(ts_hex, 16)
            return frame
        except (ValueError, IndexError):
            return None

    def version(self):
        """Return the firmware version string, e.g. 'V010203'."""
        with self._lock:
            self.ser.reset_input_buffer()
            self.ser.write(b"V\r")
            self.ser.flush()
            resp = self.ser.read_until(b"\r")
        return resp.decode("ascii", errors="replace").strip()

    def status_flags(self):
        """Return the status/error flags string, e.g. 'F00'."""
        with self._lock:
            self.ser.reset_input_buffer()
            self.ser.write(b"F\r")
            self.ser.flush()
            resp = self.ser.read_until(b"\r")
        return resp.decode("ascii", errors="replace").strip()

    def monitor(self, on_frame=None, baudrate=DEFAULT_CAN_BAUDRATE, timestamp=False):
        """Open the channel and print received frames until Ctrl-C.

        on_frame -- optional callback(frame_dict) called for each frame.
        baudrate -- CAN bus bitrate (default: %(default)s).
        timestamp -- enable CANdapter timestamps on received frames.
        """
        self.set_baudrate(baudrate)
        if timestamp:
            self.set_timestamp(True)
        self.open()
        stop_event = threading.Event()

        def reader():
            buffer = b""
            while not stop_event.is_set():
                byte = self.ser.read(1)
                if not byte:
                    continue
                buffer += byte
                if self.ser.in_waiting:
                    buffer += self.ser.read(self.ser.in_waiting)
                buffer = buffer.replace(ACK, b"").replace(ERROR, b"")
                while b"\r" in buffer:
                    line, buffer = buffer.split(b"\r", 1)
                    if line:
                        frame = self._parse_frame(line, timestamp=self.timestamp_enabled)
                        if frame:
                            if on_frame:
                                on_frame(frame)
                            else:
                                print(format_frame(frame), flush=True)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
            thread.join(timeout=2.0)
            self.close()
            self.close_port()


def format_frame(frame):
    """Format a CAN frame for display."""
    kind = "X" if frame["extended"] else "T"
    if frame["extended"]:
        id_str = f"{frame['id']:08X}"
    else:
        id_str = f"{frame['id']:03X}"
    data_str = " ".join(f"{b:02X}" for b in frame["data"])
    out = f"{kind} {id_str} {len(frame['data'])} {data_str}"
    if "timestamp" in frame:
        out += f" ts={frame['timestamp']:04X}"
    return out


def parse_data_bytes(items):
    """Parse hex byte tokens from CLI arguments into bytes."""
    data = []
    for item in items:
        for token in item.replace(",", " ").split():
            data.append(int(token, 16))
    return bytes(data)


def cmd_version(args):
    dev = CANdapter(port=args.port)
    try:
        print(dev.version())
    finally:
        dev.close_port()


def cmd_monitor(args):
    dev = CANdapter(port=args.port)
    dev.monitor(baudrate=args.can_baudrate, timestamp=args.timestamp)


def cmd_send(args):
    frame_id = int(args.id, 16)
    data = parse_data_bytes(args.data)
    dev = CANdapter(port=args.port)
    try:
        dev.set_baudrate(args.can_baudrate)
        dev.open()
        dev.transmit(frame_id, data, extended=args.extended)
        print("OK")
    finally:
        try:
            dev.close()
        except Exception:
            pass
        dev.close_port()


def main():
    parser = argparse.ArgumentParser(
        prog="candapter",
        description="CANdapter USB-to-CAN driver (CANdapter serial protocol)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", default=DEFAULT_PORT,
                        help=f"serial port (default: {DEFAULT_PORT})")

    can_common = argparse.ArgumentParser(add_help=False)
    can_common.add_argument("--port", default=DEFAULT_PORT,
                            help=f"serial port (default: {DEFAULT_PORT})")
    can_common.add_argument("--can-baudrate", type=int, default=DEFAULT_CAN_BAUDRATE,
                            dest="can_baudrate",
                            help=f"CAN bus bitrate (default: {DEFAULT_CAN_BAUDRATE})")

    sub.add_parser("version", parents=[common],
                   help="print firmware version")
    p_monitor = sub.add_parser("monitor", parents=[can_common],
                               help="open channel and print received frames")
    p_monitor.add_argument("--timestamp", action="store_true",
                           help="enable timestamps on received frames")

    p_send = sub.add_parser("send", parents=[can_common],
                            help="transmit a single CAN frame")
    p_send.add_argument("id", help="CAN ID in hex (e.g. 3CF or 18DAF110)")
    p_send.add_argument("data", nargs="+",
                        help="data bytes in hex (e.g. 11 22 33 44)")
    p_send.add_argument("--extended", action="store_true",
                        help="use 29-bit extended frame")

    args = parser.parse_args()

    if args.command == "version":
        cmd_version(args)
    elif args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "send":
        cmd_send(args)


if __name__ == "__main__":
    main()
