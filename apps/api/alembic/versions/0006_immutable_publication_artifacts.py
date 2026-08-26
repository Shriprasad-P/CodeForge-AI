"""immutable publication artifacts for P0-4"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_p04_artifacts"
down_revision = "0005_phase7_approval_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("publication_artifact", sa.LargeBinary(), nullable=True))
    op.add_column("agent_runs", sa.Column("publication_artifact_hash", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("publication_artifact_size", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("publication_artifact_version", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("publication_change_manifest", postgresql.JSONB(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("publication_artifact_status", sa.String(32), server_default="legacy", nullable=False),
    )
    op.add_column("agent_runs", sa.Column("publication_artifact_error", sa.String(1024), nullable=True))
    op.add_column("agent_runs", sa.Column("validation_artifact_hash", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("approval_artifact_hash", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("approval_artifact_version", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("approval_base_commit_sha", sa.String(64), nullable=True))


def downgrade() -> None:
    for name in (
        "approval_base_commit_sha",
        "approval_artifact_version",
        "approval_artifact_hash",
        "validation_artifact_hash",
        "publication_artifact_error",
        "publication_artifact_status",
        "publication_change_manifest",
        "publication_artifact_version",
        "publication_artifact_size",
        "publication_artifact_hash",
        "publication_artifact",
    ):
        op.drop_column("agent_runs", name)
