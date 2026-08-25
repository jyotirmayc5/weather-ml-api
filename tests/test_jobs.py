"""Wiring tests for jobs/*.py: mock the external calls (NWS, /predict) so
these are fast and deterministic, but use the real local Postgres
(plain_session, see conftest.py) to prove the fetch -> transform -> write
chain actually lands correct rows -- the transform/upsert pieces themselves
are already tested elsewhere; this is specifically about the wiring."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import select

import jobs.actual_high_update_job as actual_high_update_job
import jobs.corrected_high_update_job as corrected_high_update_job
import jobs.daily_high_forecast_job as daily_high_forecast_job
import jobs.hourly_forecast_job as hourly_forecast_job
import jobs.latest_observations_job as latest_observations_job
from src.db.models import JobRun, WeatherDailyHighPrediction, WeatherObservation, WeatherPrediction
from src.db.upsert import upsert_daily_high_prediction

NY = ZoneInfo("America/New_York")


def _obs_feature(station_url, temp_c=18.0):
    return {
        "properties": {
            "station": station_url,
            "timestamp": "2099-01-01T00:00:00+00:00",  # distinctive, avoids collisions
            "temperature": {"value": temp_c},
            "dewpoint": {"value": 10.0},
            "relativeHumidity": {"value": 55},
            "barometricPressure": {"value": 101500},
            "windSpeed": {"value": 5.0},
            "windDirection": {"value": 180},
            "visibility": {"value": 16000},
            "textDescription": "Clear",
        }
    }


def _gridpoint_payload_for_next_hours(hours_ahead: range):
    now = datetime.now(timezone.utc)
    values = [
        {
            "validTime": (now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S+00:00") + "/PT1H",
            "value": 18.0,
        }
        for h in hours_ahead
    ]
    field_names = [
        "temperature",
        "dewpoint",
        "relativeHumidity",
        "skyCover",
        "probabilityOfPrecipitation",
        "windSpeed",
        "windDirection",
    ]
    return {
        "properties": {
            "gridId": "OKX",
            "gridX": 33,
            "gridY": 37,
            **{f: {"values": values} for f in field_names},
        }
    }


def test_latest_observations_job_writes_all_5_stations(plain_session):
    items = [
        _obs_feature(f"https://api.weather.gov/stations/{s}")
        for s in ["KNYC", "KLGA", "KJFK", "KEWR", "KTEB"]
    ]
    with (
        patch.object(latest_observations_job, "get_session", lambda: plain_session),
        patch.object(latest_observations_job, "fetch_latest_observation", side_effect=items),
    ):
        latest_observations_job.run()

    rows = plain_session.scalars(
        select(WeatherObservation).where(
            WeatherObservation.observed_time == datetime(2099, 1, 1, tzinfo=timezone.utc)
        )
    ).all()
    assert {r.station for r in rows} == {"KNYC", "KLGA", "KJFK", "KEWR", "KTEB"}

    job_run = plain_session.scalars(
        select(JobRun).where(JobRun.job_name == "latest_observations_job")
    ).first()
    assert job_run.status == "success"


def test_hourly_forecast_job_calls_predict_and_writes_predictions(plain_session):
    payload = _gridpoint_payload_for_next_hours(range(1, 3))

    def fake_predict(body):
        return {
            "timestamp": body["timestamp"],
            "forecast_temperature_c": body["temperature_c"],
            "corrected_temperature_c": body["temperature_c"] + 1.0,
            "bias_c": 1.0,
            "bias_f": 1.8,
            "dewpoint_c": body["dewpoint_c"],
            "humidity_pct": body["humidity_pct"],
            "wind_speed": body["wind_speed"],
            "wind_direction": body["wind_direction"],
            "precip_probability_pct": body["precip_probability_pct"],
            "sky_cover_pct": body["sky_cover_pct"],
        }

    with (
        patch.object(hourly_forecast_job, "get_session", lambda: plain_session),
        patch.object(hourly_forecast_job, "fetch_gridpoint_forecast", return_value=payload),
        patch.object(hourly_forecast_job, "call_predict", side_effect=fake_predict),
    ):
        hourly_forecast_job.run()

    rows = plain_session.scalars(select(WeatherPrediction)).all()
    assert len(rows) >= 2
    assert all(r.source == "NWS" for r in rows)
    assert all(r.corrected_temperature_f is not None for r in rows)


def test_daily_high_forecast_job_writes_all_4_stations(plain_session):
    payload = _gridpoint_payload_for_next_hours(range(20, 26))  # spans into "tomorrow"

    with (
        patch.object(daily_high_forecast_job, "get_session", lambda: plain_session),
        patch.object(daily_high_forecast_job, "fetch_gridpoint_forecast", return_value=payload),
    ):
        daily_high_forecast_job.run()

    tomorrow_ny = (datetime.now(NY) + timedelta(days=1)).strftime("%Y-%m-%d")
    rows = plain_session.scalars(
        select(WeatherDailyHighPrediction).where(
            WeatherDailyHighPrediction.target_date == tomorrow_ny
        )
    ).all()
    assert {r.station for r in rows} == {
        "KNYC",
        "GRID_34_35_COASTAL",
        "GRID_36_33_MARINE",
        "GRID_31_39_INLAND",
    }


def test_actual_high_update_job_updates_existing_knyc_row(plain_session):
    today_ny = datetime.now(NY).strftime("%Y-%m-%d")
    plain_session.execute(
        upsert_daily_high_prediction(
            {"target_date": today_ny, "station": "KNYC", "forecast_high_f": 70.0}
        )
    )
    plain_session.commit()

    obs_payload = {
        "features": [
            {
                "properties": {
                    "station": "https://api.weather.gov/stations/KNYC",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "temperature": {"value": 25.0},  # -> 77.0F
                    "barometricPressure": {"value": 101500},
                }
            }
        ]
    }

    with (
        patch.object(actual_high_update_job, "get_session", lambda: plain_session),
        patch.object(actual_high_update_job, "fetch_day_observations", return_value=obs_payload),
    ):
        actual_high_update_job.run()

    row = plain_session.scalars(
        select(WeatherDailyHighPrediction).where(
            WeatherDailyHighPrediction.target_date == today_ny,
            WeatherDailyHighPrediction.station == "KNYC",
        )
    ).one()
    assert row.actual_high_f == 77.0
    assert row.raw_error_f == 7.0  # 77 - 70


def test_corrected_high_update_job_backfills_from_max_hourly_corrected(plain_session):
    ny_today = datetime.now(NY)
    forecast_time = ny_today.replace(hour=15, minute=0, second=0, microsecond=0)

    plain_session.add(
        WeatherPrediction(
            forecast_time=forecast_time.astimezone(timezone.utc),
            corrected_temperature_f=81.5,
            source="NWS",
        )
    )
    plain_session.execute(
        upsert_daily_high_prediction(
            {
                "target_date": ny_today.strftime("%Y-%m-%d"),
                "station": "KNYC",
                "forecast_high_f": 78.0,
            }
        )
    )
    plain_session.commit()

    with patch.object(corrected_high_update_job, "get_session", lambda: plain_session):
        corrected_high_update_job.run()

    row = plain_session.scalars(
        select(WeatherDailyHighPrediction).where(
            WeatherDailyHighPrediction.target_date == ny_today.strftime("%Y-%m-%d"),
            WeatherDailyHighPrediction.station == "KNYC",
        )
    ).one()
    assert row.corrected_high_f == 81.5
