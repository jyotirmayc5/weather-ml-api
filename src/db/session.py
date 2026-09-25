"""DB session factory for jobs/. Reads DATABASE_URL from the environment --
loads .env for local runs (no-op in production, where Render sets real env
vars directly and there's no .env file)."""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_engine = None
_SessionFactory = None


def _psycopg2_dsn(dsn: str) -> str:
    """Forces the psycopg2 dialect explicitly. Real production incident
    (WEATHER_KALSHI_TECHNICAL_PLAN.md Sec 4/job-health notes, 2026-09-25):
    a bare 'postgresql://' scheme's DBAPI resolution is SQLAlchemy's own
    default, not pinned by us -- a routine `pip install -r requirements.txt`
    on a fresh Render build picked up a newer SQLAlchemy release that
    started resolving it to the psycopg (v3) driver instead of psycopg2,
    which isn't installed (only psycopg2-binary is) -- ModuleNotFoundError
    on every single job, all at once, with no code change on our end at
    all. Rewriting the scheme to 'postgresql+psycopg2://' pins the driver
    explicitly so a future SQLAlchemy default change can't silently break
    every job again."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg2://" + dsn[len("postgresql://") :]
    return dsn


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_psycopg2_dsn(os.environ["DATABASE_URL"]))
    return _engine


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()
