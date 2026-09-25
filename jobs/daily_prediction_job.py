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

Sec 5g update: the point forecast is now a 6-model ensemble average (NWS +
ECMWF/GFS/ICON/GEM/UKMO via src/ingestion/open_meteo_client.py's
fetch_live_previous_day1_high_multi), not NWS alone -- validated walk-forward
(scripts/test_multi_model_ensemble_adjustment.py) to narrow the gap with the
real market by ~64% on 61 real days (Brier gap 0.0330 -> 0.0118) versus
NWS-only. Today's independent-model forecasts (whichever the single combined
request actually returns -- individual models can be missing from the result
without the whole request failing) are also persisted to
multi_model_forecasts so future days' residual history stays complete without
a separate backfill step. If the combined fetch fails entirely, the ensemble
degrades to NWS-only rather than blocking predictions.

Uses ALL currently available (target_date < today) ensemble-forecast-vs-
Kalshi-settlement residuals to estimate probabilities -- this is the
live-deployment analogue of walk_forward_backtest's chronological,
no-future-leakage design (each day only ever sees strictly earlier days),
just applied once per real day instead of scored retrospectively across a
historical set.

Real bug hit twice in production, not hypothetical. First: calling Open-Meteo
5 times in under a second (one per model, no spacing) hit its rate limit
every single time -- fixed by making 429 retryable at the client layer plus a
1-second delay between calls. Second, the SAME DAY that fix was validated: on
the very next real tick, all 5 calls STILL hit 429 on every attempt despite
retries and spacing -- Open-Meteo's own documented limit (600 req/min) is
nowhere near 5 sequential calls, so the real cause is something else (likely
a Render-IP-specific or shared free-tier throttle, not fully diagnosable from
here). The actual fix: fetch_live_previous_day1_high_multi() requests all 5
models in ONE combined API call (confirmed live that Open-Meteo supports a
comma-separated models= list) instead of 5 separate ones -- fewer requests,
not smarter retries around a limit whose real trigger isn't fully understood."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.backtest.daily_high_backtest import predicted_prob_bucket
from src.db.job_runs import track_job_run
from src.db.session import get_session
from src.db.upsert import upsert_kalshi_prediction, upsert_multi_model_forecast
from src.ingestion.open_meteo_client import fetch_live_previous_day1_high_multi
from src.kalshi.client import fetch_open_event
from src.scheduling import in_ny_time_window

NY = ZoneInfo("America/New_York")
SERIES_TICKER = "KXHIGHNY"
INDEPENDENT_MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "ukmo_seamless"]

FORECAST_SQL = text(
    """
    SELECT forecast_high_f FROM weather_daily_high_predictions
    WHERE station = 'KNYC' AND target_date = :today AND forecast_high_f IS NOT NULL;
    """
)

HISTORY_SQL = text(
    """
    SELECT d.target_date, d.forecast_high_f, k.settled_value_f
    FROM weather_daily_high_predictions d
    JOIN kalshi_settlements k ON k.target_date = d.target_date
    WHERE d.station = 'KNYC' AND d.forecast_high_f IS NOT NULL AND d.target_date < :today
    ORDER BY d.target_date;
    """
)

MODEL_HISTORY_SQL = text(
    """
    SELECT target_date, model, forecast_high_f FROM multi_model_forecasts
    WHERE target_date < :today AND forecast_high_f IS NOT NULL;
    """
)


def _ensemble_forecast(nws_forecast_high_f: float, model_values: dict) -> float:
    """NWS + whichever independent models actually returned data. Degrades
    gracefully to NWS alone if all 5 fail, rather than blocking anything."""
    values = [nws_forecast_high_f] + list(model_values.values())
    return sum(values) / len(values)


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
        nws_forecast_high_f = float(forecast_row[0])

        try:
            today_model_values = fetch_live_previous_day1_high_multi(INDEPENDENT_MODELS, today)
        except Exception as exc:  # noqa: BLE001 -- a failed fetch shouldn't block NWS-only fallback
            print(f"  Independent-model fetch failed entirely, degrading to NWS-only: {exc}")
            today_model_values = {}

        for model, value in today_model_values.items():
            session.execute(
                upsert_multi_model_forecast(
                    {"target_date": today, "model": model, "forecast_high_f": value, "pulled_at": now}
                )
            )
        session.commit()

        ensemble_forecast_high_f = _ensemble_forecast(nws_forecast_high_f, today_model_values)
        print(f"  Ensemble forecast: {ensemble_forecast_high_f:.1f}F "
              f"(NWS {nws_forecast_high_f:.1f}F + {len(today_model_values)}/5 independent models)")

        history_rows = session.execute(HISTORY_SQL, {"today": today}).all()
        model_by_date: dict = {}
        for target_date, model, value in session.execute(MODEL_HISTORY_SQL, {"today": today}).all():
            model_by_date.setdefault(target_date, {})[model] = float(value)

        residuals = []
        for target_date, nws_forecast, settled in history_rows:
            ensemble = _ensemble_forecast(float(nws_forecast), model_by_date.get(target_date, {}))
            residuals.append(float(settled) - ensemble)

        if not residuals:
            print("No historical residual history yet -- skipping today's predictions.")
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
                ensemble_forecast_high_f, residuals, strike_type, floor_strike, cap_strike
            )
            session.execute(
                upsert_kalshi_prediction(
                    {
                        "target_date": today,
                        "market_ticker": market["ticker"],
                        "strike_type": strike_type,
                        "floor_strike": floor_strike,
                        "cap_strike": cap_strike,
                        "forecast_high_f": ensemble_forecast_high_f,
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
