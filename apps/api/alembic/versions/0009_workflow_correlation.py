"""add durable workflow correlation identifiers"""

from alembic import op
import sqlalchemy as sa


revision = "0009_workflow_correlation"
down_revision = "0008_durable_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("agent_runs", "execution_jobs"):
        op.add_column(table, sa.Column("workflow_correlation_id", sa.UUID(), nullable=True))
        # Existing rows are correlated to themselves; new rows receive a
        # generated UUID from the SQLAlchemy model.
        op.execute(
            sa.text(
                f"UPDATE {table} SET workflow_correlation_id = id "
                "WHERE workflow_correlation_id IS NULL"
            )
        )
        op.alter_column(table, "workflow_correlation_id", nullable=False)
        op.create_index(
            f"ix_{table}_workflow_correlation_id",
            table,
            ["workflow_correlation_id"],
        )


def downgrade() -> None:
    for table in ("execution_jobs", "agent_runs"):
        op.drop_index(f"ix_{table}_workflow_correlation_id", table_name=table)
        op.drop_column(table, "workflow_correlation_id")
