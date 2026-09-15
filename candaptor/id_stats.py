"""Per-ID live stats for the sniffer / overwrite view.

One row per CAN ID, latest payload, cycle time, and age — the same
shape as cansniffer, PCAN-View overwrite mode, and SavvyCAN's unique
frames list.
"""

from __future__ import annotations

import math
import time
from collections import deque


def frame_key(can_id, extended):
    return (bool(extended), int(can_id))


def format_can_id(can_id, extended):
    if extended:
        return f"{can_id:08X}"
    return f"{can_id:03X}"


def format_data_bytes(data):
    return " ".join(f"{b:02X}" for b in data)


def format_change_mask(changed, dlc):
    """Compact per-byte change mask, e.g. ``..*.*...``."""
    if dlc <= 0:
        return ""
    parts = []
    for i in range(dlc):
        if changed and i < len(changed) and changed[i]:
            parts.append("*")
        else:
            parts.append(".")
    return "".join(parts)


def format_period_ms(period_ms):
    if period_ms is None:
        return ""
    if period_ms < 10:
        return f"{period_ms:.2f}"
    return f"{period_ms:.1f}"


def format_hz(hz):
    if hz is None:
        return ""
    if hz < 10:
        return f"{hz:.2f}"
    return f"{hz:.1f}"


def format_age_ms(age_s):
    return f"{max(0.0, age_s) * 1000:.0f}"


def format_std_ms(std_ms):
    if std_ms is None:
        return ""
    return format_period_ms(std_ms)


def can_frame_bits(dlc, extended, stuffed=True):
    """Estimate on-wire bits for one received data frame, including IFS.

    Standard: 47 + 8*DLC, extended: 67 + 8*DLC, then an extra bit per 5
    for average stuffing. This is an estimate — exact stuffing depends on
    payload, and error/overload frames are not visible through CANdaptor.
    """
    dlc = max(0, min(int(dlc), 8))
    bits = (67 if extended else 47) + 8 * dlc
    if stuffed:
        bits += bits // 5
    return bits


def format_wall_time(now):
    local = time.localtime(now)
    millis = int(now * 1000) % 1000
    return time.strftime("%H:%M:%S", local) + f".{millis:03d}"


# Fixed-width ID-table layout: (key, width, header, align, gap_after).
# Gap of 2 before Data leaves a gutter between DLC and the payload.
ID_COLUMNS = (
    ("id", 8, "ID", "<", 1),
    ("type", 4, "Type", "<", 1),
    ("dlc", 3, "DLC", ">", 2),
    ("data", 23, "Data", "<", 1),
    ("chg", 8, "Chg", "<", 1),
    ("count", 6, "Count", ">", 1),
    ("period", 9, "Period ms", ">", 1),
    ("hz", 6, "Hz", ">", 1),
    ("age", 7, "Age ms", ">", 1),
    ("last", 12, "Last", "<", 0),
)


def _column_layout():
    ranges = []
    pos = 0
    for key, width, header, align, gap in ID_COLUMNS:
        ranges.append((key, pos, pos + width, header, align, width, gap))
        pos += width + gap
    return tuple(ranges)


ID_LAYOUT = _column_layout()


def header_line():
    parts = []
    for _key, _start, _end, header, align, width, gap in ID_LAYOUT:
        parts.append(f"{header:{align}{width}}")
        parts.append(" " * gap)
    return "".join(parts).rstrip()


def column_at_offset(offset):
    """Map a character index on an ID-table line to a column key."""
    last_key = ID_LAYOUT[0][0]
    for key, start, end, _header, _align, _width, gap in ID_LAYOUT:
        last_key = key
        if start <= offset < end + gap:
            return key
    return last_key


def column_spec(key):
    for spec in ID_LAYOUT:
        if spec[0] == key:
            return spec
    raise KeyError(key)


def pad_column(key, value):
    _key, _start, _end, _header, align, width, _gap = column_spec(key)
    return f"{value:{align}{width}}"


def format_id_row(stats, now, changed=()):
    """Render one ID-table line and the character spans of each data byte.

    Each span is ``(start, end, changed)`` into the returned string, so the
    GUI can colour just the hex digits that changed.
    """
    values = {
        "id": stats.id_str(),
        "type": "Ext" if stats.extended else "Std",
        "dlc": str(stats.dlc),
        "data": format_data_bytes(stats.data),
        "chg": format_change_mask(changed, stats.dlc),
        "count": str(stats.count),
        "period": format_period_ms(stats.period_ms),
        "hz": format_hz(stats.hz),
        "age": format_age_ms(stats.age_s(now)),
        "last": format_wall_time(stats.last_seen),
    }
    parts = []
    data_start = 0
    for key, _start, _end, _header, align, width, gap in ID_LAYOUT:
        if key == "data":
            data_start = sum(len(p) for p in parts)
        parts.append(f"{values[key]:{align}{width}}")
        parts.append(" " * gap)
    line = "".join(parts)
    spans = []
    for i in range(stats.dlc):
        start = data_start + i * 3
        end = start + 2
        is_changed = bool(changed and i < len(changed) and changed[i])
        spans.append((start, end, is_changed))
    return line, spans


def _byte_changes(old, new):
    n = max(len(old), len(new))
    changed = []
    for i in range(n):
        if i >= len(old) or i >= len(new):
            changed.append(True)
        else:
            changed.append(old[i] != new[i])
    return tuple(changed)


def _period_s(previous, now, hw_ts):
    """Interval since the previous frame, preferring hardware timestamps."""
    wall = max(0.0, now - previous.last_seen)
    prev_hw = previous.hw_ts
    if prev_hw is not None and hw_ts is not None:
        hw = ((hw_ts - prev_hw) & 0xFFFF) / 1000.0
        # 16-bit ms counter wraps every 65.536 s; trust wall clock past that.
        if wall > 60:
            return wall
        return hw
    return wall


class IdStats:
    __slots__ = (
        "can_id",
        "extended",
        "data",
        "count",
        "first_seen",
        "last_seen",
        "period_s",
        "ewma_s",
        "ewmvar_s2",
        "period_samples",
        "hw_ts",
        "changed",
        "changed_until",
    )

    def __init__(self, can_id, extended, data, now, hw_ts=None):
        self.can_id = can_id
        self.extended = bool(extended)
        self.data = bytes(data)
        self.count = 1
        self.first_seen = now
        self.last_seen = now
        self.period_s = None
        self.ewma_s = None
        self.ewmvar_s2 = 0.0
        self.period_samples = 0
        self.hw_ts = hw_ts
        self.changed = ()
        self.changed_until = 0.0

    @property
    def dlc(self):
        return len(self.data)

    @property
    def period_ms(self):
        if self.period_s is None:
            return None
        return self.period_s * 1000.0

    @property
    def avg_period_ms(self):
        if self.ewma_s is None:
            return None
        return self.ewma_s * 1000.0

    @property
    def std_period_ms(self):
        if self.period_samples < 2:
            return None
        return math.sqrt(max(self.ewmvar_s2, 0.0)) * 1000.0

    @property
    def hz(self):
        period = self.ewma_s if self.ewma_s else self.period_s
        if not period:
            return None
        return 1.0 / period

    def age_s(self, now):
        return max(0.0, now - self.last_seen)

    def key(self):
        return frame_key(self.can_id, self.extended)

    def id_str(self):
        return format_can_id(self.can_id, self.extended)


class IdTracker:
    """Accumulate latest payload and timing for each CAN ID."""

    STALE_AFTER_S = 2.0
    CHANGE_HOLD_S = 1.0
    EWMA_ALPHA = 0.2
    LATE_FACTOR = 1.5
    MIN_SAMPLES_FOR_RATE = 5

    def __init__(self):
        self._ids = {}

    def update(self, frame, now):
        key = frame_key(frame["id"], frame["extended"])
        data = bytes(frame["data"])
        hw_ts = frame.get("timestamp")
        stats = self._ids.get(key)
        if stats is None:
            stats = IdStats(
                can_id=frame["id"],
                extended=frame["extended"],
                data=data,
                now=now,
                hw_ts=hw_ts,
            )
            self._ids[key] = stats
            return stats

        changed = _byte_changes(stats.data, data)
        if any(changed):
            stats.changed = changed
            stats.changed_until = now + self.CHANGE_HOLD_S
        stats.period_s = _period_s(stats, now, hw_ts)
        _update_ewma(stats, stats.period_s, self.EWMA_ALPHA)
        stats.data = data
        stats.count += 1
        stats.last_seen = now
        stats.hw_ts = hw_ts
        return stats

    def display_changed(self, stats, now):
        if stats.changed and now < stats.changed_until:
            return stats.changed
        return ()

    def clear(self):
        self._ids.clear()

    def get(self, key):
        return self._ids.get(key)

    def values(self):
        return self._ids.values()

    def __len__(self):
        return len(self._ids)

    def is_stale(self, stats, now):
        return stats.age_s(now) >= self.STALE_AFTER_S

    def is_late(self, stats, now):
        """True when the next cyclic frame is overdue (age ≥ 150% of EWMA)."""
        if stats.ewma_s is None or stats.period_samples < self.MIN_SAMPLES_FOR_RATE:
            return False
        if self.is_stale(stats, now):
            return False
        return stats.age_s(now) >= self.LATE_FACTOR * stats.ewma_s


def _update_ewma(stats, period_s, alpha):
    if period_s is None or period_s <= 0:
        return
    if stats.ewma_s is None:
        stats.ewma_s = period_s
        stats.ewmvar_s2 = 0.0
        stats.period_samples = 1
        return
    delta = period_s - stats.ewma_s
    stats.ewma_s += alpha * delta
    stats.ewmvar_s2 = (1.0 - alpha) * (stats.ewmvar_s2 + alpha * delta * delta)
    stats.period_samples += 1


class BusLoadMeter:
    """Estimate bus utilisation from received frames over a 1 s window."""

    WINDOW_S = 1.0

    def __init__(self):
        self._events = deque()

    def add_frame(self, frame, now):
        bits = can_frame_bits(len(frame["data"]), frame["extended"])
        self._events.append((now, bits))
        self._trim(now)

    def _trim(self, now):
        cutoff = now - self.WINDOW_S
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def ratio(self, now, bitrate):
        """Return estimated load in 0..n, or None if bitrate is unknown."""
        if not bitrate or bitrate <= 0:
            return None
        self._trim(now)
        if not self._events:
            return 0.0
        total_bits = sum(bits for _t, bits in self._events)
        elapsed = now - self._events[0][0]
        elapsed = min(self.WINDOW_S, max(elapsed, 0.05))
        return total_bits / (bitrate * elapsed)

    def clear(self):
        self._events.clear()
