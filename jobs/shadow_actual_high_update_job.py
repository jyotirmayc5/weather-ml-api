"""Shadow-mode variant of actual_high_update_job.py: updates
weather_daily_high_predictions_v2 instead of the real table. Requires a
matching (target_date, station='KNYC') row to already exist there (written by
shadow_daily_high_forecast_job.py) -- an UPDATE with no matching row is a
silent no-op, same as the real n8n behavior and the real job."""
from datetime import datetime, timezone

from sqlalchemy import text

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.features.actuals import eod_actuals_and_pressure
from src.ingestion.nws_client import fetch_day_observations
from src.scheduling import in_ny_time_window

UPDATE_SQL = text(
    """
    UPDATE weather_daily_high_predictions_v2
    SET
        actual_high_f = :actual_high_f,
        morning_pressure_hpa = :morning_pressure_hpa,
        afternoon_pressure_hpa = :afternoon_pressure_hpa,
        pressure_change_hpa = :pressure_change_hpa,
        avg_pressure_hpa = :avg_pressure_hpa,
        pressure_6am_hpa = :pressure_6am_hpa,
        pressure_12pm_hpa = :pressure_12pm_hpa,
        pressure_6pm_hpa = :pressure_6pm_hpa,
        raw_error_f = :actual_high_f - forecast_high_f,
        corrected_error_f = :actual_high_f - corrected_high_f
    WHERE target_date = :target_date AND station = 'KNYC';
    """
)


def run():
    if not in_ny_time_window(23, 55):
        return None

    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "actual_high_update_job_shadow"):
        payload = fetch_day_observations("KNYC", now)
        result = eod_actuals_and_pressure(payload, now)
        session.execute(UPDATE_SQL, result)
        session.commit()
        return result


if __name__ == "__main__":
    print(run())
