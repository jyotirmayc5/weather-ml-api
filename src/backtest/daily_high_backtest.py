"""Backtest for the residual-distribution-convolution approach to daily-high
probability estimation (WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5 Step 3's
faster fallback, not the full quantile-regression model -- chosen because
there are only 79 labeled KNYC days right now (2026-05-25 to 2026-08-24, one
season), too few to trust a full ML model without just memorizing noise).

METHODOLOGY NOTE, important: uses forecast_high_f (the raw 9:45am point
forecast) and raw_error_f as the residual, deliberately NOT corrected_high_f/
corrected_error_f. corrected_high_f is backfilled later in the day by
corrected_high_update_job from hourly bias-corrected temperatures -- it does
not exist yet at the 9:45am forecast moment. Using it here would leak
information from later in the day into what's supposed to be a forecast-time
backtest, producing an artificially inflated accuracy that would not hold up
live. This is exactly the backtest-overfitting trap the plan's Sec 6 warns
about.

NOT YET POSSIBLE: comparing against real historical Kalshi market prices,
which is the actual bar Sec 5 Step 4 says the model needs to beat -- there is
no Kalshi API integration or historical market data pulled yet (deliberately
deferred, per the plan's Phase 3 gating). This backtest can only report the
model's own calibration and Brier/log-loss scores against a naive baseline,
not "did it beat the market." Don't present these numbers as a market-beating
result -- they aren't that yet.
"""
import math
import sys
from urllib.parse import unquote, urlsplit

import psycopg2


def load_dsn(env_path=".env"):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"DATABASE_URL not found in {env_path}")


def connect():
    parts = urlsplit(load_dsn())
    return psycopg2.connect(
        host=parts.hostname,
        port=parts.port,
        user=unquote(parts.username),
        password=unquote(parts.password),
        dbname=parts.path.lstrip("/"),
    )


def load_labeled_days(conn):
    """KNYC days with a real actual_high_f AND raw_error_f -- both required
    for a leave-one-out residual backtest."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT target_date, forecast_high_f, raw_error_f, actual_high_f
        FROM weather_daily_high_predictions
        WHERE station = 'KNYC' AND actual_high_f IS NOT NULL AND raw_error_f IS NOT NULL
        ORDER BY target_date;
        """
    )
    return [
        (date, float(forecast), float(residual), float(actual))
        for date, forecast, residual, actual in cur.fetchall()
    ]


def predicted_prob_ge(forecast_high_f: float, residuals: list[float], strike: float) -> float:
    """P(actual_high >= strike), estimated by convolving the point forecast
    with an empirical (not assumed-normal) residual distribution: for each
    held-out residual, would forecast + that residual have cleared the
    strike? The fraction that do is the probability estimate."""
    if not residuals:
        raise ValueError("residuals must be non-empty")
    hits = sum(1 for r in residuals if (forecast_high_f + r) >= strike)
    return hits / len(residuals)


def predicted_prob_bucket(
    forecast_high_f: float,
    residuals: list[float],
    strike_type: str,
    floor_strike: float | None,
    cap_strike: float | None,
) -> float:
    """P(actual lands in a real Kalshi bucket market), built on
    predicted_prob_ge. Kalshi's KXHIGHNY buckets are whole-degree and
    non-overlapping (verified live against KXHIGHNY-26SEP05, see
    WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5): 'less' means actual < cap_strike,
    'between' means floor_strike <= actual <= cap_strike (both inclusive,
    confirmed via that event's own rules_primary text, e.g. "between 79-80"),
    'greater' means actual > floor_strike. Treats the settlement value as an
    effectively whole-degree reading for bucket-matching purposes, matching
    how Kalshi's own rules are phrased -- this is a deliberate simplification
    of the continuous residual model, not an attempt to model sub-degree
    rounding behavior."""
    if strike_type == "less":
        return 1.0 - predicted_prob_ge(forecast_high_f, residuals, cap_strike)
    if strike_type == "between":
        return predicted_prob_ge(forecast_high_f, residuals, floor_strike) - predicted_prob_ge(
            forecast_high_f, residuals, cap_strike + 1
        )
    if strike_type == "greater":
        return predicted_prob_ge(forecast_high_f, residuals, floor_strike + 1)
    raise ValueError(f"unrecognized strike_type {strike_type!r}")


def brier_score(pairs: list[tuple[float, int]]) -> float:
    """Mean squared error between predicted probability and the 0/1 realized
    outcome. Lower is better; 0 is perfect, 0.25 is what an uninformative
    constant 50% predictor scores against a 50/50 true rate."""
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]], eps: float = 1e-6) -> float:
    """Lower is better. Clamps probabilities away from exactly 0/1 so a
    single wrong confident prediction doesn't produce infinite loss."""
    total = 0.0
    for p, o in pairs:
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(pairs)


def leave_one_out_backtest(days: list[tuple], strike_offsets: list[float]):
    """For each day, fits the residual distribution from every OTHER day
    (never the day being scored, to avoid the model trivially "knowing" its
    own held-out error) and scores strikes at each offset from that day's own
    forecast_high_f. Returns the (predicted_prob, realized_outcome) pairs for
    both this model and a naive deterministic baseline (ignores uncertainty
    entirely: predicts 1.0 if forecast clears the strike, else 0.0)."""
    model_pairs = []
    naive_pairs = []

    all_residuals = [r for _, _, r, _ in days]

    for i, (date, forecast, _residual, actual) in enumerate(days):
        held_out_residuals = all_residuals[:i] + all_residuals[i + 1 :]
        for offset in strike_offsets:
            strike = forecast + offset
            outcome = 1 if actual >= strike else 0
            model_pairs.append((predicted_prob_ge(forecast, held_out_residuals, strike), outcome))
            naive_pairs.append((1.0 if forecast >= strike else 0.0, outcome))

    return model_pairs, naive_pairs


def walk_forward_backtest(days: list[tuple], strike_offsets: list[float], min_history: int = 20):
    """Chronological alternative to leave_one_out_backtest, fixing two real
    problems found only after running the leave-one-out version against the
    full (post-migration) 92-day KNYC/Kalshi dataset:

    1. Look-ahead leakage: leave-one-out lets a July residual inform a May
       prediction, which could never happen in real deployment -- on any
       given day, only residuals from STRICTLY EARLIER days would exist yet.
    2. A self-exclusion tautology at wide, sparsely-populated strike offsets
       (found via scripts/kalshi_ground_truth_backtest.py's real run): for an
       offset like +4F where few days' residuals clear it, whether a day's
       OWN residual clears the strike determines whether it's excluded from
       its own held-out pool -- which shifts its predicted probability by
       exactly 1/(n-1), just enough to perfectly separate outcome=1 days from
       outcome=0 days within that offset's narrow probability range. The
       resulting reliability-table bucket then looks either wildly wrong
       (e.g. 20% predicted, 100% realized) or suspiciously perfect, neither of
       which reflects genuine calibration -- see WEATHER_KALSHI_TECHNICAL_PLAN.md
       Sec 5 for the concrete numbers that surfaced this.

    Walk-forward eliminates both: `days` must be pre-sorted chronologically,
    and day i is scored using ONLY days[0:i]'s residuals -- day i's own
    residual is never in the pool by construction, not via explicit
    exclusion, so there's no self-referential relationship between a day's
    predicted probability and its own outcome. `min_history` skips the
    earliest days that don't yet have enough prior residuals to fit a
    meaningful distribution from (an arbitrary distributional choice, not a
    correctness requirement -- 20 is a reasonable floor, not a validated
    optimum)."""
    model_pairs = []
    naive_pairs = []

    for i in range(min_history, len(days)):
        _date, forecast, _residual, actual = days[i]
        prior_residuals = [r for _, _, r, _ in days[:i]]
        for offset in strike_offsets:
            strike = forecast + offset
            outcome = 1 if actual >= strike else 0
            model_pairs.append((predicted_prob_ge(forecast, prior_residuals, strike), outcome))
            naive_pairs.append((1.0 if forecast >= strike else 0.0, outcome))

    return model_pairs, naive_pairs


def reliability_table(pairs: list[tuple[float, int]], n_bins: int = 5):
    """Buckets predictions into n_bins equal-width probability ranges and
    reports predicted-average vs realized-frequency per bucket -- the
    standard calibration check. Sparse with this little data; read the counts,
    not just the percentages."""
    buckets = [[] for _ in range(n_bins)]
    for p, o in pairs:
        idx = min(int(p * n_bins), n_bins - 1)
        buckets[idx].append((p, o))
    rows = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if bucket:
            avg_pred = sum(p for p, _ in bucket) / len(bucket)
            realized = sum(o for _, o in bucket) / len(bucket)
        else:
            avg_pred = realized = None
        rows.append((lo, hi, len(bucket), avg_pred, realized))
    return rows


def main():
    conn = connect()
    days = load_labeled_days(conn)
    conn.close()

    print(f"Loaded {len(days)} labeled KNYC days: {days[0][0]} to {days[-1][0]}")
    print("WARNING: single season only (no winter data) -- see module docstring.\n")

    strike_offsets = [-4, -2, 0, 2, 4]
    model_pairs, naive_pairs = leave_one_out_backtest(days, strike_offsets)

    print(f"Scored {len(model_pairs)} (day, strike) pairs, {len(strike_offsets)} strikes/day\n")

    print("=== Residual-distribution model ===")
    print(f"  Brier score: {brier_score(model_pairs):.4f}")
    print(f"  Log loss:    {log_loss(model_pairs):.4f}")

    print("\n=== Naive baseline (deterministic, ignores uncertainty) ===")
    print(f"  Brier score: {brier_score(naive_pairs):.4f}")
    print(f"  Log loss:    {log_loss(naive_pairs):.4f}")

    print("\n=== Calibration (model) ===")
    print("  range        count   avg predicted   realized freq")
    for lo, hi, count, avg_pred, realized in reliability_table(model_pairs):
        if count:
            print(f"  {lo:.1f}-{hi:.1f}     {count:4d}   {avg_pred:.3f}          {realized:.3f}")
        else:
            print(f"  {lo:.1f}-{hi:.1f}     {count:4d}   --              --")

    print(
        "\nNOTE: no comparison against real Kalshi market prices -- not built "
        "yet, see module docstring. A lower Brier/log-loss than the naive "
        "baseline is a sanity check, not proof of tradeable edge."
    )


if __name__ == "__main__":
    sys.exit(main())
