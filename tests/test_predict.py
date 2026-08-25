from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

BASE_INPUT = {
    "timestamp": "2026-06-16T15:00:00Z",
    "temperature_c": 20.0,
}


def predict(**overrides):
    payload = {**BASE_INPUT, **overrides}
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_no_sky_cover_applies_no_bias():
    body = predict()
    assert body["bias_c"] == 0.0
    assert body["corrected_temperature_c"] == body["forecast_temperature_c"]


def test_sunny_bucket_under_cap():
    # sky_cover_pct < 25 -> SUNNY, SKY_BIAS_C = 1.35 / 1.8 = 0.75, under the +/-1.0 cap
    body = predict(sky_cover_pct=10)
    assert body["bias_c"] == 0.75
    assert body["bias_f"] == 1.35
    assert body["corrected_temperature_c"] == 20.75


def test_partly_cloudy_bucket_gets_clamped_to_cap():
    # 25 <= sky_cover_pct < 60 -> PARTLY_CLOUDY, raw SKY_BIAS_C = 1.93 / 1.8 = 1.0722,
    # which exceeds the +/-1.0 safety cap, so it clamps down to exactly 1.0.
    body = predict(sky_cover_pct=40)
    assert body["bias_c"] == 1.0


def test_cloudy_bucket_also_gets_clamped_to_the_same_cap():
    # sky_cover_pct >= 60 -> CLOUDY, raw SKY_BIAS_C = 2.28 / 1.8 = 1.2667, also > cap -> 1.0.
    # Currently /predict cannot numerically distinguish CLOUDY from PARTLY_CLOUDY:
    # both collapse to the same clamped bias_c. This is real, live behavior (see
    # WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 3a), not a test bug -- pin it down explicitly.
    body = predict(sky_cover_pct=90)
    assert body["bias_c"] == 1.0


def test_partly_cloudy_and_cloudy_are_numerically_indistinguishable_today():
    partly = predict(sky_cover_pct=40)
    cloudy = predict(sky_cover_pct=90)
    assert partly["bias_c"] == cloudy["bias_c"] == 1.0


def test_sky_cover_bucket_boundaries():
    # boundary is "< 25" for SUNNY, so exactly 25 falls into PARTLY_CLOUDY
    assert predict(sky_cover_pct=24.9)["bias_c"] == 0.75
    assert predict(sky_cover_pct=25)["bias_c"] == 1.0
    # boundary is "< 60" for PARTLY_CLOUDY, so exactly 60 falls into CLOUDY
    assert predict(sky_cover_pct=59.9)["bias_c"] == 1.0
    assert predict(sky_cover_pct=60)["bias_c"] == 1.0


def test_passthrough_fields_are_echoed_unchanged():
    body = predict(
        sky_cover_pct=10,
        dewpoint_c=12.5,
        humidity_pct=55,
        wind_speed=8,
        wind_direction=270,
        precip_probability_pct=20,
        timestamp="2026-01-01T00:00:00Z",
    )
    assert body["dewpoint_c"] == 12.5
    assert body["humidity_pct"] == 55
    assert body["wind_speed"] == 8
    assert body["wind_direction"] == 270
    assert body["precip_probability_pct"] == 20
    assert body["timestamp"] == "2026-01-01T00:00:00Z"


def test_temperature_c_is_required():
    resp = client.post("/predict", json={"timestamp": "2026-06-16T15:00:00Z"})
    assert resp.status_code == 422
