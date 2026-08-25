"""9:45am ET (real n8n cron: '0 45 9 * * *'): NWS 24h gridpoint forecast for
all 4 stations -> weather_daily_high_predictions. This is the actual fix for
the confirmed production bug in WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 0b --
the same upsert_daily_high_prediction() call is used for all 4 stations here,
unlike the real n8n workflow where only the KNYC branch had a real
ON CONFLICT (target_date, station); the other 3 branches lost data outright
on at least 2 confirmed dates (2026-05-25, 2026-07-28)."""
from datetime import datetime, timezone

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.db.upsert import upsert_daily_high_prediction
from src.features.daily_features import forecast_high
from src.ingestion.nws_client import fetch_gridpoint_forecast

# (office, gridX, gridY, station, source) -- exact values from archive/n8n_export.json
GRIDPOINTS = [
    ("OKX", 33, 37, "KNYC", "NWS OKX/33,37"),
    ("OKX", 34, 35, "GRID_34_35_COASTAL", "NWS OKX/34,35"),
    ("OKX", 36, 33, "GRID_36_33_MARINE", "NWS OKX/36,33"),
    ("OKX", 31, 39, "GRID_31_39_INLAND", "NWS OKX/31,39"),
]


def run():
    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "daily_high_forecast_job"):
        for office, grid_x, grid_y, station, source in GRIDPOINTS:
            payload = fetch_gridpoint_forecast(office, grid_x, grid_y)
            row = forecast_high(payload, now, station=station, source=source)
            session.execute(upsert_daily_high_prediction(row))
        session.commit()


if __name__ == "__main__":
    run()
