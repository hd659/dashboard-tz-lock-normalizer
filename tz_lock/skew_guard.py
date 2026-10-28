"""Skew guard: detect and strip client-injected TZ offsets.

Failure mode: an anti-detect browser (e.g. AdsPower) injects America/New_York
(UTC-4 in DST, UTC-5 otherwise). The frontend then either
  (a) serialises timestamps with the injected offset ("...-04:00"), or
  (b) renders naive wall-clock strings in the injected zone,
so the dashboard drifts 12-13h from Asia/Shanghai.

The guard recovers the true UTC instant and re-renders it pinned to
Asia/Shanghai. Rule: an explicit offset is truth for the instant, never for
the display zone; a naive string is reinterpreted only when the caller
declares its source zone (``assume_tz``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .normalize import DISPLAY_FMT, LOCKED_TZ, TimestampError, parse_utc

_OFFSET_RE = re.compile(r"(Z|z|[+-]\d{2}:?\d{2})$")
LOCKED_OFFSET = timedelta(hours=8)


@dataclass(frozen=True)
class GuardResult:
    raw: str
    utc: datetime
    display: str
    injected_offset_hours: Optional[float]  # offset found / assumed on input
    skew_hours: float                       # locked offset - injected offset

    @property
    def stripped(self) -> bool:
        # UTC (server truth) and +08:00 (already locked) are not injections
        return self.injected_offset_hours not in (None, 0.0, 8.0)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TimestampError(f"unknown timezone: {name!r}") from exc


def extract_offset(text: str) -> Optional[timedelta]:
    """Return the explicit UTC offset carried by an ISO string, if any."""
    m = _OFFSET_RE.search(text.strip())
    if not m:
        return None
    tok = m.group(1)
    if tok in ("Z", "z"):
        return timedelta(0)
    sign = -1 if tok[0] == "-" else 1
    digits = tok[1:].replace(":", "")
    return sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))


def guard(raw: str, assume_tz: Optional[str] = None) -> GuardResult:
    """Strip injected offset and re-pin to Asia/Shanghai.

    - offset-bearing string -> instant preserved, offset discarded for display
    - naive string + assume_tz -> wall clock reinterpreted in assume_tz
    - naive string, no assume_tz -> server truth (UTC)
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TimestampError(f"guard expects non-empty str, got {raw!r}")
    offset = extract_offset(raw)
    if offset is None and assume_tz:
        naive = parse_utc(raw).replace(tzinfo=None)
        aware = naive.replace(tzinfo=_zone(assume_tz))
        offset = aware.utcoffset()
        utc = aware.astimezone(timezone.utc)
    else:
        utc = parse_utc(raw)
    injected = None if offset is None else offset.total_seconds() / 3600.0
    skew = (LOCKED_OFFSET - (offset or timedelta(0))).total_seconds() / 3600.0
    if offset is None:
        skew = 0.0
    return GuardResult(
        raw=raw,
        utc=utc,
        display=utc.astimezone(LOCKED_TZ).strftime(DISPLAY_FMT),
        injected_offset_hours=injected,
        skew_hours=skew,
    )


def detect_display_skew(shown: str, truth_utc: Any) -> float:
    """Hours by which a naive displayed string deviates from locked rendering.

    0.0 means the frontend honoured Asia/Shanghai; 12.0 is the classic
    New_York (DST) fingerprint leak.
    """
    shown_dt = datetime.strptime(shown.strip(), DISPLAY_FMT)
    expected = parse_utc(truth_utc).astimezone(LOCKED_TZ).replace(tzinfo=None)
    return (expected - shown_dt).total_seconds() / 3600.0


def guard_payload(
    payload: Mapping[str, Any],
    fields: Iterable[str],
    assume_tz: Optional[str] = None,
) -> tuple[dict[str, Any], list[str]]:
    """Rewrite timestamp fields in a telemetry row; return (row, stripped_fields)."""
    out = dict(payload)
    stripped: list[str] = []
    for key in fields:
        val = out.get(key)
        if not isinstance(val, str):
            continue
        res = guard(val, assume_tz)
        out[key] = res.display
        if res.stripped:
            stripped.append(key)
    return out, stripped
