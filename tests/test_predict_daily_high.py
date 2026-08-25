from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def predict_daily_high(**overrides):
    payload = {"forecast_high_f": 70.0, **overrides}
    resp = client.post("/predict-daily-high", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_no_sky_or_pressure_applies_no_bias():
    body = predict_daily_high()
    assert body["bias_f"] == 0.0
    assert body["bias_corrected_high_f"] == 70.0


def test_sky_only_sunny_uncapped():
    # No safety clamp here (unlike /predict) -- the raw SKY_BIAS_F value applies,
    # then gets rounded to 1 decimal in the response same as every other bucket.
    body = predict_daily_high(avg_sky_cover_pct=10)
    assert body["bias_f"] == 1.4
    # bias_f and bias_corrected_high_f are rounded independently from two
    # different underlying floats (1.35 vs 71.35), and binary floating-point
    # rounds those to different neighbors: round(1.35, 1) == 1.4 but
    # round(71.35, 1) == 71.3. So 70 + displayed bias_f (71.4) != displayed
    # bias_corrected_high_f (71.3) here -- a real, live inconsistency, not a bug
    # in this test.
    assert body["bias_corrected_high_f"] == 71.3


def test_sky_only_partly_cloudy_uncapped():
    body = predict_daily_high(avg_sky_cover_pct=40)
    assert body["bias_f"] == 1.9


def test_sky_only_cloudy_uncapped():
    body = predict_daily_high(avg_sky_cover_pct=90)
    assert body["bias_f"] == 2.3


def test_partly_cloudy_and_cloudy_are_distinguishable_here():
    # Unlike /predict, the daily-high endpoint has no clamp, so these two buckets
    # stay numerically distinct (even after 1-decimal rounding).
    partly = predict_daily_high(avg_sky_cover_pct=40)
    cloudy = predict_daily_high(avg_sky_cover_pct=90)
    assert partly["bias_f"] != cloudy["bias_f"]


def test_sky_bucket_boundaries():
    assert predict_daily_high(avg_sky_cover_pct=24.9)["bias_f"] == 1.4
    assert predict_daily_high(avg_sky_cover_pct=25)["bias_f"] == 1.9
    assert predict_daily_high(avg_sky_cover_pct=59.9)["bias_f"] == 1.9
    assert predict_daily_high(avg_sky_cover_pct=60)["bias_f"] == 2.3


def test_pressure_only_falling():
    body = predict_daily_high(pressure_change_hpa=-5)
    assert body["bias_f"] == 2.3


def test_pressure_only_rising():
    body = predict_daily_high(pressure_change_hpa=5)
    assert body["bias_f"] == 1.1


def test_pressure_only_stable():
    body = predict_daily_high(pressure_change_hpa=0)
    assert body["bias_f"] == 1.2


def test_pressure_bucket_boundaries_are_inclusive_to_stable():
    # condition is "< -2" for FALLING and "> 2" for RISING, so exactly -2 and
    # exactly 2 both fall into STABLE.
    assert predict_daily_high(pressure_change_hpa=-2)["bias_f"] == 1.2
    assert predict_daily_high(pressure_change_hpa=2)["bias_f"] == 1.2
    assert predict_daily_high(pressure_change_hpa=-2.1)["bias_f"] == 2.3
    assert predict_daily_high(pressure_change_hpa=2.1)["bias_f"] == 1.1


def test_sky_and_pressure_combine_additively():
    # SUNNY (1.35) + STABLE_PRESSURE (1.18) = 2.53, rounded to 1 decimal -> 2.5
    body = predict_daily_high(avg_sky_cover_pct=10, pressure_change_hpa=0)
    assert body["bias_f"] == 2.5
    assert body["bias_corrected_high_f"] == 72.5


def test_forecast_high_f_is_required():
    resp = client.post("/predict-daily-high", json={"avg_sky_cover_pct": 10})
    assert resp.status_code == 422
