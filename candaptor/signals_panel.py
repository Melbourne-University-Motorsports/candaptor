"""Signals & Graphs panel for DBC decoding, live monitoring, and real-time plotting."""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from candaptor.dbc_engine import DBCEngine, SignalInfo

# Tree Columns
COL_NAME = 0
COL_VALUE = 1
COL_UNIT = 2
COL_WATCH = 3
COL_G1 = 4
COL_G2 = 5
COL_G3 = 6
COL_G4 = 7

GRAPH_COLS = [COL_G1, COL_G2, COL_G3, COL_G4]

CURVE_COLORS = [
    "#00e5ff",  # Cyan
    "#ffea00",  # Yellow
    "#00e676",  # Green
    "#ff1744",  # Red
    "#d500f9",  # Magenta
    "#ff9100",  # Orange
    "#38bdf8",  # Sky Blue
    "#f43f5e",  # Rose
    "#a78bfa",  # Purple
    "#4ade80",  # Lime
]


def _make_color_icon(color_hex: str, size: int = 12) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color_hex))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    painter.end()
    return QIcon(pix)


class SignalsPanel(QWidget):
    """Integrated DBC signal browser, live monitor, and synchronized multi-plot widget."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.dbc_engine = DBCEngine()
        self.settings = QSettings("candaptor", "candaptor")

        self.num_graphs = 2
        self.time_window = 10.0  # seconds
        self.is_paused = False
        self.start_time: Optional[float] = None
        self._last_time = 0.0

        # Monospace font for numerical values
        self._mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        # Mapping: (msg_id, sig_name) -> QTreeWidgetItem
        self.signal_items: Dict[Tuple[int, str], QTreeWidgetItem] = {}
        # Mapping: msg_id -> QTreeWidgetItem
        self.message_items: Dict[int, QTreeWidgetItem] = {}

        # Assigned colors: (msg_id, sig_name) -> hex color
        self.signal_colors: Dict[Tuple[int, str], str] = {}
        self._color_index = 0

        # Checked state: (msg_id, sig_name) -> set of graph indices (0..3)
        self.signal_graphs: Dict[Tuple[int, str], Set[int]] = {}
        self.watched_signals: Set[Tuple[int, str]] = set()

        # pyqtgraph setup
        self.plots: List[pg.PlotItem] = []
        # Mapping: (msg_id, sig_name, graph_idx) -> PlotDataItem
        self.curves: Dict[Tuple[int, str, int], pg.PlotDataItem] = {}

        self._build_ui()
        self._restore_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # Left panel: DBC Browser & Watcher
        left_widget = self._build_left_panel()
        self.splitter.addWidget(left_widget)

        # Right panel: Multi-Graph Plotter
        right_widget = self._build_right_panel()
        self.splitter.addWidget(right_widget)

        # Proportions: ~45% left, ~55% right
        self.splitter.setSizes([450, 650])
        layout.addWidget(self.splitter)

    # ---- Left Panel: DBC Browser & Watcher --------------------------------

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Top DBC bar
        dbc_bar = QHBoxLayout()
        dbc_bar.setSpacing(6)

        self.load_dbc_btn = QPushButton("Load DBC...")
        self.load_dbc_btn.clicked.connect(self._on_load_dbc_clicked)
        dbc_bar.addWidget(self.load_dbc_btn)

        self.clear_dbc_btn = QPushButton("Clear DBC")
        self.clear_dbc_btn.clicked.connect(self._on_clear_dbc_clicked)
        self.clear_dbc_btn.setEnabled(False)
        dbc_bar.addWidget(self.clear_dbc_btn)

        self.dbc_status_label = QLabel("No DBC loaded")
        self.dbc_status_label.setStyleSheet("color: #888888;")
        dbc_bar.addWidget(self.dbc_status_label, 1)

        layout.addLayout(dbc_bar)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter signals or messages...")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self.filter_edit, 1)

        self.watch_only_check = QCheckBox("Watched only")
        self.watch_only_check.toggled.connect(self._apply_filter)
        filter_bar.addWidget(self.watch_only_check)

        self.plotted_only_check = QCheckBox("Plotted only")
        self.plotted_only_check.toggled.connect(self._apply_filter)
        filter_bar.addWidget(self.plotted_only_check)

        layout.addLayout(filter_bar)

        # Signal Tree Widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            "Signal / Message", "Value", "Unit", "Watch", "G1", "G2", "G3", "G4"
        ])
        self.tree.setFont(self._mono)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)

        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(COL_UNIT, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(COL_VALUE, 90)
        self.tree.setColumnWidth(COL_UNIT, 60)
        self.tree.setColumnWidth(COL_WATCH, 48)

        for col in GRAPH_COLS:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(col, 36)

        self.tree.itemChanged.connect(self._on_tree_item_changed)
        layout.addWidget(self.tree, 1)

        return panel

    # ---- Right Panel: Multi-Graph Plotter ---------------------------------

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Graph Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Graph count selector
        toolbar.addWidget(QLabel("Graphs:"))
        self.graph_btn_group = QButtonGroup(self)
        for count in (1, 2, 3, 4):
            btn = QRadioButton(str(count))
            if count == self.num_graphs:
                btn.setChecked(True)
            self.graph_btn_group.addButton(btn, count)
            toolbar.addWidget(btn)
        self.graph_btn_group.idClicked.connect(self._on_graph_count_changed)

        toolbar.addSpacing(12)

        # Time window selector
        toolbar.addWidget(QLabel("Window:"))
        self.window_btn_group = QButtonGroup(self)
        for sec in (5, 10, 30, 60):
            btn = QRadioButton(f"{sec}s")
            if sec == int(self.time_window):
                btn.setChecked(True)
            self.window_btn_group.addButton(btn, sec)
            toolbar.addWidget(btn)
        self.window_btn_group.idClicked.connect(self._on_window_changed)

        toolbar.addSpacing(12)

        # Pause button
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause_toggled)
        toolbar.addWidget(self.pause_btn)

        # Clear graphs button
        self.clear_graphs_btn = QPushButton("Clear")
        self.clear_graphs_btn.clicked.connect(self.clear_graphs)
        toolbar.addWidget(self.clear_graphs_btn)

        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # pyqtgraph GraphicsLayoutWidget
        pg.setConfigOption("background", "#161618")
        pg.setConfigOption("foreground", "#d0d0d0")
        pg.setConfigOption("antialias", True)

        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.glw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.glw.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.glw, 1)

        # Hover readout footer strip below graphs
        self.readout_label = QLabel("Hover cursor over graphs to read values")
        self.readout_label.setFont(self._mono)
        self.readout_label.setStyleSheet("color: #9ca3af; font-size: 11px; padding: 2px 4px;")
        self.readout_label.setMinimumWidth(0)
        self.readout_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.readout_label)

        self._init_plots()

        return panel

    def _init_plots(self) -> None:
        """Initialize up to 4 PlotItems in pyqtgraph."""
        self.glw.clear()
        self.plots.clear()

        for i in range(4):
            plot = pg.PlotItem(title=f"Graph {i + 1}")
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.addLegend(offset=(10, 10))
            plot.setMouseEnabled(x=True, y=True)
            plot.enableAutoRange(axis='y', enable=True)
            plot.vb.disableAutoRange(pg.ViewBox.XAxis)
            plot.getAxis('left').setWidth(55)

            # Link X-axes with Graph 1
            if i > 0 and self.plots:
                plot.setXLink(self.plots[0])

            self.plots.append(plot)

        if self.glw.scene():
            self.glw.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self._layout_plots()

    def _layout_plots(self) -> None:
        """Layout active plots in glw and sync tree columns."""
        self.glw.clear()
        for i in range(self.num_graphs):
            self.glw.addItem(self.plots[i], row=i, col=0)

        # Update Tree Columns (G1..G4)
        for i, col in enumerate(GRAPH_COLS):
            self.tree.setColumnHidden(col, i >= self.num_graphs)

        # Re-attach curves to active plots
        self._sync_all_curves()

    # ---- Graph and Window Controls ---------------------------------------

    def _on_graph_count_changed(self, count: int) -> None:
        if count != self.num_graphs:
            self.num_graphs = count
            self.settings.setValue("num_graphs", count)
            self._layout_plots()
            self._update_engine_active_signals()

    def _on_window_changed(self, sec: int) -> None:
        self.time_window = float(sec)
        self.settings.setValue("time_window", sec)

    def _on_pause_toggled(self, paused: bool) -> None:
        self.is_paused = paused
        self.pause_btn.setText("Resume" if paused else "Pause")

    def clear_graphs(self) -> None:
        """Clear all curve data points and reset time origin."""
        self.dbc_engine.clear_history()
        self.start_time = None
        for curve in self.curves.values():
            curve.setData([], [])
        if self.plots:
            self.plots[0].enableAutoRange()

    # ---- DBC Loading & Tree Population -----------------------------------

    def _on_load_dbc_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Vector DBC File", "", "CAN Database (*.dbc);;All Files (*)"
        )
        if path:
            self.load_dbc_file(path)

    def _on_clear_dbc_clicked(self) -> None:
        self.dbc_engine.clear()
        self.settings.remove("last_dbc_path")
        self.signal_items.clear()
        self.message_items.clear()
        self.signal_colors.clear()
        self.signal_graphs.clear()
        self.watched_signals.clear()
        self.tree.clear()
        self.clear_graphs()
        self.dbc_status_label.setText("No DBC loaded")
        self.clear_dbc_btn.setEnabled(False)

    def load_dbc_file(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            self.dbc_engine.load_file(path)
            self.settings.setValue("last_dbc_path", path)
            self._populate_tree()
            filename = os.path.basename(path)
            num_msgs = len(self.dbc_engine.message_infos)
            num_sigs = len(self.dbc_engine.signal_infos)
            self.dbc_status_label.setText(f"{filename} ({num_msgs} msgs, {num_sigs} sigs)")
            self.dbc_status_label.setStyleSheet("color: #4ade80;")
            self.clear_dbc_btn.setEnabled(True)
            return True
        except Exception as e:
            self.dbc_status_label.setText(f"Error: {e}")
            self.dbc_status_label.setStyleSheet("color: #ef4444;")
            return False

    def _populate_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self.signal_items.clear()
        self.message_items.clear()

        # Sort messages by frame ID
        for frame_id in sorted(self.dbc_engine.message_infos.keys()):
            msg = self.dbc_engine.message_infos[frame_id]
            id_hex = f"0x{frame_id:03X}" if not msg.is_extended_frame else f"0x{frame_id:08X}"
            msg_title = f"{id_hex}  {msg.name}"

            msg_item = QTreeWidgetItem(self.tree)
            msg_item.setText(COL_NAME, msg_title)
            msg_item.setFont(COL_NAME, QFont(self._mono.family(), -1, QFont.Weight.Bold))
            msg_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            self.message_items[frame_id] = msg_item

            for sig in msg.signals:
                sig_item = QTreeWidgetItem(msg_item)
                sig_item.setText(COL_NAME, f"  {sig.name}")
                sig_item.setText(COL_VALUE, "—")
                sig_item.setText(COL_UNIT, sig.unit)

                # Set flags: Item is checkable for Watch and Graphs
                sig_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )

                # Initialize un-checked states
                sig_item.setCheckState(COL_WATCH, Qt.CheckState.Unchecked)
                for col in GRAPH_COLS:
                    sig_item.setCheckState(col, Qt.CheckState.Unchecked)

                self.signal_items[sig.key] = sig_item

        self.tree.blockSignals(False)
        self.tree.expandAll()
        self._apply_filter()

    # ---- Tree Interaction & Checkboxes -----------------------------------

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        # Find which signal this corresponds to
        sig_key = None
        for k, it in self.signal_items.items():
            if it is item:
                sig_key = k
                break
        if not sig_key:
            return

        if column == COL_WATCH:
            is_checked = item.checkState(COL_WATCH) == Qt.CheckState.Checked
            if is_checked:
                self.watched_signals.add(sig_key)
            else:
                self.watched_signals.discard(sig_key)

        elif column in GRAPH_COLS:
            graph_idx = column - COL_G1
            is_checked = item.checkState(column) == Qt.CheckState.Checked

            if sig_key not in self.signal_graphs:
                self.signal_graphs[sig_key] = set()

            self.tree.blockSignals(True)
            try:
                if is_checked:
                    self.signal_graphs[sig_key].add(graph_idx)
                    # Assign color icon to the item
                    color = self._get_or_assign_color(sig_key)
                    item.setIcon(column, _make_color_icon(color))
                else:
                    self.signal_graphs[sig_key].discard(graph_idx)
                    item.setIcon(column, QIcon())
            finally:
                self.tree.blockSignals(False)

            self._update_engine_active_signals()
            self._sync_curve(sig_key, graph_idx, is_checked)

    def _get_or_assign_color(self, key: Tuple[int, str]) -> str:
        if key not in self.signal_colors:
            color = CURVE_COLORS[self._color_index % len(CURVE_COLORS)]
            self._color_index += 1
            self.signal_colors[key] = color
        return self.signal_colors[key]

    def _update_engine_active_signals(self) -> None:
        """Inform DBCEngine which signals need history buffering."""
        active = set()
        for sig_key, graphs in self.signal_graphs.items():
            # Only count graphs that are currently within num_graphs
            if any(g < self.num_graphs for g in graphs):
                active.add(sig_key)
        self.dbc_engine.set_active_plot_signals(active)

    def _sync_curve(self, sig_key: Tuple[int, str], graph_idx: int, enabled: bool) -> None:
        curve_key = (sig_key[0], sig_key[1], graph_idx)
        if enabled:
            if curve_key not in self.curves and graph_idx < len(self.plots):
                color = self._get_or_assign_color(sig_key)
                sig_info = self.dbc_engine.signal_infos.get(sig_key)
                legend_label = sig_key[1]
                if sig_info and sig_info.unit:
                    legend_label = f"{sig_key[1]} ({sig_info.unit})"

                plot = self.plots[graph_idx]
                pen = pg.mkPen(color=color, width=2)
                curve = plot.plot(name=legend_label, pen=pen)
                self.curves[curve_key] = curve
        else:
            if curve_key in self.curves:
                curve = self.curves.pop(curve_key)
                if graph_idx < len(self.plots):
                    self.plots[graph_idx].removeItem(curve)

    def _sync_all_curves(self) -> None:
        """Re-sync curves when plots are modified or laid out."""
        # Remove curves for hidden graphs
        for (msg_id, sig_name, g_idx), curve in list(self.curves.items()):
            if g_idx >= self.num_graphs:
                self.plots[g_idx].removeItem(curve)
                del self.curves[(msg_id, sig_name, g_idx)]

        # Add curves for active graphs
        for sig_key, graphs in self.signal_graphs.items():
            for g_idx in graphs:
                if g_idx < self.num_graphs and (sig_key[0], sig_key[1], g_idx) not in self.curves:
                    self._sync_curve(sig_key, g_idx, True)

    # ---- Filter & Search --------------------------------------------------

    def _apply_filter(self) -> None:
        query = self.filter_edit.text().strip().lower()
        watch_only = self.watch_only_check.isChecked()
        plotted_only = self.plotted_only_check.isChecked()

        for frame_id, msg_item in self.message_items.items():
            msg_visible = False
            msg_text = msg_item.text(COL_NAME).lower()

            for i in range(msg_item.childCount()):
                child = msg_item.child(i)
                sig_name = child.text(COL_NAME).strip().lower()
                sig_key = None
                for k, it in self.signal_items.items():
                    if it is child:
                        sig_key = k
                        break

                match_text = (not query) or (query in sig_name) or (query in msg_text)
                match_watch = (not watch_only) or (sig_key in self.watched_signals)
                match_plot = (not plotted_only) or bool(self.signal_graphs.get(sig_key))

                child_visible = match_text and match_watch and match_plot
                child.setHidden(not child_visible)
                if child_visible:
                    msg_visible = True

            msg_item.setHidden(not msg_visible)

    # ---- Real-time Ingestion & Frame Processing --------------------------

    def process_frames(self, frames: list, now: float) -> None:
        """Ingest batch of CAN frames from the GUI polling timer."""
        if not self.dbc_engine.is_loaded:
            return

        if self.start_time is None and frames:
            self.start_time = now
        self._last_time = now

        for frame in frames:
            frame_id = frame["id"] if isinstance(frame, dict) else frame.id
            frame_data = frame["data"] if isinstance(frame, dict) else frame.data
            self.dbc_engine.decode_frame(frame_id, frame_data, now)

    def update_views(self) -> None:
        """Refresh visible text values and real-time graph curves (called periodically)."""
        if not self.dbc_engine.is_loaded:
            return

        # 1. Update tree values for visible items
        for key, item in self.signal_items.items():
            if not item.isHidden() and not item.parent().isHidden():
                disp = self.dbc_engine.get_display_value(key)
                if item.text(COL_VALUE) != disp:
                    item.setText(COL_VALUE, disp)

        # 2. Update real-time curves if not paused
        if self.is_paused or self.start_time is None or not self.curves:
            return

        current_elapsed = self._last_time - self.start_time
        t_min = max(0.0, current_elapsed - self.time_window)
        t_max = max(self.time_window, current_elapsed)

        # Update curve data
        for (msg_id, sig_name, g_idx), curve in self.curves.items():
            buf = self.dbc_engine.history.get((msg_id, sig_name))
            if not buf:
                continue

            # Convert history deque to numpy arrays
            pts = list(buf)
            if not pts:
                continue

            raw_t, raw_v = zip(*pts)
            t_arr = np.array(raw_t, dtype=np.float64) - self.start_time
            v_arr = np.array(raw_v, dtype=np.float64)

            # Filter points within window + 1 buffer point before to keep continuous line
            mask = t_arr >= (t_min - 1.0)
            if np.any(mask):
                curve.setData(t_arr[mask], v_arr[mask])
            else:
                curve.setData([], [])

        # Auto-scroll X-axis across all visible graphs
        if self.plots:
            self.plots[0].setXRange(t_min, t_max, padding=0.0)

    # ---- Mouse Crosshair Readout ------------------------------------------

    def _on_mouse_moved(self, pos: QPointF) -> None:
        target_idx = None
        target_plot = None
        for idx in range(min(self.num_graphs, len(self.plots))):
            p = self.plots[idx]
            if p.sceneBoundingRect().contains(pos):
                target_idx = idx
                target_plot = p
                break

        if target_plot is None:
            return

        mouse_point = target_plot.vb.mapSceneToView(pos)
        x_val = mouse_point.x()

        readouts = [f"T: {x_val:6.2f}s"]
        for (msg_id, sig_name, g_idx), curve in self.curves.items():
            if g_idx == target_idx:
                buf = self.dbc_engine.history.get((msg_id, sig_name))
                if buf:
                    pts = list(buf)
                    if pts and self.start_time is not None:
                        times = [p[0] - self.start_time for p in pts]
                        idx = np.argmin(np.abs(np.array(times) - x_val))
                        val = pts[idx][1]
                        sig_info = self.dbc_engine.signal_infos.get((msg_id, sig_name))
                        unit = f" {sig_info.unit}" if sig_info and sig_info.unit else ""
                        readouts.append(f"{sig_name}: {val:.2f}{unit}")

        new_text = " | ".join(readouts)
        if self.readout_label.text() != new_text:
            self.readout_label.setText(new_text)

    # ---- Settings Persistence --------------------------------------------

    def _restore_settings(self) -> None:
        last_dbc = self.settings.value("last_dbc_path", "")
        if last_dbc and os.path.isfile(last_dbc):
            self.load_dbc_file(last_dbc)

        saved_graphs = self.settings.value("num_graphs", None)
        if saved_graphs is not None:
            count = int(saved_graphs)
            btn = self.graph_btn_group.button(count)
            if btn:
                btn.setChecked(True)
                self.num_graphs = count
                self._layout_plots()

        saved_win = self.settings.value("time_window", None)
        if saved_win is not None:
            sec = int(saved_win)
            btn = self.window_btn_group.button(sec)
            if btn:
                btn.setChecked(True)
                self.time_window = float(sec)
