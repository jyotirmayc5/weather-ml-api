"""Shadow-mode variant of daily_high_forecast_job.py: writes to
weather_daily_high_predictions_v2 instead of the real table, using a real
ON CONFLICT (target_date, station) for all 4 stations uniformly -- same fix
as the real job. See shadow_latest_observations_job.py for the shadow-mode
rationale."""
from datetime import datetime, timezone

from sqlalchemy import text

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.features.daily_features import forecast_high
from src.ingestion.nws_client import fetch_gridpoint_forecast

GRIDPOINTS = [
    ("OKX", 33, 37, "KNYC", "NWS OKX/33,37"),
    ("OKX", 34, 35, "GRID_34_35_COASTAL", "NWS OKX/34,35"),
    ("OKX", 36, 33, "GRID_36_33_MARINE", "NWS OKX/36,33"),
    ("OKX", 31, 39, "GRID_31_39_INLAND", "NWS OKX/31,39"),
]

UPSERT_SQL = text(
    """
    INSERT INTO weather_daily_high_predictions_v2 (
        prediction_created_at, target_date, station, forecast_high_f,
        corrected_high_f, forecast_low_f, avg_humidity_pct, avg_dewpoint_f,
        avg_sky_cover_pct, max_precip_probability_pct, avg_wind_speed,
        avg_wind_sin, avg_wind_cos, peak_heating_cloud_pct,
        peak_heating_temp_f, lead_hours, month, day_of_year, source
    ) VALUES (
        :prediction_created_at, :target_date, :station, :forecast_high_f,
        NULL, :forecast_low_f, :avg_humidity_pct, :avg_dewpoint_f,
        :avg_sky_cover_pct, :max_precip_probability_pct, :avg_wind_speed,
        :avg_wind_sin, :avg_wind_cos, :peak_heating_cloud_pct,
        :peak_heating_temp_f, :lead_hours, :month, :day_of_year, :source
    )
    ON CONFLICT (target_date, station) DO UPDATE SET
        prediction_created_at = EXCLUDED.prediction_created_at,
        forecast_high_f = EXCLUDED.forecast_high_f,
        forecast_low_f = EXCLUDED.forecast_low_f,
        avg_humidity_pct = EXCLUDED.avg_humidity_pct,
        avg_dewpoint_f = EXCLUDED.avg_dewpoint_f,
        avg_sky_cover_pct = EXCLUDED.avg_sky_cover_pct,
        max_precip_probability_pct = EXCLUDED.max_precip_probability_pct,
        avg_wind_speed = EXCLUDED.avg_wind_speed,
        avg_wind_sin = EXCLUDED.avg_wind_sin,
        avg_wind_cos = EXCLUDED.avg_wind_cos,
        peak_heating_cloud_pct = EXCLUDED.peak_heating_cloud_pct,
        peak_heating_temp_f = EXCLUDED.peak_heating_temp_f,
        lead_hours = EXCLUDED.lead_hours,
        month = EXCLUDED.month,
        day_of_year = EXCLUDED.day_of_year,
        source = EXCLUDED.source;
    """
)


def run():
    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "daily_high_forecast_job_shadow"):
        rows = []
        for office, grid_x, grid_y, station, source in GRIDPOINTS:
            payload = fetch_gridpoint_forecast(office, grid_x, grid_y)
            row = forecast_high(payload, now, station=station, source=source)
            session.execute(UPSERT_SQL, row)
            rows.append(row)
        session.commit()
        return rows


if __name__ == "__main__":
    rows = run()
    print(f"wrote {len(rows)} rows to weather_daily_high_predictions_v2")
