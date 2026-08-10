"""phase4 execution jobs

Revision ID: 0003_execution_jobs
Revises: 0002_github_integrations
Create Date: 2026-08-10

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_execution_jobs"
down_revision: Union[str, Sequence[str], None] = "0002_github_integrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("agent_session_id", sa.UUID(), nullable=True),
        sa.Column("repository_connection_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("working_directory", sa.String(length=512), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("output_truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sandbox_id", sa.String(length=128), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_session_id"],
            ["agent_sessions.id"],
            name=op.f("fk_execution_jobs_agent_session_id_agent_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["repository_connection_id"],
            ["repository_connections.id"],
            name=op.f("fk_execution_jobs_repository_connection_id_repository_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_execution_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_jobs")),
    )
    op.create_index(op.f("ix_execution_jobs_user_id"), "execution_jobs", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_execution_jobs_agent_session_id"),
        "execution_jobs",
        ["agent_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_execution_jobs_repository_connection_id"),
        "execution_jobs",
        ["repository_connection_id"],
        unique=False,
    )
    op.create_index(op.f("ix_execution_jobs_status"), "execution_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_execution_jobs_status"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_repository_connection_id"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_agent_session_id"), table_name="execution_jobs")
    op.drop_index(op.f("ix_execution_jobs_user_id"), table_name="execution_jobs")
    op.drop_table("execution_jobs")
