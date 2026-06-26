#!/usr/bin/env python3
"""Unit tests for CANdapter parsing logic, validated against the manual."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from candapter import CANdapter, format_frame, parse_data_bytes


def test_parse_standard_frame():
    """Manual page 9: Tiiildd... — standard 11-bit, 3 hex ID."""
    frame = CANdapter._parse_frame("T3CF411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is False
    assert frame["data"] == bytes([0x11, 0x22, 0x33, 0x44])


def test_parse_standard_dlc0():
    """Standard frame with no data bytes."""
    frame = CANdapter._parse_frame("T3CF0")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["data"] == b""
    assert frame["extended"] is False


def test_parse_extended_frame():
    """Manual page 10: Xiiiiiiiildd... — extended 29-bit, 8 hex ID."""
    frame = CANdapter._parse_frame("X000003CF411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is True
    assert frame["data"] == bytes([0x11, 0x22, 0x33, 0x44])


def test_parse_extended_max_id():
    """Extended frame at max ID 0x1FFFFFFF."""
    frame = CANdapter._parse_frame("X1FFFFFFF0")
    assert frame is not None
    assert frame["id"] == 0x1FFFFFFF
    assert frame["extended"] is True
    assert frame["data"] == b""


def test_parse_bytes_input():
    """Parser should accept bytes input."""
    frame = CANdapter._parse_frame(b"T3CF101")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["data"] == bytes([0x01])
    assert frame["extended"] is False


def test_parse_standard_with_timestamp():
    """Manual page 11: T3CF1014521 — timestamp 4521 appended."""
    frame = CANdapter._parse_frame("T3CF1014521", timestamp=True)
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["data"] == bytes([0x01])
    assert frame["extended"] is False
    assert frame["timestamp"] == 0x4521


def test_parse_extended_with_timestamp():
    """Extended frame with timestamp."""
    frame = CANdapter._parse_frame("X18DAF110211224521", timestamp=True)
    assert frame is not None
    assert frame["id"] == 0x18DAF110
    assert frame["data"] == bytes([0x11, 0x22])
    assert frame["extended"] is True
    assert frame["timestamp"] == 0x4521


def test_parse_timestamp_off_ignores_extra():
    """Without timestamp flag, extra bytes are ignored (data-only)."""
    frame = CANdapter._parse_frame("T3CF1014521", timestamp=False)
    assert frame is not None
    assert frame["data"] == bytes([0x01])
    assert "timestamp" not in frame


def test_parse_lowercase_t():
    """Lowercase t should also work (manual says commands not case sensitive)."""
    frame = CANdapter._parse_frame("t3cf411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is False


def test_parse_lowercase_x():
    """Lowercase x should also work for extended."""
    frame = CANdapter._parse_frame("x000003cf411223344")
    assert frame is not None
    assert frame["id"] == 0x3CF
    assert frame["extended"] is True


def test_parse_invalid_kind():
    """Unknown frame type returns None."""
    assert CANdapter._parse_frame("Z3CF411223344") is None
    assert CANdapter._parse_frame("F00") is None


def test_parse_too_short():
    """Lines too short return None."""
    assert CANdapter._parse_frame("T") is None
    assert CANdapter._parse_frame("") is None
    assert CANdapter._parse_frame("ABC") is None


def test_parse_bad_hex():
    """Invalid hex in ID or data returns None."""
    assert CANdapter._parse_frame("TGGG0") is None
    assert CANdapter._parse_frame("XGGGGGGGG0") is None


def test_parse_strips_whitespace():
    """Leading/trailing whitespace is stripped."""
    frame = CANdapter._parse_frame("  T3CF0  ")
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
    from candapter import CAN_BAUDRATE_CODES
    assert CAN_BAUDRATE_CODES[10000] == "S0"
    assert CAN_BAUDRATE_CODES[20000] == "S1"
    assert CAN_BAUDRATE_CODES[50000] == "S2"
    assert CAN_BAUDRATE_CODES[100000] == "S3"
    assert CAN_BAUDRATE_CODES[125000] == "S4"
    assert CAN_BAUDRATE_CODES[250000] == "S5"
    assert CAN_BAUDRATE_CODES[500000] == "S6"
    assert CAN_BAUDRATE_CODES[800000] == "S7"
    assert CAN_BAUDRATE_CODES[1000000] == "S8"


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
