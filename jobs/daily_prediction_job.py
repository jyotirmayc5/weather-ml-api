"""~9:48am ET, shortly after daily_high_forecast_job.py: computes model
probabilities for today's real Kalshi KXHIGHNY bucket markets and logs them
alongside the market's own current prices, into kalshi_predictions.

WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 6 -- this is read-only logging
for comparison, NOT a trading signal and NOT wired to any order placement.
The backtest (src/backtest/daily_high_backtest.py, scored via
scripts/kalshi_ground_truth_backtest.py) only proves the model beats a naive
baseline, not that it beats the market -- that comparison is exactly what
this table exists to build up over the next several weeks before any
paper/live trading decision is considered.

Key timing detail, easy to get backwards: TODAY's Kalshi market (resolving
tonight) needs the forecast written YESTERDAY for target_date=today, not a
freshly-written today's row (which is daily_high_forecast_job's forecast FOR
TOMORROW). This job only reads an existing row, never writes one.

Uses ALL currently available (target_date < today) KNYC-forecast-vs-Kalshi-
settlement residuals to estimate probabilities -- this is the live-deployment
analogue of walk_forward_backtest's chronological, no-future-leakage design
(each day only ever sees strictly earlier days), just applied once per real
day instead of scored retrospectively across a historical set."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.backtest.daily_high_backtest import predicted_prob_bucket
from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.db.upsert import upsert_kalshi_prediction
from src.kalshi.client import fetch_open_event
from src.scheduling import in_ny_time_window

NY = ZoneInfo("America/New_York")
SERIES_TICKER = "KXHIGHNY"

FORECAST_SQL = text(
    """
    SELECT forecast_high_f FROM weather_daily_high_predictions
    WHERE station = 'KNYC' AND target_date = :today AND forecast_high_f IS NOT NULL;
    """
)

RESIDUALS_SQL = text(
    """
    SELECT k.settled_value_f - d.forecast_high_f
    FROM weather_daily_high_predictions d
    JOIN kalshi_settlements k ON k.target_date = d.target_date
    WHERE d.station = 'KNYC' AND d.forecast_high_f IS NOT NULL AND d.target_date < :today
    ORDER BY d.target_date;
    """
)


def run():
    # See src/scheduling.py -- render.yaml should fire this at both possible
    # UTC times for ~9:48am ET to stay correct across DST.
    if not in_ny_time_window(9, 48):
        return

    today = datetime.now(NY).date()
    now = datetime.now(timezone.utc)
    session = get_session()
    with track_job_run(session, "daily_prediction_job"):
        forecast_row = session.execute(FORECAST_SQL, {"today": today}).first()
        if forecast_row is None:
            print(f"No KNYC forecast_high_f for target_date={today} yet -- skipping today's predictions.")
            return
        forecast_high_f = float(forecast_row[0])

        residuals = [float(r[0]) for r in session.execute(RESIDUALS_SQL, {"today": today}).all()]
        if not residuals:
            print("No historical KNYC/Kalshi residual history yet -- skipping today's predictions.")
            return

        event = fetch_open_event(SERIES_TICKER, today)
        markets = event.get("markets", [])
        if not markets:
            print(f"No open {SERIES_TICKER} markets found for {today} -- skipping.")
            return

        for market in markets:
            strike_type = market["strike_type"]
            floor_strike = market.get("floor_strike")
            cap_strike = market.get("cap_strike")
            model_probability = predicted_prob_bucket(
                forecast_high_f, residuals, strike_type, floor_strike, cap_strike
            )
            session.execute(
                upsert_kalshi_prediction(
                    {
                        "target_date": today,
                        "market_ticker": market["ticker"],
                        "strike_type": strike_type,
                        "floor_strike": floor_strike,
                        "cap_strike": cap_strike,
                        "forecast_high_f": forecast_high_f,
                        "residual_sample_size": len(residuals),
                        "model_probability": model_probability,
                        "market_yes_bid": float(market["yes_bid_dollars"]),
                        "market_yes_ask": float(market["yes_ask_dollars"]),
                        "predicted_at": now,
                    }
                )
            )
        session.commit()


if __name__ == "__main__":
    run()
