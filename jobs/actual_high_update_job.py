"""11:55pm ET (real n8n cron: '0 55 23 * * *'): KNYC full-day observations ->
actual_high_f + pressure features + errors, written into weather_daily_high_predictions.
Matches the real 'KNYC actuals for EOD' -> 'Code in JavaScript' -> SQL UPDATE flow.

Faithfully reproduces a real quirk found while porting (see
WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 0): the JS computes `station` dynamically
from the observations payload (features[0].properties.station), but the n8n
SQL's WHERE clause hardcodes station = 'KNYC' literally, ignoring that
computed value entirely. Harmless today only because this job only ever
queries the KNYC observations endpoint, so the computed station is always
'KNYC' in practice -- but the WHERE clause below intentionally does NOT use
eod_actuals_and_pressure()'s station output, to match production exactly."""
from datetime import datetime, timezone

from sqlalchemy import update

from src.db.job_runs import track_job_run
from src.db.models import WeatherDailyHighPrediction
from src.db.session import get_session
from src.features.actuals import eod_actuals_and_pressure
from src.ingestion.nws_client import fetch_day_observations
from src.scheduling import in_ny_time_window


def run():
    # See src/scheduling.py -- render.yaml should fire this at both possible
    # UTC times for 11:55pm ET to stay correct across DST.
    if not in_ny_time_window(23, 55):
        return

    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "actual_high_update_job"):
        payload = fetch_day_observations("KNYC", now)
        result = eod_actuals_and_pressure(payload, now)

        stmt = (
            update(WeatherDailyHighPrediction)
            .where(
                WeatherDailyHighPrediction.target_date == result["target_date"],
                WeatherDailyHighPrediction.station == "KNYC",  # not result["station"] -- see docstring
            )
            .values(
                actual_high_f=result["actual_high_f"],
                morning_pressure_hpa=result["morning_pressure_hpa"],
                afternoon_pressure_hpa=result["afternoon_pressure_hpa"],
                pressure_change_hpa=result["pressure_change_hpa"],
                avg_pressure_hpa=result["avg_pressure_hpa"],
                pressure_6am_hpa=result["pressure_6am_hpa"],
                pressure_12pm_hpa=result["pressure_12pm_hpa"],
                pressure_6pm_hpa=result["pressure_6pm_hpa"],
                raw_error_f=result["actual_high_f"] - WeatherDailyHighPrediction.forecast_high_f,
                corrected_error_f=result["actual_high_f"] - WeatherDailyHighPrediction.corrected_high_f,
            )
        )
        session.execute(stmt)
        session.commit()


if __name__ == "__main__":
    run()
