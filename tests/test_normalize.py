import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tz_lock.normalize import (  # noqa: E402
    LOCKED_TZ_NAME,
    TimestampError,
    client_skew_hours,
    normalize,
    normalize_batch,
    parse_utc,
    render,
    to_shanghai,
)


def test_naive_datetime_assumed_utc():
    out = to_shanghai(datetime(2026, 10, 28, 16, 0, 0))
    assert out.strftime("%Y-%m-%d %H:%M") == "2026-10-29 00:00"
    assert str(out.tzinfo) == LOCKED_TZ_NAME


@pytest.mark.parametrize(
    "raw",
    ["2026-10-28T16:00:00Z", "2026-10-28T16:00:00+00:00", "2026-10-28 16:00:00",
     "2026-10-28T12:00:00-04:00", "2026-10-29T00:00:00+08:00"],
)
def test_iso_strings_all_converge(raw):
    assert render(raw) == "2026-10-29 00:00:00"


@pytest.mark.parametrize("client", [None, "America/New_York", "UTC",
                                    "Europe/London", "Asia/Tokyo", "Bogus/Zone"])
def test_client_tz_never_changes_output(client):
    assert render("2026-10-28T16:00:00Z", client) == "2026-10-29 00:00:00"


def test_new_york_injection_reports_12h_skew_in_edt():
    # 2026-10-28 is still EDT (UTC-4): Shanghai +8 => 12h skew, the observed bug
    n = normalize("2026-10-28T16:00:00Z", "America/New_York")
    assert n.client_skew_hours == 12.0
    assert n.skewed
    assert n.display == "2026-10-29 00:00:00"


def test_new_york_skew_13h_after_dst_end():
    assert client_skew_hours(parse_utc("2026-12-01T00:00:00Z"), "America/New_York") == 13.0


def test_no_or_invalid_client_tz_skew_none():
    u = parse_utc("2026-01-01T00:00:00Z")
    assert client_skew_hours(u, None) is None
    assert client_skew_hours(u, "Not/AZone") is None
    assert client_skew_hours(u, "Asia/Shanghai") == 0.0
    assert normalize(u, "Asia/Shanghai").skewed is False


def test_epoch_seconds_and_millis():
    secs = 1_761_667_200  # 2025-10-28T16:00:00Z
    assert render(secs) == "2025-10-29 00:00:00"
    assert render(secs * 1000) == "2025-10-29 00:00:00"
    assert render(float(secs) + 0.5) == "2025-10-29 00:00:00"


def test_aware_input_converted():
    ny = timezone(timedelta(hours=-5))
    assert render(datetime(2026, 1, 1, 7, 0, tzinfo=ny)) == "2026-01-01 20:00:00"


def test_independent_of_process_tz(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert render(1_761_667_200) == "2025-10-29 00:00:00"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_day_boundary_and_year_rollover():
    assert render("2026-12-31T15:59:59Z") == "2026-12-31 23:59:59"
    assert render("2026-12-31T16:00:00Z") == "2027-01-01 00:00:00"


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", None, True, [1], 1e20])
def test_invalid_inputs_raise(bad):
    with pytest.raises(TimestampError):
        parse_utc(bad)


def test_batch_and_custom_format():
    out = normalize_batch(["2026-10-28T16:00:00Z", 0], "America/New_York")
    assert [n.display for n in out] == ["2026-10-29 00:00:00", "1970-01-01 08:00:00"]
    assert render("2026-10-28T16:00:00Z", fmt="%H:%M %Z") == "00:00 CST"
