import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.models import Base

TEST_DB_URL = "postgresql+psycopg2://test:test@localhost:5433/weather_test"


@pytest.fixture(scope="session")
def pg_engine():
    engine = create_engine(TEST_DB_URL)
    last_error = None
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except Exception as e:  # pragma: no cover - startup race only
            last_error = e
            time.sleep(1)
    else:
        pytest.skip(f"local test Postgres not reachable at {TEST_DB_URL}: {last_error}")

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(pg_engine):
    connection = pg_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def plain_session(pg_engine):
    """For job tests: jobs call session.commit() internally (job_runs
    tracking, then the actual writes) as real, intended behavior, not
    incidentally -- the rollback-wrapped db_session fixture's savepoint
    nesting isn't set up to handle that safely. Uses real commits against the
    ephemeral tmpfs test Postgres instead; job tests should use distinctive
    test data (an unusual date/station) rather than relying on rollback for
    isolation."""
    session = Session(bind=pg_engine)
    yield session
    session.close()
