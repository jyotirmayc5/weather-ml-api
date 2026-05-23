from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

class WeatherInput(BaseModel):
    timestamp: Optional[str] = None

    temperature_c: float
    dewpoint_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_direction: Optional[float] = None
    precip_probability_pct: Optional[float] = None
    sky_cover_pct: Optional[float] = None

@app.get("/")
def home():
    return {
        "status": "running"
    }

@app.post("/predict")
def predict(data: WeatherInput):

    corrected_temp = data.temperature_c

    if data.humidity_pct is not None and data.humidity_pct > 80:
        corrected_temp -= 1

    if data.sky_cover_pct is not None and data.sky_cover_pct > 80:
        corrected_temp -= 0.5

    if data.wind_speed is not None and data.wind_speed > 20:
        corrected_temp -= 0.5

    return {
        "timestamp": data.timestamp,

        "forecast_temperature_c": data.temperature_c,
        "corrected_temperature_c": corrected_temp,

        "dewpoint_c": data.dewpoint_c,
        "humidity_pct": data.humidity_pct,
        "wind_speed": data.wind_speed,
        "wind_direction": data.wind_direction,
        "precip_probability_pct": data.precip_probability_pct,
        "sky_cover_pct": data.sky_cover_pct
    }