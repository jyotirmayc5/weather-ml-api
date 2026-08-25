import math

import pytest

from src.backtest.daily_high_backtest import (
    brier_score,
    leave_one_out_backtest,
    log_loss,
    predicted_prob_ge,
    reliability_table,
)


def test_predicted_prob_ge_counts_fraction_of_residuals_clearing_strike():
    # forecast=70, residuals [-2,-1,0,1,2] -> actual candidates [68,69,70,71,72]
    residuals = [-2, -1, 0, 1, 2]
    assert predicted_prob_ge(70, residuals, strike=70) == 3 / 5  # 70,71,72 clear
    assert predicted_prob_ge(70, residuals, strike=72) == 1 / 5  # only 72 clears
    assert predicted_prob_ge(70, residuals, strike=100) == 0.0
    assert predicted_prob_ge(70, residuals, strike=0) == 1.0


def test_brier_score_perfect_predictions_score_zero():
    assert brier_score([(1.0, 1), (0.0, 0), (1.0, 1)]) == 0.0


def test_brier_score_worst_case_confident_and_wrong():
    assert brier_score([(1.0, 0), (0.0, 1)]) == 1.0


def test_brier_score_uninformative_half_predictor():
    # constant 0.5 prediction against a 50/50 true split -> (0.5)^2 average
    pairs = [(0.5, 1), (0.5, 0)]
    assert brier_score(pairs) == 0.25


def test_log_loss_perfect_prediction_near_zero():
    pairs = [(1.0, 1), (0.0, 0)]
    assert log_loss(pairs) < 1e-4


def test_log_loss_confident_wrong_prediction_is_large():
    pairs = [(0.99, 0)]
    assert log_loss(pairs) > 4  # -ln(0.01) ~= 4.6


def test_log_loss_matches_hand_computed_value():
    # single case: p=0.7, outcome=1 -> loss = -ln(0.7)
    pairs = [(0.7, 1)]
    assert math.isclose(log_loss(pairs), -math.log(0.7), rel_tol=1e-9)


def test_leave_one_out_never_uses_the_held_out_days_own_residual():
    # 3 days, all with the SAME extreme residual value -- if day i's own
    # residual were included when scoring day i, every strike at the day's
    # own forecast+residual would predict probability 1.0 (100% confident,
    # trivially "correct" against itself). With it correctly excluded, the
    # remaining 2 residuals are used instead, which differ from day i's own.
    days = [
        ("d1", 70.0, 5.0, 75.0),  # forecast=70, residual=5, actual=75
        ("d2", 70.0, 5.0, 75.0),
        ("d3", 70.0, -5.0, 65.0),
    ]
    model_pairs, _ = leave_one_out_backtest(days, strike_offsets=[5.0])
    # day3's strike = 70+5 = 75; day3's held-out residuals are [5.0, 5.0]
    # (from d1, d2) -> forecast(70)+5=75 clears strike(75) both times -> 1.0
    # even though day3's OWN residual (-5) would have predicted 0 confidence.
    assert model_pairs[2] == (1.0, 0)  # actual=65 does not clear strike=75


def test_naive_baseline_ignores_uncertainty_entirely():
    # needs >= 2 days so leave-one-out has at least one residual to hold out
    days = [("d1", 70.0, 3.0, 73.0), ("d2", 70.0, -1.0, 69.0)]
    _, naive_pairs = leave_one_out_backtest(days, strike_offsets=[0.0])
    # strike = forecast = 70 for both days; naive predicts 1.0 purely because
    # forecast>=strike, regardless of any residual/uncertainty
    assert naive_pairs == [(1.0, 1), (1.0, 0)]  # actual 73>=70, 69<70


def test_reliability_table_buckets_and_averages_correctly():
    pairs = [(0.05, 0), (0.15, 1), (0.85, 1), (0.95, 1)]
    rows = reliability_table(pairs, n_bins=5)
    # bucket 0 covers [0.0, 0.2) -- both 0.05 and 0.15 land here
    low_bucket = rows[0]
    assert low_bucket[2] == 2
    assert low_bucket[3] == pytest.approx(0.10)
    assert low_bucket[4] == pytest.approx(0.5)  # one 0-outcome, one 1-outcome
    high_bucket = rows[4]  # 0.8-1.0
    assert high_bucket[2] == 2
    assert high_bucket[3] == pytest.approx(0.90)
    assert high_bucket[4] == 1.0
