from src.ingestion.open_meteo_client import daily_highs_from_hourly


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
