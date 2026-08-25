"""Shadow-mode variant of corrected_high_update_job.py: reads from
weather_predictions_v2 and updates weather_daily_high_predictions_v2. Same
no-station-filter quirk as the real job, faithfully preserved -- see that
file's docstring."""
from sqlalchemy import text

from src.db.job_runs import track_job_run
from src.db.session import get_session

CORRECTED_HIGH_UPDATE_SQL = text(
    """
    UPDATE weather_daily_high_predictions_v2 d
    SET corrected_high_f = x.corrected_high_f
    FROM (
      SELECT
        (forecast_time AT TIME ZONE 'America/New_York')::date AS target_date,
        MAX(corrected_temperature_f) AS corrected_high_f
      FROM weather_predictions_v2
      GROUP BY
        (forecast_time AT TIME ZONE 'America/New_York')::date
    ) x
    WHERE d.target_date::date = x.target_date;
    """
)


def run():
    session = get_session()
    with track_job_run(session, "corrected_high_update_job_shadow"):
        session.execute(CORRECTED_HIGH_UPDATE_SQL)
        session.commit()


if __name__ == "__main__":
    run()
    print("done")
