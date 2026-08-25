from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# Bias values measured from SQL in Fahrenheit
SKY_BIAS_F = {
    "CLOUDY": 2.28,
    "PARTLY_CLOUDY": 1.93,
    "SUNNY": 1.35
}

PRESSURE_BIAS_F = {
    "FALLING_PRESSURE": 2.33,
    "RISING_PRESSURE": 1.10,
    "STABLE_PRESSURE": 1.18
}

# Converted once to Celsius for hourly /predict
SKY_BIAS_C = {
    key: value / 1.8
    for key, value in SKY_BIAS_F.items()
}


class WeatherInput(BaseModel):
    timestamp: Optional[str] = None
    temperature_c: float
    dewpoint_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_direction: Optional[float] = None
    precip_probability_pct: Optional[float] = None
    sky_cover_pct: Optional[float] = None


class DailyHighInput(BaseModel):
    forecast_high_f: float
    avg_sky_cover_pct: Optional[float] = None
    pressure_change_hpa: Optional[float] = None


@app.get("/")
def home():
    return {"status": "running"}


@app.post("/predict")
def predict(data: WeatherInput):
    corrected_temp_c = data.temperature_c
    bias_c = 0.0

    if data.sky_cover_pct is not None:
        if data.sky_cover_pct < 25:
            bias_c += SKY_BIAS_C["SUNNY"]
        elif data.sky_cover_pct < 60:
            bias_c += SKY_BIAS_C["PARTLY_CLOUDY"]
        else:
            bias_c += SKY_BIAS_C["CLOUDY"]

        # Safety cap while dataset is small
        # Cap between -1°C and +1°C
        bias_c = max(min(bias_c, 1.0), -1.0)

        corrected_temp_c += bias_c

    return {
        "timestamp": data.timestamp,
        "forecast_temperature_c": data.temperature_c,
        "corrected_temperature_c": round(corrected_temp_c, 2),
        "bias_c": round(bias_c, 2),
        "bias_f": round(bias_c * 1.8, 2),
        "dewpoint_c": data.dewpoint_c,
        "humidity_pct": data.humidity_pct,
        "wind_speed": data.wind_speed,
        "wind_direction": data.wind_direction,
        "precip_probability_pct": data.precip_probability_pct,
        "sky_cover_pct": data.sky_cover_pct
    }


@app.post("/predict-daily-high")
def predict_daily_high(data: DailyHighInput):
    bias_f = 0.0

    if data.avg_sky_cover_pct is not None:
        if data.avg_sky_cover_pct < 25:
            bias_f += SKY_BIAS_F["SUNNY"]
        elif data.avg_sky_cover_pct < 60:
            bias_f += SKY_BIAS_F["PARTLY_CLOUDY"]
        else:
            bias_f += SKY_BIAS_F["CLOUDY"]

    if data.pressure_change_hpa is not None:
        if data.pressure_change_hpa < -2:
            bias_f += PRESSURE_BIAS_F["FALLING_PRESSURE"]
        elif data.pressure_change_hpa > 2:
            bias_f += PRESSURE_BIAS_F["RISING_PRESSURE"]
        else:
            bias_f += PRESSURE_BIAS_F["STABLE_PRESSURE"]

    corrected_high_f = data.forecast_high_f + bias_f

    return {
        "forecast_high_f": data.forecast_high_f,
        "bias_f": round(bias_f, 1),
        "bias_corrected_high_f": round(corrected_high_f, 1),
        "avg_sky_cover_pct": data.avg_sky_cover_pct,
        "pressure_change_hpa": data.pressure_change_hpa
    }
