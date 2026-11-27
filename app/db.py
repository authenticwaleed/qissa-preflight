"""Database engine and sessions (SQLAlchemy 2 with psycopg 3)."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    """An engine with pool_pre_ping, so connections dropped by the server are replaced."""
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions with expire_on_commit=False, so rows can still be read after commit."""
    return sessionmaker(engine, expire_on_commit=False)
