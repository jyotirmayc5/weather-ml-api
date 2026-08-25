"""Real integration tests against a local Postgres (docker-compose.yml),
proving the upsert helpers in src/db/upsert.py actually execute correctly --
tests/test_upsert_sql.py only proves the SQL is shaped correctly. Requires
`docker compose up -d` to be running; auto-skips otherwise (see conftest.py).

Every query here filters down to this test's own distinctive values rather
than selecting the whole table -- tests/test_jobs.py writes real committed
rows into this same database via plain_session (not rollback-isolated, since
jobs commit internally as real behavior), so an unfiltered "assert the table
has N rows" is only correct by accident depending on test order. Learned this
the hard way: these three tests originally did exactly that and started
failing the moment test_jobs.py existed."""
from sqlalchemy import func, select

from src.db.models import WeatherDailyHighPrediction, WeatherObservation, WeatherPrediction
from src.db.upsert import (
    upsert_daily_high_prediction,
    upsert_weather_observation,
    upsert_weather_prediction,
)


def test_weather_observation_upsert_do_nothing_on_conflict(db_session):
    values = {
        "station": "KNYC",
        "observed_time": "2026-06-16T15:00:00Z",
        "actual_temperature_f": 68.0,
    }
    db_session.execute(upsert_weather_observation(values))
    # same (station, observed_time), different payload -- must be silently ignored
    db_session.execute(
        upsert_weather_observation({**values, "actual_temperature_f": 999.0})
    )
    db_session.flush()

    rows = db_session.scalars(
        select(WeatherObservation).where(
            WeatherObservation.station == "KNYC",
            WeatherObservation.observed_time == "2026-06-16T15:00:00Z",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].actual_temperature_f == 68.0  # first write wins, not overwritten


def test_weather_prediction_upsert_updates_in_place_except_predicted_error_f(db_session):
    values = {
        "forecast_time": "2026-06-16T15:00:00Z",
        "source": "NWS",
        "forecast_temperature_f": 68.0,
        "predicted_error_f": 1.5,
    }
    db_session.execute(upsert_weather_prediction(values))
    db_session.execute(
        upsert_weather_prediction(
            {**values, "forecast_temperature_f": 70.0, "predicted_error_f": 99.0}
        )
    )
    db_session.flush()

    rows = db_session.scalars(
        select(WeatherPrediction).where(
            WeatherPrediction.forecast_time == "2026-06-16T15:00:00Z",
            WeatherPrediction.source == "NWS",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].forecast_temperature_f == 70.0  # this field does refresh
    assert rows[0].predicted_error_f == 1.5  # this one deliberately doesn't


def test_daily_high_upsert_all_four_stations_survive_a_same_day_rerun(db_session):
    # This is the real regression test for the confirmed production bug
    # (WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 0b): 2026-05-25 and 2026-07-28
    # both ended up with only KNYC's row after the daily-high flow ran,
    # because 3 of the 4 n8n insert nodes had no real ON CONFLICT on
    # (target_date, station). Simulate exactly that scenario -- a full
    # 4-station insert, then a second run for the same target_date (e.g. a
    # retry) -- and prove all 4 rows survive with updated values, not errors
    # or silent drops.
    stations = ["KNYC", "GRID_34_35_COASTAL", "GRID_36_33_MARINE", "GRID_31_39_INLAND"]

    for station in stations:
        db_session.execute(
            upsert_daily_high_prediction(
                {
                    "target_date": "2026-06-17",
                    "station": station,
                    "forecast_high_f": 73.0,
                    "prediction_created_at": "2026-06-16T13:45:00Z",
                }
            )
        )
    db_session.flush()

    count_after_first_run = db_session.scalar(
        select(func.count())
        .select_from(WeatherDailyHighPrediction)
        .where(WeatherDailyHighPrediction.target_date == "2026-06-17")
    )
    assert count_after_first_run == 4

    # rerun: same target_date, same 4 stations, updated forecast values
    for station in stations:
        db_session.execute(
            upsert_daily_high_prediction(
                {
                    "target_date": "2026-06-17",
                    "station": station,
                    "forecast_high_f": 75.0,
                    "prediction_created_at": "2026-06-16T14:00:00Z",
                }
            )
        )
    db_session.flush()

    rows = db_session.scalars(
        select(WeatherDailyHighPrediction).where(
            WeatherDailyHighPrediction.target_date == "2026-06-17"
        )
    ).all()
    assert len(rows) == 4  # no duplicates, no failures, still exactly one row per station
    assert {r.station for r in rows} == set(stations)
    assert all(r.forecast_high_f == 75.0 for r in rows)  # the rerun's values won


def test_daily_high_upsert_never_touches_actual_high_or_corrected_high(db_session):
    db_session.execute(
        upsert_daily_high_prediction(
            {"target_date": "2026-06-18", "station": "KNYC", "forecast_high_f": 70.0}
        )
    )
    db_session.flush()

    row = db_session.scalars(
        select(WeatherDailyHighPrediction).where(
            WeatherDailyHighPrediction.target_date == "2026-06-18"
        )
    ).one()
    assert row.corrected_high_f is None
    assert row.actual_high_f is None

    # simulate the separate EOD actuals job writing actual_high_f directly
    row.actual_high_f = 71.0
    db_session.flush()

    # a later daily-high rerun for the same date must not clobber actual_high_f
    db_session.execute(
        upsert_daily_high_prediction(
            {"target_date": "2026-06-18", "station": "KNYC", "forecast_high_f": 72.0}
        )
    )
    db_session.flush()
    db_session.refresh(row)
    assert row.actual_high_f == 71.0
