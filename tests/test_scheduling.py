from datetime import datetime
from zoneinfo import ZoneInfo

from src.scheduling import in_ny_time_window

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def test_matches_exact_target_time():
    now = datetime(2026, 6, 16, 9, 45, tzinfo=NY)
    assert in_ny_time_window(9, 45, now=now)


def test_within_tolerance():
    now = datetime(2026, 6, 16, 9, 52, tzinfo=NY)  # 7 min after 9:45
    assert in_ny_time_window(9, 45, tolerance_minutes=10, now=now)


def test_outside_tolerance():
    now = datetime(2026, 6, 16, 10, 30, tzinfo=NY)
    assert not in_ny_time_window(9, 45, tolerance_minutes=10, now=now)


def test_edt_utc_tick_matches_edt_time():
    # 13:45 UTC in June (EDT, UTC-4) is 9:45am NY -- the "correct" tick
    now = datetime(2026, 6, 16, 13, 45, tzinfo=UTC)
    assert in_ny_time_window(9, 45, now=now)


def test_edt_utc_tick_does_not_match_in_est_season():
    # 13:45 UTC in January (EST, UTC-5) is 8:45am NY -- the "wrong" tick for
    # a 9:45am target; this is exactly the bug being fixed.
    now = datetime(2026, 1, 16, 13, 45, tzinfo=UTC)
    assert not in_ny_time_window(9, 45, now=now)


def test_est_utc_tick_matches_in_est_season():
    # 14:45 UTC in January (EST, UTC-5) is 9:45am NY -- the "correct" tick
    # for winter, fired by the second scheduled hour in "45 13,14 * * *"
    now = datetime(2026, 1, 16, 14, 45, tzinfo=UTC)
    assert in_ny_time_window(9, 45, now=now)


def test_est_utc_tick_does_not_match_in_edt_season():
    now = datetime(2026, 6, 16, 14, 45, tzinfo=UTC)
    assert not in_ny_time_window(9, 45, now=now)


def test_target_near_midnight_still_matches_just_after_rollover():
    # actual_high_update_job's real target (23:55). A few minutes of
    # execution delay pushing this past midnight must not be treated as
    # "almost a full day away" from 23:55.
    now = datetime(2026, 6, 17, 0, 2, tzinfo=NY)  # 7 min after 23:55 the day before
    assert in_ny_time_window(23, 55, tolerance_minutes=10, now=now)


def test_target_near_midnight_does_not_match_far_into_the_next_day():
    now = datetime(2026, 6, 17, 10, 0, tzinfo=NY)
    assert not in_ny_time_window(23, 55, tolerance_minutes=10, now=now)
