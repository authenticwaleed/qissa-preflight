"""Create audio_files (one row per unique audio) and uploads (one row per upload request).

Revision ID: 0001
Revises:
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Names are spelled out rather than left to the naming convention, so this migration never
# changes when app/models.py does. They are wrapped in op.f() because Alembic applies that
# convention here as well and would turn ck_audio_files_sha256_length into
# ck_audio_files_ck_audio_files_sha256_length.


def upgrade() -> None:
    op.create_table(
        "audio_files",
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("storage_key", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Double(), nullable=False),
        sa.Column("bitrate_kbps", sa.Integer(), nullable=False),
        sa.Column("bitrate_mode", sa.String(length=8), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("channels", sa.SmallInteger(), nullable=False),
        sa.Column("quality_score", sa.SmallInteger(), nullable=False),
        sa.Column("quality_deductions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_audio_files")),
        sa.CheckConstraint("octet_length(sha256) = 32", name=op.f("ck_audio_files_sha256_length")),
        sa.CheckConstraint(
            "quality_score BETWEEN 1 AND 10", name=op.f("ck_audio_files_quality_score_range")
        ),
        sa.CheckConstraint(
            "bitrate_mode IN ('CBR', 'VBR', 'ABR', 'UNKNOWN')",
            name=op.f("ck_audio_files_bitrate_mode"),
        ),
    )
    op.create_table(
        "uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("book_id", sa.String(length=100), nullable=True),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("is_duration_outlier", sa.Boolean(), nullable=False),
        sa.Column("outlier_reason", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uploads")),
        sa.ForeignKeyConstraint(
            ["sha256"], ["audio_files.sha256"], name=op.f("fk_uploads_sha256_audio_files")
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"], ["uploads.id"], name=op.f("fk_uploads_duplicate_of_id_uploads")
        ),
    )
    op.create_index(op.f("ix_uploads_book_id"), "uploads", ["book_id"])
    # At most one original upload per audio; it also serves the "find the original" lookup.
    op.create_index(
        op.f("uq_uploads_original_sha256"),
        "uploads",
        ["sha256"],
        unique=True,
        postgresql_where=sa.text("duplicate_of_id IS NULL"),
    )


def downgrade() -> None:
    # Indexes and constraints go with their tables; uploads first, since it references audio_files.
    op.drop_table("uploads")
    op.drop_table("audio_files")
