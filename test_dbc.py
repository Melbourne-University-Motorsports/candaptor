"""Tests for DBC engine, frame decoding, and Signals & Graphs panel."""

import os
import tempfile
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from candaptor.dbc_engine import DBCEngine
from candaptor.signals_panel import (
    COL_G1,
    COL_G2,
    COL_G3,
    COL_G4,
    COL_NAME,
    COL_UNIT,
    COL_VALUE,
    COL_WATCH,
    SignalsPanel,
)

SAMPLE_DBC = """VERSION ""

NS_ :

BS_:

BU_:

BO_ 256 EngineData: 8 Vector__XXX
 SG_ EngineRPM : 0|16@1+ (0.25,0) [0|8000] "rpm" Vector__XXX
 SG_ CoolantTemp : 16|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
 SG_ ThrottlePos : 24|8@1+ (0.5,0) [0|100] "%" Vector__XXX

BO_ 512 VehicleSpeed: 4 Vector__XXX
 SG_ Speed : 0|16@1+ (0.1,0) [0|300] "km/h" Vector__XXX
"""


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_dbc_file():
    with tempfile.NamedTemporaryFile("w", suffix=".dbc", delete=False) as f:
        f.write(SAMPLE_DBC)
        f.flush()
        path = f.name
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_dbc_engine_loading(temp_dbc_file):
    engine = DBCEngine()
    assert not engine.is_loaded
    engine.load_file(temp_dbc_file)
    assert engine.is_loaded
    assert len(engine.message_infos) == 2
    assert 256 in engine.message_infos
    assert 512 in engine.message_infos

    msg256 = engine.message_infos[256]
    assert msg256.name == "EngineData"
    assert len(msg256.signals) == 3

    rpm_sig = engine.signal_infos[(256, "EngineRPM")]
    assert rpm_sig.name == "EngineRPM"
    assert rpm_sig.unit == "rpm"
    assert rpm_sig.scale == 0.25
    assert rpm_sig.offset == 0.0

    temp_sig = engine.signal_infos[(256, "CoolantTemp")]
    assert temp_sig.unit == "degC"
    assert temp_sig.scale == 1.0
    assert temp_sig.offset == -40.0


def test_dbc_engine_decode_frame(temp_dbc_file):
    engine = DBCEngine()
    engine.load_file(temp_dbc_file)

    # Frame 256:
    # EngineRPM = 2000 rpm -> raw 8000 = 0x1F40 (little endian: 0x40, 0x1F)
    # CoolantTemp = 90 degC -> raw 130 = 0x82
    # ThrottlePos = 50 % -> raw 100 = 0x64
    payload = bytes([0x40, 0x1F, 0x82, 0x64, 0x00, 0x00, 0x00, 0x00])
    decoded = engine.decode_frame(256, payload, timestamp=100.5)

    assert decoded is not None
    assert decoded["EngineRPM"] == 2000.0
    assert decoded["CoolantTemp"] == 90.0
    assert decoded["ThrottlePos"] == 50.0

    assert engine.get_display_value((256, "EngineRPM")) == "2000"
    assert engine.get_display_value((256, "CoolantTemp")) == "90"

    # Frame with unknown ID
    assert engine.decode_frame(999, bytes(8), 100.6) is None


def test_dbc_engine_history(temp_dbc_file):
    engine = DBCEngine()
    engine.load_file(temp_dbc_file)
    engine.set_active_plot_signals({(256, "EngineRPM")})

    payload = bytes([0x40, 0x1F, 0x82, 0x64, 0x00, 0x00, 0x00, 0x00])
    engine.decode_frame(256, payload, timestamp=10.0)

    history = engine.history[(256, "EngineRPM")]
    assert len(history) == 1
    assert history[0] == (10.0, 2000.0)

    # Signal not active in plot shouldn't keep history
    assert (256, "CoolantTemp") not in engine.history

    engine.clear_history()
    assert len(engine.history[(256, "EngineRPM")]) == 0


def test_signals_panel_ui(qapp, temp_dbc_file):
    panel = SignalsPanel()
    assert panel.load_dbc_file(temp_dbc_file)
    assert panel.tree.topLevelItemCount() == 2

    # Check tree structure
    engine_item = panel.message_items[256]
    assert "EngineData" in engine_item.text(COL_NAME)
    assert engine_item.childCount() == 3

    rpm_item = panel.signal_items[(256, "EngineRPM")]
    assert "EngineRPM" in rpm_item.text(COL_NAME)
    assert rpm_item.text(COL_UNIT) == "rpm"

    # Toggle Graph 1 for RPM
    rpm_item.setCheckState(COL_G1, Qt.CheckState.Checked)
    assert (256, "EngineRPM", 0) in panel.curves

    # Ingest frames
    frame = {"id": 256, "data": bytes([0x40, 0x1F, 0x82, 0x64, 0x00, 0x00, 0x00, 0x00]), "extended": False}
    panel.process_frames([frame], now=1.0)
    panel.update_views()

    assert rpm_item.text(COL_VALUE) == "2000"

    # Change graph count to 3
    panel._on_graph_count_changed(3)
    assert panel.num_graphs == 3
    assert not panel.tree.isColumnHidden(COL_G1)
    assert not panel.tree.isColumnHidden(COL_G2)
    assert not panel.tree.isColumnHidden(COL_G3)
    assert panel.tree.isColumnHidden(COL_G4)

    # Change to 1 graph
    panel._on_graph_count_changed(1)
    assert panel.num_graphs == 1
    assert not panel.tree.isColumnHidden(COL_G1)
    assert panel.tree.isColumnHidden(COL_G2)

    # Filter test
    panel.filter_edit.setText("Coolant")
    assert not panel.signal_items[(256, "CoolantTemp")].isHidden()
    assert panel.signal_items[(256, "EngineRPM")].isHidden()

    # Clear filter
    panel.filter_edit.setText("")
    assert not panel.signal_items[(256, "EngineRPM")].isHidden()

    # Clear graphs
    panel.clear_graphs()
    assert len(panel.dbc_engine.history.get((256, "EngineRPM"), [])) == 0
