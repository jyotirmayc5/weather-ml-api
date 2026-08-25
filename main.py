"""Deployment entry point. Render's start command (configured in its
dashboard, not in this repo) points at `main:app` -- keep this working
regardless of that setting rather than requiring a coordinated dashboard
change. The real implementation lives in src/prediction_api/main.py."""
from src.prediction_api.main import app

__all__ = ["app"]
