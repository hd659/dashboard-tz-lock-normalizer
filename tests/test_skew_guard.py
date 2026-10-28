from datetime import datetime, timezone

import pytest

from tz_lock.normalize import TimestampError
from tz_lock.skew_guard import (
    detect_display_skew,
    extract_offset,
    guard,
    guard_payload,
)


@pytest.mark.parametrize(
    "text,hours",
    [
        ("2026-07-01T08:00:00-04:00", -4.0),
        ("2026-07-01T08:00:00+0530", 5.5),
        ("2026-07-01T08:00:00Z", 0.0),
        ("2026-07-01T08:00:00", None),
    ],
)
def test_extract_offset(text, hours):
    off = extract_offset(text)
    assert (None if off is None else off.total_seconds() / 3600) == hours


def test_injected_new_york_dst_offset_is_stripped():
    # 08:00 New_York EDT == 12:00 UTC == 20:00 Shanghai
    r = guard("2026-07-01T08:00:00-04:00")
    assert r.utc == datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    assert r.display == "2026-07-01 20:00:00"
    assert r.injected_offset_hours == -4.0
    assert r.skew_hours == 12.0
    assert r.stripped


def test_injected_new_york_winter_offset_is_13h():
    r = guard("2026-01-15T07:00:00-05:00")
    assert r.display == "2026-01-15 20:00:00"
    assert r.skew_hours == 13.0


def test_naive_wallclock_with_assumed_injected_zone():
    r = guard("2026-07-01 08:00:00", assume_tz="America/New_York")
    assert r.display == "2026-07-01 20:00:00"
    assert r.injected_offset_hours == -4.0
    assert r.stripped


def test_naive_without_assumption_is_server_utc():
    r = guard("2026-07-01T12:00:00")
    assert r.display == "2026-07-01 20:00:00"
    assert r.injected_offset_hours is None
    assert r.skew_hours == 0.0
    assert not r.stripped


@pytest.mark.parametrize("raw", ["2026-07-01T12:00:00Z", "2026-07-01T20:00:00+08:00"])
def test_utc_and_locked_offsets_not_flagged(raw):
    r = guard(raw)
    assert r.display == "2026-07-01 20:00:00"
    assert not r.stripped


def test_all_injections_converge_to_same_display():
    variants = [
        "2026-07-01T12:00:00Z",
        "2026-07-01T08:00:00-04:00",
        "2026-07-01T21:00:00+09:00",
        "2026-07-01T13:00:00+01:00",
    ]
    assert {guard(v).display for v in variants} == {"2026-07-01 20:00:00"}


def test_detect_display_skew():
    truth = "2026-07-01T12:00:00Z"
    assert detect_display_skew("2026-07-01 08:00:00", truth) == 12.0
    assert detect_display_skew("2026-07-01 20:00:00", truth) == 0.0


def test_guard_payload_rewrites_and_reports():
    row = {
        "id": 7,
        "ts": "2026-07-01T08:00:00-04:00",
        "seen": "2026-07-01T12:00:00Z",
        "note": None,
    }
    out, stripped = guard_payload(row, ["ts", "seen", "note", "missing"])
    assert out["ts"] == out["seen"] == "2026-07-01 20:00:00"
    assert stripped == ["ts"]
    assert out["id"] == 7 and row["ts"].endswith("-04:00")  # input untouched


@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_guard_rejects_bad_input(bad):
    with pytest.raises(TimestampError):
        guard(bad)


def test_unknown_assume_tz_raises():
    with pytest.raises(TimestampError):
        guard("2026-07-01 08:00:00", assume_tz="Mars/Olympus")
