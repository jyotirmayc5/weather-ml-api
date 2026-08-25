"""One-off: backfills kalshi_settlements with KXHIGHNY's FULL settlement
history (confirmed ~1,841 days back to 2021-08-06), not just the rolling
14-day window jobs/kalshi_settlement_job.py maintains going forward. Safe to
re-run -- upsert_kalshi_settlement is ON CONFLICT DO NOTHING on event_ticker,
and this skips event_tickers already stored before making any API calls for
them, so a re-run after a partial failure only does the remaining work.

Uses a single shared httpx.Client (src.kalshi.client.new_client) across all
requests instead of opening a new connection per call. Reconnects to the DB
every 100 events (a fresh engine/session, not one held open for the whole
run) -- a full run once died partway through with "server closed the
connection unexpectedly" (Supabase's pooler dropping a long-held idle-ish
connection), and everything before that point had already committed safely
thanks to the per-100 commit cadence; this periodic-reconnect change is the
fix for it happening again on the same connection.
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.upsert import upsert_kalshi_settlement
from src.kalshi.client import event_ticker_to_date, fetch_settled_events, get_settlement_value, new_client

SERIES_TICKER = "KXHIGHNY"


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def load_existing_event_tickers(engine) -> set[str]:
    with Session(bind=engine) as session:
        rows = session.execute(text("SELECT event_ticker FROM kalshi_settlements")).all()
    return {row[0] for row in rows}


def main():
    dsn = load_dsn()
    engine = create_engine(dsn)

    print(f"Listing all settled {SERIES_TICKER} events (paginated)...")
    with new_client() as http_client:
        events = fetch_settled_events(SERIES_TICKER, client=http_client)
    print(f"Found {len(events)} total settled events.")

    already_stored = load_existing_event_tickers(engine)
    remaining = [e for e in events if e["event_ticker"] not in already_stored]
    print(f"{len(already_stored)} already stored, {len(remaining)} remaining.\n")

    session = Session(bind=engine)
    now = datetime.now(timezone.utc)
    stored, skipped_no_value, skipped_bad_ticker = 0, 0, 0

    with new_client() as http_client:
        for i, event in enumerate(remaining, start=1):
            try:
                target_date = event_ticker_to_date(event["event_ticker"])
            except ValueError:
                skipped_bad_ticker += 1
                continue

            settled_value = get_settlement_value(event["event_ticker"], client=http_client)
            if settled_value is None:
                skipped_no_value += 1
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

            if i % 100 == 0:
                session.commit()
                session.close()
                session = Session(bind=engine)  # fresh connection, not one held open the whole run
                print(f"  ...{i}/{len(remaining)} processed, {stored} stored so far")

    session.commit()
    session.close()

    print(f"\nDone. stored={stored} skipped_no_value={skipped_no_value} skipped_bad_ticker={skipped_bad_ticker}")


if __name__ == "__main__":
    sys.exit(main())
