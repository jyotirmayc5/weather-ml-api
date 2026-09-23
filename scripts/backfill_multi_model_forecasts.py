"""One-time backfill: pulls day-ahead ('Previous Runs') forecasts from 5
genuinely independent global weather models -- ECMWF, GFS, ICON, GEM, UKMO,
all via Open-Meteo's existing historical-forecast client
(src/ingestion/open_meteo_client.py already supports arbitrary `model=`
values, no client changes needed) -- for every one of our real production
days. WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5g.

Deliberately different from the ensemble-spread feature already tested weak
(r=+0.110): that used 4 NWS gridpoints a few miles apart -- spatial variation
within ONE model. These are 5 independently-built models from different
national weather services, a genuinely different kind of signal.

Confirmed live before building this: all 5 models return real data via the
historical-forecast-api's `_previous_day1` Previous-Runs feature (not just
the live forecast endpoint), so this can backfill our EXISTING ~112 days
immediately rather than needing months of live accumulation.

One call per model covering the full date range (not one call per day) --
5 total API calls for the whole backfill. Idempotent (ON CONFLICT DO NOTHING
on (target_date, model)).
"""
import sys
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.upsert import upsert_multi_model_forecast
from src.ingestion.open_meteo_client import daily_highs_from_hourly, fetch_historical_hourly

MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "ukmo_seamless"]


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def load_date_range(engine) -> tuple[date, date]:
    with Session(bind=engine) as session:
        row = session.execute(
            text(
                "SELECT MIN(target_date), MAX(target_date) FROM weather_daily_high_predictions "
                "WHERE station = 'KNYC' AND forecast_high_f IS NOT NULL;"
            )
        ).first()
    return row[0], row[1]


def main():
    engine = create_engine(load_dsn())
    start_date, end_date = load_date_range(engine)
    print(f"Backfilling {len(MODELS)} models for {start_date} to {end_date}.\n")

    session = Session(bind=engine)
    now = datetime.now(timezone.utc)
    total_stored = 0

    for model in MODELS:
        print(f"Fetching {model}...")
        payload = fetch_historical_hourly(start_date, end_date, model=model)
        daily = daily_highs_from_hourly(payload)

        stored = 0
        for day, values in daily.items():
            if day < start_date or day > end_date:
                continue
            session.execute(
                upsert_multi_model_forecast(
                    {
                        "target_date": day,
                        "model": model,
                        "forecast_high_f": values["forecast_high_f"],
                        "pulled_at": now,
                    }
                )
            )
            stored += 1
        session.commit()
        total_stored += stored
        print(f"  stored {stored} days for {model}")

    session.close()
    print(f"\nDone. Total rows stored: {total_stored}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
