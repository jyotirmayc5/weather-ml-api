"""One-off: backfills open_meteo_historical_daily with daily forecast-vs-
actual pairs from 2021-03-01 (Open-Meteo's stated start of GFS 2m-temperature
history) through 2026-05-24 (the day before our own real NWS-based collection
began) -- see WEATHER_KALSHI_TECHNICAL_PLAN.md for why this exists and why it
uses the Previous Runs (_previous_day1) feature specifically, not the plain
historical-forecast endpoint.

Batches requests in 90-day chunks rather than one 5-year request -- keeps
individual responses a manageable size. Safe to re-run: upsert is
ON CONFLICT DO NOTHING on (target_date, model).
"""
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.upsert import upsert_open_meteo_historical_daily
from src.ingestion.open_meteo_client import daily_highs_from_hourly, fetch_historical_hourly

START = date(2021, 3, 1)
END = date(2026, 5, 24)
MODEL = "gfs_seamless"
CHUNK_DAYS = 90


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def date_chunks(start, end, chunk_days):
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def main():
    engine = create_engine(load_dsn())
    session = Session(bind=engine)
    now = datetime.now(timezone.utc)

    total_stored = 0
    chunks = list(date_chunks(START, END, CHUNK_DAYS))
    print(f"Backfilling {START} to {END} in {len(chunks)} chunks of ~{CHUNK_DAYS} days...\n")

    for i, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        print(f"[{i}/{len(chunks)}] {chunk_start} to {chunk_end}...", end=" ")
        payload = fetch_historical_hourly(chunk_start, chunk_end, model=MODEL)
        daily = daily_highs_from_hourly(payload)

        for target_date, values in daily.items():
            session.execute(
                upsert_open_meteo_historical_daily(
                    {
                        "target_date": target_date,
                        "model": MODEL,
                        "forecast_high_f": values["forecast_high_f"],
                        "actual_high_f": values["actual_high_f"],
                        "pulled_at": now,
                    }
                )
            )
        session.commit()
        total_stored += len(daily)
        print(f"{len(daily)} days stored")

    session.close()
    print(f"\nDone. Total days stored: {total_stored}")


if __name__ == "__main__":
    sys.exit(main())
