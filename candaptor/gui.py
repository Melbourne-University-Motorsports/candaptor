#!/usr/bin/env python3
"""CANdaptor GUI — Qt frontend for CAN monitoring and sending.

Reuses the CANdaptor driver from the candaptor package. Serial I/O runs in a
background thread; frames are delivered to the Qt event loop via a queue.

The frames panel has two views:
- Trace: chronological log of every received frame
- IDs: one live row per CAN ID (overwrite / sniffer style), with changed
  bytes painted in the Data column
"""

from __future__ import annotations

import argparse
import glob
import queue
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from candaptor import (
    ACK,
    CAN_BAUDRATE_CODES,
    CANdaptor,
    ERROR,
    format_baudrate,
    parse_baudrate,
    parse_data_bytes,
)
from candaptor.id_stats import (
    BusLoadMeter,
    IdTracker,
    format_age_ms,
    format_can_id,
    format_change_mask,
    format_data_bytes,
    format_hz,
    format_period_ms,
    format_std_ms,
    format_wall_time,
    frame_key,
)

DEFAULT_PORT = "/dev/cu.usbserial-DN2RIILM"
DEFAULT_CAN_BAUDRATE = 500000
MAX_ROWS = 10000
READ_TIMEOUT = 0.1
POLL_INTERVAL_MS = 50
NO_PORTS_MESSAGE = "No USB devices detected"

DATA_ROLE = Qt.ItemDataRole.UserRole
CHANGED_ROLE = Qt.ItemDataRole.UserRole + 1
STALE_ROLE = Qt.ItemDataRole.UserRole + 2
IID_ROLE = Qt.ItemDataRole.UserRole + 3

CHANGED_FG = QColor("#c0392b")
CHANGED_BG = QColor("#fadbd8")
STALE_FG = QColor("#888888")
LATE_FG = QColor("#d35400")
STATUS_COLORS = {
    "red": "#c0392b",
    "green": "#27ae60",
    "orange": "#e67e22",
    "gray": "#888888",
}

ID_COLUMNS = (
    ("id", "ID", 80),
    ("type", "Type", 48),
    ("dlc", "DLC", 40),
    ("data", "Data", 210),
    ("chg", "Chg", 64),
    ("count", "Count", 54),
    ("period", "Period", 64),
    ("avg", "Avg ms", 64),
    ("std", "σ ms", 56),
    ("hz", "Hz", 50),
    ("age", "Age ms", 64),
    ("last", "Last", 100),
)
ID_COL = {key: i for i, (key, _title, _w) in enumerate(ID_COLUMNS)}
ID_SORTABLE = frozenset({
    "id", "type", "dlc", "count", "period", "avg", "std", "hz", "age", "last",
})
TRACE_COL_ID = 4
TRACE_COL_DATA = 6


class HexBytesDelegate(QStyledItemDelegate):
    """Paint CAN payload hex, colouring bytes that changed."""

    def __init__(self, mono, parent=None):
        super().__init__(parent)
        self._mono = QFont(mono)
        self._mono_bold = QFont(mono)
        self._mono_bold.setBold(True)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        data = index.data(DATA_ROLE)
        if not isinstance(data, (bytes, bytearray)):
            return

        changed = index.data(CHANGED_ROLE) or ()
        stale = bool(index.data(STALE_ROLE))
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        palette = opt.palette
        group = (
            QPalette.ColorGroup.Disabled
            if not (opt.state & QStyle.StateFlag.State_Enabled)
            else QPalette.ColorGroup.Normal
        )
        if selected:
            base_fg = palette.color(group, QPalette.ColorRole.HighlightedText)
        elif stale:
            base_fg = STALE_FG
        else:
            base_fg = palette.color(group, QPalette.ColorRole.Text)

        painter.save()
        painter.setClipRect(opt.rect)
        painter.setFont(self._mono)
        gap = painter.fontMetrics().horizontalAdvance(" ")
        x = opt.rect.x() + 6
        y = opt.rect.y()
        h = opt.rect.height()
        for i, byte in enumerate(data):
            hexb = f"{byte:02X}"
            is_chg = (not stale) and i < len(changed) and changed[i]
            font = self._mono_bold if is_chg else self._mono
            painter.setFont(font)
            w = painter.fontMetrics().horizontalAdvance(hexb)
            if is_chg:
                if not selected:
                    painter.fillRect(x - 2, y + 2, w + 4, h - 4, CHANGED_BG)
                painter.setPen(CHANGED_FG)
            else:
                painter.setPen(base_fg)
            painter.drawText(x, y, w, h, Qt.AlignmentFlag.AlignVCenter, hexb)
            x += w + gap
        painter.restore()


class CandaptorWindow(QMainWindow):
    def __init__(self, default_port=DEFAULT_PORT):
        super().__init__()
        self.default_port = default_port
        self.dev = None
        self.reader_thread = None
        self.stop_event = threading.Event()
        self.frame_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.connected = False
        self.frame_count = 0
        self.id_tracker = IdTracker()
        self.bus_load = BusLoadMeter()
        self.can_bitrate = None
        self._id_sort = ("id", False)
        self._id_rows = {}
        self._id_visual = {}
        self._id_style_row = -1
        self._mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.setWindowTitle("CANdaptor")
        self.resize(1120, 640)
        self.setMinimumSize(860, 500)

        self._build_ui()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_queues)
        self.poll_timer.start(POLL_INTERVAL_MS)

    # ---- UI construction -------------------------------------------------

    def _build_ui(self):
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(self._build_connection_bar())
        layout.addWidget(self._build_frames_panel(), 1)
        layout.addWidget(self._build_send_panel())
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS['gray']};")
        layout.addWidget(self.status_label)

    def _build_connection_bar(self):
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ports = self._list_ports()
        if ports:
            self.port_combo.addItems(ports)
            self.port_combo.setCurrentText(ports[0])
        else:
            self.port_combo.addItem(NO_PORTS_MESSAGE)
            self.port_combo.setCurrentText("")
        bar.addWidget(self.port_combo, 1)

        bar.addWidget(QLabel("CAN Baud:"))
        self.baud_combo = QComboBox()
        baud_values = [format_baudrate(b) for b in sorted(CAN_BAUDRATE_CODES.keys())]
        self.baud_combo.addItems(baud_values)
        self.baud_combo.setCurrentText(format_baudrate(DEFAULT_CAN_BAUDRATE))
        bar.addWidget(self.baud_combo)

        self.timestamp_check = QCheckBox("Timestamp")
        bar.addWidget(self.timestamp_check)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connect)
        bar.addWidget(self.connect_btn)

        self.version_btn = QPushButton("Version")
        self.version_btn.clicked.connect(self.show_version)
        bar.addWidget(self.version_btn)

        self.flags_btn = QPushButton("Flags")
        self.flags_btn.clicked.connect(self.show_flags)
        bar.addWidget(self.flags_btn)
        return bar

    def _build_frames_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("Received Frames")
        title_font = QFont(title.font())
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch(1)

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self.clear_frames)
        header.addWidget(clear_btn)

        self.pause_check = QCheckBox("Pause")
        header.addWidget(self.pause_check)
        self.auto_scroll_check = QCheckBox("Auto-scroll")
        self.auto_scroll_check.setChecked(True)
        header.addWidget(self.auto_scroll_check)
        self.count_label = QLabel("0 frames · 0 IDs")
        header.addWidget(self.count_label)
        self.load_label = QLabel("Load —")
        self.load_label.setToolTip(
            "Estimated bus utilisation from received frames at the configured "
            "bitrate (includes IFS and average stuffing). Error frames and "
            "exact bit stuffing are not visible through the adapter."
        )
        header.addWidget(self.load_label)
        layout.addLayout(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_trace_table(), "Trace")
        tabs.addTab(self._build_id_table(), "IDs")
        layout.addWidget(tabs, 1)
        return panel

    def _configure_table(self, table):
        table.setFont(self._mono)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(22)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        copy = QShortcut(QKeySequence.StandardKey.Copy, table)
        copy.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy.activated.connect(lambda: self._copy_current_cell(table))

    def _build_trace_table(self):
        self.trace_table = QTableWidget(0, 7)
        self._configure_table(self.trace_table)
        self.trace_table.setHorizontalHeaderLabels(
            ["#", "Time", "TS", "Type", "ID", "DLC", "Data"]
        )
        header = self.trace_table.horizontalHeader()
        widths = (50, 95, 55, 50, 90, 40, 300)
        aligns = (
            Qt.AlignmentFlag.AlignRight,
            Qt.AlignmentFlag.AlignLeft,
            Qt.AlignmentFlag.AlignRight,
            Qt.AlignmentFlag.AlignHCenter,
            Qt.AlignmentFlag.AlignLeft,
            Qt.AlignmentFlag.AlignHCenter,
            Qt.AlignmentFlag.AlignLeft,
        )
        self._trace_aligns = aligns
        for i, width in enumerate(widths):
            self.trace_table.setColumnWidth(i, width)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.trace_table.cellDoubleClicked.connect(self._fill_send_from_trace)
        self.trace_table.customContextMenuRequested.connect(self._trace_context_menu)
        return self.trace_table

    def _build_id_table(self):
        self.id_table = QTableWidget(0, len(ID_COLUMNS))
        self._configure_table(self.id_table)
        self.id_table.setHorizontalHeaderLabels([title for _k, title, _w in ID_COLUMNS])
        header = self.id_table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(ID_COL["id"], Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self._on_id_header)
        for i, (_key, _title, width) in enumerate(ID_COLUMNS):
            self.id_table.setColumnWidth(i, width)
        header.setSectionResizeMode(ID_COL["data"], QHeaderView.ResizeMode.Stretch)
        self.id_table.setItemDelegateForColumn(
            ID_COL["data"], HexBytesDelegate(self._mono, self.id_table)
        )
        self.id_table.cellDoubleClicked.connect(self._fill_send_from_ids)
        self.id_table.customContextMenuRequested.connect(self._id_context_menu)
        self.id_table.itemSelectionChanged.connect(self._on_id_selection_changed)
        return self.id_table

    def _build_send_panel(self):
        box = QGroupBox("Send Frame")
        layout = QVBoxLayout(box)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("ID (hex):"))
        self.id_edit = QLineEdit()
        self.id_edit.setFixedWidth(110)
        self.id_edit.returnPressed.connect(self.send_frame)
        row1.addWidget(self.id_edit)
        self.extended_check = QCheckBox("Extended (29-bit)")
        row1.addWidget(self.extended_check)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Data (hex, space/comma separated):"))
        self.data_edit = QLineEdit()
        self.data_edit.returnPressed.connect(self.send_frame)
        row2.addWidget(self.data_edit, 1)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.send_frame)
        row2.addWidget(send_btn)
        layout.addLayout(row2)
        return box

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
        if self.default_port in ports:
            ports.remove(self.default_port)
            ports.insert(0, self.default_port)
        return ports

    # ---- Connection management ------------------------------------------

    def toggle_connect(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_combo.currentText().strip()
        if not port or port == NO_PORTS_MESSAGE:
            self._set_status("Error: no port specified", "red")
            return
        try:
            baudrate = parse_baudrate(self.baud_combo.currentText())
        except ValueError:
            self._set_status("Error: invalid baudrate", "red")
            return

        self._set_status("Connecting...", "orange")
        QApplication.processEvents()

        try:
            self.dev = CANdaptor(port=port)
            self.dev.ser.timeout = READ_TIMEOUT
        except Exception as e:
            self._set_status(f"Error: cannot open {port}: {e}", "red")
            self.dev = None
            return

        try:
            self.dev.set_baudrate(baudrate)
            self.dev.set_timestamp(self.timestamp_check.isChecked())
            self.dev.open()
        except Exception as e:
            self._set_status(f"Error: {e}", "red")
            try:
                self.dev.close_port()
            except Exception:
                pass
            self.dev = None
            return

        self.can_bitrate = baudrate
        self.bus_load.clear()
        self.stop_event.clear()
        self.frame_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="can-reader"
        )
        self.reader_thread.start()
        self.connected = True
        self._update_connect_button()
        self._set_status(
            f"Connected — monitoring {port} @ {format_baudrate(baudrate)}", "green"
        )

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
        self.can_bitrate = None
        self.bus_load.clear()
        self._update_connect_button()
        self._set_status("Disconnected", "gray")
        self._update_load_label(time.time())

    def _update_connect_button(self):
        self.connect_btn.setText("Disconnect" if self.connected else "Connect")

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
                        frame = CANdaptor._parse_frame(
                            line, timestamp=self.dev.timestamp_enabled
                        )
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
            if not self.pause_check.isChecked():
                self._add_frame_row(frame)
            drained += 1
            if drained > 500:
                break

        try:
            err = self.error_queue.get_nowait()
            self._handle_reader_error(err)
        except queue.Empty:
            pass

        if not self.pause_check.isChecked():
            if len(self.id_tracker):
                self._refresh_id_ages()
            self._update_load_label(time.time())

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
        self.can_bitrate = None
        self.bus_load.clear()
        self._update_connect_button()
        self._update_load_label(time.time())
        if was_connected:
            self._set_status(f"Device error: {err}", "red")

    # ---- Frames tables ---------------------------------------------------

    def _add_frame_row(self, frame):
        now = time.time()
        self._add_trace_row(frame, now)
        self._upsert_id_row(frame, now)
        self.bus_load.add_frame(frame, now)
        self._update_count_label()

    def _add_trace_row(self, frame, now):
        self.frame_count += 1
        values = (
            str(self.frame_count),
            format_wall_time(now),
            f"{frame['timestamp']:04X}" if "timestamp" in frame else "",
            "Ext" if frame["extended"] else "Std",
            format_can_id(frame["id"], frame["extended"]),
            str(len(frame["data"])),
            format_data_bytes(frame["data"]),
        )
        row = self.trace_table.rowCount()
        self.trace_table.insertRow(row)
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setTextAlignment(
                self._trace_aligns[col] | Qt.AlignmentFlag.AlignVCenter
            )
            self.trace_table.setItem(row, col, item)
        if self.trace_table.rowCount() > MAX_ROWS:
            self.trace_table.removeRow(0)
        if self.auto_scroll_check.isChecked():
            self.trace_table.scrollToBottom()

    def _id_iid(self, stats):
        prefix = "X" if stats.extended else "T"
        return f"{prefix}{stats.can_id:08X}"

    def _key_from_iid(self, iid):
        return frame_key(int(iid[1:], 16), iid[0] == "X")

    def _set_cell(self, row, col, text, align=Qt.AlignmentFlag.AlignLeft):
        item = self.id_table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.id_table.setItem(row, col, item)
        item.setText(str(text))
        item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _paint_id_row(self, row, stats, now):
        changed = self.id_tracker.display_changed(stats, now)
        stale = self.id_tracker.is_stale(stats, now)
        late = self.id_tracker.is_late(stats, now)
        selected = self.id_table.currentRow() == row
        iid = self._id_iid(stats)
        kind = "Ext" if stats.extended else "Std"
        values = {
            "id": stats.id_str(),
            "type": kind,
            "dlc": stats.dlc,
            "data": format_data_bytes(stats.data),
            "chg": format_change_mask(changed, stats.dlc),
            "count": stats.count,
            "period": format_period_ms(stats.period_ms),
            "avg": format_period_ms(stats.avg_period_ms),
            "std": format_std_ms(stats.std_period_ms),
            "hz": format_hz(stats.hz),
            "age": format_age_ms(stats.age_s(now)),
            "last": format_wall_time(stats.last_seen),
        }
        right = Qt.AlignmentFlag.AlignRight
        center = Qt.AlignmentFlag.AlignHCenter
        left = Qt.AlignmentFlag.AlignLeft
        aligns = {
            "id": left,
            "type": center,
            "dlc": center,
            "data": left,
            "chg": left,
            "count": right,
            "period": right,
            "avg": right,
            "std": right,
            "hz": right,
            "age": right,
            "last": left,
        }
        for key, _title, _w in ID_COLUMNS:
            col = ID_COL[key]
            item = self._set_cell(row, col, values[key], aligns[key])
            item.setBackground(QBrush())
            item.setData(Qt.ItemDataRole.BackgroundRole, None)
            if stale and not selected:
                item.setForeground(QBrush(STALE_FG))
            else:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
        if late and not stale and not selected:
            for key in ("age", "last"):
                item = self.id_table.item(row, ID_COL[key])
                item.setForeground(QBrush(LATE_FG))
        id_item = self.id_table.item(row, ID_COL["id"])
        id_item.setData(IID_ROLE, iid)
        data_item = self.id_table.item(row, ID_COL["data"])
        data_item.setData(DATA_ROLE, bytes(stats.data))
        data_item.setData(CHANGED_ROLE, tuple(changed))
        data_item.setData(STALE_ROLE, stale)
        self._id_visual[iid] = (stale, any(changed), late)

    def _shift_id_rows(self, inserted_at):
        for iid, row in list(self._id_rows.items()):
            if row >= inserted_at:
                self._id_rows[iid] = row + 1

    def _upsert_id_row(self, frame, now):
        stats = self.id_tracker.update(frame, now)
        iid = self._id_iid(stats)
        if iid in self._id_rows:
            self._paint_id_row(self._id_rows[iid], stats, now)
            return
        index = self._id_insert_index(stats)
        row = self.id_table.rowCount() if index == "end" else index
        if index != "end":
            self._shift_id_rows(row)
        self.id_table.insertRow(row)
        self._id_rows[iid] = row
        self._paint_id_row(row, stats, now)

    def _id_insert_index(self, stats):
        col, reverse = self._id_sort
        if col not in ID_SORTABLE:
            return "end"
        now = time.time()
        new_key = self._id_sort_key(stats, col, now)
        best = None
        for iid, row in self._id_rows.items():
            other = self.id_tracker.get(self._key_from_iid(iid))
            if other is None:
                continue
            other_key = self._id_sort_key(other, col, now)
            comes_before = new_key > other_key if reverse else new_key < other_key
            if comes_before and (best is None or row < best):
                best = row
        return "end" if best is None else best

    def _refresh_id_ages(self):
        now = time.time()
        for iid, row in self._id_rows.items():
            stats = self.id_tracker.get(self._key_from_iid(iid))
            if stats is None:
                continue
            age_item = self.id_table.item(row, ID_COL["age"])
            if age_item is not None:
                age_item.setText(format_age_ms(stats.age_s(now)))
            stale = self.id_tracker.is_stale(stats, now)
            late = self.id_tracker.is_late(stats, now)
            has_chg = (not stale) and any(self.id_tracker.display_changed(stats, now))
            prev = self._id_visual.get(iid)
            if prev == (stale, has_chg, late):
                continue
            self._paint_id_row(row, stats, now)

    def _on_id_header(self, section):
        key = ID_COLUMNS[section][0]
        header = self.id_table.horizontalHeader()
        if key not in ID_SORTABLE:
            order = (
                Qt.SortOrder.DescendingOrder
                if self._id_sort[1]
                else Qt.SortOrder.AscendingOrder
            )
            header.setSortIndicator(ID_COL[self._id_sort[0]], order)
            return
        reverse = self._id_sort[0] == key and not self._id_sort[1]
        self._id_sort = (key, reverse)
        header.setSortIndicator(
            section,
            Qt.SortOrder.DescendingOrder if reverse else Qt.SortOrder.AscendingOrder,
        )
        selected_iid = self._selected_id_iid()
        now = time.time()
        rows = []
        for stats in self.id_tracker.values():
            rows.append((self._id_sort_key(stats, key, now), self._id_iid(stats)))
        rows.sort(key=lambda item: item[0], reverse=reverse)
        ordered = [iid for _, iid in rows]
        self.id_table.setUpdatesEnabled(False)
        self.id_table.setRowCount(0)
        self._id_rows = {}
        restore_row = -1
        for iid in ordered:
            stats = self.id_tracker.get(self._key_from_iid(iid))
            if stats is None:
                continue
            row = self.id_table.rowCount()
            self.id_table.insertRow(row)
            self._id_rows[iid] = row
            self._paint_id_row(row, stats, now)
            if iid == selected_iid:
                restore_row = row
        self.id_table.setUpdatesEnabled(True)
        if restore_row >= 0:
            self.id_table.selectRow(restore_row)
            self.id_table.setCurrentCell(restore_row, ID_COL[key])

    def _id_sort_key(self, stats, col, now):
        if col == "id":
            return (stats.can_id, stats.extended)
        if col == "type":
            return (stats.extended, stats.can_id)
        if col == "dlc":
            return stats.dlc
        if col == "data":
            return stats.data
        if col == "chg":
            return sum(1 for bit in stats.changed if bit)
        if col == "count":
            return stats.count
        if col == "period":
            return stats.period_s if stats.period_s is not None else -1.0
        if col == "avg":
            return stats.ewma_s if stats.ewma_s is not None else -1.0
        if col == "std":
            return stats.std_period_ms if stats.std_period_ms is not None else -1.0
        if col == "hz":
            return stats.hz if stats.hz is not None else -1.0
        if col == "age":
            return stats.age_s(now)
        if col == "last":
            return stats.last_seen
        return stats.id_str()

    def _selected_id_iid(self):
        row = self.id_table.currentRow()
        if row < 0:
            return None
        item = self.id_table.item(row, ID_COL["id"])
        if item is None:
            return None
        return item.data(IID_ROLE)

    def _stats_for_id_row(self, row):
        item = self.id_table.item(row, ID_COL["id"])
        if item is None:
            return None
        iid = item.data(IID_ROLE)
        if not iid:
            return None
        return self.id_tracker.get(self._key_from_iid(iid))

    def _on_id_selection_changed(self):
        now = time.time()
        current = self.id_table.currentRow()
        rows = {current, self._id_style_row}
        self._id_style_row = current
        for row in rows:
            if row is None or row < 0:
                continue
            stats = self._stats_for_id_row(row)
            if stats is not None:
                self._paint_id_row(row, stats, now)

    def _table_cell_text(self, table, row, col):
        if table is self.id_table and col == ID_COL["data"]:
            item = table.item(row, col)
            data = item.data(DATA_ROLE) if item is not None else None
            if isinstance(data, (bytes, bytearray)):
                return format_data_bytes(data)
        item = table.item(row, col)
        return "" if item is None else item.text()

    def _table_row_text(self, table, row):
        parts = []
        for col in range(table.columnCount()):
            parts.append(self._table_cell_text(table, row, col))
        return "\t".join(parts)

    def _copy_text(self, text):
        text = (text or "").strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)

    def _copy_current_cell(self, table):
        row = table.currentRow()
        col = table.currentColumn()
        if row < 0 or col < 0:
            return
        self._copy_text(self._table_cell_text(table, row, col))

    def _copy_id_from_table(self, table):
        row = table.currentRow()
        if row < 0:
            return
        if table is self.id_table:
            self._copy_text(self._table_cell_text(table, row, ID_COL["id"]))
        else:
            self._copy_text(self._table_cell_text(table, row, TRACE_COL_ID))

    def _copy_data_from_table(self, table):
        row = table.currentRow()
        if row < 0:
            return
        if table is self.id_table:
            self._copy_text(self._table_cell_text(table, row, ID_COL["data"]))
        else:
            self._copy_text(self._table_cell_text(table, row, TRACE_COL_DATA))

    def _copy_row_from_table(self, table):
        row = table.currentRow()
        if row < 0:
            return
        self._copy_text(self._table_row_text(table, row))

    def _select_table_index(self, table, pos):
        index = table.indexAt(pos)
        if not index.isValid():
            return False
        table.selectRow(index.row())
        table.setCurrentCell(index.row(), index.column())
        return True

    def _table_copy_menu(self, table, pos, send_slot):
        if not self._select_table_index(table, pos):
            return
        menu = QMenu(table)
        copy_cell = QAction("Copy", menu)
        copy_cell.setShortcut(QKeySequence.StandardKey.Copy)
        copy_cell.triggered.connect(lambda: self._copy_current_cell(table))
        copy_id = QAction("Copy ID", menu)
        copy_id.triggered.connect(lambda: self._copy_id_from_table(table))
        copy_data = QAction("Copy data", menu)
        copy_data.triggered.connect(lambda: self._copy_data_from_table(table))
        copy_row = QAction("Copy row", menu)
        copy_row.triggered.connect(lambda: self._copy_row_from_table(table))
        send = QAction("Send this frame", menu)
        send.triggered.connect(lambda: send_slot(table.currentRow(), table.currentColumn()))
        menu.addAction(copy_cell)
        menu.addAction(copy_id)
        menu.addAction(copy_data)
        menu.addAction(copy_row)
        menu.addSeparator()
        menu.addAction(send)
        menu.exec(table.viewport().mapToGlobal(pos))

    def _id_context_menu(self, pos):
        self._table_copy_menu(self.id_table, pos, self._fill_send_from_ids)

    def _trace_context_menu(self, pos):
        self._table_copy_menu(self.trace_table, pos, self._fill_send_from_trace)

    def _fill_send_from_trace(self, row, _col):
        type_item = self.trace_table.item(row, 3)
        id_item = self.trace_table.item(row, 4)
        data_item = self.trace_table.item(row, 6)
        if type_item is None or id_item is None or data_item is None:
            return
        self.extended_check.setChecked(type_item.text() == "Ext")
        self.id_edit.setText(id_item.text())
        self.data_edit.setText(data_item.text())

    def _fill_send_from_ids(self, row, _col):
        id_item = self.id_table.item(row, ID_COL["id"])
        if id_item is None:
            return
        iid = id_item.data(IID_ROLE)
        stats = self.id_tracker.get(self._key_from_iid(iid)) if iid else None
        if stats is None:
            return
        self.extended_check.setChecked(stats.extended)
        self.id_edit.setText(stats.id_str())
        self.data_edit.setText(format_data_bytes(stats.data))

    def _update_count_label(self):
        self.count_label.setText(
            f"{self.frame_count} frames · {len(self.id_tracker)} IDs"
        )

    def _update_load_label(self, now):
        ratio = self.bus_load.ratio(now, self.can_bitrate)
        if ratio is None:
            self.load_label.setText("Load —")
            self.load_label.setStyleSheet(f"color: {STATUS_COLORS['gray']};")
            return
        pct = 100.0 * ratio
        self.load_label.setText(f"Load {pct:.1f}%")
        if pct >= 80:
            color = STATUS_COLORS["red"]
        elif pct >= 50:
            color = STATUS_COLORS["orange"]
        else:
            color = STATUS_COLORS["green"]
        self.load_label.setStyleSheet(f"color: {color};")

    def clear_frames(self):
        self.trace_table.setRowCount(0)
        self.id_table.setRowCount(0)
        self._id_rows = {}
        self._id_visual = {}
        self.id_tracker.clear()
        self.frame_count = 0
        self._id_sort = ("id", False)
        self._id_style_row = -1
        header = self.id_table.horizontalHeader()
        header.setSortIndicator(ID_COL["id"], Qt.SortOrder.AscendingOrder)
        self._update_count_label()

    # ---- Send ------------------------------------------------------------

    def send_frame(self):
        if not self.connected or self.dev is None:
            self._set_status("Error: not connected", "red")
            return
        id_text = self.id_edit.text().strip()
        if not id_text:
            self._set_status("Error: CAN ID required", "red")
            return
        try:
            frame_id = int(id_text, 16)
        except ValueError:
            self._set_status(f"Error: invalid CAN ID '{id_text}'", "red")
            return
        try:
            data_bytes = parse_data_bytes([self.data_edit.text()])
        except ValueError:
            self._set_status("Error: invalid data bytes", "red")
            return
        if len(data_bytes) > 8:
            self._set_status("Error: data exceeds 8 bytes", "red")
            return
        extended = self.extended_check.isChecked()
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
            QMessageBox.information(self, "Firmware Version", ver)
            self._set_status(f"Version: {ver}", "green")
        except Exception as e:
            self._set_status(f"Version error: {e}", "red")

    def show_flags(self):
        if not self._ensure_connected():
            return
        try:
            flags = self.dev.status_flags()
            QMessageBox.information(self, "Status Flags", flags)
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
        self.status_label.setText(text)
        if color:
            self.status_label.setStyleSheet(
                f"color: {STATUS_COLORS.get(color, color)};"
            )

    def closeEvent(self, event):
        self.disconnect()
        event.accept()


def main():
    parser = argparse.ArgumentParser(
        prog="candaptor-gui", description="CANdaptor GUI (Qt)"
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help=f"default serial port (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("CANdaptor")
    window = CandaptorWindow(default_port=args.port)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
