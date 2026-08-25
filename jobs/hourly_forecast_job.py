"""Hourly (real n8n cron: minute 2 of every hour): NWS gridpoint forecast
(KNYC, OKX/33,37) -> normalize -> /predict bias correction -> weather_predictions.
Matches the real 'Schedule prediction engine' flow, including capping to the
first 24 rows (the 'Limit' node) and hardcoding source='NWS' on write (n8n's
INSERT literal does this regardless of what the JS computed for `source`)."""
from datetime import datetime, timezone

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.db.upsert import upsert_weather_prediction
from src.ingestion.normalize import convert_to_f, normalize_nws_data
from src.ingestion.nws_client import fetch_gridpoint_forecast
from src.ingestion.predict_client import call_predict


def run():
    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "hourly_forecast_job"):
        payload = fetch_gridpoint_forecast("OKX", 33, 37)
        normalized_rows = normalize_nws_data(payload, now)[:24]

        predicted_items = []
        for row in normalized_rows:
            predicted_items.append(
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
            )

        converted_rows = convert_to_f(predicted_items, now)
        for row in converted_rows:
            session.execute(
                upsert_weather_prediction(
                    {
                        "forecast_time": row["forecast_time"],
                        "forecast_temperature_f": row["forecast_temperature_f"],
                        "corrected_temperature_f": row["corrected_temperature_f"],
                        "humidity_pct": row["humidity_pct"],
                        "wind_speed": row["wind_speed"],
                        "wind_direction": row["wind_direction"],
                        "source": "NWS",  # matches n8n's SQL literal, not row["source"]
                        "created_at": now,
                        "forecast_created_at": row["forecast_created_at"],
                        "sky_cover_pct": row["sky_cover_pct"],
                        "precip_probability_pct": row["precip_probability_pct"],
                        "dewpoint_f": row["dewpoint_f"],
                    }
                )
            )
        session.commit()


if __name__ == "__main__":
    run()
