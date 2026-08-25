"""Shadow-mode proof of concept for latest_observations_job.py: same fetch +
transform, but writes into weather_observations_v2 (a staging table cloned
from the real weather_observations via `CREATE TABLE ... LIKE ... INCLUDING
ALL`) instead of the production table. Temporary validation-only code --
deliberately not sharing src/db/upsert.py's API, so shadow-mode concerns don't
leak into the real job code. Once shadow mode has matched n8n's real output
for 7-14 days (WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 4 Step 5), this file and
the _v2 table should be deleted, not kept around."""
from sqlalchemy import text

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.ingestion.normalize import return_observations
from src.ingestion.nws_client import fetch_latest_observation

STATIONS = ["KNYC", "KLGA", "KJFK", "KEWR", "KTEB"]

UPSERT_SQL = text(
    """
    INSERT INTO weather_observations_v2 (
        observed_time, station, actual_temperature_f, actual_dewpoint_f,
        actual_humidity_pct, actual_pressure_pa, actual_pressure_hpa,
        wind_u, wind_v, actual_wind_speed, actual_wind_direction,
        visibility_m, text_description
    ) VALUES (
        :observed_time, :station, :actual_temperature_f, :actual_dewpoint_f,
        :actual_humidity_pct, :actual_pressure_pa, :actual_pressure_hpa,
        :wind_u, :wind_v, :actual_wind_speed, :actual_wind_direction,
        :visibility_m, :text_description
    )
    ON CONFLICT (station, observed_time) DO NOTHING;
    """
)


def run():
    session = get_session()
    with track_job_run(session, "latest_observations_job_shadow"):
        items = [fetch_latest_observation(station) for station in STATIONS]
        rows = return_observations(items)
        for row in rows:
            session.execute(UPSERT_SQL, row)
        session.commit()
        return rows


if __name__ == "__main__":
    for row in run():
        print(row)
