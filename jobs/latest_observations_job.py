"""Every 15 min: KNYC/KLGA/KJFK/KEWR/KTEB latest observations -> weather_observations.
Matches the real n8n 'Schedule actuals latest' flow. First in the migration
cutover order per WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 4 Step 5 -- simplest
job, highest run frequency, validates the plumbing fastest."""
from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.db.upsert import upsert_weather_observation
from src.ingestion.normalize import return_observations
from src.ingestion.nws_client import fetch_latest_observation

STATIONS = ["KNYC", "KLGA", "KJFK", "KEWR", "KTEB"]


def run():
    session = get_session()
    with track_job_run(session, "latest_observations_job"):
        items = [fetch_latest_observation(station) for station in STATIONS]
        rows = return_observations(items)
        for row in rows:
            session.execute(upsert_weather_observation(row))
        session.commit()


if __name__ == "__main__":
    run()
