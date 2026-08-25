"""Shadow-mode variant of hourly_forecast_job.py: writes to
weather_predictions_v2 instead of the real table. See
shadow_latest_observations_job.py for the shadow-mode rationale -- delete
this file (and the _v2 tables) once shadow mode has validated."""
from datetime import datetime, timezone

from sqlalchemy import text

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.ingestion.normalize import convert_to_f, normalize_nws_data
from src.ingestion.nws_client import fetch_gridpoint_forecast
from src.ingestion.predict_client import call_predict

UPSERT_SQL = text(
    """
    INSERT INTO weather_predictions_v2 (
        forecast_time, forecast_temperature_f, corrected_temperature_f,
        humidity_pct, wind_speed, wind_direction, source, created_at,
        forecast_created_at, sky_cover_pct, precip_probability_pct, dewpoint_f
    ) VALUES (
        :forecast_time, :forecast_temperature_f, :corrected_temperature_f,
        :humidity_pct, :wind_speed, :wind_direction, :source, :created_at,
        :forecast_created_at, :sky_cover_pct, :precip_probability_pct, :dewpoint_f
    )
    ON CONFLICT (forecast_time, source) DO UPDATE SET
        forecast_temperature_f = EXCLUDED.forecast_temperature_f,
        corrected_temperature_f = EXCLUDED.corrected_temperature_f,
        humidity_pct = EXCLUDED.humidity_pct,
        wind_speed = EXCLUDED.wind_speed,
        wind_direction = EXCLUDED.wind_direction,
        created_at = EXCLUDED.created_at,
        forecast_created_at = EXCLUDED.forecast_created_at,
        sky_cover_pct = EXCLUDED.sky_cover_pct,
        precip_probability_pct = EXCLUDED.precip_probability_pct,
        dewpoint_f = EXCLUDED.dewpoint_f;
    """
)


def run():
    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "hourly_forecast_job_shadow"):
        payload = fetch_gridpoint_forecast("OKX", 33, 37)
        normalized_rows = normalize_nws_data(payload, now)[:24]

        predicted_items = [
            call_predict(
                {
                    "timestamp": row["timestamp"],
                    "temperature_c": row["temperature_c"],
                    "dewpoint_c": row["dewpoint_c"],
                    "humidity_pct": row["humidity_pct"],
                    "wind_speed": row["wind_speed"],
                    "wind_direction": row["wind_direction"],
                    "precip_probability_pct": row["precip_probability_pct"],
                    "sky_cover_pct": row["sky_cover_pct"],
                }
            )
            for row in normalized_rows
        ]

        converted_rows = convert_to_f(predicted_items, now)
        for row in converted_rows:
            session.execute(
                UPSERT_SQL,
                {
                    "forecast_time": row["forecast_time"],
                    "forecast_temperature_f": row["forecast_temperature_f"],
                    "corrected_temperature_f": row["corrected_temperature_f"],
                    "humidity_pct": row["humidity_pct"],
                    "wind_speed": row["wind_speed"],
                    "wind_direction": row["wind_direction"],
                    "source": "NWS",
                    "created_at": now,
                    "forecast_created_at": row["forecast_created_at"],
                    "sky_cover_pct": row["sky_cover_pct"],
                    "precip_probability_pct": row["precip_probability_pct"],
                    "dewpoint_f": row["dewpoint_f"],
                },
            )
        session.commit()
        return converted_rows


if __name__ == "__main__":
    rows = run()
    print(f"wrote {len(rows)} rows to weather_predictions_v2")
