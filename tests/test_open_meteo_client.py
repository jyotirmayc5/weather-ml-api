from datetime import date
from unittest.mock import patch

import httpx

from src.ingestion.open_meteo_client import BASE_URL, daily_highs_from_hourly, fetch_live_previous_day1_high


def test_groups_by_ny_local_date_and_takes_max():
    payload = {
        "hourly": {
            "time": [
                "2026-06-15T22:00",
                "2026-06-15T23:00",
                "2026-06-16T00:00",
                "2026-06-16T12:00",
                "2026-06-16T15:00",
            ],
            "temperature_2m": [70.0, 71.0, 65.0, 80.0, 82.0],
            "temperature_2m_previous_day1": [72.0, 73.0, 66.0, 81.0, 84.0],
        }
    }
    result = daily_highs_from_hourly(payload)
    assert set(result.keys()) == {
        __import__("datetime").date(2026, 6, 15),
        __import__("datetime").date(2026, 6, 16),
    }
    day15 = result[__import__("datetime").date(2026, 6, 15)]
    assert day15["actual_high_f"] == 71.0
    assert day15["forecast_high_f"] == 73.0
    day16 = result[__import__("datetime").date(2026, 6, 16)]
    assert day16["actual_high_f"] == 82.0
    assert day16["forecast_high_f"] == 84.0


def test_skips_days_missing_either_series_entirely():
    payload = {
        "hourly": {
            "time": ["2026-06-15T12:00", "2026-06-16T12:00"],
            "temperature_2m": [70.0, None],
            "temperature_2m_previous_day1": [None, 80.0],
        }
    }
    # day15 has an actual but no forecast; day16 has a forecast but no actual
    # -- neither day has both, so both should be skipped entirely
    result = daily_highs_from_hourly(payload)
    assert result == {}


def test_handles_none_values_within_an_otherwise_valid_day():
    payload = {
        "hourly": {
            "time": ["2026-06-15T12:00", "2026-06-15T13:00", "2026-06-15T14:00"],
            "temperature_2m": [70.0, None, 75.0],
            "temperature_2m_previous_day1": [72.0, 74.0, None],
        }
    }
    result = daily_highs_from_hourly(payload)
    from datetime import date

    assert result[date(2026, 6, 15)] == {"actual_high_f": 75.0, "forecast_high_f": 74.0}


def test_empty_payload_returns_empty_dict():
    assert daily_highs_from_hourly({"hourly": {"time": [], "temperature_2m": [], "temperature_2m_previous_day1": []}}) == {}


def _fake_client(payload):
    fake = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    return fake


def test_fetch_live_previous_day1_high_queries_historical_endpoint_with_tight_range():
    # Deliberately the historical endpoint, not a "live" one -- confirmed
    # live that the live endpoint's previous_day1 support returns all None
    # even for today's date, while the historical endpoint works correctly
    # when queried with start_date=end_date=target_date.
    payload = {"hourly": {"temperature_2m_previous_day1": [60.0, 65.0, 62.0]}}
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_live_previous_day1_high("ecmwf_ifs025", date(2026, 9, 23), client=client)

    assert result == 65.0
    assert captured["url"].startswith(BASE_URL)
    assert "start_date=2026-09-23" in captured["url"]
    assert "end_date=2026-09-23" in captured["url"]
    assert "models=ecmwf_ifs025" in captured["url"]


def test_fetch_live_previous_day1_high_returns_none_when_all_null():
    payload = {"hourly": {"temperature_2m_previous_day1": [None, None, None]}}
    client = _fake_client(payload)
    assert fetch_live_previous_day1_high("gfs_seamless", date(2026, 9, 23), client=client) is None


def test_fetch_live_previous_day1_high_skips_none_values():
    payload = {"hourly": {"temperature_2m_previous_day1": [None, 70.0, None, 68.0]}}
    client = _fake_client(payload)
    assert fetch_live_previous_day1_high("icon_seamless", date(2026, 9, 23), client=client) == 70.0
