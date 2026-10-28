"""Server-truth timezone normalizer.

All telemetry timestamps are stored as UTC on the server. Rendering must be
pinned to Asia/Shanghai regardless of any client-supplied timezone hint
(e.g. a browser fingerprint injecting America/New_York). Client TZ is
accepted only for audit/skew reporting and never affects output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCKED_TZ_NAME = "Asia/Shanghai"
LOCKED_TZ = ZoneInfo(LOCKED_TZ_NAME)
DISPLAY_FMT = "%Y-%m-%d %H:%M:%S"

Stamp = Union[datetime, str, int, float]


class TimestampError(ValueError):
    """Raised when a stored timestamp cannot be interpreted as UTC."""


@dataclass(frozen=True)
class NormalizedStamp:
    utc: datetime
    local: datetime
    display: str
    client_tz: Optional[str]
    client_skew_hours: Optional[float]

    @property
    def skewed(self) -> bool:
        return bool(self.client_skew_hours)


def parse_utc(value: Stamp) -> datetime:
    """Interpret a stored value as an aware UTC datetime.

    - naive datetime / ISO string without offset -> assumed UTC (server truth)
    - aware datetime / ISO string with offset -> converted to UTC
    - int/float -> epoch seconds (values > 1e11 treated as milliseconds)
    """
    if isinstance(value, bool):
        raise TimestampError("bool is not a timestamp")
    if isinstance(value, (int, float)):
        secs = value / 1000.0 if abs(value) > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise TimestampError(f"epoch out of range: {value!r}") from exc
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise TimestampError("empty timestamp string")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TimestampError(f"unparseable timestamp: {value!r}") from exc
    if not isinstance(value, datetime):
        raise TimestampError(f"unsupported type: {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_client_tz(name: Optional[str]) -> Optional[ZoneInfo]:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def client_skew_hours(utc: datetime, client_tz: Optional[str]) -> Optional[float]:
    """Hours the client's injected TZ would differ from the locked TZ.

    Returns None if no/invalid client TZ. Positive => locked TZ is ahead.
    """
    tz = _resolve_client_tz(client_tz)
    if tz is None:
        return None
    locked = utc.astimezone(LOCKED_TZ).utcoffset() or timedelta(0)
    client = utc.astimezone(tz).utcoffset() or timedelta(0)
    return (locked - client).total_seconds() / 3600.0


def to_shanghai(value: Stamp, client_tz: Optional[str] = None) -> datetime:
    """Convert stored UTC to Asia/Shanghai. ``client_tz`` is deliberately ignored."""
    del client_tz  # explicit: client fingerprint never influences rendering
    return parse_utc(value).astimezone(LOCKED_TZ)


def normalize(value: Stamp, client_tz: Optional[str] = None) -> NormalizedStamp:
    utc = parse_utc(value)
    local = utc.astimezone(LOCKED_TZ)
    return NormalizedStamp(
        utc=utc,
        local=local,
        display=local.strftime(DISPLAY_FMT),
        client_tz=client_tz,
        client_skew_hours=client_skew_hours(utc, client_tz),
    )


def render(value: Stamp, client_tz: Optional[str] = None, fmt: str = DISPLAY_FMT) -> str:
    return to_shanghai(value, client_tz).strftime(fmt)


def normalize_batch(
    values: Iterable[Stamp], client_tz: Optional[str] = None
) -> list[NormalizedStamp]:
    return [normalize(v, client_tz) for v in values]
