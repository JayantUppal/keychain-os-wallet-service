"""Database engine, session factory, and table creation."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import config


class Base(DeclarativeBase):
    """Base class for all ORM models."""


engine = create_engine(config.database_url, pool_pre_ping=True, future=True)

# expire_on_commit=False lets us read model attributes after a commit,
# which keeps the service layer simple.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables if they do not exist."""
    from . import models  # noqa: F401  (import registers models on Base.metadata)

    Base.metadata.create_all(engine)
