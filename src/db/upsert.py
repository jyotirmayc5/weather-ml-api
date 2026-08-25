"""Upsert helpers reproducing the exact ON CONFLICT semantics of the real n8n
SQL nodes (archive/n8n_export.json), for all three tables. Using ONE function
per table -- called identically for every station -- is the actual fix for
the confirmed production bug in WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 0b: the
three non-KNYC daily-high nodes never had a real upsert on (target_date,
station) at all, unlike the KNYC node. There's no "per-station" special case
here, deliberately."""
from sqlalchemy.dialects.postgresql import insert

from src.db.models import WeatherDailyHighPrediction, WeatherObservation, WeatherPrediction


def upsert_weather_observation(values: dict):
    """Matches: INSERT ... ON CONFLICT (station, observed_time) DO NOTHING."""
    stmt = insert(WeatherObservation).values(**values)
    return stmt.on_conflict_do_nothing(index_elements=["station", "observed_time"])


def upsert_weather_prediction(values: dict):
    """Matches the real SQL exactly, including that `source` (the conflict
    target) and `predicted_error_f` are deliberately NOT in the UPDATE SET --
    predicted_error_f keeps its original value across updates in production
    today. Don't "helpfully" add it to the update set without checking
    whether anything already depends on that behavior."""
    stmt = insert(WeatherPrediction).values(**values)
    update_columns = [
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
    ]
    return stmt.on_conflict_do_update(
        index_elements=["forecast_time", "source"],
        set_={col: getattr(stmt.excluded, col) for col in update_columns},
    )


def upsert_daily_high_prediction(values: dict):
    """Matches the real KNYC-branch SQL exactly (target_date, station) --
    applied uniformly to all 4 stations, which is the actual fix for the
    confirmed bug. `corrected_high_f`/`actual_high_f`/`raw_error_f`/
    `corrected_error_f` are deliberately excluded from both insert defaults
    and the update set here, matching production: corrected_high_f starts
    NULL and gets backfilled by a separate later query
    (corrected_high_update_job.py), and the actual_high_f/error columns are
    only ever touched by the EOD actuals job.

    Deliberately strips `corrected_high_f` out of `values` if present rather
    than trusting the caller to omit it -- forecast_high()'s real output
    (src/features/daily_features.py) includes a `corrected_high_f: None` key
    of its own, and passing that dict straight through used to crash here
    with "got multiple values for keyword argument" before this was caught
    while wiring jobs/daily_high_forecast_job.py. No test had exercised that
    exact combination until then."""
    values = {k: v for k, v in values.items() if k != "corrected_high_f"}
    stmt = insert(WeatherDailyHighPrediction).values(**values, corrected_high_f=None)
    update_columns = [
        "prediction_created_at",
        "forecast_high_f",
        "forecast_low_f",
        "avg_humidity_pct",
        "avg_dewpoint_f",
        "avg_sky_cover_pct",
        "max_precip_probability_pct",
        "avg_wind_speed",
        "avg_wind_sin",
        "avg_wind_cos",
        "peak_heating_cloud_pct",
        "peak_heating_temp_f",
        "lead_hours",
        "month",
        "day_of_year",
        "source",
    ]
    return stmt.on_conflict_do_update(
        index_elements=["target_date", "station"],
        set_={col: getattr(stmt.excluded, col) for col in update_columns},
    )
