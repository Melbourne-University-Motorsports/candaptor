#!/usr/bin/env python3
"""Unit tests for CANdaptor parsing logic, validated against the manual."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from candaptor import (
    CANdaptor,
    CAN_BAUDRATE_CODES,
    format_frame,
    parse_data_bytes,
    format_baudrate,
    parse_baudrate,
)


def test_parse_standard_frame():
    """Manual page 9: Tiiildd... — standard 11-bit, 3 hex ID."""
    frame = CANdaptor._parse_frame("T3CF411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is False
    assert frame["data"] == bytes([0x11, 0x22, 0x33, 0x44])


def test_parse_standard_dlc0():
    """Standard frame with no data bytes."""
    frame = CANdaptor._parse_frame("T3CF0")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["data"] == b""
    assert frame["extended"] is False


def test_parse_extended_frame():
    """Manual page 10: Xiiiiiiiildd... — extended 29-bit, 8 hex ID."""
    frame = CANdaptor._parse_frame("X000003CF411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is True
    assert frame["data"] == bytes([0x11, 0x22, 0x33, 0x44])


def test_parse_extended_max_id():
    """Extended frame at max ID 0x1FFFFFFF."""
    frame = CANdaptor._parse_frame("X1FFFFFFF0")
    assert frame is not None
    assert frame["id"] == 0x1FFFFFFF
    assert frame["extended"] is True
    assert frame["data"] == b""


def test_parse_bytes_input():
    """Parser should accept bytes input."""
    frame = CANdaptor._parse_frame(b"T3CF101")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["data"] == bytes([0x01])
    assert frame["extended"] is False


def test_parse_standard_with_timestamp():
    """Manual page 11: T3CF1014521 — timestamp 4521 appended."""
    frame = CANdaptor._parse_frame("T3CF1014521", timestamp=True)
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["data"] == bytes([0x01])
    assert frame["extended"] is False
    assert frame["timestamp"] == 0x4521


def test_parse_extended_with_timestamp():
    """Extended frame with timestamp."""
    frame = CANdaptor._parse_frame("X18DAF110211224521", timestamp=True)
    assert frame is not None
    assert frame["id"] == 0x18DAF110
    assert frame["data"] == bytes([0x11, 0x22])
    assert frame["extended"] is True
    assert frame["timestamp"] == 0x4521


def test_parse_timestamp_off_ignores_extra():
    """Without timestamp flag, extra bytes are ignored (data-only)."""
    frame = CANdaptor._parse_frame("T3CF1014521", timestamp=False)
    assert frame is not None
    assert frame["data"] == bytes([0x01])
    assert "timestamp" not in frame


def test_parse_lowercase_t():
    """Lowercase t should also work (manual says commands not case sensitive)."""
    frame = CANdaptor._parse_frame("t3cf411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is False


def test_parse_lowercase_x():
    """Lowercase x should also work for extended."""
    frame = CANdaptor._parse_frame("x000003cf411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is True


def test_parse_invalid_kind():
    """Unknown frame type returns None."""
    assert CANdaptor._parse_frame("Z3CF411223344") is None
    assert CANdaptor._parse_frame("F00") is None


def test_parse_too_short():
    """Lines too short return None."""
    assert CANdaptor._parse_frame("T") is None
    assert CANdaptor._parse_frame("") is None
    assert CANdaptor._parse_frame("ABC") is None


def test_parse_bad_hex():
    """Invalid hex in ID or data returns None."""
    assert CANdaptor._parse_frame("TGGG0") is None
    assert CANdaptor._parse_frame("XGGGGGGGG0") is None


def test_parse_strips_whitespace():
    """Leading/trailing whitespace is stripped."""
    frame = CANdaptor._parse_frame("  T3CF0  ")
    assert frame is not None
    assert frame["id"] == 0x3CF


def test_format_frame_standard():
    """format_frame uses T for standard (per manual)."""
    frame = {"id": 0x3CF, "data": bytes([0x11, 0x22]), "extended": False}
    out = format_frame(frame)
    assert out.startswith("T ")
    assert "3CF" in out


def test_format_frame_extended():
    """format_frame uses X for extended (per manual)."""
    frame = {"id": 0x18DAF110, "data": bytes([0x11]), "extended": True}
    out = format_frame(frame)
    assert out.startswith("X ")
    assert "18DAF110" in out


def test_format_frame_with_timestamp():
    """format_frame appends ts= when timestamp present."""
    frame = {"id": 0x3CF, "data": b"", "extended": False, "timestamp": 0x4521}
    out = format_frame(frame)
    assert "ts=4521" in out


def test_parse_data_bytes_spaces():
    """parse_data_bytes handles space-separated hex."""
    assert parse_data_bytes(["11 22 33"]) == bytes([0x11, 0x22, 0x33])


def test_parse_data_bytes_commas():
    """parse_data_bytes handles comma-separated hex."""
    assert parse_data_bytes(["11,22,33"]) == bytes([0x11, 0x22, 0x33])


def test_parse_data_bytes_mixed():
    """parse_data_bytes handles mixed separators and multiple args."""
    assert parse_data_bytes(["11, 22", "33 44"]) == bytes([0x11, 0x22, 0x33, 0x44])


def test_baudrate_codes():
    """CAN_BAUDRATE_CODES matches manual S0-S8."""
    from candaptor import CAN_BAUDRATE_CODES
    assert CAN_BAUDRATE_CODES[10000] == "S0"
    assert CAN_BAUDRATE_CODES[20000] == "S1"
    assert CAN_BAUDRATE_CODES[50000] == "S2"
    assert CAN_BAUDRATE_CODES[100000] == "S3"
    assert CAN_BAUDRATE_CODES[125000] == "S4"
    assert CAN_BAUDRATE_CODES[250000] == "S5"
    assert CAN_BAUDRATE_CODES[500000] == "S6"
    assert CAN_BAUDRATE_CODES[800000] == "S7"
    assert CAN_BAUDRATE_CODES[1000000] == "S8"


def test_format_baudrate_kbps():
    """format_baudrate shows kbps for values under 1 Mbps."""
    assert format_baudrate(10000) == "10 kbps"
    assert format_baudrate(50000) == "50 kbps"
    assert format_baudrate(125000) == "125 kbps"
    assert format_baudrate(500000) == "500 kbps"
    assert format_baudrate(800000) == "800 kbps"


def test_format_baudrate_mbps():
    """format_baudrate shows Mbps for 1 Mbps and above."""
    assert format_baudrate(1000000) == "1 Mbps"


def test_parse_baudrate_formatted():
    """parse_baudrate converts kbps/Mbps strings back to ints."""
    assert parse_baudrate("500 kbps") == 500000
    assert parse_baudrate("1 Mbps") == 1000000
    assert parse_baudrate("125 kbps") == 125000


def test_parse_baudrate_plain():
    """parse_baudrate also accepts plain integer strings."""
    assert parse_baudrate("500000") == 500000
    assert parse_baudrate("1000000") == 1000000


def test_parse_baudrate_roundtrip():
    """format_baudrate and parse_baudrate are inverses for all supported rates."""
    for rate in CAN_BAUDRATE_CODES:
        assert parse_baudrate(format_baudrate(rate)) == rate


def _frame(can_id=0x3CF, data=b"\x11\x22", extended=False, timestamp=None):
    frame = {"id": can_id, "data": data, "extended": extended}
    if timestamp is not None:
        frame["timestamp"] = timestamp
    return frame


def test_id_tracker_new_id_has_no_period():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    stats = tracker.update(_frame(), now=1.0)
    assert stats.count == 1
    assert stats.period_ms is None
    assert stats.hz() is None
    assert stats.data == b"\x11\x22"
    assert len(tracker) == 1


def test_id_tracker_updates_bytes_and_period():
    from candaptor.id_stats import IdTracker, format_change_mask

    tracker = IdTracker()
    tracker.update(_frame(data=b"\x11\x22"), now=1.0)
    stats = tracker.update(_frame(data=b"\x11\x33"), now=1.01)
    assert stats.count == 2
    assert stats.data == b"\x11\x33"
    assert abs(stats.period_ms - 10.0) < 0.01
    assert abs(stats.hz() - 100.0) < 0.1
    assert stats.changed == (False, True)
    assert format_change_mask(tracker.display_changed(stats, 1.01), stats.dlc) == ".*"
    assert tracker.display_changed(stats, 1.01 + IdTracker.CHANGE_HOLD_S) == ()


def test_id_tracker_treats_std_and_ext_as_distinct():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    tracker.update(_frame(extended=False), now=1.0)
    tracker.update(_frame(extended=True), now=1.0)
    assert len(tracker) == 2


def test_id_tracker_uses_hardware_timestamp_wrap():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    tracker.update(_frame(timestamp=0xFFFE), now=10.0)
    stats = tracker.update(_frame(timestamp=0x0005), now=10.5)
    # (0x0005 - 0xFFFE) mod 65536 = 7 ms, not the 500 ms wall-clock gap
    assert abs(stats.period_ms - 7.0) < 0.01


def test_id_tracker_hw_timestamp_idle_uses_wall_clock():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    tracker.update(_frame(timestamp=0x0010), now=1.0)
    stats = tracker.update(_frame(timestamp=0x0020), now=70.0)
    assert abs(stats.period_ms - 69000.0) < 1.0


def test_id_tracker_holds_change_mask_after_unchanged_followup():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    tracker.update(_frame(data=b"\x11\x22"), now=1.0)
    tracker.update(_frame(data=b"\x11\x33"), now=1.01)
    stats = tracker.update(_frame(data=b"\x11\x33"), now=1.02)
    assert tracker.display_changed(stats, 1.02) == (False, True)


def test_id_tracker_falls_back_to_wall_clock_without_hw_ts():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    tracker.update(_frame(), now=2.0)
    stats = tracker.update(_frame(), now=2.25)
    assert abs(stats.period_ms - 250.0) < 0.01


def test_id_tracker_stale_and_clear():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    stats = tracker.update(_frame(), now=1.0)
    assert tracker.is_stale(stats, now=2.0) is False
    assert tracker.is_stale(stats, now=3.0) is True
    tracker.clear()
    assert len(tracker) == 0


def test_id_tracker_ewma_and_late():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    t = 1.0
    stats = tracker.update(_frame(), now=t)
    for _ in range(8):
        t += 0.010
        stats = tracker.update(_frame(), now=t)
    assert abs(stats.avg_period_ms - 10.0) < 0.2
    assert stats.std_period_ms is not None
    assert stats.std_period_ms < 1.0
    assert tracker.is_late(stats, now=t + 0.012) is False
    assert tracker.is_late(stats, now=t + 0.016) is True
    assert tracker.is_late(stats, now=t + 3.0) is False  # stale wins


def test_id_tracker_jitter_raises_std():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    t = 1.0
    tracker.update(_frame(), now=t)
    for dt in (0.010, 0.020, 0.010, 0.020, 0.010, 0.020, 0.010):
        t += dt
        stats = tracker.update(_frame(), now=t)
    assert stats.std_period_ms > 3.0


def test_id_tracker_hz_is_rolling_window():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    t = 1.0
    tracker.update(_frame(), now=t)
    for _ in range(10):
        t += 0.010
        stats = tracker.update(_frame(), now=t)
    assert abs(stats.hz(t) - 100.0) < 1.0

    # A quiet stretch slides old arrivals out of the 1 s window.
    assert stats.hz(t + 0.5) < 25.0
    assert stats.hz(t + IdTracker.HZ_WINDOW_S + 0.05) is None


def test_id_tracker_hz_averages_jitter():
    from candaptor.id_stats import IdTracker

    tracker = IdTracker()
    t = 1.0
    tracker.update(_frame(), now=t)
    for dt in (0.010, 0.020, 0.010, 0.020, 0.010, 0.020, 0.010, 0.020):
        t += dt
        stats = tracker.update(_frame(), now=t)
    # Eight intervals summing to 120 ms → 8 / 0.12 ≈ 66.7 Hz, not 50 or 100.
    assert abs(stats.hz(t) - (8 / 0.12)) < 0.1
    assert abs(stats.rolling_period_ms(t) - 15.0) < 0.1
    assert abs(stats.period_ms - 20.0) < 0.01


def test_can_frame_bits_and_bus_load():
    from candaptor.id_stats import BusLoadMeter, can_frame_bits

    assert can_frame_bits(8, False, stuffed=False) == 47 + 64
    assert can_frame_bits(8, True, stuffed=False) == 67 + 64
    std8 = can_frame_bits(8, False, stuffed=True)
    assert std8 == (47 + 64) + (47 + 64) // 5

    meter = BusLoadMeter()
    for i in range(1000):
        meter.add_frame(_frame(data=b"\x00" * 8), now=1.0 + i / 1000.0)
    ratio = meter.ratio(now=2.0, bitrate=500000)
    assert abs(ratio - (1000 * std8) / 500000) < 0.02
    assert meter.ratio(now=2.0, bitrate=0) is None


def test_format_change_mask_and_age():
    from candaptor.id_stats import format_age_ms, format_change_mask, format_hz, format_period_ms

    assert format_change_mask((), 4) == "...."
    assert format_change_mask((True, False, True), 3) == "*.*"
    assert format_change_mask(None, 0) == ""
    assert format_period_ms(None) == ""
    assert format_period_ms(1.25) == "1.25"
    assert format_period_ms(12.34) == "12.3"
    assert format_hz(None) == ""
    assert format_hz(100.0) == "100.0"
    assert format_age_ms(0.123) == "123"


def test_format_id_row_colours_changed_bytes():
    from candaptor.id_stats import (
        ID_LAYOUT,
        IdTracker,
        column_at_offset,
        format_id_row,
        header_line,
    )

    tracker = IdTracker()
    tracker.update(_frame(data=b"\x11\x22\x33"), now=1.0)
    stats = tracker.update(_frame(data=b"\x11\xAA\x33"), now=1.01)
    changed = tracker.display_changed(stats, 1.01)
    line, spans = format_id_row(stats, 1.01, changed)
    assert [line[start:end] for start, end, _chg in spans] == ["11", "AA", "33"]
    assert [chg for _s, _e, chg in spans] == [False, True, False]
    data_start = next(start for key, start, _end, *_ in ID_LAYOUT if key == "data")
    assert spans[0][0] == data_start
    assert column_at_offset(0) == "id"
    assert column_at_offset(data_start) == "data"
    assert header_line().startswith("ID")
    assert "Data" in header_line()
    from candaptor.id_stats import pad_column
    assert len(pad_column("age", "123")) == 7
    assert pad_column("age", "123").endswith("123")


def test_port_combo_state_selects_first_when_empty():
    from candaptor.gui import NO_PORTS_MESSAGE, port_combo_state

    items, selected = port_combo_state(["/dev/cu.usbserial-A", "/dev/cu.usbserial-B"], "")
    assert items == ["/dev/cu.usbserial-A", "/dev/cu.usbserial-B"]
    assert selected == "/dev/cu.usbserial-A"


def test_port_combo_state_keeps_current_even_if_missing():
    from candaptor.gui import NO_PORTS_MESSAGE, port_combo_state

    items, selected = port_combo_state(
        ["/dev/cu.usbserial-B"], " /dev/cu.usbserial-A "
    )
    assert items == ["/dev/cu.usbserial-B"]
    assert selected == "/dev/cu.usbserial-A"

    items, selected = port_combo_state([], NO_PORTS_MESSAGE)
    assert items == [NO_PORTS_MESSAGE]
    assert selected == ""

    items, selected = port_combo_state([], "/dev/cu.usbserial-A")
    assert items == [NO_PORTS_MESSAGE]
    assert selected == "/dev/cu.usbserial-A"


def test_trace_log_grows_then_overwrites_oldest():
    from candaptor.gui import TraceLog

    log = TraceLog(max_rows=3)
    assert log.append(("1",)) is True
    assert log.append(("2",)) is True
    assert log.append(("3",)) is True
    assert [log[i][0] for i in range(len(log))] == ["1", "2", "3"]
    assert log.append(("4",)) is False
    assert [log[i][0] for i in range(len(log))] == ["2", "3", "4"]
    log.append(("5",))
    assert [log[i][0] for i in range(3)] == ["3", "4", "5"]
    assert log[-1][0] == "5"
    log.clear()
    assert len(log) == 0


def test_enqueue_latest_drops_oldest_when_full():
    import queue as queue_mod
    from candaptor.gui import enqueue_latest

    q = queue_mod.Queue(maxsize=2)
    enqueue_latest(q, "a")
    enqueue_latest(q, "b")
    enqueue_latest(q, "c")
    assert q.get_nowait() == "b"
    assert q.get_nowait() == "c"


def test_trace_model_wraps_without_growing_past_max():
    import os
    import pytest
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        from candaptor.gui import TraceModel
    except Exception:
        pytest.skip("PySide6 or system GUI libraries not available")

    app = QApplication.instance() or QApplication([])
    model = TraceModel(max_rows=3)
    rows = [(str(i), "t", "", "Std", "001", "0", "") for i in range(1, 6)]
    model.extend(rows[:2])
    assert model.rowCount() == 2
    assert model.cell_text(0, 0) == "1"
    model.extend(rows[2:])
    assert model.rowCount() == 3
    assert [model.cell_text(i, 0) for i in range(3)] == ["3", "4", "5"]
    model.clear()
    assert model.rowCount() == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"FAIL: {test.__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
