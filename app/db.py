"""Database engine and sessions (SQLAlchemy 2 with psycopg 3)."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    """An engine with pool_pre_ping, so connections dropped by the server are replaced."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        # psycopg would wait up to 130 s for a host that does not answer, holding a worker, or
        # the health check, all that time.
        connect_args={"connect_timeout": 5},
        # Statement values, such as a filename or book id, stay out of exception messages and so
        # out of the logs.
        hide_parameters=True,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions with expire_on_commit=False, so rows can still be read after commit."""
    return sessionmaker(engine, expire_on_commit=False)
