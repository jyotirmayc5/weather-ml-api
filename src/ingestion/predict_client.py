"""Client for weather-ml-api's own /predict endpoint -- deliberately kept
separate from the model's implementation (src/prediction_api/main.py). Matches
the real n8n 'prediction engine API' node: POST with retry on failure, 5s wait
between tries. n8n's exact max-retry count for `retryOnFail: true` isn't
captured in the export (only `waitBetweenTries: 5000` is) -- 3 attempts here
is a reasonable match for n8n's documented default, not a confirmed exact
figure."""
import os

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

PREDICT_API_URL = os.environ.get("PREDICT_API_URL", "https://weather-ml-api-uv0s.onrender.com")


@retry(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True)
def call_predict(payload: dict) -> dict:
    with httpx.Client(base_url=PREDICT_API_URL, timeout=120) as client:
        resp = client.post("/predict", json=payload)
        resp.raise_for_status()
        return resp.json()
