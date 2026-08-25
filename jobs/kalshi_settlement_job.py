"""Daily: pulls newly-settled KXHIGHNY events and stores their real
settlement value -> kalshi_settlements. Why this exists:
WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5b -- our own actual_high_f is
systematically ~0.64F off from what Kalshi actually pays out on, so future
model work needs Kalshi's real settlement as ground truth, collected
automatically rather than re-pulled by hand each time.

No DST/ET-clock-time guard needed here (unlike daily_high_forecast_job etc,
src/scheduling.py) -- this isn't tied to a specific NY clock moment, it's
just "check periodically for newly-settled markets." Idempotent: upserts
with ON CONFLICT DO NOTHING on event_ticker, so a rolling lookback window
(rather than the full series history) keeps each run fast without risking
permanently missing a day if one run fails."""
from datetime import date, datetime, timedelta, timezone

from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.db.upsert import upsert_kalshi_settlement
from src.kalshi.client import event_ticker_to_date, fetch_settled_events, get_settlement_value

SERIES_TICKER = "KXHIGHNY"
LOOKBACK_DAYS = 14


def run():
    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "kalshi_settlement_job"):
        min_date = (now - timedelta(days=LOOKBACK_DAYS)).date()
        events = fetch_settled_events(SERIES_TICKER, min_date=min_date)

        stored = 0
        for event in events:
            try:
                target_date = event_ticker_to_date(event["event_ticker"])
            except ValueError:
                continue
            if target_date < min_date:
                continue

            settled_value = get_settlement_value(event["event_ticker"])
            if settled_value is None:
                continue

            session.execute(
                upsert_kalshi_settlement(
                    {
                        "series_ticker": SERIES_TICKER,
                        "event_ticker": event["event_ticker"],
                        "target_date": target_date,
                        "settled_value_f": settled_value,
                        "pulled_at": now,
                    }
                )
            )
            stored += 1

        session.commit()
        return stored


if __name__ == "__main__":
    print(f"upserted {run()} settlement rows")
