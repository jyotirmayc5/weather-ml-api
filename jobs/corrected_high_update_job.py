"""Runs after hourly_forecast_job.py and daily_high_forecast_job.py are both
stable for the day: backfills corrected_high_f from the day's max corrected
hourly temperature. Matches the real n8n 'Update corrected_high_f' node
exactly -- kept as literal SQL rather than translated to ORM Core, since it's
a pure set-based bulk operation with no per-row Python logic and translating
it risks subtly diverging from the real query.

Faithfully preserves a real quirk (see WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 0):
this UPDATE has no station filter, so the SAME corrected_high_f (derived
entirely from KNYC's hourly data, since weather_predictions has no per-station
breakdown) gets applied to all 4 stations' rows for a given date -- not a
per-station corrected value. Don't "fix" this without updating the plan first.
"""
from sqlalchemy import text

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.scheduling import in_ny_time_window

CORRECTED_HIGH_UPDATE_SQL = text(
    """
    UPDATE weather_daily_high_predictions d
    SET corrected_high_f = x.corrected_high_f
    FROM (
      SELECT
        (forecast_time AT TIME ZONE 'America/New_York')::date AS target_date,
        MAX(corrected_temperature_f) AS corrected_high_f
      FROM weather_predictions
      GROUP BY
        (forecast_time AT TIME ZONE 'America/New_York')::date
    ) x
    WHERE d.target_date::date = x.target_date;
    """
)


def run():
    # See src/scheduling.py -- render.yaml should fire this at both possible
    # UTC times for ~9:50am ET (shortly after daily_high_forecast_job).
    if not in_ny_time_window(9, 50):
        return

    session = get_session()
    with track_job_run(session, "corrected_high_update_job"):
        session.execute(CORRECTED_HIGH_UPDATE_SQL)
        session.commit()


if __name__ == "__main__":
    run()
