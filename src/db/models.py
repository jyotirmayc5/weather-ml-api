"""SQLAlchemy models mirroring the real Supabase schema exactly, as confirmed
in WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 0b (pulled directly via the SQL Editor,
not guessed). Describes what already exists -- no schema changes here."""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WeatherObservation(Base):
    __tablename__ = "weather_observations"
    __table_args__ = (UniqueConstraint("station", "observed_time", name="obs_unique"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    observed_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    station: Mapped[Optional[str]] = mapped_column(String)
    actual_temperature_f: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_dewpoint_f: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_humidity_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_pressure_pa: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_pressure_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_wind_speed: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_wind_direction: Mapped[Optional[float]] = mapped_column(Numeric)
    wind_u: Mapped[Optional[float]] = mapped_column(Numeric)
    wind_v: Mapped[Optional[float]] = mapped_column(Numeric)
    visibility_m: Mapped[Optional[float]] = mapped_column(Numeric)
    text_description: Mapped[Optional[str]] = mapped_column(String)


class WeatherPrediction(Base):
    __tablename__ = "weather_predictions"
    __table_args__ = (
        UniqueConstraint("forecast_time", "source", name="weather_predictions_unique_hour"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    forecast_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    forecast_temperature_f: Mapped[Optional[float]] = mapped_column(Numeric)
    corrected_temperature_f: Mapped[Optional[float]] = mapped_column(Numeric)
    predicted_error_f: Mapped[Optional[float]] = mapped_column(Numeric)
    humidity_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    wind_speed: Mapped[Optional[float]] = mapped_column(Numeric)
    wind_direction: Mapped[Optional[float]] = mapped_column(Numeric)
    sky_cover_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    precip_probability_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    dewpoint_f: Mapped[Optional[float]] = mapped_column(Numeric)
    source: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    forecast_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class WeatherDailyHighPrediction(Base):
    __tablename__ = "weather_daily_high_predictions"
    __table_args__ = (
        UniqueConstraint("target_date", "station", name="daily_high_unique"),
        # Second real constraint in prod, kept here for fidelity even though it's
        # dead weight (prediction_created_at is always "now" so this can never
        # collide) -- see plan Sec 0b.
        UniqueConstraint(
            "prediction_created_at",
            "target_date",
            "station",
            name="weather_daily_high_prediction_prediction_created_at_target__key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    target_date: Mapped[Optional[date]] = mapped_column(Date)
    station: Mapped[Optional[str]] = mapped_column(String)
    forecast_high_f: Mapped[Optional[float]] = mapped_column(Numeric)
    forecast_low_f: Mapped[Optional[float]] = mapped_column(Numeric)
    corrected_high_f: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_high_f: Mapped[Optional[float]] = mapped_column(Numeric)
    raw_error_f: Mapped[Optional[float]] = mapped_column(Numeric)
    corrected_error_f: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_humidity_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_dewpoint_f: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_sky_cover_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    max_precip_probability_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_wind_speed: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_wind_sin: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_wind_cos: Mapped[Optional[float]] = mapped_column(Numeric)
    peak_heating_cloud_pct: Mapped[Optional[float]] = mapped_column(Numeric)
    peak_heating_temp_f: Mapped[Optional[float]] = mapped_column(Numeric)
    lead_hours: Mapped[Optional[float]] = mapped_column(Numeric)
    month: Mapped[Optional[int]] = mapped_column()
    day_of_year: Mapped[Optional[int]] = mapped_column()
    source: Mapped[Optional[str]] = mapped_column(String)
    morning_pressure_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    afternoon_pressure_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    pressure_change_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_pressure_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    pressure_6am_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    pressure_12pm_hpa: Mapped[Optional[float]] = mapped_column(Numeric)
    pressure_6pm_hpa: Mapped[Optional[float]] = mapped_column(Numeric)


class KalshiSettlement(Base):
    """New table, doesn't exist by default -- see
    WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 5b for why this exists: our own
    actual_high_f is systematically ~0.64F off from what Kalshi actually
    settles on, so future model training/backtesting needs Kalshi's real
    settlement value as ground truth, not just weather_daily_high_predictions'
    own actual_high_f."""

    __tablename__ = "kalshi_settlements"
    __table_args__ = (UniqueConstraint("event_ticker", name="kalshi_settlements_event_ticker_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_ticker: Mapped[str] = mapped_column(String)
    event_ticker: Mapped[str] = mapped_column(String)
    target_date: Mapped[date] = mapped_column(Date)
    settled_value_f: Mapped[float] = mapped_column(Numeric)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OpenMeteoHistoricalDaily(Base):
    """New table -- see WEATHER_KALSHI_TECHNICAL_PLAN.md: backfills daily
    forecast-vs-actual pairs for seasons before our own NWS-based collection
    started (2026-05-25), from Open-Meteo's Historical Forecast API
    (src/ingestion/open_meteo_client.py). Deliberately kept in its own table,
    not merged into weather_daily_high_predictions -- different underlying
    model (GFS via Open-Meteo, not NWS's official blended forecast), so
    mixing them silently would conflate two methodologically distinct
    sources."""

    __tablename__ = "open_meteo_historical_daily"
    __table_args__ = (
        UniqueConstraint("target_date", "model", name="open_meteo_historical_daily_target_date_model_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_date: Mapped[date] = mapped_column(Date)
    model: Mapped[str] = mapped_column(String)
    forecast_high_f: Mapped[Optional[float]] = mapped_column(Numeric)
    actual_high_f: Mapped[Optional[float]] = mapped_column(Numeric)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRun(Base):
    """New table, doesn't exist in the real Supabase project yet -- see
    WEATHER_KALSHI_TECHNICAL_PLAN.md checklist for the CREATE TABLE to run
    there before any job in jobs/ can be pointed at production. This is the
    only visibility into cron health once n8n's execution log is gone."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    error_message: Mapped[Optional[str]] = mapped_column(String)
