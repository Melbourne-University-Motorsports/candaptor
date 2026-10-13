"""DBC parsing, frame decoding, and time-series signal buffering."""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cantools

logger = logging.getLogger(__name__)

# Maximum data points kept per tracked signal for plotting (e.g. 100Hz for 120s = 12000)
MAX_SIGNAL_HISTORY = 20000


@dataclass
class SignalInfo:
    """Metadata for a CAN signal from DBC."""
    message_id: int
    message_name: str
    name: str
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    scale: float = 1.0
    offset: float = 0.0
    choices: Optional[Dict[int, str]] = None
    comment: Optional[str] = None
    is_multiplexer: bool = False

    @property
    def key(self) -> Tuple[int, str]:
        return (self.message_id, self.name)


@dataclass
class MessageInfo:
    """Metadata for a CAN message from DBC."""
    frame_id: int
    name: str
    length: int
    is_extended_frame: bool
    signals: List[SignalInfo] = field(default_factory=list)
    comment: Optional[str] = None


class DBCEngine:
    """Manages DBC database loading, real-time decoding, and history buffering."""

    def __init__(self) -> None:
        self.db_path: Optional[str] = None
        self._db: Optional[cantools.database.can.Database] = None
        self._messages_by_id: Dict[int, Any] = {}
        self.message_infos: Dict[int, MessageInfo] = {}
        self.signal_infos: Dict[Tuple[int, str], SignalInfo] = {}

        # Live values cache: (message_id, signal_name) -> (value: float | int | str, timestamp: float)
        self.latest_values: Dict[Tuple[int, str], Tuple[Any, float]] = {}

        # Active signals being plotted: set of (message_id, signal_name)
        self._active_plot_signals: set[Tuple[int, str]] = set()

        # History ring buffers: (message_id, signal_name) -> deque of (timestamp, float_value)
        self.history: Dict[Tuple[int, str], collections.deque[Tuple[float, float]]] = {}

    @property
    def is_loaded(self) -> bool:
        return self._db is not None

    def load_file(self, path: str) -> None:
        """Load and parse a DBC file."""
        db = cantools.database.load_file(path)
        self._db = db
        self.db_path = path
        self._messages_by_id.clear()
        self.message_infos.clear()
        self.signal_infos.clear()
        self.latest_values.clear()
        self.history.clear()

        for msg in db.messages:
            self._messages_by_id[msg.frame_id] = msg
            s_infos: List[SignalInfo] = []
            for sig in msg.signals:
                info = SignalInfo(
                    message_id=msg.frame_id,
                    message_name=msg.name,
                    name=sig.name,
                    unit=sig.unit or "",
                    minimum=sig.minimum,
                    maximum=sig.maximum,
                    scale=sig.scale,
                    offset=sig.offset,
                    choices=sig.choices,
                    comment=sig.comment,
                    is_multiplexer=sig.is_multiplexer,
                )
                s_infos.append(info)
                self.signal_infos[info.key] = info

            self.message_infos[msg.frame_id] = MessageInfo(
                frame_id=msg.frame_id,
                name=msg.name,
                length=msg.length,
                is_extended_frame=msg.is_extended_frame,
                signals=s_infos,
                comment=msg.comment,
            )

    def clear(self) -> None:
        """Clear the loaded DBC database."""
        self._db = None
        self.db_path = None
        self._messages_by_id.clear()
        self.message_infos.clear()
        self.signal_infos.clear()
        self.latest_values.clear()
        self.history.clear()
        self._active_plot_signals.clear()

    def set_active_plot_signals(self, signals: set[Tuple[int, str]]) -> None:
        """Update the set of signals that require time-series history for plotting."""
        self._active_plot_signals = set(signals)
        # Ensure deques exist for all active signals
        for key in self._active_plot_signals:
            if key not in self.history:
                self.history[key] = collections.deque(maxlen=MAX_SIGNAL_HISTORY)

    def clear_history(self) -> None:
        """Clear plotted signal history buffers."""
        for buf in self.history.values():
            buf.clear()

    def decode_frame(self, frame_id: int, data: bytes, timestamp: float) -> Optional[Dict[str, Any]]:
        """Decode a CAN frame if its ID exists in the database.

        Updates latest_values and time-series history for active signals.
        Returns decoded dict {signal_name: raw_numeric_value} or None.
        """
        if not self._db or frame_id not in self._messages_by_id:
            return None

        msg_def = self._messages_by_id[frame_id]
        try:
            # decode_choices=False returns pure numeric values (floats/ints)
            decoded = msg_def.decode(data, decode_choices=False)
        except Exception:
            # e.g. payload length too short or bad CRC
            return None

        for sig_name, val in decoded.items():
            key = (frame_id, sig_name)
            self.latest_values[key] = (val, timestamp)

            if key in self._active_plot_signals:
                if isinstance(val, (int, float)):
                    if key not in self.history:
                        self.history[key] = collections.deque(maxlen=MAX_SIGNAL_HISTORY)
                    self.history[key].append((timestamp, float(val)))

        return decoded

    def get_display_value(self, key: Tuple[int, str]) -> str:
        """Get formatted display string for a signal's latest value (including choice string if any)."""
        latest = self.latest_values.get(key)
        if latest is None:
            return "—"
        val, _ = latest
        sig_info = self.signal_infos.get(key)
        if sig_info and sig_info.choices and val in sig_info.choices:
            return f"{val} ({sig_info.choices[val]})"
        if isinstance(val, float):
            return f"{val:.3f}".rstrip("0").rstrip(".") if abs(val) < 10000 else f"{val:.2e}"
        return str(val)
