"""One-time backfill: pulls the real Kalshi market-implied probability near
the ~9:45am ET forecast moment for every one of our real production days
(2026-05-25 onward, where we have a real KNYC forecast_high_f), for every
bucket market that day. WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 4 -- this
is the missing half of "beats the market": kalshi_settlements only ever
recorded the FINAL outcome, never what the market thought beforehand.

Deliberately NOT run for the large Open-Meteo/1,661-day exploratory set --
market-price comparison only makes sense against OUR OWN model's actual
forecast, and we only have real forecast_high_f for our own 92 production
days. Going forward (not backfill), jobs/daily_prediction_job.py already
captures live market prices alongside the model's own prediction each
morning, so this script should never need to run again past today.

Idempotent (ON CONFLICT DO NOTHING on (target_date, market_ticker)) -- skips
already-stored (target_date, ticker) pairs before making any API calls for
them, so a re-run after a partial failure only does the remaining work.
Reconnects to the DB periodically rather than holding one connection the
whole run, same fix as backfill_kalshi_settlements.py needed for a real
Supabase pooler connection drop.

Real 429 rate-limiting hit on the first full run (roughly 2 requests/day x 92
days = ~180 rapid sequential calls) -- fixed at the client layer (429 is now
retryable, src/kalshi/client.py), plus a small proactive delay here between
requests to make hitting the limit at all less likely in the first place.
"""
import sys
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.upsert import upsert_kalshi_market_price
from src.kalshi.client import fetch_candlesticks, fetch_event_markets, market_prob_near_time, new_client

REQUEST_DELAY_SECONDS = 0.25

NY = ZoneInfo("America/New_York")
SERIES_TICKER = "KXHIGHNY"


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def load_target_dates(engine) -> list[date]:
    with Session(bind=engine) as session:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT target_date FROM weather_daily_high_predictions
                WHERE station = 'KNYC' AND forecast_high_f IS NOT NULL
                ORDER BY target_date;
                """
            )
        ).all()
    return [row[0] for row in rows]


def load_already_stored(engine) -> set[tuple[date, str]]:
    with Session(bind=engine) as session:
        rows = session.execute(text("SELECT target_date, market_ticker FROM kalshi_market_prices")).all()
    return {(row[0], row[1]) for row in rows}


def forecast_moment_ts(target_date: date) -> int:
    """9:45am ET on target_date, as a Unix timestamp -- the real forecast
    moment, DST-safe via zoneinfo."""
    dt = datetime(target_date.year, target_date.month, target_date.day, 9, 45, tzinfo=NY)
    return int(dt.timestamp())


def main():
    engine = create_engine(load_dsn())
    target_dates = load_target_dates(engine)
    already_stored = load_already_stored(engine)
    print(f"{len(target_dates)} real production days to backfill market prices for.")

    session = Session(bind=engine)
    now = datetime.now(timezone.utc)
    stored, skipped_no_markets, skipped_no_candles, skipped_already_stored = 0, 0, 0, 0

    with new_client() as http_client:
        for i, target_date in enumerate(target_dates, start=1):
            time.sleep(REQUEST_DELAY_SECONDS)
            markets = fetch_event_markets(SERIES_TICKER, target_date, client=http_client)
            if not markets:
                skipped_no_markets += 1
                continue

            target_ts = forecast_moment_ts(target_date)
            window_start = target_ts - 30 * 60
            window_end = target_ts + 30 * 60

            for market in markets:
                ticker = market["ticker"]
                if (target_date, ticker) in already_stored:
                    skipped_already_stored += 1
                    continue

                time.sleep(REQUEST_DELAY_SECONDS)
                candles = fetch_candlesticks(
                    SERIES_TICKER, ticker, window_start, window_end, period_interval=1, client=http_client
                )
                prob = market_prob_near_time(candles, target_ts)
                if prob is None:
                    skipped_no_candles += 1
                    continue

                closest_candle = min(candles, key=lambda c: abs(c["end_period_ts"] - target_ts))
                session.execute(
                    upsert_kalshi_market_price(
                        {
                            "target_date": target_date,
                            "market_ticker": ticker,
                            "strike_type": market["strike_type"],
                            "floor_strike": market.get("floor_strike"),
                            "cap_strike": market.get("cap_strike"),
                            "market_prob_at_forecast_time": prob,
                            "candle_ts": closest_candle["end_period_ts"],
                            "pulled_at": now,
                        }
                    )
                )
                stored += 1

            if i % 20 == 0:
                session.commit()
                session.close()
                session = Session(bind=engine)  # fresh connection, not one held open the whole run
                print(f"  ...{i}/{len(target_dates)} days processed, {stored} rows stored so far")

    session.commit()
    session.close()

    print(
        f"\nDone. stored={stored} skipped_already_stored={skipped_already_stored} "
        f"skipped_no_markets={skipped_no_markets} skipped_no_candles={skipped_no_candles}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
