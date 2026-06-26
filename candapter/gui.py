#!/usr/bin/env python3
"""CANdapter GUI - a tkinter frontend for CAN monitoring and sending.

Reuses the CANdapter driver from the candapter package. All serial I/O happens
in a background reader thread; frames are passed to the GUI thread via a
queue (tkinter is not thread-safe).
"""

import argparse
import glob
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from candapter import (
    CANdapter,
    CANdapterError,
    CAN_BAUDRATE_CODES,
    parse_data_bytes,
    ACK,
    ERROR,
)

DEFAULT_PORT = "/dev/cu.usbserial-DN2RIILM"
DEFAULT_CAN_BAUDRATE = 500000
MAX_ROWS = 10000
READ_TIMEOUT = 0.1
POLL_INTERVAL_MS = 50


class CandapterGUI:
    def __init__(self, root, default_port=DEFAULT_PORT):
        self.root = root
        self.default_port = default_port
        self.dev = None
        self.reader_thread = None
        self.stop_event = threading.Event()
        self.frame_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.connected = False
        self.frame_count = 0

        self.port_var = tk.StringVar(value=default_port)
        self.baud_var = tk.StringVar(value=str(DEFAULT_CAN_BAUDRATE))
        self.paused_var = tk.BooleanVar(value=False)
        self.auto_scroll_var = tk.BooleanVar(value=True)
        self.extended_var = tk.BooleanVar(value=False)
        self.timestamp_var = tk.BooleanVar(value=False)
        self.id_var = tk.StringVar(value="")
        self.data_var = tk.StringVar(value="")

        root.title("CANdapter")
        root.geometry("820x600")
        root.minsize(620, 440)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._poll_queues()

    # ---- UI construction -------------------------------------------------

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self._build_connection_bar()
        self._build_frames_panel()
        self._build_send_panel()
        self._build_status_bar()

    def _build_connection_bar(self):
        bar = ttk.Frame(self.root, padding=(10, 10, 10, 6))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        ttk.Label(bar, text="Port:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.port_combo = ttk.Combobox(
            bar, textvariable=self.port_var, values=self._list_ports()
        )
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        ttk.Label(bar, text="CAN Baud:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        baud_values = [str(b) for b in sorted(CAN_BAUDRATE_CODES.keys())]
        self.baud_combo = ttk.Combobox(
            bar, textvariable=self.baud_var, values=baud_values, width=8,
            state="readonly",
        )
        self.baud_combo.grid(row=0, column=3, padx=(0, 10))

        ttk.Checkbutton(bar, text="Timestamp", variable=self.timestamp_var).grid(
            row=0, column=4, padx=(0, 10)
        )

        self.connect_btn = ttk.Button(bar, text="Connect", command=self.toggle_connect)
        self.connect_btn.grid(row=0, column=5, padx=(0, 4))

        self.version_btn = ttk.Button(bar, text="Version", command=self.show_version)
        self.version_btn.grid(row=0, column=6, padx=(0, 4))

        self.flags_btn = ttk.Button(bar, text="Flags", command=self.show_flags)
        self.flags_btn.grid(row=0, column=7)

    def _build_frames_panel(self):
        panel = ttk.Frame(self.root, padding=(10, 4, 10, 4))
        panel.grid(row=1, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        header = ttk.Frame(panel)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Received Frames", font=("", 11, "bold")).pack(
            side="left"
        )

        ctrl = ttk.Frame(header)
        ctrl.pack(side="right")
        ttk.Button(ctrl, text="Clear", command=self.clear_frames, width=8).pack(
            side="left", padx=2
        )
        ttk.Checkbutton(ctrl, text="Pause", variable=self.paused_var).pack(
            side="left", padx=4
        )
        ttk.Checkbutton(
            ctrl, text="Auto-scroll", variable=self.auto_scroll_var
        ).pack(side="left", padx=4)
        self.count_label = ttk.Label(ctrl, text="0 frames")
        self.count_label.pack(side="left", padx=4)

        tree_frame = ttk.Frame(panel)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("num", "time", "ts", "type", "id", "dlc", "data")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", height=16
        )
        self.tree.heading("num", text="#")
        self.tree.heading("time", text="Time")
        self.tree.heading("ts", text="TS")
        self.tree.heading("type", text="Type")
        self.tree.heading("id", text="ID")
        self.tree.heading("dlc", text="DLC")
        self.tree.heading("data", text="Data")
        self.tree.column("num", width=50, anchor="e")
        self.tree.column("time", width=95, anchor="w")
        self.tree.column("ts", width=55, anchor="e")
        self.tree.column("type", width=50, anchor="center")
        self.tree.column("id", width=90, anchor="w")
        self.tree.column("dlc", width=40, anchor="center")
        self.tree.column("data", width=300, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

    def _build_send_panel(self):
        panel = ttk.LabelFrame(self.root, text="Send Frame", padding=(10, 6, 10, 8))
        panel.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 4))
        panel.columnconfigure(2, weight=1)

        ttk.Label(panel, text="ID (hex):").grid(row=0, column=0, sticky="w", padx=(0, 4))
        id_entry = ttk.Entry(panel, textvariable=self.id_var, width=12)
        id_entry.grid(row=0, column=1, sticky="w", padx=(0, 12))

        ttk.Checkbutton(panel, text="Extended (29-bit)", variable=self.extended_var).grid(
            row=0, column=3, sticky="w", padx=(12, 12)
        )

        ttk.Label(panel, text="Data (hex, space/comma separated):").grid(
            row=1, column=0, columnspan=1, sticky="w", padx=(0, 4), pady=(6, 0)
        )
        data_entry = ttk.Entry(panel, textvariable=self.data_var)
        data_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=(6, 0))

        self.send_btn = ttk.Button(panel, text="Send", command=self.send_frame)
        self.send_btn.grid(row=1, column=3, sticky="w", pady=(6, 0))

        id_entry.bind("<Return>", lambda _e: self.send_frame())
        data_entry.bind("<Return>", lambda _e: self.send_frame())

    def _build_status_bar(self):
        bar = ttk.Frame(self.root, padding=(10, 4, 10, 8))
        bar.grid(row=3, column=0, sticky="ew")
        self.status_label = ttk.Label(bar, text="Disconnected", foreground="#888")
        self.status_label.pack(side="left")

    # ---- Port discovery --------------------------------------------------

    def _list_ports(self):
        ports = []
        if sys.platform == "darwin":
            ports = sorted(set(
                glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.usbmodem*")
            ))
        elif sys.platform.startswith("linux"):
            ports = sorted(set(
                glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
            ))
        elif sys.platform == "win32":
            try:
                from serial.tools import list_ports
                ports = sorted(p.device for p in list_ports.comports())
            except Exception:
                ports = []
        if self.default_port not in ports:
            ports.insert(0, self.default_port)
        return ports

    # ---- Connection management ------------------------------------------

    def toggle_connect(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get().strip()
        if not port:
            self._set_status("Error: no port specified", "red")
            return
        try:
            baudrate = int(self.baud_var.get())
        except ValueError:
            self._set_status("Error: invalid baudrate", "red")
            return

        self._set_status("Connecting...", "orange")
        self.root.update_idletasks()

        try:
            self.dev = CANdapter(port=port)
            self.dev.ser.timeout = READ_TIMEOUT
        except Exception as e:
            self._set_status(f"Error: cannot open {port}: {e}", "red")
            self.dev = None
            return

        try:
            self.dev.set_baudrate(baudrate)
            self.dev.set_timestamp(self.timestamp_var.get())
            self.dev.open()
        except Exception as e:
            self._set_status(f"Error: {e}", "red")
            try:
                self.dev.close_port()
            except Exception:
                pass
            self.dev = None
            return

        self.stop_event.clear()
        self.frame_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="can-reader"
        )
        self.reader_thread.start()
        self.connected = True
        self._update_connect_button()
        self._set_status(f"Connected — monitoring {port} @ {baudrate} Kbit/s", "green")

    def disconnect(self):
        if not self.connected and self.dev is None and self.reader_thread is None:
            return
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2.0)
        self.reader_thread = None
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
            try:
                self.dev.close_port()
            except Exception:
                pass
            self.dev = None
        self.connected = False
        self._update_connect_button()
        self._set_status("Disconnected", "gray")

    def _update_connect_button(self):
        if self.connected:
            self.connect_btn.config(text="Disconnect")
            self.version_btn.config(state="normal")
            self.flags_btn.config(state="normal")
        else:
            self.connect_btn.config(text="Connect")
            self.version_btn.config(state="normal")
            self.flags_btn.config(state="normal")

    # ---- Reader thread ---------------------------------------------------

    def _reader_loop(self):
        buffer = b""
        while not self.stop_event.is_set():
            try:
                with self.dev._lock:
                    chunk = self.dev.ser.read(64)
            except Exception as e:
                self.error_queue.put(e)
                self.stop_event.set()
                return
            if chunk:
                chunk = chunk.replace(ACK, b"").replace(ERROR, b"")
                buffer += chunk
                while b"\r" in buffer:
                    line, buffer = buffer.split(b"\r", 1)
                    if line:
                        frame = CANdapter._parse_frame(line, timestamp=self.dev.timestamp_enabled)
                        if frame:
                            self.frame_queue.put(frame)

    # ---- GUI queue polling ----------------------------------------------

    def _poll_queues(self):
        drained = 0
        while True:
            try:
                frame = self.frame_queue.get_nowait()
            except queue.Empty:
                break
            if not self.paused_var.get():
                self._add_frame_row(frame)
            drained += 1
            if drained > 500:
                break

        try:
            err = self.error_queue.get_nowait()
            self._handle_reader_error(err)
        except queue.Empty:
            pass

        self.root.after(POLL_INTERVAL_MS, self._poll_queues)

    def _handle_reader_error(self, err):
        was_connected = self.connected
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
        self.reader_thread = None
        if self.dev is not None:
            try:
                self.dev.close_port()
            except Exception:
                pass
            self.dev = None
        self.connected = False
        self._update_connect_button()
        if was_connected:
            self._set_status(f"Device error: {err}", "red")

    # ---- Frames table ----------------------------------------------------

    def _add_frame_row(self, frame):
        self.frame_count += 1
        now = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"
        ts_str = f"{frame['timestamp']:04X}" if "timestamp" in frame else ""
        kind = "Ext" if frame["extended"] else "Std"
        if frame["extended"]:
            id_str = f"{frame['id']:08X}"
        else:
            id_str = f"{frame['id']:03X}"
        data_str = " ".join(f"{b:02X}" for b in frame["data"])
        self.tree.insert(
            "", "end",
            values=(self.frame_count, ts, ts_str, kind, id_str, len(frame["data"]), data_str),
        )
        children = self.tree.get_children("")
        if len(children) > MAX_ROWS:
            self.tree.delete(children[0])
        self.count_label.config(text=f"{self.frame_count} frames")
        if self.auto_scroll_var.get():
            self.tree.see(children[-1])

    def clear_frames(self):
        self.tree.delete(*self.tree.get_children(""))
        self.frame_count = 0
        self.count_label.config(text="0 frames")

    # ---- Send ------------------------------------------------------------

    def send_frame(self):
        if not self.connected or self.dev is None:
            self._set_status("Error: not connected", "red")
            return
        id_text = self.id_var.get().strip()
        if not id_text:
            self._set_status("Error: CAN ID required", "red")
            return
        try:
            frame_id = int(id_text, 16)
        except ValueError:
            self._set_status(f"Error: invalid CAN ID '{id_text}'", "red")
            return
        try:
            data_bytes = parse_data_bytes([self.data_var.get()])
        except ValueError:
            self._set_status("Error: invalid data bytes", "red")
            return
        if len(data_bytes) > 8:
            self._set_status("Error: data exceeds 8 bytes", "red")
            return
        extended = self.extended_var.get()
        try:
            self.dev.transmit(frame_id, data_bytes, extended=extended)
            tag = "T" if extended else "t"
            self._set_status(
                f"Sent {tag} {frame_id:X} DLC {len(data_bytes)}", "green"
            )
        except Exception as e:
            self._set_status(f"Send error: {e}", "red")

    # ---- Version / flags -------------------------------------------------

    def show_version(self):
        if not self._ensure_connected():
            return
        try:
            ver = self.dev.version()
            messagebox.showinfo("Firmware Version", ver)
            self._set_status(f"Version: {ver}", "green")
        except Exception as e:
            self._set_status(f"Version error: {e}", "red")

    def show_flags(self):
        if not self._ensure_connected():
            return
        try:
            flags = self.dev.status_flags()
            messagebox.showinfo("Status Flags", flags)
            self._set_status(f"Flags: {flags}", "green")
        except Exception as e:
            self._set_status(f"Flags error: {e}", "red")

    def _ensure_connected(self):
        if not self.connected or self.dev is None:
            self._set_status("Error: not connected", "red")
            return False
        return True

    # ---- Misc ------------------------------------------------------------

    def _set_status(self, text, color=None):
        self.status_label.config(text=text)
        if color:
            color_map = {
                "red": "#c0392b",
                "green": "#27ae60",
                "orange": "#e67e22",
                "gray": "#888888",
            }
            self.status_label.config(foreground=color_map.get(color, color))

    def on_close(self):
        self.disconnect()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(
        prog="candapter-gui", description="CANdapter GUI (tkinter)"
    )
    parser.add_argument(
        "--port", default=DEFAULT_PORT, help=f"default serial port (default: {DEFAULT_PORT})"
    )
    args = parser.parse_args()
    root = tk.Tk()
    CandapterGUI(root, default_port=args.port)
    root.mainloop()


if __name__ == "__main__":
    main()
