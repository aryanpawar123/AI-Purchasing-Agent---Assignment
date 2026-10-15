"""
Database engine and session factory.

Uses SQLite for simplicity. The DB_PATH env var controls the file location;
tests can override it to use an in-memory database.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import settings


# For in-memory SQLite, we must use StaticPool so all sessions share
# the same underlying connection (otherwise each gets a separate DB).
_connect_args = {"check_same_thread": False}
_extra: dict = {}
if settings.db_path == ":memory:":
    _extra["poolclass"] = StaticPool

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args=_connect_args,
    echo=False,
    **_extra,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


def get_db():
    """FastAPI dependency that yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Called once at startup or by seed.py."""
    Base.metadata.create_all(bind=engine)


def reset_db():
    """Drop and recreate all tables. Used by tests and eval harness."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
