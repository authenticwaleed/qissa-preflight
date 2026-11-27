"""Runs the migrations in migrations/versions.

The database is sqlalchemy.url when the caller sets it (the tests point it at their own
database), otherwise DATABASE_URL from app.config, the same setting the app uses.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # The tests run migrations inside the app's process: keep the loggers that already exist.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Lets `alembic check` and `alembic revision --autogenerate` compare app/models.py with the
# database.
target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (`alembic upgrade head --sql`), for review."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # NullPool: a migration run is short-lived and should not keep connections open afterwards.
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Off by default in Alembic; on, so a changed server default counts as drift too.
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
