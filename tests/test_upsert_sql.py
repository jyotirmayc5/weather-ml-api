"""Compile-time checks on the generated SQL for the upsert helpers -- no live
Postgres needed (Docker isn't available on this machine; see
WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 3a). These can't prove the SQL executes
correctly against a real server, but they do prove the ON CONFLICT clause
targets the right columns, which is exactly the class of bug confirmed in
production (Sec 0b): the 3 non-KNYC daily-high nodes never had a real
ON CONFLICT (target_date, station) at all."""
from sqlalchemy.dialects import postgresql

from src.db.upsert import (
    upsert_daily_high_prediction,
    upsert_weather_observation,
    upsert_weather_prediction,
)


def compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))


def test_weather_observation_upsert_is_do_nothing_on_station_observed_time():
    stmt = upsert_weather_observation({"station": "KNYC", "observed_time": "2026-06-16T15:00:00Z"})
    sql = compiled(stmt)
    assert "ON CONFLICT (station, observed_time) DO NOTHING" in sql


def test_weather_prediction_upsert_matches_real_conflict_target_and_update_set():
    stmt = upsert_weather_prediction(
        {
            "forecast_time": "2026-06-16T15:00:00Z",
            "source": "NWS",
            "forecast_temperature_f": 68.0,
        }
    )
    sql = compiled(stmt)
    assert "ON CONFLICT (forecast_time, source) DO UPDATE SET" in sql
    for col in [
        "forecast_temperature_f",
        "corrected_temperature_f",
        "humidity_pct",
        "wind_speed",
        "wind_direction",
        "created_at",
        "forecast_created_at",
        "sky_cover_pct",
        "precip_probability_pct",
        "dewpoint_f",
    ]:
        assert f"{col} = excluded.{col}" in sql


def test_weather_prediction_upsert_deliberately_does_not_refresh_predicted_error_f():
    # Matches real production behavior: predicted_error_f keeps its original
    # value across an update, it is not in the SQL's UPDATE SET.
    stmt = upsert_weather_prediction(
        {"forecast_time": "2026-06-16T15:00:00Z", "source": "NWS"}
    )
    sql = compiled(stmt)
    assert "predicted_error_f = excluded.predicted_error_f" not in sql


def test_daily_high_upsert_targets_target_date_and_station_for_every_station():
    # This is the actual regression test for the confirmed bug: unlike the
    # real n8n workflow (which only did this correctly for KNYC), this same
    # function must produce a real ON CONFLICT (target_date, station) for
    # ALL FOUR stations, not just KNYC.
    for station in ["KNYC", "GRID_34_35_COASTAL", "GRID_36_33_MARINE", "GRID_31_39_INLAND"]:
        stmt = upsert_daily_high_prediction(
            {
                "target_date": "2026-06-17",
                "station": station,
                "forecast_high_f": 73.0,
                "prediction_created_at": "2026-06-16T13:45:00Z",
            }
        )
        sql = compiled(stmt)
        assert "ON CONFLICT (target_date, station) DO UPDATE SET" in sql, station


def test_daily_high_upsert_accepts_forecast_high_output_directly():
    # Regression test: forecast_high()'s real output includes its own
    # corrected_high_f: None key, which used to collide with this function's
    # own corrected_high_f=None and crash. Feed it something shaped exactly
    # like that real output (not a hand-trimmed test dict) to catch this class
    # of bug going forward.
    from datetime import datetime, timezone

    from src.features.daily_features import forecast_high

    fake_payload = {
        "properties": {
            "temperature": {
                "values": [{"validTime": "2026-06-17T18:00:00+00:00/PT1H", "value": 20.0}]
            }
        }
    }
    row = forecast_high(fake_payload, datetime(2026, 6, 16, 13, 45, tzinfo=timezone.utc))
    assert "corrected_high_f" in row  # sanity: this is what caused the collision
    stmt = upsert_daily_high_prediction(row)  # must not raise
    compiled(stmt)


def test_daily_high_upsert_never_overwrites_actual_or_corrected_columns():
    # actual_high_f / raw_error_f / corrected_error_f are only ever written by
    # the separate EOD actuals job; corrected_high_f is only ever written by
    # the separate corrected_high_update_job backfill. None of the 4 should
    # appear in this upsert's UPDATE SET.
    stmt = upsert_daily_high_prediction(
        {"target_date": "2026-06-17", "station": "KNYC", "forecast_high_f": 73.0}
    )
    sql = compiled(stmt)
    for col in ["actual_high_f", "raw_error_f", "corrected_error_f", "corrected_high_f"]:
        assert f"{col} = excluded.{col}" not in sql
